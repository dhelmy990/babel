from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
import pyarrow as pa
from pydantic import ValidationError

from babel_online.transfer import (
    CATALOG_ARROW_SCHEMA,
    EMBEDDINGS_ARROW_SCHEMA,
    PARQUET_WRITER_SETTINGS,
    POPULATION_HASH_DERIVATIONS,
    OriginToFreshRebindingV1,
    PayloadMetadataV1,
    PopulationTransferManifestV1,
)


ORIGIN_TRIAL_ID = UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43")
ORIGIN_RUN_ID = UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9")
SERVING_MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
EMBEDDING_SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")


def manifest_values() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "bundleFormatVersion": 1,
        "originTrialId": ORIGIN_TRIAL_ID,
        "originRunId": ORIGIN_RUN_ID,
        "rowCount": 10_000,
        "creatorCount": 50,
        "periodCounts": {"2026-06": 5_000, "2026-07": 5_000},
        "vectorDimension": 100,
        "vectorDtype": "float32",
        "byteOrder": "little",
        "normalization": "l2",
        "normalizationTolerance": 1e-5,
        "modelRepository": "dhelmy990/babel-qwen-navigation-2016-interview",
        "modelRevision": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "modelArtifactId": "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
        "servingModelId": SERVING_MODEL_ID,
        "materializedModelVersion": 0,
        "embeddingSpaceId": EMBEDDING_SPACE_ID,
        "embeddingSpaceVersion": "babel-qwen-100d-v1",
        "baseModelRepository": "Qwen/Qwen3-Embedding-0.6B",
        "baseModelRevision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "datasetRepository": "dhelmy990/babel-wikipedia-experiment",
        "datasetConfiguration": "crosswalk_2026_06_07",
        "datasetRevision": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "frozenPopulationSha256": "5" * 64,
        "orderedPopulationSha256": "6" * 64,
        "snapshotSha256": "7" * 64,
        "scheduleSha256": "8" * 64,
        "contentSha256": "9" * 64,
        "createdAt": datetime(2026, 8, 27, 1, 2, 3, tzinfo=timezone.utc),
        "vectorNormMin": 1.0,
        "vectorNormMean": 1.0,
        "vectorNormP01": 1.0,
        "vectorNormMedian": 1.0,
        "vectorNormP99": 1.0,
        "vectorNormMax": 1.0,
        "arrowSchemas": {
            "babel_embeddings": EMBEDDINGS_ARROW_SCHEMA,
            "babel_catalog": CATALOG_ARROW_SCHEMA,
        },
        "writerSettings": {
            **PARQUET_WRITER_SETTINGS,
            "pyarrowVersion": pa.__version__,
        },
        "hashDerivations": POPULATION_HASH_DERIVATIONS,
        "payloads": {
            "babel_catalog.parquet": PayloadMetadataV1(
                sha256="a" * 64, bytes=123
            ),
            "babel_embeddings.parquet": PayloadMetadataV1(
                sha256="b" * 64, bytes=456
            ),
            "import_population.py": PayloadMetadataV1(
                sha256="c" * 64, bytes=78
            ),
        },
        "rebinding": OriginToFreshRebindingV1(
            originRunId=ORIGIN_RUN_ID,
            freshTrialIdBinding="allocate_uuid4",
            freshPopulationRunIdBinding="uuid5(fresh_trial_id,'population')",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveSourceIdentity=True,
            preserveModelIdentity=True,
            preserveArtifactIdentity=True,
            preserveEmbeddingSpaceIdentity=True,
            preserveContentIdentities=True,
            preserveScheduleIdentities=True,
            preserveVectorIdentities=True,
        ),
    }


def test_manifest_round_trips_as_a_frozen_strict_v1_contract() -> None:
    manifest = PopulationTransferManifestV1.model_validate(manifest_values())

    encoded = manifest.model_dump_json(by_alias=True)
    restored = PopulationTransferManifestV1.model_validate_json(encoded)

    assert restored == manifest
    assert restored.materializedModelVersion == 0
    assert restored.writerSettings.pyarrowVersion == pa.__version__
    assert restored.hashDerivations.model_dump() == dict(POPULATION_HASH_DERIVATIONS)
    assert restored.createdAt.tzinfo == timezone.utc
    with pytest.raises(ValidationError):
        PopulationTransferManifestV1.model_validate({**manifest_values(), "extra": 1})
    with pytest.raises(ValidationError):
        manifest.rowCount = 3  # type: ignore[misc]
    with pytest.raises(TypeError):
        manifest.periodCounts["2026-06"] = 1
    with pytest.raises(TypeError):
        manifest.payloads["manifest.json"] = PayloadMetadataV1(
            sha256="d" * 64, bytes=1
        )


def test_exported_schema_and_writer_constants_are_defensively_immutable() -> None:
    with pytest.raises(TypeError):
        PARQUET_WRITER_SETTINGS["compressionLevel"] = 1
    with pytest.raises(TypeError):
        EMBEDDINGS_ARROW_SCHEMA[0]["name"] = "changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 2),
        ("bundleFormatVersion", 2),
        ("originTrialId", UUID("00000000-0000-4000-8000-000000000001")),
        ("originRunId", UUID("00000000-0000-5000-8000-000000000002")),
        ("rowCount", 9_999),
        ("creatorCount", 49),
        ("periodCounts", {"2026-06": 4_999, "2026-07": 5_001}),
        ("vectorDimension", 99),
        ("vectorDtype", "<f4"),
        ("byteOrder", "big"),
        ("normalization", "none"),
        ("normalizationTolerance", 1e-4),
        ("modelRepository", "private/model"),
        ("modelRevision", "1" * 40),
        ("modelArtifactId", "2" * 64),
        ("servingModelId", UUID("00000000-0000-4000-8000-000000000003")),
        ("materializedModelVersion", 1),
        ("embeddingSpaceId", UUID("00000000-0000-4000-8000-000000000004")),
        ("embeddingSpaceVersion", "other"),
        ("baseModelRepository", "other/base"),
        ("baseModelRevision", "3" * 40),
        ("datasetRepository", "private/dataset"),
        ("datasetConfiguration", "distillation_2016_interview"),
        ("datasetRevision", "4" * 40),
        ("createdAt", datetime(2026, 8, 27)),
        ("vectorNormMin", 0.99),
        ("vectorNormMax", 1.01),
    ],
)
def test_manifest_rejects_malformed_identity_counts_vectors_and_time(
    field: str, value: object
) -> None:
    values = manifest_values()
    values[field] = value

    with pytest.raises(ValidationError):
        PopulationTransferManifestV1.model_validate(values)


def test_manifest_requires_exact_schemas_settings_payloads_and_rebinding() -> None:
    mutations = []

    wrong_schema = manifest_values()
    wrong_schema["arrowSchemas"] = {
        "babel_embeddings": CATALOG_ARROW_SCHEMA,
        "babel_catalog": CATALOG_ARROW_SCHEMA,
    }
    mutations.append(wrong_schema)

    wrong_settings = manifest_values()
    wrong_settings["writerSettings"] = {
        **PARQUET_WRITER_SETTINGS,
        "compressionLevel": 8,
    }
    mutations.append(wrong_settings)

    missing_payload = manifest_values()
    missing_payload["payloads"] = {
        "babel_catalog.parquet": PayloadMetadataV1(sha256="a" * 64, bytes=123)
    }
    mutations.append(missing_payload)

    wrong_rebinding = manifest_values()
    wrong_rebinding["rebinding"] = {
        **OriginToFreshRebindingV1(
            originRunId=ORIGIN_RUN_ID,
            freshTrialIdBinding="allocate_uuid4",
            freshPopulationRunIdBinding="uuid5(fresh_trial_id,'population')",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveSourceIdentity=True,
            preserveModelIdentity=True,
            preserveArtifactIdentity=True,
            preserveEmbeddingSpaceIdentity=True,
            preserveContentIdentities=True,
            preserveScheduleIdentities=True,
            preserveVectorIdentities=True,
        ).model_dump(),
        "preserveBabelIds": False,
    }
    mutations.append(wrong_rebinding)

    for values in mutations:
        with pytest.raises(ValidationError):
            PopulationTransferManifestV1.model_validate(values)
