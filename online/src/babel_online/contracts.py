"""Closed observable contracts shared by online processes."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import EnvironmentPeriod, RetrievalBackend


SHA256_PATTERN = r"^[a-f0-9]{40,64}$"
ARTICLE_KEY_PATTERN = r"^enwiki:[1-9][0-9]*$"


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class EmbeddingSpaceV1(FrozenContract):
    schemaVersion: Literal[1]
    embeddingSpaceId: UUID
    dimension: Literal[100]
    distance: Literal["cosine"]
    distilledEncoderArtifact: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=SHA256_PATTERN)
    compatibilityVersion: str = Field(min_length=1)


class RunConfigV1(FrozenContract):
    schemaVersion: Literal[1]
    runId: UUID
    datasetRepo: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=SHA256_PATTERN)
    startingModelId: UUID
    retrievalBackend: RetrievalBackend = "pgvector"
    creatorCount: int = Field(default=50, ge=1, le=10_000)
    embeddingDimension: Literal[100] = 100
    environmentSequence: list[EnvironmentPeriod] = Field(min_length=1, max_length=2)
    perMonthEventBudget: dict[EnvironmentPeriod, int]
    recommendationK: int = Field(default=10, gt=0, le=100)
    topL: int = Field(default=100, gt=0)
    kafkaTopic: str = Field(default="babel.feedback.v1", min_length=1)
    kafkaGroup: str = Field(default="babel-online-trainer-v1", min_length=1)
    checkpointEveryEvents: int = Field(default=100, gt=0)
    syncEverySteps: int = Field(default=10, gt=0)
    artifactRoot: str = Field(default="artifacts/online", min_length=1)
    stateRoot: str = Field(default="state/online", min_length=1)

    @field_validator("environmentSequence")
    @classmethod
    def periods_are_ordered_and_unique(
        cls, value: list[EnvironmentPeriod]
    ) -> list[EnvironmentPeriod]:
        if value not in [["2026-06"], ["2026-06", "2026-07"]]:
            raise ValueError("environmentSequence must start in June and may continue to July")
        return value

    @model_validator(mode="after")
    def budgets_cover_periods(self) -> "RunConfigV1":
        if set(self.perMonthEventBudget) != set(self.environmentSequence):
            raise ValueError("perMonthEventBudget must exactly cover environmentSequence")
        if any(value <= 0 for value in self.perMonthEventBudget.values()):
            raise ValueError("per-month event budgets must be positive")
        return self


class RecommendationRequestV1(FrozenContract):
    schemaVersion: Literal[1]
    requestId: UUID
    runId: UUID
    creatorId: UUID
    newBabelId: UUID
    newSourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    historyBabelIds: list[UUID]
    candidateCount: int = Field(gt=0, le=100)


class RecommendationCandidateV1(FrozenContract):
    babelId: UUID
    creatorId: UUID
    sourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    rank: int = Field(gt=0)
    modelScore: float = Field(ge=-1.0, le=1.0)


class CandidateActionV1(FrozenContract):
    babelId: UUID
    sourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    rank: int = Field(gt=0)
    modelScore: float = Field(ge=-1.0, le=1.0)
    action: Literal["include", "exclude", "ignore"]


_TIMING_STAGES = {
    "queue",
    "encode",
    "context",
    "ann",
    "filtering",
    "serialization",
    "serverTotal",
}


class RecommendationResponseV1(FrozenContract):
    schemaVersion: Literal[1]
    requestId: UUID
    runId: UUID
    modelId: UUID
    modelVersion: int = Field(ge=0)
    retrievalBackend: RetrievalBackend
    embeddingSpaceId: UUID
    pgvectorSnapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    backendSnapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    queryVectorSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidates: list[RecommendationCandidateV1]
    timingsNs: dict[str, int]

    @field_validator("timingsNs")
    @classmethod
    def timings_are_complete(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != _TIMING_STAGES:
            raise ValueError("timingsNs must contain the seven frozen timing stages")
        if any(duration < 0 for duration in value.values()):
            raise ValueError("timingsNs durations must be nonnegative")
        if value["serverTotal"] < sum(
            value[stage] for stage in _TIMING_STAGES - {"serverTotal"}
        ):
            raise ValueError("serverTotal must cover every measured stage")
        return value

    @field_validator("candidates")
    @classmethod
    def candidates_are_ranked(
        cls, value: list[RecommendationCandidateV1]
    ) -> list[RecommendationCandidateV1]:
        if [row.rank for row in value] != list(range(1, len(value) + 1)):
            raise ValueError("candidate ranks must be contiguous from one")
        if len({row.babelId for row in value}) != len(value):
            raise ValueError("candidate Babel IDs must be unique")
        return value


class FeedbackEventV1(FrozenContract):
    schemaVersion: Literal[1]
    eventId: UUID
    requestId: UUID
    runId: UUID
    creatorId: UUID
    newBabelId: UUID
    newSourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    modelId: UUID
    modelVersion: int = Field(ge=0)
    embeddingSpaceId: UUID
    retrievalBackend: RetrievalBackend
    candidateActions: list[CandidateActionV1]
    occurredAtNs: int = Field(ge=0)


class ActivityLogV1(FrozenContract):
    schemaVersion: Literal[1]
    runId: UUID
    sequence: int = Field(gt=0)
    occurredAtNs: int = Field(ge=0)
    level: Literal["debug", "info", "warning", "error"]
    component: Literal["supervisor", "serving", "training", "feedback"]
    event: str = Field(min_length=1)
    message: str = Field(min_length=1)
    metrics: dict[str, int | float]

    @field_validator("metrics")
    @classmethod
    def metrics_are_observable(
        cls, value: dict[str, int | float]
    ) -> dict[str, int | float]:
        forbidden = ("graph", "ppr", "clickstream", "profile", "random")
        if any(part in key.casefold() for key in value for part in forbidden):
            raise ValueError("activity metrics contain a hidden field")
        return value


class ModelManifestV1(FrozenContract):
    schemaVersion: Literal[1]
    modelId: UUID
    label: str = Field(min_length=1)
    parentModelId: UUID | None
    producingRunId: UUID | None
    encoderRepo: str = Field(min_length=1)
    encoderRevision: str = Field(pattern=SHA256_PATTERN)
    datasetRepo: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=SHA256_PATTERN)
    environmentSequence: list[EnvironmentPeriod] = Field(min_length=1, max_length=2)
    trainingExamples: int = Field(ge=0)
    checkpointPath: str = Field(min_length=1)
    checkpointSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    embeddingSpace: EmbeddingSpaceV1
    immutable: Literal[True]

    @model_validator(mode="after")
    def lineage_is_complete(self) -> "ModelManifestV1":
        if (self.parentModelId is None) != (self.producingRunId is None):
            raise ValueError("parentModelId and producingRunId must both be null or set")
        return self


class HnswSnapshotV1(FrozenContract):
    schemaVersion: Literal[1]
    runId: UUID
    servingModelId: UUID
    servingModelVersion: int = Field(ge=0)
    embeddingSpaceId: UUID
    pgvectorSnapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    orderedBabelIds: list[UUID]
    rowCount: int = Field(ge=0)
    vectorSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    m: int = Field(default=16, gt=0)
    efConstruction: int = Field(default=200, gt=0)
    efSearch: int = Field(default=100, gt=0)

    @model_validator(mode="after")
    def row_count_matches_ids(self) -> "HnswSnapshotV1":
        if self.rowCount != len(self.orderedBabelIds):
            raise ValueError("rowCount must equal orderedBabelIds length")
        if len(set(self.orderedBabelIds)) != len(self.orderedBabelIds):
            raise ValueError("orderedBabelIds must be unique")
        return self


_CONTRACT_MODELS: dict[str, type[FrozenContract]] = {
    "experiment-run-v1": RunConfigV1,
    "recommendation-request-v1": RecommendationRequestV1,
    "recommendation-response-v1": RecommendationResponseV1,
    "feedback-event-v1": FeedbackEventV1,
    "activity-log-v1": ActivityLogV1,
    "model-manifest-v1": ModelManifestV1,
    "embedding-space-v1": EmbeddingSpaceV1,
    "hnsw-snapshot-v1": HnswSnapshotV1,
}


def validate_contract(name: str, document: object) -> FrozenContract:
    """Validate one named online contract through its frozen Pydantic model."""
    try:
        model = _CONTRACT_MODELS[name]
    except KeyError as error:
        raise ValueError(f"unknown online contract: {name}") from error
    return model.model_validate(document)


def contract_schema_documents() -> dict[str, dict[str, object]]:
    """Return deterministic JSON Schema documents for every public contract."""
    documents: dict[str, dict[str, object]] = {}
    for name, model in _CONTRACT_MODELS.items():
        document = model.model_json_schema(mode="validation")
        document["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        document["$id"] = f"https://babel.local/schemas/online/{name}.json"
        documents[name] = document
    return documents


__all__ = [
    "ActivityLogV1",
    "CandidateActionV1",
    "contract_schema_documents",
    "EmbeddingSpaceV1",
    "FeedbackEventV1",
    "HnswSnapshotV1",
    "ModelManifestV1",
    "RecommendationCandidateV1",
    "RecommendationRequestV1",
    "RecommendationResponseV1",
    "RunConfigV1",
    "validate_contract",
]
