"""Closed, portable contracts for a population transfer bundle."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_PATTERN = r"^[a-f0-9]{64}$"
ORIGIN_TRIAL_ID = UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43")
ORIGIN_RUN_ID = UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9")
SERVING_MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
EMBEDDING_SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")
MODEL_REPOSITORY = "dhelmy990/babel-qwen-navigation-2016-interview"
MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
MODEL_ARTIFACT_ID = "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"
BASE_MODEL_REPOSITORY = "Qwen/Qwen3-Embedding-0.6B"
BASE_MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIGURATION = "crosswalk_2026_06_07"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"

_EMBEDDINGS_ARROW_SCHEMA = [
    {"name": "babel_id", "type": "string", "nullable": False},
    {"name": "creator_id", "type": "string", "nullable": False},
    {"name": "serving_model_id", "type": "string", "nullable": False},
    {"name": "materialized_model_version", "type": "int32", "nullable": False},
    {"name": "embedding_space_id", "type": "string", "nullable": False},
    {"name": "catalog_content_hash", "type": "string", "nullable": False},
    {"name": "model_artifact_id", "type": "string", "nullable": False},
    {"name": "dataset_revision", "type": "string", "nullable": False},
    {
        "name": "vector",
        "type": "fixed_size_list<float32>[100]",
        "nullable": False,
    },
    {"name": "vector_sha256", "type": "string", "nullable": False},
]
EMBEDDINGS_ARROW_SCHEMA: tuple[Mapping[str, object], ...] = tuple(
    MappingProxyType(field) for field in _EMBEDDINGS_ARROW_SCHEMA
)

_CATALOG_ARROW_SCHEMA = [
    {"name": "babel_id", "type": "string", "nullable": False},
    {"name": "creator_id", "type": "string", "nullable": False},
    {"name": "source_article_key", "type": "string", "nullable": False},
    {"name": "title", "type": "string", "nullable": False},
    {"name": "article_text", "type": "string", "nullable": False},
    {"name": "catalog_content_hash", "type": "string", "nullable": False},
    {"name": "event_number", "type": "int64", "nullable": False},
    {"name": "created_at_ns", "type": "int64", "nullable": False},
    {"name": "finalized_at_ns", "type": "int64", "nullable": False},
    {"name": "schedule_index", "type": "int32", "nullable": False},
    {"name": "creator_event_number", "type": "int32", "nullable": False},
    {"name": "period", "type": "string", "nullable": False},
    {"name": "root_babel_id", "type": "string", "nullable": False},
    {"name": "traversal_session_id", "type": "string", "nullable": False},
    {"name": "work_id", "type": "string", "nullable": False},
    {"name": "workload_sha256", "type": "string", "nullable": False},
    {"name": "schedule_created_at_ns", "type": "int64", "nullable": False},
    {"name": "dataset_repository", "type": "string", "nullable": False},
    {"name": "dataset_configuration", "type": "string", "nullable": False},
    {"name": "dataset_revision", "type": "string", "nullable": False},
    {"name": "dataset_row_reference", "type": "string", "nullable": False},
]
CATALOG_ARROW_SCHEMA: tuple[Mapping[str, object], ...] = tuple(
    MappingProxyType(field) for field in _CATALOG_ARROW_SCHEMA
)

PARQUET_WRITER_SETTINGS: Mapping[str, object] = MappingProxyType(
    {
        "parquetVersion": "2.6",
        "dataPageVersion": "1.0",
        "compression": "zstd",
        "compressionLevel": 9,
        "useDictionary": False,
        "writeStatistics": True,
        "useCompliantNestedType": True,
        "storeSchema": True,
        "rowGroupSize": 10_000,
        "timestampRepresentation": "integer_ns",
    }
)

POPULATION_HASH_DERIVATIONS: Mapping[str, str] = MappingProxyType(
    {
        "orderedPopulationSha256": "sha256(concat(vector_f32le in lowercase babel_id order))",
        "snapshotSha256": "canonical_pgvector_snapshot_sha256(v1)",
        "scheduleSha256": "sha256(model.frozen_population schedule.jsonl v1)",
        "contentSha256": "sha256(model.frozen_population babels.jsonl v1)",
        "frozenPopulationSha256": "sha256(sorted-key compact JSON of contentSha256,orderedPopulationSha256,scheduleSha256,snapshotSha256)",
    }
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class _FrozenDict(dict):
    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("frozen contract mapping does not support mutation")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class ArrowFieldV1(_StrictFrozenModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    nullable: Literal[False]


class ArrowSchemasV1(_StrictFrozenModel):
    babel_embeddings: tuple[ArrowFieldV1, ...]
    babel_catalog: tuple[ArrowFieldV1, ...]

    @field_validator("babel_embeddings", "babel_catalog", mode="before")
    @classmethod
    def freeze_field_sequence(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                dict(item) if isinstance(item, Mapping) else item for item in value
            )
        return value

    @model_validator(mode="after")
    def exact_schemas(self) -> "ArrowSchemasV1":
        if tuple(field.model_dump() for field in self.babel_embeddings) != tuple(
            dict(field) for field in EMBEDDINGS_ARROW_SCHEMA
        ):
            raise ValueError("babel_embeddings Arrow schema is not the frozen schema")
        if tuple(field.model_dump() for field in self.babel_catalog) != tuple(
            dict(field) for field in CATALOG_ARROW_SCHEMA
        ):
            raise ValueError("babel_catalog Arrow schema is not the frozen schema")
        return self


class ParquetWriterSettingsV1(_StrictFrozenModel):
    parquetVersion: Literal["2.6"]
    dataPageVersion: Literal["1.0"]
    compression: Literal["zstd"]
    compressionLevel: Literal[9]
    useDictionary: Literal[False]
    writeStatistics: Literal[True]
    useCompliantNestedType: Literal[True]
    storeSchema: Literal[True]
    rowGroupSize: Literal[10_000]
    timestampRepresentation: Literal["integer_ns"]
    pyarrowVersion: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)?$")


class PopulationHashDerivationsV1(_StrictFrozenModel):
    orderedPopulationSha256: Literal[
        "sha256(concat(vector_f32le in lowercase babel_id order))"
    ]
    snapshotSha256: Literal["canonical_pgvector_snapshot_sha256(v1)"]
    scheduleSha256: Literal[
        "sha256(model.frozen_population schedule.jsonl v1)"
    ]
    contentSha256: Literal[
        "sha256(model.frozen_population babels.jsonl v1)"
    ]
    frozenPopulationSha256: Literal[
        "sha256(sorted-key compact JSON of contentSha256,orderedPopulationSha256,scheduleSha256,snapshotSha256)"
    ]


class PayloadMetadataV1(_StrictFrozenModel):
    sha256: str = Field(pattern=SHA256_PATTERN)
    bytes: int = Field(gt=0)


class OriginToFreshRebindingV1(_StrictFrozenModel):
    originRunId: UUID
    freshTrialIdBinding: Literal["allocate_uuid4"]
    freshPopulationRunIdBinding: Literal["uuid5(fresh_trial_id,'population')"]
    preserveBabelIds: Literal[True]
    preserveCreatorIds: Literal[True]
    preserveSourceIdentity: Literal[True]
    preserveModelIdentity: Literal[True]
    preserveArtifactIdentity: Literal[True]
    preserveEmbeddingSpaceIdentity: Literal[True]
    preserveContentIdentities: Literal[True]
    preserveScheduleIdentities: Literal[True]
    preserveVectorIdentities: Literal[True]

    @field_validator("originRunId")
    @classmethod
    def exact_origin_run(cls, value: UUID) -> UUID:
        if value != ORIGIN_RUN_ID:
            raise ValueError("originRunId is not the frozen population run")
        return value


class PopulationTransferMetadataV1(_StrictFrozenModel):
    """Source identities needed to build a manifest around payload rows."""

    originTrialId: UUID
    originRunId: UUID
    modelRepository: Literal[MODEL_REPOSITORY]
    modelRevision: Literal[MODEL_REVISION]
    modelArtifactId: Literal[MODEL_ARTIFACT_ID]
    servingModelId: UUID
    materializedModelVersion: Literal[0]
    embeddingSpaceId: UUID
    embeddingSpaceVersion: Literal["babel-qwen-100d-v1"]
    baseModelRepository: Literal[BASE_MODEL_REPOSITORY]
    baseModelRevision: Literal[BASE_MODEL_REVISION]
    datasetRepository: Literal[DATASET_REPOSITORY]
    datasetConfiguration: Literal[DATASET_CONFIGURATION]
    datasetRevision: Literal[DATASET_REVISION]
    createdAt: datetime
    rebinding: OriginToFreshRebindingV1

    @field_validator("originTrialId")
    @classmethod
    def exact_origin_trial(cls, value: UUID) -> UUID:
        if value != ORIGIN_TRIAL_ID:
            raise ValueError("originTrialId is not the frozen production trial")
        return value

    @field_validator("originRunId")
    @classmethod
    def exact_origin_run(cls, value: UUID) -> UUID:
        if value != ORIGIN_RUN_ID:
            raise ValueError("originRunId is not the frozen population run")
        return value

    @field_validator("servingModelId")
    @classmethod
    def exact_serving_model(cls, value: UUID) -> UUID:
        if value != SERVING_MODEL_ID:
            raise ValueError("servingModelId is not the frozen starting model")
        return value

    @field_validator("embeddingSpaceId")
    @classmethod
    def exact_embedding_space(cls, value: UUID) -> UUID:
        if value != EMBEDDING_SPACE_ID:
            raise ValueError("embeddingSpaceId is not the frozen Qwen space")
        return value

    @field_validator("createdAt")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("createdAt must be an aware UTC timestamp")
        return value

    @model_validator(mode="after")
    def rebinding_matches_origin(self) -> "PopulationTransferMetadataV1":
        if self.rebinding.originRunId != self.originRunId:
            raise ValueError("rebinding originRunId differs from metadata originRunId")
        return self


class PopulationTransferManifestV1(_StrictFrozenModel):
    """Manifest whose identities and payload properties are closed under import."""

    schemaVersion: Literal[1]
    bundleFormatVersion: Literal[1]
    originTrialId: UUID
    originRunId: UUID
    rowCount: Literal[10_000]
    creatorCount: Literal[50]
    periodCounts: Mapping[str, int]
    vectorDimension: Literal[100]
    vectorDtype: Literal["float32"]
    byteOrder: Literal["little"]
    normalization: Literal["l2"]
    normalizationTolerance: Literal[1e-5]
    modelRepository: Literal[MODEL_REPOSITORY]
    modelRevision: Literal[MODEL_REVISION]
    modelArtifactId: Literal[MODEL_ARTIFACT_ID]
    servingModelId: UUID
    materializedModelVersion: Literal[0]
    embeddingSpaceId: UUID
    embeddingSpaceVersion: Literal["babel-qwen-100d-v1"]
    baseModelRepository: Literal[BASE_MODEL_REPOSITORY]
    baseModelRevision: Literal[BASE_MODEL_REVISION]
    datasetRepository: Literal[DATASET_REPOSITORY]
    datasetConfiguration: Literal[DATASET_CONFIGURATION]
    datasetRevision: Literal[DATASET_REVISION]
    frozenPopulationSha256: str = Field(pattern=SHA256_PATTERN)
    orderedPopulationSha256: str = Field(pattern=SHA256_PATTERN)
    snapshotSha256: str = Field(pattern=SHA256_PATTERN)
    scheduleSha256: str = Field(pattern=SHA256_PATTERN)
    contentSha256: str = Field(pattern=SHA256_PATTERN)
    createdAt: datetime
    vectorNormMin: float
    vectorNormMean: float
    vectorNormP01: float
    vectorNormMedian: float
    vectorNormP99: float
    vectorNormMax: float
    arrowSchemas: ArrowSchemasV1
    writerSettings: ParquetWriterSettingsV1
    hashDerivations: PopulationHashDerivationsV1
    payloads: Mapping[str, PayloadMetadataV1]
    rebinding: OriginToFreshRebindingV1

    @field_validator("hashDerivations", mode="before")
    @classmethod
    def accept_immutable_hash_derivations(cls, value: object) -> object:
        return dict(value) if isinstance(value, Mapping) else value

    @field_validator("originTrialId")
    @classmethod
    def exact_origin_trial(cls, value: UUID) -> UUID:
        if value != ORIGIN_TRIAL_ID:
            raise ValueError("originTrialId is not the frozen production trial")
        return value

    @field_validator("originRunId")
    @classmethod
    def exact_origin_run(cls, value: UUID) -> UUID:
        if value != ORIGIN_RUN_ID:
            raise ValueError("originRunId is not the frozen population run")
        return value

    @field_validator("servingModelId")
    @classmethod
    def exact_serving_model(cls, value: UUID) -> UUID:
        if value != SERVING_MODEL_ID:
            raise ValueError("servingModelId is not the frozen starting model")
        return value

    @field_validator("embeddingSpaceId")
    @classmethod
    def exact_embedding_space(cls, value: UUID) -> UUID:
        if value != EMBEDDING_SPACE_ID:
            raise ValueError("embeddingSpaceId is not the frozen Qwen space")
        return value

    @field_validator("createdAt")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("createdAt must be an aware UTC timestamp")
        return value

    @field_validator("periodCounts")
    @classmethod
    def periods_are_exact_and_ordered(
        cls, value: Mapping[str, int]
    ) -> Mapping[str, int]:
        if value != {"2026-06": 5_000, "2026-07": 5_000}:
            raise ValueError("periodCounts must be exactly 5,000 June and 5,000 July rows")
        return _FrozenDict(value)

    @field_validator("payloads")
    @classmethod
    def freeze_payloads(
        cls, value: Mapping[str, PayloadMetadataV1]
    ) -> Mapping[str, PayloadMetadataV1]:
        return _FrozenDict(value)

    @model_validator(mode="after")
    def closed_population_contract(self) -> "PopulationTransferManifestV1":
        if sum(self.periodCounts.values()) != self.rowCount:
            raise ValueError("periodCounts must sum to rowCount")
        if self.creatorCount > self.rowCount:
            raise ValueError("creatorCount cannot exceed rowCount")
        if self.rebinding.originRunId != self.originRunId:
            raise ValueError("rebinding originRunId differs from manifest originRunId")
        if set(self.payloads) != {
            "babel_catalog.parquet",
            "babel_embeddings.parquet",
            "import_population.py",
        }:
            raise ValueError("payloads must cover both Parquet files and the launcher")
        norms = (
            self.vectorNormMin,
            self.vectorNormP01,
            self.vectorNormMedian,
            self.vectorNormMean,
            self.vectorNormP99,
            self.vectorNormMax,
        )
        if any(value < 0.99999 or value > 1.00001 for value in norms):
            raise ValueError("vector norm statistics are outside the unit-norm tolerance")
        if not (
            self.vectorNormMin
            <= self.vectorNormP01
            <= self.vectorNormMedian
            <= self.vectorNormP99
            <= self.vectorNormMax
        ):
            raise ValueError("vector norm quantiles are not ordered")
        if not self.vectorNormMin <= self.vectorNormMean <= self.vectorNormMax:
            raise ValueError("vector norm mean is outside min/max")
        return self
