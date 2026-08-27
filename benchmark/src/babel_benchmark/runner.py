"""Synchronous POST replay runner with injected condition telemetry."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Protocol
from collections.abc import Iterator, Sequence

from .contracts import (
    BenchmarkManifestV1,
    BenchmarkManifestV2,
    ConditionSpecV1,
    ConditionSpecV2,
    ConditionTelemetryV1,
    RecommendationResponseV1,
    RecommendationResponseV2,
    RequestMeasurementV1,
    RequestMeasurementV2,
)
from .replay import CandidateUniverse, ReplayCorpus
from .topology import infer_v1_condition_identity
from .resources import PeriodicResourceCollector, ResourceObservationV1


class JsonTransport(Protocol):
    def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]: ...

    def close(self) -> None: ...


class AsyncJsonTransport(Protocol):
    async def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]: ...

    async def close(self) -> None: ...


class ConditionDriver(Protocol):
    def activate(
        self,
        condition: ConditionSpecV1 | ConditionSpecV2,
        telemetry: "ConditionTelemetryRecorder",
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


class AsyncHttpxTransport:
    def __init__(self, endpoint: str, *, max_connections: int) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            base_url=endpoint,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
        )

    async def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        import httpx

        try:
            response = await self._client.post(
                path, json=payload, timeout=timeout_seconds
            )
        except httpx.TimeoutException as error:
            raise TimeoutError("recommendation POST timed out") from error
        return response.status_code, response.json()

    async def close(self) -> None:
        await self._client.aclose()


class AlreadyConfiguredConditionDriver:
    """CLI seam when the operator has configured the selected condition externally."""

    @contextmanager
    def activate(
        self,
        condition: ConditionSpecV1 | ConditionSpecV2,
        telemetry: "ConditionTelemetryRecorder",
    ):
        yield


class ConditionTelemetryRecorder:
    def __init__(
        self,
        manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
        condition: ConditionSpecV1 | ConditionSpecV2,
        clock,
    ):
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


@dataclass(frozen=True, slots=True)
class ConcurrentConditionResult(Sequence[RequestMeasurementV2]):
    measurements: tuple[RequestMeasurementV2, ...]
    resources: tuple[ResourceObservationV1, ...]

    def __getitem__(self, index: int | slice):
        return self.measurements[index]

    def __len__(self) -> int:
        return len(self.measurements)

    def __iter__(self) -> Iterator[RequestMeasurementV2]:
        return iter(self.measurements)


def _parse_recommendation_response(
    body: dict[str, Any],
) -> RecommendationResponseV1 | RecommendationResponseV2:
    contract = (
        RecommendationResponseV2
        if body.get("schemaVersion") == 2
        else RecommendationResponseV1
    )
    return contract.model_validate(body)


def _cache_evidence(
    response: RecommendationResponseV1 | RecommendationResponseV2,
) -> tuple[str, str | None]:
    if not isinstance(response, RecommendationResponseV2):
        return "unavailable", None
    cache_status = {
        "cache_hit": "hit",
        "qwen_encode": "miss",
        "pgvector_load": "bypass",
    }[response.sourceVectorOrigin]
    return cache_status, response.sourceVectorOrigin


def _validate_serving_identity(
    condition: ConditionSpecV1 | ConditionSpecV2,
    response: RecommendationResponseV1 | RecommendationResponseV2,
) -> None:
    identity = (
        condition.identity
        if isinstance(condition, ConditionSpecV2)
        else infer_v1_condition_identity(condition)
    )
    if response.embeddingSpaceId != condition.expectedEmbeddingSpaceId:
        raise ValueError("recommendation embedding space differs from condition")
    if response.retrievalBackend != identity.retrievalBackend:
        raise ValueError("retrieval backend differs from condition")
    if isinstance(condition, ConditionSpecV1):
        if response.modelId != condition.expectedModelId:
            raise ValueError("recommendation model identity differs from condition")
        if (
            not condition.syncEnabled
            and response.pgvectorSnapshotSha256
            != condition.expectedPgvectorSnapshotSha256
        ):
            raise ValueError("pgvector snapshot differs from condition")
        return

    permitted = {
        (
            condition.expectedModelId,
            condition.expectedModelVersion,
            condition.expectedPgvectorSnapshotSha256,
            condition.expectedBackendSnapshotSha256,
        ),
        *(
            (
                target.modelId,
                target.modelVersion,
                target.pgvectorSnapshotSha256,
                target.backendSnapshotSha256,
            )
            for target in condition.activationTargets
        ),
    }
    returned = (
        response.modelId,
        response.modelVersion,
        response.pgvectorSnapshotSha256,
        response.backendSnapshotSha256,
    )
    if returned not in permitted:
        raise ValueError("serving state is outside the pinned model lineage")


def _failed_measurement(
    manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
    condition: ConditionSpecV1 | ConditionSpecV2,
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
    manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
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
    manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
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
    manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
    condition: ConditionSpecV1 | ConditionSpecV2,
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

                response = _parse_recommendation_response(body)
                if (
                    response.requestId != replay_row.request.requestId
                    or response.runId != replay_row.request.runId
                ):
                    raise ValueError("recommendation response identity mismatch")
                _validate_serving_identity(condition, response)
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


async def run_concurrent_condition(
    manifest: BenchmarkManifestV1 | BenchmarkManifestV2,
    condition: ConditionSpecV1 | ConditionSpecV2,
    replay: ReplayCorpus,
    universe: CandidateUniverse,
    *,
    transport: AsyncJsonTransport,
    schedule_mode: str,
    max_in_flight: int,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    async_sleep: Callable[[float], Any] = asyncio.sleep,
    trainer_model_version: int | None = None,
    resource_collector: PeriodicResourceCollector | None = None,
) -> ConcurrentConditionResult:
    """Replay one deterministic bounded concurrent schedule."""
    _validate_inputs(manifest, replay, universe)
    if schedule_mode not in {"closed_loop", "open_loop"}:
        raise ValueError("schedule mode must be closed_loop or open_loop")
    if max_in_flight <= 0:
        raise ValueError("max_in_flight must be positive")
    identity = (
        condition.identity
        if isinstance(condition, ConditionSpecV2)
        else infer_v1_condition_identity(condition)
    )
    condition_id = identity.stable_key
    base_ns = monotonic_ns()
    semaphore = asyncio.Semaphore(max_in_flight)
    active = 0
    active_lock = asyncio.Lock()
    measurements: list[RequestMeasurementV2] = []

    async def execute(index: int) -> None:
        nonlocal active
        replay_row = replay.rows[index]
        intended = base_ns + replay_row.scheduleOffsetNs
        remaining = intended - monotonic_ns()
        if remaining > 0:
            await async_sleep(remaining / 1_000_000_000)
        async with semaphore:
            async with active_lock:
                active += 1
                in_flight = active
            started = monotonic_ns()
            queue_delay = max(0, started - intended)
            common: dict[str, Any] = {
                "schemaVersion": 2,
                "benchmarkRunId": manifest.benchmarkRunId,
                "conditionId": condition_id,
                "requestId": replay_row.request.requestId,
                "scheduleIndex": index,
                "scheduleMode": schedule_mode,
                "intendedStartMonotonicNs": intended,
                "actualStartMonotonicNs": started,
                "queueDelayNs": queue_delay,
                "inFlightAtStart": in_flight,
                "isWarmup": index < manifest.warmupCount,
                "trainerModelVersion": trainer_model_version,
            }
            try:
                status, body = await transport.post_json(
                    manifest.requestPath,
                    replay_row.request.model_dump(mode="json"),
                    manifest.timeoutSeconds,
                )
                completed = monotonic_ns()
                if not 200 <= status < 300:
                    measurements.append(
                        RequestMeasurementV2(
                            **common,
                            completedAtMonotonicNs=completed,
                            clientTotalNs=completed - started,
                            outcome="error",
                            httpStatus=status,
                            errorType=f"http_{status}",
                        )
                    )
                    return
                response = _parse_recommendation_response(body)
                if (
                    response.requestId != replay_row.request.requestId
                    or response.runId != replay_row.request.runId
                ):
                    raise ValueError("recommendation response identity mismatch")
                _validate_serving_identity(condition, response)
                universe.validate_candidates(
                    requester=replay_row.request.creatorId,
                    response_rows=response.candidates,
                )
                staleness = None
                if trainer_model_version is not None:
                    staleness = max(0, trainer_model_version - response.modelVersion)
                client_total = completed - started
                cache_status, source_vector_origin = _cache_evidence(response)
                measurements.append(
                    RequestMeasurementV2(
                        **common,
                        completedAtMonotonicNs=completed,
                        clientTotalNs=client_total,
                        clientOverheadNs=client_total
                        - response.timingsNs["serverTotal"],
                        outcome="success",
                        httpStatus=status,
                        serverTimingsNs=response.timingsNs,
                        cacheStatus=cache_status,
                        sourceVectorOrigin=source_vector_origin,
                        modelId=response.modelId,
                        servingModelVersion=response.modelVersion,
                        versionStaleness=staleness,
                        retrievalBackend=response.retrievalBackend,
                        datasetSnapshotSha256=(
                            condition.expectedDatasetSnapshotSha256
                            if isinstance(condition, ConditionSpecV2)
                            else universe.sha256
                        ),
                        pgvectorSnapshotSha256=response.pgvectorSnapshotSha256,
                        backendSnapshotSha256=response.backendSnapshotSha256,
                        queryVectorSha256=response.queryVectorSha256,
                        candidateCount=len(response.candidates),
                    )
                )
            except TimeoutError as error:
                completed = monotonic_ns()
                measurements.append(
                    RequestMeasurementV2(
                        **common,
                        completedAtMonotonicNs=completed,
                        clientTotalNs=completed - started,
                        outcome="timeout",
                        errorType=type(error).__name__,
                    )
                )
            except Exception as error:
                completed = monotonic_ns()
                measurements.append(
                    RequestMeasurementV2(
                        **common,
                        completedAtMonotonicNs=completed,
                        clientTotalNs=completed - started,
                        outcome="error",
                        errorType=type(error).__name__,
                    )
                )
            finally:
                async with active_lock:
                    active -= 1

    resource_stop = asyncio.Event()
    resource_task = (
        asyncio.create_task(resource_collector.run(resource_stop))
        if resource_collector is not None
        else None
    )
    try:
        if schedule_mode == "open_loop":
            await asyncio.gather(*(execute(index) for index in range(len(replay.rows))))
        else:

            async def worker(worker_index: int) -> None:
                for index in range(worker_index, len(replay.rows), max_in_flight):
                    await execute(index)

            await asyncio.gather(*(worker(index) for index in range(max_in_flight)))
    finally:
        await transport.close()
        resource_stop.set()
        if resource_task is not None:
            await resource_task
    return ConcurrentConditionResult(
        tuple(sorted(measurements, key=lambda row: row.scheduleIndex)),
        tuple(resource_collector.rows) if resource_collector is not None else (),
    )


__all__ = [
    "AlreadyConfiguredConditionDriver",
    "ConditionDriver",
    "ConditionTelemetryRecorder",
    "ConcurrentConditionResult",
    "HttpxTransport",
    "AsyncHttpxTransport",
    "AsyncJsonTransport",
    "JsonTransport",
    "MeasuredConditionOperations",
    "SuiteResult",
    "run_condition",
    "run_concurrent_condition",
    "run_suite",
]
