"""Synchronous POST replay runner with injected condition telemetry."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Protocol

from .contracts import (
    BenchmarkManifestV1,
    ConditionSpecV1,
    ConditionTelemetryV1,
    RecommendationResponseV1,
    RequestMeasurementV1,
)
from .replay import CandidateUniverse, ReplayCorpus


class JsonTransport(Protocol):
    def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]: ...

    def close(self) -> None: ...


class ConditionDriver(Protocol):
    def activate(
        self, condition: ConditionSpecV1, telemetry: "ConditionTelemetryRecorder"
    ) -> ContextManager[None]: ...


class HttpxTransport:
    def __init__(self, endpoint: str) -> None:
        import httpx

        self._client = httpx.Client(base_url=endpoint)

    def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        import httpx

        try:
            response = self._client.post(path, json=payload, timeout=timeout_seconds)
        except httpx.TimeoutException as error:
            raise TimeoutError("recommendation POST timed out") from error
        return response.status_code, response.json()

    def close(self) -> None:
        self._client.close()


class AlreadyConfiguredConditionDriver:
    """CLI seam when the operator has configured the selected condition externally."""

    @contextmanager
    def activate(
        self, condition: ConditionSpecV1, telemetry: "ConditionTelemetryRecorder"
    ):
        yield


class ConditionTelemetryRecorder:
    def __init__(self, manifest: BenchmarkManifestV1, condition: ConditionSpecV1, clock):
        self._manifest = manifest
        self._condition = condition
        self._clock = clock
        self.rows: list[ConditionTelemetryV1] = []

    def trainer_step(self, *, step: int, duration_ns: int) -> None:
        self.rows.append(
            ConditionTelemetryV1(
                schemaVersion=1,
                benchmarkRunId=self._manifest.benchmarkRunId,
                condition=self._condition.name,
                observedAtMonotonicNs=self._clock(),
                kind="trainer_step",
                trainerStep=step,
                durationNs=duration_ns,
            )
        )

    def kafka_lag(self, lag: int) -> None:
        self.rows.append(
            ConditionTelemetryV1(
                schemaVersion=1,
                benchmarkRunId=self._manifest.benchmarkRunId,
                condition=self._condition.name,
                observedAtMonotonicNs=self._clock(),
                kind="kafka_lag",
                kafkaLag=lag,
            )
        )

    def synchronization(self, *, version: int, duration_ns: int) -> None:
        self.rows.append(
            ConditionTelemetryV1(
                schemaVersion=1,
                benchmarkRunId=self._manifest.benchmarkRunId,
                condition=self._condition.name,
                observedAtMonotonicNs=self._clock(),
                kind="synchronization",
                synchronizationVersion=version,
                durationNs=duration_ns,
            )
        )


class MeasuredConditionOperations:
    """Thin integration adapter around existing trainer and synchronizer calls."""

    def __init__(
        self,
        recorder: ConditionTelemetryRecorder,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._recorder = recorder
        self._clock = monotonic_ns

    def trainer_step(self, *, step: int, operation: Callable[[], Any]) -> Any:
        started = self._clock()
        result = operation()
        self._recorder.trainer_step(step=step, duration_ns=self._clock() - started)
        return result

    def kafka_lag(self, lag: int) -> None:
        self._recorder.kafka_lag(lag)

    def synchronization(self, *, version: int, operation: Callable[[], Any]) -> Any:
        started = self._clock()
        result = operation()
        self._recorder.synchronization(
            version=version,
            duration_ns=self._clock() - started,
        )
        return result


@dataclass(frozen=True, slots=True)
class SuiteResult:
    measurements: tuple[RequestMeasurementV1, ...]
    telemetry: tuple[ConditionTelemetryV1, ...]


def _failed_measurement(
    manifest: BenchmarkManifestV1,
    condition: ConditionSpecV1,
    replay_row,
    index: int,
    warmup: bool,
    started: int,
    completed: int,
    queue_delay: int,
    outcome: str,
    error_type: str,
    status: int | None = None,
) -> RequestMeasurementV1:
    return RequestMeasurementV1(
        schemaVersion=1,
        benchmarkRunId=manifest.benchmarkRunId,
        condition=condition.name,
        requestId=replay_row.request.requestId,
        scheduleIndex=index,
        scheduleOffsetNs=replay_row.scheduleOffsetNs,
        isWarmup=warmup,
        startedAtMonotonicNs=started,
        completedAtMonotonicNs=completed,
        queueDelayNs=queue_delay,
        clientTotalNs=completed - started,
        outcome=outcome,
        httpStatus=status,
        errorType=error_type,
    )


def run_suite(
    manifest: BenchmarkManifestV1,
    replay: ReplayCorpus,
    universe: CandidateUniverse,
    *,
    transport_factory: Callable[[], JsonTransport],
    condition_driver: ConditionDriver,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> SuiteResult:
    _validate_inputs(manifest, replay, universe)
    measurements: list[RequestMeasurementV1] = []
    telemetry_rows: list[ConditionTelemetryV1] = []
    for condition in manifest.conditions:
        result = run_condition(
            manifest,
            condition,
            replay,
            universe,
            transport=transport_factory(),
            condition_driver=condition_driver,
            monotonic_ns=monotonic_ns,
            sleep=sleep,
            validate_inputs=False,
        )
        measurements.extend(result.measurements)
        telemetry_rows.extend(result.telemetry)
    return SuiteResult(tuple(measurements), tuple(telemetry_rows))


def _validate_inputs(
    manifest: BenchmarkManifestV1,
    replay: ReplayCorpus,
    universe: CandidateUniverse,
) -> None:
    if replay.sha256 != manifest.requestCorpusSha256:
        raise ValueError("request corpus checksum differs from the manifest")
    if universe.sha256 != manifest.candidateUniverseSha256:
        raise ValueError("candidate universe checksum differs from the manifest")
    if tuple(row.scheduleOffsetNs for row in replay.rows) != manifest.scheduleOffsetsNs:
        raise ValueError("replay schedule differs from the manifest")


def run_condition(
    manifest: BenchmarkManifestV1,
    condition: ConditionSpecV1,
    replay: ReplayCorpus,
    universe: CandidateUniverse,
    *,
    transport: JsonTransport,
    condition_driver: ConditionDriver,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
    validate_inputs: bool = True,
) -> SuiteResult:
    if validate_inputs:
        _validate_inputs(manifest, replay, universe)
    measurements: list[RequestMeasurementV1] = []
    recorder = ConditionTelemetryRecorder(manifest, condition, monotonic_ns)
    try:
        with condition_driver.activate(condition, recorder):
            base_ns = monotonic_ns()
            for index, replay_row in enumerate(replay.rows):
                    target_ns = base_ns + replay_row.scheduleOffsetNs
                    remaining_ns = target_ns - monotonic_ns()
                    if remaining_ns > 0:
                        sleep(remaining_ns / 1_000_000_000)
                    started = monotonic_ns()
                    queue_delay = max(0, started - target_ns)
                    try:
                        status, body = transport.post_json(
                            manifest.requestPath,
                            replay_row.request.model_dump(mode="json"),
                            manifest.timeoutSeconds,
                        )
                    except TimeoutError as error:
                        completed = monotonic_ns()
                        measurements.append(
                            _failed_measurement(
                                manifest,
                                condition,
                                replay_row,
                                index,
                                index < manifest.warmupCount,
                                started,
                                completed,
                                queue_delay,
                                "timeout",
                                type(error).__name__,
                            )
                        )
                        continue
                    except Exception as error:
                        completed = monotonic_ns()
                        measurements.append(
                            _failed_measurement(
                                manifest,
                                condition,
                                replay_row,
                                index,
                                index < manifest.warmupCount,
                                started,
                                completed,
                                queue_delay,
                                "error",
                                type(error).__name__,
                            )
                        )
                        continue
                    completed = monotonic_ns()
                    if not 200 <= status < 300:
                        measurements.append(
                            _failed_measurement(
                                manifest,
                                condition,
                                replay_row,
                                index,
                                index < manifest.warmupCount,
                                started,
                                completed,
                                queue_delay,
                                "error",
                                f"http_{status}",
                                status,
                            )
                        )
                        continue

                    response = RecommendationResponseV1.model_validate(body)
                    if (
                        response.requestId != replay_row.request.requestId
                        or response.runId != replay_row.request.runId
                    ):
                        raise ValueError("recommendation response identity mismatch")
                    if response.modelId != condition.expectedModelId:
                        raise ValueError("recommendation model identity differs from condition")
                    if response.embeddingSpaceId != condition.expectedEmbeddingSpaceId:
                        raise ValueError("recommendation embedding space differs from condition")
                    if (
                        not condition.syncEnabled
                        and response.pgvectorSnapshotSha256
                        != condition.expectedPgvectorSnapshotSha256
                    ):
                        raise ValueError("pgvector snapshot differs from condition")
                    universe.validate_candidates(
                        requester=replay_row.request.creatorId,
                        response_rows=response.candidates,
                    )
                    client_total = completed - started
                    server_total = response.timingsNs["serverTotal"]
                    measurements.append(
                        RequestMeasurementV1(
                            schemaVersion=1,
                            benchmarkRunId=manifest.benchmarkRunId,
                            condition=condition.name,
                            requestId=replay_row.request.requestId,
                            scheduleIndex=index,
                            scheduleOffsetNs=replay_row.scheduleOffsetNs,
                            isWarmup=index < manifest.warmupCount,
                            startedAtMonotonicNs=started,
                            completedAtMonotonicNs=completed,
                            queueDelayNs=queue_delay,
                            clientTotalNs=client_total,
                            clientOverheadNs=client_total - server_total,
                            outcome="success",
                            httpStatus=status,
                            serverTimingsNs=response.timingsNs,
                            modelId=response.modelId,
                            modelVersion=response.modelVersion,
                            retrievalBackend=response.retrievalBackend,
                            pgvectorSnapshotSha256=response.pgvectorSnapshotSha256,
                            backendSnapshotSha256=response.backendSnapshotSha256,
                            queryVectorSha256=response.queryVectorSha256,
                            candidateCount=len(response.candidates),
                        )
                    )
    finally:
        transport.close()
    return SuiteResult(tuple(measurements), tuple(recorder.rows))


__all__ = [
    "AlreadyConfiguredConditionDriver",
    "ConditionDriver",
    "ConditionTelemetryRecorder",
    "HttpxTransport",
    "JsonTransport",
    "MeasuredConditionOperations",
    "SuiteResult",
    "run_condition",
    "run_suite",
]
