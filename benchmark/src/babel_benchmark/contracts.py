"""Closed contracts for the Friday synchronous-POST performance lane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


LegacyConditionName = Literal[
    "pgvector_serving_only",
    "pgvector_training_no_sync",
    "pgvector_training_and_sync",
]
ConditionName = str
TopologyName = Literal[
    "same_process", "same_host_split", "same_host_isolated", "cross_host"
]
RetrievalBackendName = Literal["pgvector", "hnswlib"]
TimingStage = Literal[
    "queue", "encode", "context", "ann", "filtering", "serialization", "serverTotal"
]
_STAGES = {"queue", "encode", "context", "ann", "filtering", "serialization"}
_SHA256 = r"^[a-f0-9]{64}$"


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
        },
    )


class ConditionIdentityV2(FrozenContract):
    topology: TopologyName
    trainingEnabled: bool
    activationEnabled: bool
    retrievalBackend: RetrievalBackendName = "pgvector"

    @property
    def stable_key(self) -> str:
        training = "training" if self.trainingEnabled else "serving"
        activation = "activation" if self.activationEnabled else "no_activation"
        return f"{self.topology}.{training}.{activation}.{self.retrievalBackend}"

    @model_validator(mode="after")
    def activation_requires_training(self) -> "ConditionIdentityV2":
        if self.activationEnabled and not self.trainingEnabled:
            raise ValueError("model activation requires training")
        return self


class ActivationTargetV1(FrozenContract):
    """One immutable child state permitted during an activation condition."""

    modelId: UUID
    parentModelId: UUID
    modelVersion: int = Field(gt=0)
    pgvectorSnapshotSha256: str = Field(pattern=_SHA256)
    backendSnapshotSha256: str = Field(pattern=_SHA256)


class ConditionSpecV1(FrozenContract):
    name: LegacyConditionName
    trainingEnabled: bool
    syncEnabled: bool
    requestCorpusSha256: str = Field(pattern=_SHA256)
    scheduleOffsetsNs: tuple[int, ...]
    expectedModelId: UUID
    expectedEmbeddingSpaceId: UUID
    expectedPgvectorSnapshotSha256: str = Field(pattern=_SHA256)

    @field_validator("scheduleOffsetsNs")
    @classmethod
    def offsets_are_monotonic(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or value[0] != 0 or any(offset < 0 for offset in value):
            raise ValueError("schedule offsets must start at zero and be nonnegative")
        if any(left >= right for left, right in zip(value, value[1:])):
            raise ValueError("schedule offsets must be strictly increasing")
        return value

    @model_validator(mode="after")
    def flags_match_name(self) -> "ConditionSpecV1":
        expected = {
            "pgvector_serving_only": (False, False),
            "pgvector_training_no_sync": (True, False),
            "pgvector_training_and_sync": (True, True),
        }[self.name]
        if (self.trainingEnabled, self.syncEnabled) != expected:
            raise ValueError("condition flags do not match the stable condition name")
        return self


class BenchmarkManifestV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    endpoint: HttpUrl
    requestPath: Literal["/api/v1/recommendations"]
    requestCorpusPath: str = Field(min_length=1)
    requestCorpusSha256: str = Field(pattern=_SHA256)
    candidateUniversePath: str = Field(min_length=1)
    candidateUniverseSha256: str = Field(pattern=_SHA256)
    scheduleOffsetsNs: tuple[int, ...]
    warmupCount: int = Field(ge=0)
    timeoutSeconds: float = Field(gt=0)
    conditions: tuple[ConditionSpecV1, ...]

    @field_validator("endpoint")
    @classmethod
    def endpoint_is_loopback(cls, value: HttpUrl) -> HttpUrl:
        if value.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("benchmark endpoint must be loopback")
        return value

    @model_validator(mode="after")
    def conditions_are_paired(self) -> "BenchmarkManifestV1":
        names = tuple(condition.name for condition in self.conditions)
        if names != (
            "pgvector_serving_only",
            "pgvector_training_no_sync",
            "pgvector_training_and_sync",
        ):
            raise ValueError(
                "manifest must contain the three Friday conditions in order"
            )
        if self.warmupCount >= len(self.scheduleOffsetsNs):
            raise ValueError("warmup count must leave at least one measured request")
        for condition in self.conditions:
            if condition.requestCorpusSha256 != self.requestCorpusSha256:
                raise ValueError("every condition must use the frozen request corpus")
            if condition.scheduleOffsetsNs != self.scheduleOffsetsNs:
                raise ValueError("every condition must use the frozen request schedule")
        return self


class ConditionSpecV2(FrozenContract):
    identity: ConditionIdentityV2
    requestCorpusSha256: str = Field(pattern=_SHA256)
    scheduleOffsetsNs: tuple[int, ...]
    expectedModelId: UUID
    expectedModelVersion: int = Field(default=0, ge=0)
    expectedEmbeddingSpaceId: UUID
    expectedDatasetSnapshotSha256: str = Field(pattern=_SHA256)
    expectedPgvectorSnapshotSha256: str = Field(pattern=_SHA256)
    expectedBackendSnapshotSha256: str = Field(pattern=_SHA256)
    activationTargets: tuple[ActivationTargetV1, ...] = ()

    @field_validator("scheduleOffsetsNs")
    @classmethod
    def offsets_are_monotonic(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return ConditionSpecV1.offsets_are_monotonic(value)

    @property
    def name(self) -> str:
        return self.identity.stable_key

    @property
    def trainingEnabled(self) -> bool:
        return self.identity.trainingEnabled

    @property
    def syncEnabled(self) -> bool:
        return self.identity.activationEnabled

    @model_validator(mode="after")
    def activation_targets_form_a_pinned_lineage(self) -> "ConditionSpecV2":
        if bool(self.activationTargets) != self.identity.activationEnabled:
            raise ValueError(
                "activation targets must exist only for activation conditions"
            )
        lineage = {self.expectedModelId}
        versions = {self.expectedModelVersion}
        for target in self.activationTargets:
            if target.modelId in lineage:
                raise ValueError("activation model IDs must be unique")
            if target.parentModelId not in lineage:
                raise ValueError(
                    "activation target parent is outside the pinned lineage"
                )
            if target.modelVersion in versions:
                raise ValueError("activation model versions must be unique")
            lineage.add(target.modelId)
            versions.add(target.modelVersion)
        return self


class BenchmarkManifestV2(FrozenContract):
    schemaVersion: Literal[2]
    benchmarkRunId: UUID
    endpoint: HttpUrl
    requestPath: Literal["/api/v1/recommendations", "/api/v2/recommendations"]
    requestCorpusPath: str = Field(min_length=1)
    requestCorpusSha256: str = Field(pattern=_SHA256)
    candidateUniversePath: str = Field(min_length=1)
    candidateUniverseSha256: str = Field(pattern=_SHA256)
    scheduleOffsetsNs: tuple[int, ...]
    warmupCount: int = Field(ge=0)
    timeoutSeconds: float = Field(gt=0)
    scheduleMode: Literal["closed_loop", "open_loop"]
    maxInFlight: int = Field(gt=0, le=10_000)
    conditions: tuple[ConditionSpecV2, ...] = Field(min_length=1)

    @field_validator("endpoint")
    @classmethod
    def endpoint_is_loopback(cls, value: HttpUrl) -> HttpUrl:
        return BenchmarkManifestV1.endpoint_is_loopback(value)

    @model_validator(mode="after")
    def conditions_share_frozen_inputs(self) -> "BenchmarkManifestV2":
        if self.warmupCount >= len(self.scheduleOffsetsNs):
            raise ValueError("warmup count must leave a measured request")
        names = [condition.name for condition in self.conditions]
        if len(set(names)) != len(names):
            raise ValueError("generalized condition identities must be unique")
        for condition in self.conditions:
            if condition.requestCorpusSha256 != self.requestCorpusSha256:
                raise ValueError("every condition must use the frozen request corpus")
            if condition.scheduleOffsetsNs != self.scheduleOffsetsNs:
                raise ValueError("every condition must use the frozen request schedule")
            if condition.expectedDatasetSnapshotSha256 != self.candidateUniverseSha256:
                raise ValueError(
                    "condition dataset snapshot must match candidate universe"
                )
        return self


class RecommendationRequestV1(FrozenContract):
    schemaVersion: Literal[1]
    requestId: UUID
    runId: UUID
    creatorId: UUID
    newBabelId: UUID
    newSourceArticleKey: str = Field(pattern=r"^enwiki:[1-9][0-9]*$")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    historyBabelIds: tuple[UUID, ...]
    candidateCount: int = Field(gt=0, le=100)


class ReplayRequestV1(FrozenContract):
    scheduleOffsetNs: int = Field(ge=0)
    request: RecommendationRequestV1


class RecommendationRequestV2(FrozenContract):
    schemaVersion: Literal[2]
    requestId: UUID
    runId: UUID
    creatorId: UUID
    sourceBabelId: UUID
    sourceArticleKey: str = Field(pattern=r"^enwiki:[1-9][0-9]*$")
    traversalSessionId: UUID
    parentRequestId: UUID | None
    traversalDepth: Literal[0, 1]
    title: str | None = None
    text: str | None = None
    historyBabelIds: tuple[UUID, ...]
    candidateCount: int = Field(gt=0, le=100)

    @model_validator(mode="after")
    def root_and_existing_source_fields_are_unambiguous(
        self,
    ) -> "RecommendationRequestV2":
        if self.traversalDepth == 0:
            if self.parentRequestId is not None:
                raise ValueError("root request cannot have a parent request")
            if not self.title or not self.text:
                raise ValueError("root request must carry observable title and text")
        else:
            if self.parentRequestId is None:
                raise ValueError("depth-one request must identify its parent request")
            if self.title is not None or self.text is not None:
                raise ValueError("existing-source request loads persisted content")
        return self


class ReplayRequestV2(FrozenContract):
    scheduleOffsetNs: int = Field(ge=0)
    request: RecommendationRequestV2


class CreatedBabelV1(FrozenContract):
    babelId: UUID
    runId: UUID
    creatorId: UUID
    sourceArticleKey: str = Field(pattern=r"^enwiki:[1-9][0-9]*$")
    createdBySyntheticCreator: bool
    createdInRun: bool

    @model_validator(mode="after")
    def is_a_created_synthetic_babel(self) -> "CreatedBabelV1":
        if not (self.createdBySyntheticCreator and self.createdInRun):
            raise ValueError(
                "candidate universe must contain only a created synthetic Babel"
            )
        return self


class RecommendationCandidateV1(FrozenContract):
    babelId: UUID
    creatorId: UUID
    sourceArticleKey: str = Field(pattern=r"^enwiki:[1-9][0-9]*$")
    rank: int = Field(gt=0)
    modelScore: float = Field(ge=-1.0, le=1.0)


class RecommendationResponseV1(FrozenContract):
    schemaVersion: Literal[1]
    requestId: UUID
    runId: UUID
    modelId: UUID
    modelVersion: int = Field(ge=0)
    retrievalBackend: RetrievalBackendName
    embeddingSpaceId: UUID
    pgvectorSnapshotSha256: str = Field(pattern=_SHA256)
    backendSnapshotSha256: str = Field(pattern=_SHA256)
    queryVectorSha256: str = Field(pattern=_SHA256)
    candidates: tuple[RecommendationCandidateV1, ...]
    timingsNs: dict[str, int]

    @field_validator("timingsNs")
    @classmethod
    def timings_are_complete(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != _STAGES | {"serverTotal"}:
            raise ValueError(
                "response must contain the seven frozen server timing stages"
            )
        if any(duration < 0 for duration in value.values()):
            raise ValueError("server timings must be nonnegative")
        if value["serverTotal"] < sum(value[stage] for stage in _STAGES):
            raise ValueError("server total must cover every stage")
        return value


class RecommendationResponseV2(RecommendationResponseV1):
    schemaVersion: Literal[2]
    sourceVectorOrigin: Literal["qwen_encode", "cache_hit", "pgvector_load"]


class RequestMeasurementV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    condition: ConditionName
    requestId: UUID
    scheduleIndex: int = Field(ge=0)
    scheduleOffsetNs: int = Field(ge=0)
    isWarmup: bool = False
    startedAtMonotonicNs: int = Field(ge=0)
    completedAtMonotonicNs: int = Field(ge=0)
    queueDelayNs: int = Field(ge=0)
    clientTotalNs: int = Field(ge=0)
    clientOverheadNs: int | None = Field(default=None, ge=0)
    outcome: Literal["success", "error", "timeout"]
    httpStatus: int | None = Field(default=None, ge=100, le=599)
    errorType: str | None = None
    serverTimingsNs: dict[str, int] | None = None
    modelId: UUID | None = None
    modelVersion: int | None = Field(default=None, ge=0)
    retrievalBackend: RetrievalBackendName | None = None
    pgvectorSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    backendSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    queryVectorSha256: str | None = Field(default=None, pattern=_SHA256)
    candidateCount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def timing_and_outcome_are_consistent(self) -> "RequestMeasurementV1":
        if (
            self.completedAtMonotonicNs - self.startedAtMonotonicNs
            != self.clientTotalNs
        ):
            raise ValueError("client total must use the recorded monotonic timestamps")
        response_fields = (
            self.serverTimingsNs,
            self.modelId,
            self.modelVersion,
            self.retrievalBackend,
            self.pgvectorSnapshotSha256,
            self.backendSnapshotSha256,
            self.queryVectorSha256,
            self.candidateCount,
        )
        if self.outcome == "success":
            if any(value is None for value in response_fields):
                raise ValueError(
                    "successful measurement requires response identity and timings"
                )
            checked = RecommendationResponseV1.timings_are_complete(
                self.serverTimingsNs or {}
            )
            if checked["serverTotal"] > self.clientTotalNs:
                raise ValueError("server total cannot exceed end-to-end latency")
            if self.clientOverheadNs != self.clientTotalNs - checked["serverTotal"]:
                raise ValueError(
                    "client overhead must equal client total minus server total"
                )
        elif not self.errorType:
            raise ValueError("failed measurement requires an error type")
        return self


class RequestMeasurementV2(FrozenContract):
    """Raw concurrent request row; failures retain their scheduling evidence."""

    schemaVersion: Literal[2]
    benchmarkRunId: UUID
    conditionId: str = Field(min_length=1)
    requestId: UUID
    scheduleIndex: int = Field(ge=0)
    scheduleMode: Literal["closed_loop", "open_loop"]
    intendedStartMonotonicNs: int = Field(ge=0)
    actualStartMonotonicNs: int = Field(ge=0)
    completedAtMonotonicNs: int = Field(ge=0)
    queueDelayNs: int = Field(ge=0)
    inFlightAtStart: int = Field(ge=1)
    clientTotalNs: int = Field(ge=0)
    clientOverheadNs: int | None = Field(default=None, ge=0)
    isWarmup: bool = False
    outcome: Literal["success", "error", "timeout"]
    httpStatus: int | None = Field(default=None, ge=100, le=599)
    errorType: str | None = None
    serverTimingsNs: dict[str, int] | None = None
    cacheStatus: Literal["hit", "miss", "bypass", "unavailable"] = "unavailable"
    sourceVectorOrigin: Literal["qwen_encode", "cache_hit", "pgvector_load"] | None = (
        None
    )
    modelId: UUID | None = None
    servingModelVersion: int | None = Field(default=None, ge=0)
    trainerModelVersion: int | None = Field(default=None, ge=0)
    versionStaleness: int | None = Field(default=None, ge=0)
    retrievalBackend: RetrievalBackendName | None = None
    datasetSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    pgvectorSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    backendSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    queryVectorSha256: str | None = Field(default=None, pattern=_SHA256)
    candidateCount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def raw_timing_and_identity_are_consistent(self) -> "RequestMeasurementV2":
        if self.actualStartMonotonicNs < self.intendedStartMonotonicNs:
            raise ValueError("actual start cannot precede intended start")
        if (
            self.queueDelayNs
            != self.actualStartMonotonicNs - self.intendedStartMonotonicNs
        ):
            raise ValueError("queue delay must equal actual minus intended start")
        if (
            self.completedAtMonotonicNs - self.actualStartMonotonicNs
            != self.clientTotalNs
        ):
            raise ValueError("client total must use actual start and completion")
        if self.versionStaleness is not None:
            if self.trainerModelVersion is None or self.servingModelVersion is None:
                raise ValueError(
                    "version staleness requires trainer and serving versions"
                )
            if (
                self.versionStaleness
                != self.trainerModelVersion - self.servingModelVersion
            ):
                raise ValueError(
                    "version staleness does not match trainer-serving versions"
                )
        if self.outcome == "success":
            required = (
                self.serverTimingsNs,
                self.modelId,
                self.servingModelVersion,
                self.retrievalBackend,
                self.datasetSnapshotSha256,
                self.pgvectorSnapshotSha256,
                self.backendSnapshotSha256,
                self.queryVectorSha256,
                self.candidateCount,
            )
            if any(value is None for value in required):
                raise ValueError("successful concurrent row requires response identity")
            checked = RecommendationResponseV1.timings_are_complete(
                self.serverTimingsNs or {}
            )
            if checked["serverTotal"] > self.clientTotalNs:
                raise ValueError("server total cannot exceed end-to-end latency")
            if self.clientOverheadNs != self.clientTotalNs - checked["serverTotal"]:
                raise ValueError("client overhead must exclude server total")
        elif not self.errorType:
            raise ValueError("failed concurrent row requires an error type")
        return self


class ConditionTelemetryV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    condition: ConditionName
    observedAtMonotonicNs: int = Field(ge=0)
    kind: Literal["trainer_step", "kafka_lag", "synchronization"]
    durationNs: int | None = Field(default=None, ge=0)
    trainerStep: int | None = Field(default=None, ge=0)
    kafkaLag: int | None = Field(default=None, ge=0)
    synchronizationVersion: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def fields_match_kind(self) -> "ConditionTelemetryV1":
        required = {
            "trainer_step": self.durationNs is not None
            and self.trainerStep is not None,
            "kafka_lag": self.kafkaLag is not None,
            "synchronization": self.durationNs is not None
            and self.synchronizationVersion is not None,
        }
        if not required[self.kind]:
            raise ValueError("telemetry fields do not match its kind")
        return self


class PercentileSummaryV1(FrozenContract):
    count: int = Field(ge=1)
    p50: int = Field(ge=0)
    p95: int = Field(ge=0)
    p99: int = Field(ge=0)
    max: int = Field(ge=0)


class ConditionSummaryV1(FrozenContract):
    condition: ConditionName
    requests: int = Field(ge=0)
    successes: int = Field(ge=0)
    errors: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    rps: float = Field(ge=0)
    endToEndNs: PercentileSummaryV1 | None
    serverStagesNs: dict[str, PercentileSummaryV1]
    trainerStepNs: PercentileSummaryV1 | None
    kafkaLag: PercentileSummaryV1 | None
    syncSpikeNs: int | None = Field(default=None, ge=0)
    slowdownRatioP95: float | None = Field(default=None, ge=0)


class InterferenceSummaryV1(FrozenContract):
    Itraining: float = Field(ge=0)
    Ifull: float = Field(ge=0)
    IActivationIncrement: float = Field(ge=0)
    ItrainingPercent: float
    IfullPercent: float
    IActivationIncrementPercent: float


class PerformanceSummaryV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    baselineCondition: str
    conditions: tuple[ConditionSummaryV1, ...]
    interference: InterferenceSummaryV1 | None = None


ContractT = TypeVar("ContractT", bound=BaseModel)


def load_jsonl(path: str | Path, contract: type[ContractT]) -> list[ContractT]:
    rows: list[ContractT] = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(contract.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"invalid {path} line {line_number}: {error}") from error
    return rows


def dump_jsonl(rows: list[BaseModel] | tuple[BaseModel, ...]) -> str:
    return "".join(
        json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        + "\n"
        for row in rows
    )


def load_request_measurements(
    path: str | Path,
) -> list[RequestMeasurementV1 | RequestMeasurementV2]:
    rows: list[RequestMeasurementV1 | RequestMeasurementV2] = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
            contract = (
                RequestMeasurementV2
                if document.get("schemaVersion") == 2
                else RequestMeasurementV1
            )
            rows.append(contract.model_validate(document))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"invalid {path} line {line_number}: {error}") from error
    return rows


def load_benchmark_manifest(
    path: str | Path,
) -> BenchmarkManifestV1 | BenchmarkManifestV2:
    document = json.loads(Path(path).read_text())
    contract = (
        BenchmarkManifestV2
        if document.get("schemaVersion") == 2
        else BenchmarkManifestV1
    )
    return contract.model_validate(document)


__all__ = [
    "BenchmarkManifestV1",
    "BenchmarkManifestV2",
    "ActivationTargetV1",
    "ConditionIdentityV2",
    "ConditionName",
    "ConditionSpecV1",
    "ConditionSpecV2",
    "ConditionSummaryV1",
    "ConditionTelemetryV1",
    "CreatedBabelV1",
    "InterferenceSummaryV1",
    "PerformanceSummaryV1",
    "PercentileSummaryV1",
    "RecommendationRequestV1",
    "RecommendationRequestV2",
    "RecommendationResponseV1",
    "RecommendationResponseV2",
    "ReplayRequestV1",
    "ReplayRequestV2",
    "RequestMeasurementV1",
    "RequestMeasurementV2",
    "dump_jsonl",
    "load_jsonl",
    "load_benchmark_manifest",
    "load_request_measurements",
]
