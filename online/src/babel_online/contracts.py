"""Closed observable contracts shared by online processes."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Literal
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


class DistilledServingArtifactV1(FrozenContract):
    """Closed binding from an immutable training artifact to serving semantics.

    The upstream training artifact records weights and training identities, but
    its manifest does not spell out input formatting, pooling, or output
    normalization.  Those fields are therefore bound explicitly here to the
    training source revision carried by that artifact.
    """

    schemaVersion: Literal[1]
    artifactRepo: str = Field(min_length=1)
    artifactRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    artifactPath: str = Field(pattern=r"^artifacts/[a-f0-9]{64}$")
    artifactId: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifactSchema: Literal["babel-distillation-2016-interview-v1"]
    baseModelId: Literal["Qwen/Qwen3-Embedding-0.6B"]
    baseModelRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    tokenizerRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    datasetRepo: Literal["dhelmy990/babel-wikipedia-experiment"]
    datasetConfig: Literal["distillation_2016_interview"]
    datasetRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    trainingSourceRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    semanticsAuthority: Literal["pinned_training_source"]
    inputFormat: Literal["canonical_title\\n\\nlead_text"]
    maxLength: Literal[384]
    paddingSide: Literal["left"]
    pooling: Literal["last_non_padding_token"]
    projectionInputDimension: Literal[1024]
    embeddingDimension: Literal[100]
    normalization: Literal["l2"]
    adapterSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    projectionSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validationSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    immutable: Literal[True]

    @model_validator(mode="after")
    def artifact_path_matches_id(self) -> "DistilledServingArtifactV1":
        if self.artifactPath != f"artifacts/{self.artifactId}":
            raise ValueError("artifactPath must identify artifactId")
        if self.tokenizerRevision != self.baseModelRevision:
            raise ValueError("tokenizer and base model revisions must match")
        return self


class RunConfigV1(FrozenContract):
    schemaVersion: Literal[1]
    runId: UUID
    datasetRepo: str = Field(min_length=1)
    datasetConfig: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=SHA256_PATTERN)
    startingModelId: UUID
    retrievalBackend: RetrievalBackend = "pgvector"
    creatorCount: int = Field(default=50, ge=1, le=10_000)
    embeddingDimension: Literal[100] = 100
    environmentSequence: list[EnvironmentPeriod] = Field(min_length=1, max_length=2)
    perMonthEventBudget: dict[EnvironmentPeriod, int]
    runSeed: int = Field(ge=0, le=9_223_372_036_854_775_807)
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


class RunConfigV2(RunConfigV1):
    """Scaled-run settings; V1 remains the frozen smoke/demo contract."""

    schemaVersion: Literal[2]
    sourceArticlesPerMonth: int = Field(default=5_000, ge=1, le=1_000_000)
    targetCreatedBabels: int = Field(ge=1, le=1_000_000)
    concurrentUsers: int = Field(default=50, ge=1, le=10_000)
    recommendationStartProbability: float = Field(default=0.4, ge=0.0, le=1.0)
    continuationProbability: float = Field(default=0.4, ge=0.0, le=1.0)
    maximumTraversalDepth: Literal[2] = 2
    maximumRequestsPerTraversal: int = Field(default=10, ge=1, le=10)
    interleaveCreationAndRecommendations: bool = True

    @model_validator(mode="after")
    def concurrency_is_bounded_by_creators(self) -> "RunConfigV2":
        if self.concurrentUsers > self.creatorCount:
            raise ValueError("concurrentUsers cannot exceed creatorCount")
        if self.targetCreatedBabels != sum(self.perMonthEventBudget.values()):
            raise ValueError(
                "targetCreatedBabels must equal the scheduled per-month event total"
            )
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


class RecommendationRequestV2(FrozenContract):
    schemaVersion: Literal[2]
    requestId: UUID
    runId: UUID
    creatorId: UUID
    sourceBabelId: UUID
    sourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    traversalSessionId: UUID
    parentRequestId: UUID | None
    traversalDepth: Literal[0, 1]
    title: str | None = None
    text: str | None = None
    historyBabelIds: list[UUID]
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
                raise ValueError("existing-source request loads persisted observable content")
        return self


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


class RecommendationResponseV2(RecommendationResponseV1):
    schemaVersion: Literal[2]
    sourceVectorOrigin: Literal["qwen_encode", "cache_hit", "pgvector_load"]


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


class FeedbackEventV2(FrozenContract):
    schemaVersion: Literal[2]
    eventId: UUID
    requestId: UUID
    runId: UUID
    creatorId: UUID
    sourceBabelId: UUID
    sourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    traversalSessionId: UUID
    parentRequestId: UUID | None
    traversalDepth: Literal[0, 1]
    modelId: UUID
    modelVersion: int = Field(ge=0)
    embeddingSpaceId: UUID
    retrievalBackend: RetrievalBackend
    candidateActions: list[CandidateActionV1]
    occurredAtNs: int = Field(ge=0, le=9_223_372_036_854_775_807)

    @model_validator(mode="after")
    def parent_matches_depth(self) -> "FeedbackEventV2":
        if (self.traversalDepth == 0) != (self.parentRequestId is None):
            raise ValueError("feedback parentRequestId must match traversalDepth")
        return self


class LifecycleActivityV1(FrozenContract):
    kind: Literal["lifecycle"]


class RecommendationActivityV1(FrozenContract):
    kind: Literal["recommendation"]
    creatorId: UUID
    newBabelId: UUID
    newBabelTitle: str = Field(min_length=1)
    candidateBabelIds: list[UUID]
    includeBabelIds: list[UUID]
    excludeBabelIds: list[UUID]
    ignoreBabelIds: list[UUID]
    acceptedEdgeCount: int = Field(ge=0)
    modelId: UUID
    modelVersion: int = Field(ge=0)


class FeedbackActivityV1(FrozenContract):
    kind: Literal["feedback"]
    kafkaOffset: int = Field(ge=0)
    kafkaLag: int = Field(ge=0)


class TrainingActivityV1(FrozenContract):
    kind: Literal["training"]
    trainerStep: int = Field(ge=0)
    rollingRankLoss: float = Field(ge=0)


class SynchronizationActivityV1(FrozenContract):
    kind: Literal["synchronization"]
    checkpointPath: str = Field(min_length=1)
    checkpointSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    synchronizationVersion: int = Field(ge=0)
    modelId: UUID
    modelVersion: int = Field(ge=0)


ActivityDetailsV1 = Annotated[
    LifecycleActivityV1
    | RecommendationActivityV1
    | FeedbackActivityV1
    | TrainingActivityV1
    | SynchronizationActivityV1,
    Field(discriminator="kind"),
]


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
    details: ActivityDetailsV1

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


class ModelManifestV2(FrozenContract):
    """Immutable identity of the accepted 50k distilled Qwen serving model.

    V1 remains readable for the checked-in smoke fixture.  V2 deliberately
    closes every identity needed to distinguish the real encoder from that
    fixture and to reproduce its 100-dimensional vector space.
    """

    schemaVersion: Literal[2]
    modelId: UUID
    label: str = Field(min_length=1)
    parentModelId: UUID | None
    producingRunId: UUID | None
    encoderRepo: Literal["dhelmy990/babel-qwen-navigation-2016-interview"]
    encoderRevision: Literal["57d949cd634b920cc1a46f27c9b21df094b5240e"]
    artifactPath: str = Field(pattern=r"^artifacts/[a-f0-9]{64}$")
    artifactId: Literal["3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"]
    artifactManifestSha256: Literal["5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"]
    checkpointTreeSha256: Literal["ddf8721cc38abc9f61b8738d6092e4f6c9542c3c533fc6a81677b307533edcff"]
    baseModelId: Literal["Qwen/Qwen3-Embedding-0.6B"]
    baseModelRevision: Literal["97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"]
    tokenizerRevision: Literal["97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"]
    datasetRepo: Literal["dhelmy990/babel-wikipedia-experiment"]
    datasetConfig: Literal["distillation_2016_interview"]
    datasetRevision: Literal["b440e98b04ab77afed7caf0455eca3189235fc3b"]
    datasetManifestSha256: Literal["33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09"]
    trainingSourceRevision: Literal["92f3ac697d78eb827d75b033df92dcbed887def7"]
    adapterSha256: Literal["4792009bfdaa9df25e3cd79f634ddfa081dc3c620828bda478be5db2fd7b8921"]
    projectionSha256: Literal["e156701da777fbb37e999c7d897f09cdd1993cd5c9d740aaafcfdeb6395d3ddb"]
    validationSha256: Literal["e4b76f00f65f4de0165e4eb47c652531295b4718d4d7bcc5008c5945a86f9e13"]
    trainingExamples: Literal[50_000]
    embeddingSpace: EmbeddingSpaceV1
    acceptance: Literal["real_50k_qwen"]
    immutable: Literal[True]

    @model_validator(mode="after")
    def real_lineage_and_artifact_are_closed(self) -> "ModelManifestV2":
        if (self.parentModelId is None) != (self.producingRunId is None):
            raise ValueError("parentModelId and producingRunId must both be null or set")
        if self.artifactPath != f"artifacts/{self.artifactId}":
            raise ValueError("artifactPath must identify artifactId")
        expected_artifact = (
            f"hf://{self.encoderRepo}@{self.encoderRevision}/{self.artifactPath}"
        )
        if self.embeddingSpace.distilledEncoderArtifact != expected_artifact:
            raise ValueError("embedding space does not identify the accepted Qwen artifact")
        if self.embeddingSpace.datasetRevision != self.datasetRevision:
            raise ValueError("embedding space and model dataset revisions differ")
        if self.embeddingSpace.compatibilityVersion != "babel-qwen-100d-v1":
            raise ValueError("real Qwen embedding compatibility version is invalid")
        return self


ModelManifest = ModelManifestV1 | ModelManifestV2


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
    "experiment-run-v2": RunConfigV2,
    "recommendation-request-v2": RecommendationRequestV2,
    "recommendation-response-v2": RecommendationResponseV2,
    "feedback-event-v2": FeedbackEventV2,
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


def canonical_vector_sha256(
    vectors: Mapping[UUID | str, Sequence[float]],
) -> str:
    """Hash UUID-sorted normalized vectors as little-endian float32 bytes."""
    digest = hashlib.sha256()
    ordered = sorted(
        ((UUID(str(identifier)), values) for identifier, values in vectors.items()),
        key=lambda row: str(row[0]).lower(),
    )
    for _identifier, values in ordered:
        if len(values) != 100 or any(not math.isfinite(float(value)) for value in values):
            raise ValueError("snapshot vectors must contain 100 finite values")
        norm = math.sqrt(math.fsum(float(value) ** 2 for value in values))
        if norm == 0.0:
            raise ValueError("snapshot vectors must be nonzero")
        digest.update(struct.pack("<100f", *(float(value) / norm for value in values)))
    return digest.hexdigest()


_SNAPSHOT_ROW_FIELDS = {
    "babelId",
    "creatorId",
    "sourceArticleKey",
    "catalogContentHash",
    "embeddingSpaceId",
    "servingModelId",
    "materializedModelVersion",
    "vectorSha256",
}


def canonical_pgvector_snapshot_sha256(
    rows: Iterable[Mapping[str, object]],
) -> str:
    """Hash the frozen canonical JSONL identity of active pgvector rows."""
    canonical: list[dict[str, object]] = []
    for row in rows:
        if set(row) != _SNAPSHOT_ROW_FIELDS:
            raise ValueError("pgvector snapshot row fields do not match v1 contract")
        value = dict(row)
        for field in ("babelId", "creatorId", "embeddingSpaceId", "servingModelId"):
            value[field] = str(UUID(str(value[field]))).lower()
        if not isinstance(value["materializedModelVersion"], int) or value[
            "materializedModelVersion"
        ] < 0:
            raise ValueError("materializedModelVersion must be nonnegative")
        for field in ("catalogContentHash", "vectorSha256"):
            text = value[field]
            if (
                not isinstance(text, str)
                or len(text) != 64
                or any(character not in "0123456789abcdef" for character in text)
            ):
                raise ValueError(f"{field} must be a lowercase SHA-256")
        if not isinstance(value["sourceArticleKey"], str):
            raise ValueError("sourceArticleKey must be text")
        canonical.append(value)
    canonical.sort(key=lambda row: str(row["babelId"]))
    payload = b"".join(
        (
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        for row in canonical
    )
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ActivityLogV1",
    "ActivityDetailsV1",
    "CandidateActionV1",
    "canonical_pgvector_snapshot_sha256",
    "canonical_vector_sha256",
    "contract_schema_documents",
    "EmbeddingSpaceV1",
    "DistilledServingArtifactV1",
    "FeedbackEventV1",
    "FeedbackEventV2",
    "FeedbackActivityV1",
    "HnswSnapshotV1",
    "ModelManifestV1",
    "ModelManifestV2",
    "ModelManifest",
    "LifecycleActivityV1",
    "RecommendationActivityV1",
    "RecommendationCandidateV1",
    "RecommendationRequestV1",
    "RecommendationRequestV2",
    "RecommendationResponseV1",
    "RecommendationResponseV2",
    "RunConfigV1",
    "RunConfigV2",
    "SynchronizationActivityV1",
    "TrainingActivityV1",
    "validate_contract",
]
