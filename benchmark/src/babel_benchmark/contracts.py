"""Closed contracts for the Friday synchronous-POST performance lane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


ConditionName = Literal[
    "pgvector_serving_only",
    "pgvector_training_no_sync",
    "pgvector_training_and_sync",
]
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


class ConditionSpecV1(FrozenContract):
    name: ConditionName
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
            raise ValueError("manifest must contain the three Friday conditions in order")
        if self.warmupCount >= len(self.scheduleOffsetsNs):
            raise ValueError("warmup count must leave at least one measured request")
        for condition in self.conditions:
            if condition.requestCorpusSha256 != self.requestCorpusSha256:
                raise ValueError("every condition must use the frozen request corpus")
            if condition.scheduleOffsetsNs != self.scheduleOffsetsNs:
                raise ValueError("every condition must use the frozen request schedule")
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
            raise ValueError("candidate universe must contain only a created synthetic Babel")
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
    retrievalBackend: Literal["pgvector"]
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
            raise ValueError("response must contain the seven frozen server timing stages")
        if any(duration < 0 for duration in value.values()):
            raise ValueError("server timings must be nonnegative")
        if value["serverTotal"] < sum(value[stage] for stage in _STAGES):
            raise ValueError("server total must cover every stage")
        return value


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
    retrievalBackend: Literal["pgvector"] | None = None
    pgvectorSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    backendSnapshotSha256: str | None = Field(default=None, pattern=_SHA256)
    queryVectorSha256: str | None = Field(default=None, pattern=_SHA256)
    candidateCount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def timing_and_outcome_are_consistent(self) -> "RequestMeasurementV1":
        if self.completedAtMonotonicNs - self.startedAtMonotonicNs != self.clientTotalNs:
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
                raise ValueError("successful measurement requires response identity and timings")
            checked = RecommendationResponseV1.timings_are_complete(self.serverTimingsNs or {})
            if checked["serverTotal"] > self.clientTotalNs:
                raise ValueError("server total cannot exceed end-to-end latency")
            if self.clientOverheadNs != self.clientTotalNs - checked["serverTotal"]:
                raise ValueError("client overhead must equal client total minus server total")
        elif not self.errorType:
            raise ValueError("failed measurement requires an error type")
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
            "trainer_step": self.durationNs is not None and self.trainerStep is not None,
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


class PerformanceSummaryV1(FrozenContract):
    schemaVersion: Literal[1]
    benchmarkRunId: UUID
    baselineCondition: Literal["pgvector_serving_only"]
    conditions: tuple[ConditionSummaryV1, ...]


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
        json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )


__all__ = [
    "BenchmarkManifestV1",
    "ConditionName",
    "ConditionSpecV1",
    "ConditionSummaryV1",
    "ConditionTelemetryV1",
    "CreatedBabelV1",
    "PerformanceSummaryV1",
    "PercentileSummaryV1",
    "RecommendationRequestV1",
    "RecommendationResponseV1",
    "ReplayRequestV1",
    "RequestMeasurementV1",
    "dump_jsonl",
    "load_jsonl",
]
