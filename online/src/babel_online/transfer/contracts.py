"""Closed, portable contracts for a population transfer bundle."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_PATTERN = r"^[a-f0-9]{64}$"
REVISION_PATTERN = r"^[a-f0-9]{40,64}$"

EMBEDDINGS_ARROW_SCHEMA: list[dict[str, object]] = [
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

CATALOG_ARROW_SCHEMA: list[dict[str, object]] = [
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

PARQUET_WRITER_SETTINGS: dict[str, object] = {
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


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


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
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_schemas(self) -> "ArrowSchemasV1":
        if [field.model_dump() for field in self.babel_embeddings] != EMBEDDINGS_ARROW_SCHEMA:
            raise ValueError("babel_embeddings Arrow schema is not the frozen schema")
        if [field.model_dump() for field in self.babel_catalog] != CATALOG_ARROW_SCHEMA:
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


class PayloadMetadataV1(_StrictFrozenModel):
    sha256: str = Field(pattern=SHA256_PATTERN)
    bytes: int = Field(gt=0)


class OriginToFreshRebindingV1(_StrictFrozenModel):
    originRunId: UUID
    runIdBinding: Literal["allocate_fresh_run_id"]
    servingModelIdBinding: Literal["allocate_fresh_serving_model_id"]
    preserveBabelIds: Literal[True]
    preserveCreatorIds: Literal[True]
    preserveEmbeddingSpaceId: Literal[True]
    preserveContentIdentity: Literal[True]


class PopulationTransferMetadataV1(_StrictFrozenModel):
    """Source identities needed to build a manifest around payload rows."""

    originTrialId: str = Field(min_length=1)
    originRunId: UUID
    modelRepository: str = Field(min_length=1)
    modelRevision: str = Field(pattern=REVISION_PATTERN)
    modelArtifactId: str = Field(pattern=SHA256_PATTERN)
    servingModelId: UUID
    materializedModelVersion: int = Field(ge=0, le=2_147_483_647)
    embeddingSpaceId: UUID
    embeddingSpaceVersion: str = Field(min_length=1)
    baseModelRepository: Literal["Qwen/Qwen3-Embedding-0.6B"]
    baseModelRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    datasetRepository: str = Field(min_length=1)
    datasetConfiguration: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=REVISION_PATTERN)
    frozenPopulationSha256: str = Field(pattern=SHA256_PATTERN)
    orderedPopulationSha256: str = Field(pattern=SHA256_PATTERN)
    snapshotSha256: str = Field(pattern=SHA256_PATTERN)
    scheduleSha256: str = Field(pattern=SHA256_PATTERN)
    contentSha256: str = Field(pattern=SHA256_PATTERN)
    createdAt: datetime
    rebinding: OriginToFreshRebindingV1

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
    originTrialId: str = Field(min_length=1)
    originRunId: UUID
    rowCount: int = Field(gt=0)
    creatorCount: int = Field(gt=0)
    periodCounts: dict[str, int]
    vectorDimension: Literal[100]
    vectorDtype: Literal["<f4"]
    vectorEndian: Literal["little"]
    modelRepository: str = Field(min_length=1)
    modelRevision: str = Field(pattern=REVISION_PATTERN)
    modelArtifactId: str = Field(pattern=SHA256_PATTERN)
    servingModelId: UUID
    materializedModelVersion: int = Field(ge=0, le=2_147_483_647)
    embeddingSpaceId: UUID
    embeddingSpaceVersion: str = Field(min_length=1)
    baseModelRepository: Literal["Qwen/Qwen3-Embedding-0.6B"]
    baseModelRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    datasetRepository: str = Field(min_length=1)
    datasetConfiguration: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=REVISION_PATTERN)
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
    payloads: dict[str, PayloadMetadataV1]
    rebinding: OriginToFreshRebindingV1

    @field_validator("createdAt")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("createdAt must be an aware UTC timestamp")
        return value

    @field_validator("periodCounts")
    @classmethod
    def periods_are_exact_and_ordered(cls, value: dict[str, int]) -> dict[str, int]:
        if not value or list(value) != sorted(value):
            raise ValueError("periodCounts must be non-empty and lexically ordered")
        if any(count <= 0 for count in value.values()):
            raise ValueError("periodCounts must contain positive exact counts")
        if any(
            len(period) != 7
            or period[4] != "-"
            or not period[:4].isdigit()
            or not period[5:].isdigit()
            or not 1 <= int(period[5:]) <= 12
            for period in value
        ):
            raise ValueError("periodCounts keys must be YYYY-MM periods")
        return value

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
