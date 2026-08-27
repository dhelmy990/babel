from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

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


ORIGIN_TRIAL_ID = UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43")
ORIGIN_RUN_ID = UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9")
SERVING_MODEL_ID = "2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67"
EMBEDDING_SPACE_ID = "f3665769-b470-5228-8df4-08004e252aa4"
MODEL_ARTIFACT_ID = "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
UNIT_VECTOR = np.concatenate((np.ones(1, dtype=np.float32), np.zeros(99, dtype=np.float32)))
DATASET_FIXTURE = (
    Path(__file__).parents[3] / "fixtures/monthly/demo/june/articles.jsonl"
)


def metadata() -> PopulationTransferMetadataV1:
    return PopulationTransferMetadataV1(
        originTrialId=ORIGIN_TRIAL_ID,
        originRunId=ORIGIN_RUN_ID,
        modelRepository="dhelmy990/babel-qwen-navigation-2016-interview",
        modelRevision="57d949cd634b920cc1a46f27c9b21df094b5240e",
        modelArtifactId=MODEL_ARTIFACT_ID,
        servingModelId=UUID(SERVING_MODEL_ID),
        materializedModelVersion=0,
        embeddingSpaceId=UUID(EMBEDDING_SPACE_ID),
        embeddingSpaceVersion="babel-qwen-100d-v1",
        baseModelRepository="Qwen/Qwen3-Embedding-0.6B",
        baseModelRevision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        datasetRepository="dhelmy990/babel-wikipedia-experiment",
        datasetConfiguration="crosswalk_2026_06_07",
        datasetRevision=DATASET_REVISION,
        createdAt=datetime(2026, 8, 27, 1, 2, 3, tzinfo=timezone.utc),
        rebinding=OriginToFreshRebindingV1(
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
    )


def row(
    ordinal: int,
    *,
    babel_id: str | None = None,
    model_artifact_id: str = MODEL_ARTIFACT_ID,
    vector: object | None = None,
) -> PopulationTransferRow:
    identifier = str(uuid5(ORIGIN_RUN_ID, f"babel:{ordinal}"))
    creator = str(uuid5(ORIGIN_RUN_ID, f"creator:{ordinal % 50}"))
    period = "2026-06" if ordinal < 5_000 else "2026-07"
    article_text = f"Article {ordinal}"
    creator_event_number = ordinal // 50
    work_id = uuid5(ORIGIN_RUN_ID, f"work:{creator}:{creator_event_number}")
    workload = {
        "creatorEventNumber": creator_event_number,
        "creatorId": creator,
        "period": period,
        "rootBabelId": identifier,
        "runId": str(ORIGIN_RUN_ID),
        "sourceArticleKey": f"enwiki:{ordinal + 1}",
        "workId": str(work_id),
    }
    schedule_created_at_ns = ordinal * 1_000
    created_at_ns = schedule_created_at_ns + 100
    return PopulationTransferRow(
        babel_id=babel_id or identifier,
        creator_id=creator,
        serving_model_id=SERVING_MODEL_ID,
        materialized_model_version=0,
        embedding_space_id=EMBEDDING_SPACE_ID,
        catalog_content_hash=hashlib.sha256(article_text.encode()).hexdigest(),
        model_artifact_id=model_artifact_id,
        dataset_revision=DATASET_REVISION,
        vector=UNIT_VECTOR if vector is None else vector,
        source_article_key=f"enwiki:{ordinal + 1}",
        title=f"Title {ordinal}",
        article_text=article_text,
        event_number=ordinal,
        created_at_ns=created_at_ns,
        finalized_at_ns=created_at_ns + 200,
        schedule_index=ordinal,
        creator_event_number=creator_event_number,
        period=period,
        root_babel_id=identifier,
        traversal_session_id=str(
            uuid5(ORIGIN_RUN_ID, f"traversal:{creator}:{creator_event_number}")
        ),
        work_id=str(work_id),
        workload_sha256=hashlib.sha256(
            json.dumps(workload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        schedule_created_at_ns=schedule_created_at_ns,
        dataset_repository="dhelmy990/babel-wikipedia-experiment",
        dataset_configuration="crosswalk_2026_06_07",
    )


@pytest.fixture(scope="module")
def population_rows() -> tuple[PopulationTransferRow, ...]:
    return tuple(row(index) for index in reversed(range(10_000)))


@pytest.fixture(scope="module")
def population_input(
    population_rows: tuple[PopulationTransferRow, ...],
) -> PopulationTransferBundleInput:
    return PopulationTransferBundleInput(metadata=metadata(), rows=population_rows)


@pytest.fixture(scope="module")
def production_bundle(
    tmp_path_factory: pytest.TempPathFactory,
    population_input: PopulationTransferBundleInput,
) -> BundleFiles:
    return write_bundle_payloads(
        population_input, tmp_path_factory.mktemp("production-bundle") / "bundle"
    )


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


def test_bundle_preserves_schedule_metadata_recorded_after_babel_lifecycle(
    tmp_path: Path, population_rows: tuple[PopulationTransferRow, ...]
) -> None:
    rows = list(population_rows)
    schedule_created_at_ns = rows[0].finalized_at_ns + 1_800_000_000_000_000_000
    rows[0] = replace(rows[0], schedule_created_at_ns=schedule_created_at_ns)

    files = write_bundle_payloads(
        PopulationTransferBundleInput(metadata=metadata(), rows=tuple(rows)),
        tmp_path / "late-schedule-metadata",
    )
    verified = verify_bundle(files.root, files.digest)
    catalog = pq.read_table(verified.catalog).to_pylist()
    exported = next(item for item in catalog if item["babel_id"] == rows[0].babel_id)

    assert exported["created_at_ns"] <= exported["finalized_at_ns"]
    assert exported["schedule_created_at_ns"] == schedule_created_at_ns
    assert exported["schedule_created_at_ns"] > exported["finalized_at_ns"]


def resign_bundle(files: BundleFiles, *payload_names: str) -> str:
    manifest_document = json.loads(files.manifest.read_text(encoding="utf-8"))
    for payload_name in payload_names:
        payload = files.root / payload_name
        manifest_document["payloads"][payload_name] = {
            "bytes": payload.stat().st_size,
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        }
    files.manifest.write_text(
        json.dumps(manifest_document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    covered = sorted(
        [files.embeddings, files.catalog, files.manifest, files.launcher],
        key=lambda path: path.name,
    )
    files.checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in covered
        ),
        encoding="ascii",
    )
    return hashlib.sha256(files.checksums.read_bytes()).hexdigest()


def write_exact_table(table: pa.Table, path: Path) -> None:
    pq.write_table(
        table,
        path,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        use_compliant_nested_type=True,
        store_schema=True,
        row_group_size=10_000,
    )


def replace_first(table: pa.Table, field: str, value: object) -> pa.Table:
    index = table.schema.get_field_index(field)
    values = table.column(index).to_pylist()
    values[0] = value
    arrow_field = table.schema.field(index)
    return table.set_column(index, arrow_field, pa.array(values, type=arrow_field.type))


def mutated_bundle(
    production_bundle: BundleFiles,
    root: Path,
    *,
    catalog_changes: dict[str, object] | None = None,
    embedding_changes: dict[str, object] | None = None,
) -> tuple[BundleFiles, str]:
    shutil.copytree(production_bundle.root, root)
    files = verify_bundle(root, production_bundle.digest)
    changed: list[str] = []
    if catalog_changes:
        table = pq.read_table(files.catalog)
        for field, value in catalog_changes.items():
            table = replace_first(table, field, value)
        source = table.column("source_article_key")[0].as_py()
        period = table.column("period")[0].as_py()
        content_hash = table.column("catalog_content_hash")[0].as_py()
        reference = json.dumps(
            {
                "catalogContentHash": content_hash,
                "period": period,
                "sourceArticleKey": source,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        table = replace_first(table, "dataset_row_reference", reference)
        write_exact_table(table, files.catalog)
        changed.append("babel_catalog.parquet")
    if embedding_changes:
        table = pq.read_table(files.embeddings)
        for field, value in embedding_changes.items():
            table = replace_first(table, field, value)
        write_exact_table(table, files.embeddings)
        changed.append("babel_embeddings.parquet")
    return files, resign_bundle(files, *changed)


def test_vector_f32le_is_exact_and_rejects_shape_finiteness_and_norm() -> None:
    unit = np.zeros(100)
    unit[3] = 1.0

    encoded = vector_f32le(unit)

    assert len(encoded) == 400
    assert encoded == np.asarray(unit, dtype="<f4").tobytes(order="C")
    for malformed in (
        np.zeros(99),
        np.zeros(101),
        np.full(100, np.nan),
        np.full(100, np.inf),
        np.full(100, -np.inf),
        np.zeros(100),
        np.full(100, 0.2),
    ):
        with pytest.raises(PopulationTransferIntegrityError):
            vector_f32le(malformed)


def test_write_bundle_is_deterministic_complete_and_uses_exact_parquet_contract(
    tmp_path: Path,
    production_bundle: BundleFiles,
    population_input: PopulationTransferBundleInput,
) -> None:
    first = production_bundle
    second = write_bundle_payloads(population_input, tmp_path / "second")

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
    assert embedding_metadata.row_group(0).num_rows == 10_000
    assert embedding_metadata.row_group(0).column(0).compression == "ZSTD"
    assert embedding_metadata.row_group(0).column(0).statistics is not None

    embeddings = pq.read_table(first.embeddings).to_pylist()
    catalog = pq.read_table(first.catalog).to_pylist()
    assert [item["babel_id"] for item in embeddings] == sorted(
        item["babel_id"] for item in embeddings
    )
    assert embeddings[0]["vector_sha256"] == hashlib.sha256(
        vector_f32le(UNIT_VECTOR)
    ).hexdigest()
    first_source = next(item for item in catalog if item["source_article_key"] == "enwiki:1")
    assert first_source["dataset_row_reference"] == json.dumps(
        {
            "catalogContentHash": hashlib.sha256(b"Article 0").hexdigest(),
            "period": "2026-06",
            "sourceArticleKey": "enwiki:1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    manifest = first.manifest_contract
    assert manifest.bundleFormatVersion == 1
    assert manifest.rowCount == 10_000
    assert manifest.creatorCount == 50
    assert manifest.periodCounts == {"2026-06": 5_000, "2026-07": 5_000}
    assert manifest.vectorDtype == "float32"
    assert manifest.byteOrder == "little"
    assert manifest.normalization == "l2"
    assert manifest.normalizationTolerance == 1e-5
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

    canonical_content = b"".join(
        (
            json.dumps(
                {
                    "babelId": item["babel_id"],
                    "catalogContentHash": item["catalog_content_hash"],
                    "createdAtNs": item["created_at_ns"],
                    "creatorId": item["creator_id"],
                    "eventNumber": item["event_number"],
                    "ordinal": ordinal,
                    "sourceArticleKey": item["source_article_key"],
                    "text": item["article_text"],
                    "title": item["title"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for ordinal, item in enumerate(catalog)
    )
    canonical_schedule = b"".join(
        (
            json.dumps(
                {
                    "creatorEventNumber": item["creator_event_number"],
                    "creatorId": item["creator_id"],
                    "ordinal": ordinal,
                    "period": item["period"],
                    "rootBabelId": item["root_babel_id"],
                    "scheduleIndex": item["schedule_index"],
                    "sourceArticleKey": item["source_article_key"],
                    "traversalSessionId": item["traversal_session_id"],
                    "workId": item["work_id"],
                    "workloadSha256": item["workload_sha256"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for ordinal, item in enumerate(catalog)
    )
    assert manifest.contentSha256 == hashlib.sha256(canonical_content).hexdigest()
    assert manifest.scheduleSha256 == hashlib.sha256(canonical_schedule).hexdigest()

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


def test_write_accepts_lead_text_bound_to_the_full_source_article_hash(
    tmp_path: Path, population_rows: tuple[PopulationTransferRow, ...]
) -> None:
    with DATASET_FIXTURE.open(encoding="utf-8") as fixture:
        article = json.loads(next(fixture))
    assert article["lead_text"] != article["article_text"]
    assert hashlib.sha256(article["article_text"].encode()).hexdigest() == article[
        "content_hash"
    ]
    assert hashlib.sha256(article["lead_text"].encode()).hexdigest() != article[
        "content_hash"
    ]

    original = population_rows[0]
    workload = {
        "creatorEventNumber": original.creator_event_number,
        "creatorId": original.creator_id,
        "period": original.period,
        "rootBabelId": original.root_babel_id,
        "runId": str(ORIGIN_RUN_ID),
        "sourceArticleKey": article["article_key"],
        "workId": original.work_id,
    }
    fixture_row = replace(
        original,
        source_article_key=article["article_key"],
        title=article["canonical_title"],
        article_text=article["lead_text"],
        catalog_content_hash=article["content_hash"],
        workload_sha256=hashlib.sha256(
            json.dumps(workload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    source = PopulationTransferBundleInput(
        metadata=metadata(), rows=(fixture_row, *population_rows[1:])
    )

    bundle = write_bundle_payloads(source, tmp_path / "lead-text-bundle")

    exported = next(
        item
        for item in pq.read_table(bundle.catalog).to_pylist()
        if item["source_article_key"] == article["article_key"]
        and item["creator_id"] == original.creator_id
    )
    assert exported["article_text"] == article["lead_text"]
    assert exported["catalog_content_hash"] == article["content_hash"]
    assert json.loads(exported["dataset_row_reference"])["catalogContentHash"] == article[
        "content_hash"
    ]


def test_write_requires_exactly_ten_thousand_rows(
    tmp_path: Path, population_rows: tuple[PopulationTransferRow, ...]
) -> None:
    undersized = PopulationTransferBundleInput(
        metadata=metadata(), rows=population_rows[:-1]
    )

    with pytest.raises(PopulationTransferIntegrityError, match="exactly 10,000"):
        write_bundle_payloads(undersized, tmp_path / "undersized")


def test_write_rejects_duplicate_babel_ids_and_embedding_pairs(
    tmp_path: Path, population_rows: tuple[PopulationTransferRow, ...]
) -> None:
    original = list(population_rows)
    duplicate_id = replace(
        original[1], babel_id=original[0].babel_id, model_artifact_id="a" * 64
    )
    duplicate_id_rows = tuple([original[0], duplicate_id, *original[2:]])
    with pytest.raises(PopulationTransferIntegrityError, match="duplicate Babel ID"):
        write_bundle_payloads(
            PopulationTransferBundleInput(metadata=metadata(), rows=duplicate_id_rows),
            tmp_path / "id",
        )

    duplicate_pair = replace(original[1], babel_id=original[0].babel_id)
    duplicate_pair_rows = tuple([original[0], duplicate_pair, *original[2:]])
    with pytest.raises(PopulationTransferIntegrityError, match="duplicate embedding pair"):
        write_bundle_payloads(
            PopulationTransferBundleInput(metadata=metadata(), rows=duplicate_pair_rows),
            tmp_path / "pair",
        )


def test_verify_hard_fails_wrong_digest_file_mutation_and_schema_mismatch(
    tmp_path: Path,
    production_bundle: BundleFiles,
) -> None:
    with pytest.raises(PopulationTransferIntegrityError, match="trusted digest"):
        verify_bundle(production_bundle.root, "0" * 64)

    mutated_root = tmp_path / "mutated"
    shutil.copytree(production_bundle.root, mutated_root)
    mutated = verify_bundle(mutated_root, production_bundle.digest)
    mutated.catalog.write_bytes(mutated.catalog.read_bytes() + b"mutation")
    with pytest.raises(PopulationTransferIntegrityError, match="checksum"):
        verify_bundle(mutated.root, mutated.digest)

    malformed_root = tmp_path / "malformed"
    shutil.copytree(production_bundle.root, malformed_root)
    malformed = verify_bundle(malformed_root, production_bundle.digest)
    catalog_table = pq.read_table(malformed.catalog).drop(["dataset_row_reference"])
    pq.write_table(catalog_table, malformed.catalog)
    malformed_digest = resign_bundle(malformed, "babel_catalog.parquet")
    with pytest.raises(PopulationTransferIntegrityError, match="schema"):
        verify_bundle(malformed.root, malformed_digest)


def test_verify_rejects_noncompliant_nested_physical_layout(
    tmp_path: Path, production_bundle: BundleFiles
) -> None:
    copied_root = tmp_path / "legacy-nested"
    shutil.copytree(production_bundle.root, copied_root)
    copied = verify_bundle(copied_root, production_bundle.digest)
    loaded = pq.read_table(copied.embeddings)
    table = pa.Table.from_arrays(loaded.columns, schema=expected_embeddings_schema())
    pq.write_table(
        table,
        copied.embeddings,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        use_compliant_nested_type=False,
        store_schema=True,
        row_group_size=10_000,
    )
    digest = resign_bundle(copied, "babel_embeddings.parquet")

    with pytest.raises(PopulationTransferIntegrityError, match="compliant nested"):
        verify_bundle(copied.root, digest)


def test_verify_rejects_an_extra_file(
    tmp_path: Path, production_bundle: BundleFiles
) -> None:
    copied = tmp_path / "extra-file"
    shutil.copytree(production_bundle.root, copied)
    (copied / "unexpected.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(PopulationTransferIntegrityError, match="exactly five files"):
        verify_bundle(copied, production_bundle.digest)


@pytest.mark.parametrize(
    ("case", "catalog_changes", "embedding_changes", "message"),
    [
        ("workload", {"workload_sha256": "0" * 64}, None, "workload"),
        (
            "content_hash",
            {"catalog_content_hash": "a" * 64},
            {"catalog_content_hash": "a" * 64},
            "snapshotSha256",
        ),
        ("empty_title", {"title": ""}, None, "title"),
        (
            "empty_text",
            {
                "article_text": "",
                "catalog_content_hash": hashlib.sha256(b"").hexdigest(),
            },
            {"catalog_content_hash": hashlib.sha256(b"").hexdigest()},
            "article text",
        ),
        ("event", {"event_number": -1}, None, "event number"),
        ("schedule", {"schedule_index": -1}, None, "schedule index"),
        (
            "negative_created_timestamp",
            {"created_at_ns": -1},
            None,
            "created timestamp",
        ),
        (
            "negative_finalized_timestamp",
            {"finalized_at_ns": -1},
            None,
            "finalized timestamp",
        ),
        (
            "negative_schedule_timestamp",
            {"schedule_created_at_ns": -1},
            None,
            "schedule timestamp",
        ),
        (
            "babel_timestamp_order",
            {"created_at_ns": 9_000_000_000_000_000_000},
            None,
            "timestamp order",
        ),
        ("period", {"period": "2026-08"}, None, "period must"),
        ("source", {"source_article_key": "wiki:1"}, None, "source article key"),
        (
            "root",
            {"root_babel_id": str(ORIGIN_TRIAL_ID)},
            None,
            "root Babel ID",
        ),
    ],
)
def test_verify_rejects_resigned_semantically_malformed_rows(
    tmp_path: Path,
    production_bundle: BundleFiles,
    case: str,
    catalog_changes: dict[str, object],
    embedding_changes: dict[str, object] | None,
    message: str,
) -> None:
    files, digest = mutated_bundle(
        production_bundle,
        tmp_path / case,
        catalog_changes=catalog_changes,
        embedding_changes=embedding_changes,
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        verify_bundle(files.root, digest)


@pytest.mark.parametrize(
    "field",
    [
        "orderedPopulationSha256",
        "snapshotSha256",
        "scheduleSha256",
        "contentSha256",
        "frozenPopulationSha256",
    ],
)
def test_verify_recomputes_every_derivable_manifest_hash(
    tmp_path: Path, production_bundle: BundleFiles, field: str
) -> None:
    copied_root = tmp_path / field
    shutil.copytree(production_bundle.root, copied_root)
    files = verify_bundle(copied_root, production_bundle.digest)
    manifest = json.loads(files.manifest.read_text(encoding="utf-8"))
    manifest[field] = "f" * 64
    files.manifest.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    digest = resign_bundle(files)

    with pytest.raises(PopulationTransferIntegrityError, match=field):
        verify_bundle(files.root, digest)


def test_verify_rejects_a_different_pyarrow_writer_version(
    tmp_path: Path, production_bundle: BundleFiles
) -> None:
    copied_root = tmp_path / "pyarrow-version"
    shutil.copytree(production_bundle.root, copied_root)
    files = verify_bundle(copied_root, production_bundle.digest)
    manifest = json.loads(files.manifest.read_text(encoding="utf-8"))
    manifest["writerSettings"]["pyarrowVersion"] = "0.0.0"
    files.manifest.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    digest = resign_bundle(files)

    with pytest.raises(PopulationTransferIntegrityError, match="PyArrow"):
        verify_bundle(files.root, digest)


@pytest.mark.parametrize(
    "name",
    [
        "SHA256SUMS",
        "babel_catalog.parquet",
        "babel_embeddings.parquet",
        "import_population.py",
        "manifest.json",
    ],
)
def test_verify_rejects_every_symlinked_bundle_file(
    tmp_path: Path, production_bundle: BundleFiles, name: str
) -> None:
    copied_root = tmp_path / name.replace(".", "-")
    shutil.copytree(production_bundle.root, copied_root)
    linked = copied_root / name
    linked.unlink()
    linked.symlink_to(production_bundle.root / name)

    with pytest.raises(PopulationTransferIntegrityError, match="regular file|symlink"):
        verify_bundle(copied_root, production_bundle.digest)


def test_verify_rejects_a_symlinked_bundle_root(
    tmp_path: Path, production_bundle: BundleFiles
) -> None:
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(production_bundle.root, target_is_directory=True)

    with pytest.raises(PopulationTransferIntegrityError, match="root.*symlink|root.*directory"):
        verify_bundle(linked_root, production_bundle.digest)


def test_import_launcher_help_is_runnable(production_bundle: BundleFiles) -> None:
    completed = subprocess.run(
        [sys.executable, str(production_bundle.launcher), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "population bundle" in completed.stdout


def test_import_launcher_fails_closed_without_exact_operational_arguments(
    production_bundle: BundleFiles,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(production_bundle.launcher)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    for required in (
        "--database-url-env",
        "--bundle",
        "--trusted-digest",
        "--operator-receipt",
        "--fresh-trial-id",
        "--fresh-run-id",
        "--model-artifact-manifest",
        "--model-checkpoint-root",
        "--frozen-output-root",
        "--import-receipt",
    ):
        assert required in completed.stderr


def test_writer_passes_every_frozen_parquet_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parquet_bundle = importlib.import_module("babel_online.transfer.parquet_bundle")
    calls: list[dict[str, object]] = []

    def capture_write_table(_table, _path, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(pq, "write_table", capture_write_table)
    parquet_bundle._write_parquet([row(0)], [vector_f32le(UNIT_VECTOR)], tmp_path)

    assert calls == [
        {
            "version": "2.6",
            "data_page_version": "1.0",
            "compression": "zstd",
            "compression_level": 9,
            "use_dictionary": False,
            "write_statistics": True,
            "use_compliant_nested_type": True,
            "store_schema": True,
            "row_group_size": 10_000,
        },
        {
            "version": "2.6",
            "data_page_version": "1.0",
            "compression": "zstd",
            "compression_level": 9,
            "use_dictionary": False,
            "write_statistics": True,
            "use_compliant_nested_type": True,
            "store_schema": True,
            "row_group_size": 10_000,
        },
    ]


def test_atomic_failure_leaves_no_accepted_output_or_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    population_input: PopulationTransferBundleInput,
) -> None:
    parquet_bundle = importlib.import_module("babel_online.transfer.parquet_bundle")
    destination = tmp_path / "failed-bundle"
    observed_modes: list[int] = []

    def fail_in_protected_temporary(_rows, _vectors, root: Path):
        observed_modes.append(stat.S_IMODE(root.stat().st_mode))
        raise RuntimeError("simulated writer failure")

    monkeypatch.setattr(parquet_bundle, "_write_parquet", fail_in_protected_temporary)
    with pytest.raises(RuntimeError, match="simulated writer failure"):
        write_bundle_payloads(population_input, destination)

    assert observed_modes == [0o700]
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
