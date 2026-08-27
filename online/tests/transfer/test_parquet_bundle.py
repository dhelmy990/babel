from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import babel_online.transfer as transfer_package
from babel_online.transfer import (
    BundleFiles,
    OriginToFreshRebindingV1,
    PopulationTransferBundleInput,
    PopulationTransferIntegrityError,
    PopulationTransferMetadataV1,
    PopulationTransferRow,
    vector_f32le,
    verify_bundle,
    write_bundle_payloads,
)


ORIGIN_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SERVING_MODEL_ID = "22222222-2222-4222-8222-222222222222"
EMBEDDING_SPACE_ID = "33333333-3333-4333-8333-333333333333"


def metadata() -> PopulationTransferMetadataV1:
    return PopulationTransferMetadataV1(
        originTrialId="trial-2026-08-27",
        originRunId=ORIGIN_RUN_ID,
        modelRepository="private/model",
        modelRevision="1" * 40,
        modelArtifactId="2" * 64,
        servingModelId=UUID(SERVING_MODEL_ID),
        materializedModelVersion=0,
        embeddingSpaceId=UUID(EMBEDDING_SPACE_ID),
        embeddingSpaceVersion="babel-qwen-100d-v1",
        baseModelRepository="Qwen/Qwen3-Embedding-0.6B",
        baseModelRevision="3" * 40,
        datasetRepository="private/dataset",
        datasetConfiguration="distillation_2016_interview",
        datasetRevision="4" * 40,
        frozenPopulationSha256="5" * 64,
        orderedPopulationSha256="6" * 64,
        snapshotSha256="7" * 64,
        scheduleSha256="8" * 64,
        contentSha256="9" * 64,
        createdAt=datetime(2026, 8, 27, 1, 2, 3, tzinfo=timezone.utc),
        rebinding=OriginToFreshRebindingV1(
            originRunId=ORIGIN_RUN_ID,
            runIdBinding="allocate_fresh_run_id",
            servingModelIdBinding="allocate_fresh_serving_model_id",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveEmbeddingSpaceId=True,
            preserveContentIdentity=True,
        ),
    )


def row(
    ordinal: int,
    *,
    babel_id: str | None = None,
    model_artifact_id: str = "2" * 64,
    vector: object | None = None,
) -> PopulationTransferRow:
    identifiers = (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    creators = (
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    )
    periods = ("2026-06", "2026-07")
    source_keys = ("enwiki:20", "enwiki:10")
    unit = np.zeros(100, dtype=np.float64)
    unit[ordinal] = 1.0
    return PopulationTransferRow(
        babel_id=babel_id or identifiers[ordinal],
        creator_id=creators[ordinal],
        serving_model_id=SERVING_MODEL_ID,
        materialized_model_version=0,
        embedding_space_id=EMBEDDING_SPACE_ID,
        catalog_content_hash=str(ordinal + 1) * 64,
        model_artifact_id=model_artifact_id,
        dataset_revision="4" * 40,
        vector=unit if vector is None else vector,
        source_article_key=source_keys[ordinal],
        title=f"Title {ordinal}",
        article_text=f"Article {ordinal}",
        event_number=ordinal + 1,
        created_at_ns=100 + ordinal,
        finalized_at_ns=200 + ordinal,
        schedule_index=ordinal,
        creator_event_number=1,
        period=periods[ordinal],
        root_babel_id=identifiers[ordinal],
        traversal_session_id=f"eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee{ordinal}",
        work_id=f"ffffffff-ffff-4fff-8fff-fffffffffff{ordinal}",
        workload_sha256=str(ordinal + 7) * 64,
        schedule_created_at_ns=50 + ordinal,
        dataset_repository="private/dataset",
        dataset_configuration="distillation_2016_interview",
    )


def bundle_input(*rows: PopulationTransferRow) -> PopulationTransferBundleInput:
    selected = rows or (row(1), row(0))
    return PopulationTransferBundleInput(metadata=metadata(), rows=tuple(selected))


def expected_embeddings_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("babel_id", pa.string(), nullable=False),
            pa.field("creator_id", pa.string(), nullable=False),
            pa.field("serving_model_id", pa.string(), nullable=False),
            pa.field("materialized_model_version", pa.int32(), nullable=False),
            pa.field("embedding_space_id", pa.string(), nullable=False),
            pa.field("catalog_content_hash", pa.string(), nullable=False),
            pa.field("model_artifact_id", pa.string(), nullable=False),
            pa.field("dataset_revision", pa.string(), nullable=False),
            pa.field(
                "vector",
                pa.list_(pa.float32(), 100),
                nullable=False,
            ),
            pa.field("vector_sha256", pa.string(), nullable=False),
        ]
    )


def expected_catalog_schema() -> pa.Schema:
    names_and_types = [
        ("babel_id", pa.string()),
        ("creator_id", pa.string()),
        ("source_article_key", pa.string()),
        ("title", pa.string()),
        ("article_text", pa.string()),
        ("catalog_content_hash", pa.string()),
        ("event_number", pa.int64()),
        ("created_at_ns", pa.int64()),
        ("finalized_at_ns", pa.int64()),
        ("schedule_index", pa.int32()),
        ("creator_event_number", pa.int32()),
        ("period", pa.string()),
        ("root_babel_id", pa.string()),
        ("traversal_session_id", pa.string()),
        ("work_id", pa.string()),
        ("workload_sha256", pa.string()),
        ("schedule_created_at_ns", pa.int64()),
        ("dataset_repository", pa.string()),
        ("dataset_configuration", pa.string()),
        ("dataset_revision", pa.string()),
        ("dataset_row_reference", pa.string()),
    ]
    return pa.schema(
        [pa.field(name, arrow_type, nullable=False) for name, arrow_type in names_and_types]
    )


def test_vector_f32le_is_exact_and_rejects_shape_finiteness_and_norm() -> None:
    unit = np.zeros(100)
    unit[3] = 1.0

    encoded = vector_f32le(unit)

    assert len(encoded) == 400
    assert encoded == np.asarray(unit, dtype="<f4").tobytes(order="C")
    for malformed in (
        np.zeros(99),
        np.full(100, np.nan),
        np.zeros(100),
        np.full(100, 0.2),
    ):
        with pytest.raises(PopulationTransferIntegrityError):
            vector_f32le(malformed)


def test_write_bundle_is_deterministic_complete_and_uses_exact_parquet_contract(
    tmp_path: Path,
) -> None:
    first = write_bundle_payloads(bundle_input(), tmp_path / "first")
    second = write_bundle_payloads(bundle_input(), tmp_path / "second")

    assert isinstance(first, BundleFiles)
    assert {path.name for path in first.root.iterdir()} == {
        "babel_embeddings.parquet",
        "babel_catalog.parquet",
        "manifest.json",
        "import_population.py",
        "SHA256SUMS",
    }
    assert stat.S_IMODE(first.launcher.stat().st_mode) == 0o700
    source_launcher = Path(transfer_package.__file__).with_name("import_population.py")
    assert first.launcher.read_bytes() == source_launcher.read_bytes()
    assert first.digest == second.digest
    for name in sorted(path.name for path in first.root.iterdir()):
        assert (first.root / name).read_bytes() == (second.root / name).read_bytes()

    assert pq.read_schema(first.embeddings) == expected_embeddings_schema()
    assert pq.read_schema(first.catalog) == expected_catalog_schema()
    embedding_metadata = pq.ParquetFile(first.embeddings).metadata
    assert embedding_metadata.format_version == "2.6"
    assert embedding_metadata.num_row_groups == 1
    assert embedding_metadata.row_group(0).column(0).compression == "ZSTD"
    assert embedding_metadata.row_group(0).column(0).statistics is not None

    embeddings = pq.read_table(first.embeddings).to_pylist()
    catalog = pq.read_table(first.catalog).to_pylist()
    assert [item["babel_id"] for item in embeddings] == sorted(
        item["babel_id"] for item in embeddings
    )
    assert embeddings[0]["vector_sha256"] == hashlib.sha256(
        vector_f32le(row(0).vector)
    ).hexdigest()
    assert catalog[0]["dataset_row_reference"] == json.dumps(
        {
            "catalogContentHash": "1" * 64,
            "period": "2026-06",
            "sourceArticleKey": "enwiki:20",
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    manifest = first.manifest_contract
    assert manifest.rowCount == 2
    assert manifest.creatorCount == 2
    assert manifest.periodCounts == {"2026-06": 1, "2026-07": 1}
    assert (
        manifest.vectorNormMin
        == manifest.vectorNormMean
        == manifest.vectorNormP01
        == manifest.vectorNormMedian
        == manifest.vectorNormP99
        == manifest.vectorNormMax
        == 1.0
    )
    assert manifest.writerSettings.rowGroupSize == 10_000

    checksum_lines = first.checksums.read_text(encoding="ascii").splitlines()
    assert [line.split("  ", 1)[1] for line in checksum_lines] == sorted(
        [
            "babel_embeddings.parquet",
            "babel_catalog.parquet",
            "manifest.json",
            "import_population.py",
        ]
    )
    assert first.digest == hashlib.sha256(first.checksums.read_bytes()).hexdigest()
    assert verify_bundle(first.root, first.digest) == first


def test_write_rejects_duplicate_babel_ids_and_embedding_pairs(tmp_path: Path) -> None:
    duplicate_id = row(1, babel_id=row(0).babel_id, model_artifact_id="a" * 64)
    with pytest.raises(PopulationTransferIntegrityError, match="duplicate Babel ID"):
        write_bundle_payloads(bundle_input(row(0), duplicate_id), tmp_path / "id")

    duplicate_pair = row(1, babel_id=row(0).babel_id)
    with pytest.raises(PopulationTransferIntegrityError, match="duplicate embedding pair"):
        write_bundle_payloads(bundle_input(row(0), duplicate_pair), tmp_path / "pair")


def test_verify_hard_fails_wrong_digest_file_mutation_and_schema_mismatch(
    tmp_path: Path,
) -> None:
    wrong = write_bundle_payloads(bundle_input(), tmp_path / "wrong")
    with pytest.raises(PopulationTransferIntegrityError, match="trusted digest"):
        verify_bundle(wrong.root, "0" * 64)

    mutated = write_bundle_payloads(bundle_input(), tmp_path / "mutated")
    mutated.catalog.write_bytes(mutated.catalog.read_bytes() + b"mutation")
    with pytest.raises(PopulationTransferIntegrityError, match="checksum"):
        verify_bundle(mutated.root, mutated.digest)

    malformed = write_bundle_payloads(bundle_input(), tmp_path / "malformed")
    catalog_table = pq.read_table(malformed.catalog).drop(["dataset_row_reference"])
    pq.write_table(catalog_table, malformed.catalog)
    manifest_document = json.loads(malformed.manifest.read_text(encoding="utf-8"))
    manifest_document["payloads"]["babel_catalog.parquet"] = {
        "bytes": malformed.catalog.stat().st_size,
        "sha256": hashlib.sha256(malformed.catalog.read_bytes()).hexdigest(),
    }
    malformed.manifest.write_text(
        json.dumps(manifest_document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    covered = sorted(
        [
            malformed.embeddings,
            malformed.catalog,
            malformed.manifest,
            malformed.launcher,
        ],
        key=lambda path: path.name,
    )
    malformed.checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in covered
        ),
        encoding="ascii",
    )
    malformed_digest = hashlib.sha256(malformed.checksums.read_bytes()).hexdigest()
    with pytest.raises(PopulationTransferIntegrityError, match="schema"):
        verify_bundle(malformed.root, malformed_digest)
