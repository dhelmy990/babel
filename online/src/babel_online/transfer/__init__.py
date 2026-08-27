"""Portable population-transfer contracts and pure bundle operations."""

from .contracts import (
    CATALOG_ARROW_SCHEMA,
    EMBEDDINGS_ARROW_SCHEMA,
    PARQUET_WRITER_SETTINGS,
    ArrowFieldV1,
    ArrowSchemasV1,
    OriginToFreshRebindingV1,
    ParquetWriterSettingsV1,
    PayloadMetadataV1,
    PopulationTransferManifestV1,
    PopulationTransferMetadataV1,
)
from .parquet_bundle import (
    BundleFiles,
    PopulationTransferBundleInput,
    PopulationTransferIntegrityError,
    PopulationTransferRow,
    vector_f32le,
    verify_bundle,
    write_bundle_payloads,
)

__all__ = [
    "CATALOG_ARROW_SCHEMA",
    "EMBEDDINGS_ARROW_SCHEMA",
    "PARQUET_WRITER_SETTINGS",
    "ArrowFieldV1",
    "ArrowSchemasV1",
    "OriginToFreshRebindingV1",
    "ParquetWriterSettingsV1",
    "PayloadMetadataV1",
    "BundleFiles",
    "PopulationTransferBundleInput",
    "PopulationTransferIntegrityError",
    "PopulationTransferManifestV1",
    "PopulationTransferMetadataV1",
    "PopulationTransferRow",
    "vector_f32le",
    "verify_bundle",
    "write_bundle_payloads",
]
