from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from babel_online.transfer import (
    CATALOG_ARROW_SCHEMA,
    EMBEDDINGS_ARROW_SCHEMA,
    PARQUET_WRITER_SETTINGS,
    OriginToFreshRebindingV1,
    PayloadMetadataV1,
    PopulationTransferManifestV1,
)


ORIGIN_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SERVING_MODEL_ID = UUID("22222222-2222-4222-8222-222222222222")
EMBEDDING_SPACE_ID = UUID("33333333-3333-4333-8333-333333333333")


def manifest_values() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "originTrialId": "trial-2026-08-27",
        "originRunId": ORIGIN_RUN_ID,
        "rowCount": 2,
        "creatorCount": 2,
        "periodCounts": {"2026-06": 1, "2026-07": 1},
        "vectorDimension": 100,
        "vectorDtype": "<f4",
        "vectorEndian": "little",
        "modelRepository": "private/model",
        "modelRevision": "1" * 40,
        "modelArtifactId": "2" * 64,
        "servingModelId": SERVING_MODEL_ID,
        "materializedModelVersion": 0,
        "embeddingSpaceId": EMBEDDING_SPACE_ID,
        "embeddingSpaceVersion": "babel-qwen-100d-v1",
        "baseModelRepository": "Qwen/Qwen3-Embedding-0.6B",
        "baseModelRevision": "3" * 40,
        "datasetRepository": "private/dataset",
        "datasetConfiguration": "distillation_2016_interview",
        "datasetRevision": "4" * 40,
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
        "writerSettings": PARQUET_WRITER_SETTINGS,
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
            runIdBinding="allocate_fresh_run_id",
            servingModelIdBinding="allocate_fresh_serving_model_id",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveEmbeddingSpaceId=True,
            preserveContentIdentity=True,
        ),
    }


def test_manifest_round_trips_as_a_frozen_strict_v1_contract() -> None:
    manifest = PopulationTransferManifestV1.model_validate(manifest_values())

    encoded = manifest.model_dump_json(by_alias=True)
    restored = PopulationTransferManifestV1.model_validate_json(encoded)

    assert restored == manifest
    assert restored.materializedModelVersion == 0
    assert restored.createdAt.tzinfo == timezone.utc
    with pytest.raises(ValidationError):
        PopulationTransferManifestV1.model_validate({**manifest_values(), "extra": 1})
    with pytest.raises(ValidationError):
        manifest.rowCount = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 2),
        ("rowCount", 3),
        ("creatorCount", 3),
        ("vectorDimension", 99),
        ("vectorDtype", "float32"),
        ("vectorEndian", "big"),
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
            runIdBinding="allocate_fresh_run_id",
            servingModelIdBinding="allocate_fresh_serving_model_id",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveEmbeddingSpaceId=True,
            preserveContentIdentity=True,
        ).model_dump(),
        "preserveBabelIds": False,
    }
    mutations.append(wrong_rebinding)

    for values in mutations:
        with pytest.raises(ValidationError):
            PopulationTransferManifestV1.model_validate(values)
