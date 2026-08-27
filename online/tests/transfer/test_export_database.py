from __future__ import annotations

import hashlib
import json
import stat
import struct
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import numpy as np
import pytest

from babel_online.model.frozen_population import FrozenPopulationManifestV1
from babel_online.transfer import PopulationTransferIntegrityError
from babel_online.transfer.contracts import (
    DATASET_CONFIGURATION,
    DATASET_REPOSITORY,
    DATASET_REVISION,
    EMBEDDING_SPACE_ID,
    MODEL_ARTIFACT_ID,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    ORIGIN_RUN_ID,
    ORIGIN_TRIAL_ID,
    SERVING_MODEL_ID,
)
from babel_online.transfer.database import _write_authoritative_bundle, export_population


ARTIFACT_MANIFEST_SHA = "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
MODEL_MANIFEST_SHA = "9" * 64
SNAPSHOT_SHA = "8" * 64
SCHEDULE_SHA = "7" * 64
CONTENT_SHA = "6" * 64
UNIT_F32LE = np.concatenate(
    (np.ones(1, dtype="<f4"), np.zeros(99, dtype="<f4"))
).tobytes()
ORDERED_SHA = hashlib.sha256(UNIT_F32LE * 10_000).hexdigest()
VECTOR_WIRE = struct.pack(">hh", 100, 0) + np.frombuffer(
    UNIT_F32LE, dtype="<f4"
).astype(">f4").tobytes()


def frozen_manifest() -> FrozenPopulationManifestV1:
    return FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=str(ORIGIN_TRIAL_ID),
        sourcePopulationRunId=ORIGIN_RUN_ID,
        babelCount=10_000,
        scheduleCount=10_000,
        juneCount=5_000,
        julyCount=5_000,
        creatorCount=50,
        modelId=SERVING_MODEL_ID,
        modelVersion=0,
        modelManifestSha256=MODEL_MANIFEST_SHA,
        artifactManifestSha256=ARTIFACT_MANIFEST_SHA,
        artifactRepo=MODEL_REPOSITORY,
        artifactRevision=MODEL_REVISION,
        artifactId=MODEL_ARTIFACT_ID,
        trainingDatasetRevision="b440e98b04ab77afed7caf0455eca3189235fc3b",
        datasetRepo=DATASET_REPOSITORY,
        datasetConfig=DATASET_CONFIGURATION,
        datasetRevision=DATASET_REVISION,
        datasetManifestSha256="5" * 64,
        embeddingSpaceId=EMBEDDING_SPACE_ID,
        embeddingSpaceVersion="babel-qwen-100d-v1",
        embeddingDimension=100,
        babelsSha256=CONTENT_SHA,
        vectorsSha256=ORDERED_SHA,
        pgvectorSnapshotSha256=SNAPSHOT_SHA,
        scheduleSha256=SCHEDULE_SHA,
        babelsBytes=1,
        vectorBytes=4_000_000,
        scheduleBytes=1,
    )


def canonical_manifest_bytes(manifest: FrozenPopulationManifestV1) -> bytes:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode()


def source_files(tmp_path: Path, manifest: FrozenPopulationManifestV1):
    frozen_root = tmp_path / "frozen"
    frozen_root.mkdir(parents=True)
    manifest_bytes = canonical_manifest_bytes(manifest)
    (frozen_root / "manifest.json").write_bytes(manifest_bytes)
    state_root = tmp_path / "state"
    journal_root = state_root / str(ORIGIN_RUN_ID) / "population"
    journal_root.mkdir(parents=True)
    journal = {
        "schema_version": 1,
        "identity": manifest.population_identity().document(),
        "created_content_manifest_sha256": "4" * 64,
        "last_committed_babel_id": str(uuid5(ORIGIN_RUN_ID, "babel:9999")),
        "committed_count": 10_000,
        "committed_prefix_sha256": "3" * 64,
        "failure_count": 0,
        "failure_attempt_count": 0,
        "unresolved_failure_count": 0,
        "complete": True,
        "snapshot_sha256": SNAPSHOT_SHA,
        "hnsw_used": True,
    }
    (journal_root / "journal.json").write_text(json.dumps(journal))
    return frozen_root, state_root, journal


def evidence_row(frozen_root: Path, state_root: Path, manifest_sha: str) -> tuple:
    embedding_space = {
        "schemaVersion": 1,
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "dimension": 100,
        "distance": "cosine",
        "distilledEncoderArtifact": (
            f"hf://{MODEL_REPOSITORY}@{MODEL_REVISION}/artifacts/{MODEL_ARTIFACT_ID}"
        ),
        "datasetRevision": "b440e98b04ab77afed7caf0455eca3189235fc3b",
        "compatibilityVersion": "babel-qwen-100d-v1",
    }
    return (
        ORIGIN_TRIAL_ID,
        ORIGIN_RUN_ID,
        SERVING_MODEL_ID,
        True,
        10_000,
        ORDERED_SHA,
        MODEL_REPOSITORY,
        MODEL_REVISION,
        MODEL_REPOSITORY,
        MODEL_REVISION,
        MODEL_MANIFEST_SHA,
        DATASET_REPOSITORY,
        DATASET_REVISION,
        DATASET_REPOSITORY,
        DATASET_REVISION,
        "5" * 64,
        manifest_sha,
        str(frozen_root),
        "completed",
        DATASET_CONFIGURATION,
        str(state_root),
        10_000,
        50,
        SERVING_MODEL_ID,
        0,
        EMBEDDING_SPACE_ID,
        SNAPSHOT_SHA,
        SNAPSHOT_SHA,
        MODEL_REPOSITORY,
        MODEL_REVISION,
        "dhelmy990/babel-wikipedia-experiment",
        "b440e98b04ab77afed7caf0455eca3189235fc3b",
        ARTIFACT_MANIFEST_SHA,
        embedding_space,
        "population_ready",
        10_000,
        10_000,
        10_000,
        10_000,
        ORDERED_SHA,
    )


def database_row(ordinal: int) -> tuple:
    babel_id = uuid5(ORIGIN_RUN_ID, f"babel:{ordinal}")
    creator_id = uuid5(ORIGIN_RUN_ID, f"creator:{ordinal % 50}")
    creator_event_number = ordinal // 50
    work_id = uuid5(ORIGIN_RUN_ID, f"work:{creator_id}:{creator_event_number}")
    traversal_id = uuid5(
        ORIGIN_RUN_ID, f"traversal:{creator_id}:{creator_event_number}"
    )
    period = "2026-06" if ordinal < 5_000 else "2026-07"
    workload = {
        "creatorEventNumber": creator_event_number,
        "creatorId": str(creator_id),
        "period": period,
        "rootBabelId": str(babel_id),
        "runId": str(ORIGIN_RUN_ID),
        "sourceArticleKey": f"enwiki:{ordinal + 1}",
        "workId": str(work_id),
    }
    return (
        babel_id,
        creator_id,
        f"enwiki:{ordinal + 1}",
        f"Title {ordinal}",
        f"Article {ordinal}",
        hashlib.sha256(f"Article {ordinal}".encode()).hexdigest(),
        ordinal,
        ordinal * 1_000 + 100,
        ordinal * 1_000 + 300,
        ordinal,
        creator_event_number,
        period,
        babel_id,
        traversal_id,
        work_id,
        hashlib.sha256(
            json.dumps(workload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        ordinal * 1_000,
        SERVING_MODEL_ID,
        0,
        EMBEDDING_SPACE_ID,
        VECTOR_WIRE,
        creator_id,
        hashlib.sha256(f"Article {ordinal}".encode()).hexdigest(),
        DATASET_REPOSITORY,
        DATASET_CONFIGURATION,
        DATASET_REVISION,
    )


class RecordingCursor:
    def __init__(self, evidence: tuple, rows: list[tuple]) -> None:
        self.evidence = evidence
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []
        self._kind = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=()):
        normalized = " ".join(str(query).split())
        self.queries.append((normalized, tuple(parameters)))
        if "latest_progress" in normalized:
            self._kind = "evidence"
        elif "public.vector_send" in normalized:
            self._kind = "rows"
        else:
            self._kind = "transaction"

    def fetchone(self):
        return self.evidence if self._kind == "evidence" else None

    def fetchall(self):
        return list(self.rows) if self._kind == "rows" else []


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def configured_source(tmp_path: Path):
    manifest = frozen_manifest()
    frozen_root, state_root, _journal = source_files(tmp_path, manifest)
    manifest_sha = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    evidence = evidence_row(frozen_root, state_root, manifest_sha)
    rows = [database_row(index) for index in range(10_000)]
    cursor = RecordingCursor(evidence, rows)
    return manifest, cursor


def test_export_uses_one_read_only_snapshot_and_exact_binary_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cursor = configured_source(tmp_path)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )
    captured = {}

    def record_bundle(source, root):
        captured["source"] = source
        captured["root"] = Path(root)
        bundle_root = Path(root) / ("a" * 64)
        return SimpleNamespace(
            root=bundle_root,
            digest="a" * 64,
            manifest_contract=SimpleNamespace(rowCount=10_000),
        )

    monkeypatch.setattr(
        "babel_online.transfer.database._write_authoritative_bundle",
        lambda source, root, _manifest: record_bundle(source, root),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database._utc_now",
        lambda: datetime(2026, 8, 27, 3, 4, 5, tzinfo=timezone.utc),
    )

    receipt = export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle")

    assert receipt.bundleDigest == "a" * 64
    assert receipt.bundlePath == str((tmp_path / "bundle" / ("a" * 64)).resolve())
    assert receipt.rowCount == 10_000
    assert len(captured["source"].rows) == 10_000
    assert captured["source"].metadata.createdAt == datetime(
        2026, 8, 27, 3, 4, 5, tzinfo=timezone.utc
    )
    transaction_sql = cursor.queries[0][0].upper()
    assert transaction_sql == "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    evidence_sql = next(query for query, _ in cursor.queries if "latest_progress" in query)
    assert "phase IN ('population','population_ready')" in evidence_sql
    selector, parameters = next(
        item for item in cursor.queries if "public.vector_send" in item[0]
    )
    for table in (
        "performance_experiments",
        "experiment_runs",
        "experiment_babels",
        "experiment_work_schedule",
        "babel_embeddings",
        "run_embedding_states",
        "recommender_models",
    ):
        assert table in selector
    assert "ORDER BY xb.babel_id" in selector
    assert "eb.creator_id=xb.creator_id" in selector
    assert "eb.catalog_content_hash=xb.catalog_content_hash" in selector
    assert "embedding::text" not in selector
    assert parameters == (
        ORIGIN_TRIAL_ID,
        ORIGIN_RUN_ID,
        SERVING_MODEL_ID,
        0,
        EMBEDDING_SPACE_ID,
    )


def test_final_frozen_hash_check_precedes_atomic_bundle_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "bundles"
    destination = output_root / ("a" * 64)
    manifest = frozen_manifest()

    def staged_writer(_source, root):
        root = Path(root)
        root.mkdir()
        return SimpleNamespace(root=root, digest="a" * 64)

    monkeypatch.setattr(
        "babel_online.transfer.database.write_bundle_payloads", staged_writer
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle",
        lambda root, digest: SimpleNamespace(
            root=Path(root),
            digest=digest,
            manifest_contract=SimpleNamespace(
                rowCount=10_000,
                orderedPopulationSha256="0" * 64,
                snapshotSha256=SNAPSHOT_SHA,
                scheduleSha256=SCHEDULE_SHA,
                contentSha256=CONTENT_SHA,
            ),
        ),
    )

    with pytest.raises(PopulationTransferIntegrityError, match="ordered population"):
        _write_authoritative_bundle(SimpleNamespace(), output_root, manifest)

    assert not destination.exists()


def test_bundle_installs_create_only_under_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "bundles"
    digest = "a" * 64
    manifest = frozen_manifest()

    def staged_writer(_source, root):
        root = Path(root)
        root.mkdir()
        (root / "sentinel").write_text("verified")
        return SimpleNamespace(root=root, digest=digest)

    verified_contract = SimpleNamespace(
        rowCount=10_000,
        orderedPopulationSha256=ORDERED_SHA,
        snapshotSha256=SNAPSHOT_SHA,
        scheduleSha256=SCHEDULE_SHA,
        contentSha256=CONTENT_SHA,
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.write_bundle_payloads", staged_writer
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle",
        lambda root, trusted: SimpleNamespace(
            root=Path(root), digest=trusted, manifest_contract=verified_contract
        ),
    )

    installed = _write_authoritative_bundle(
        SimpleNamespace(), output_root, manifest
    )

    assert installed.root == output_root / digest
    assert installed.root.joinpath("sentinel").read_text() == "verified"
    assert stat.S_IMODE(output_root.stat().st_mode) == 0o700
    with pytest.raises(PopulationTransferIntegrityError, match="collision"):
        _write_authoritative_bundle(SimpleNamespace(), output_root, manifest)
    assert installed.root.joinpath("sentinel").read_text() == "verified"


@pytest.mark.parametrize(
    ("position", "value", "message"),
    [
        (3, False, "population_ready"),
        (1, None, "immutable population binding"),
        (17, None, "immutable population binding"),
        (8, "fixture/model", "population model repository"),
        (10, "0" * 64, "population model manifest"),
        (13, "fixture/dataset", "population dataset repository"),
        (18, "failed", "source run"),
        (34, "population", "latest durable phase"),
        (35, 9_999, "seeded"),
        (36, 9_999, "created"),
        (37, 9_999, "indexed"),
        (38, 9_999, "approval count"),
        (39, "0" * 64, "approval vector"),
        (26, "0" * 64, "active snapshot"),
        (27, "0" * 64, "active snapshot"),
        (28, "fixture/model", "model repository"),
        (29, "0" * 40, "model revision"),
        (24, 1, "model version"),
        (25, UUID(int=9), "embedding space"),
        (22, 49, "source creator count"),
    ],
)
def test_export_rejects_durable_evidence_drift_before_selecting_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: int,
    value: object,
    message: str,
) -> None:
    manifest, cursor = configured_source(tmp_path)
    evidence = list(cursor.evidence)
    evidence[position] = value
    cursor.evidence = tuple(evidence)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle")

    assert not any("public.vector_send" in query for query, _ in cursor.queries)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("complete", False, "journal complete"),
        ("committed_count", 9_999, "journal count"),
        ("failure_count", 1, "current failure"),
        ("unresolved_failure_count", 1, "unresolved failure"),
        ("hnsw_used", False, "HNSW"),
        ("snapshot_sha256", "0" * 64, "journal snapshot"),
        ("identity", {}, "journal identity"),
    ],
)
def test_export_rejects_incomplete_or_stale_population_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    manifest, cursor = configured_source(tmp_path)
    state_root = Path(cursor.evidence[20])
    journal_path = state_root / str(ORIGIN_RUN_ID) / "population/journal.json"
    journal = json.loads(journal_path.read_text())
    journal[field] = value
    journal_path.write_text(json.dumps(journal))
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle")


def test_export_rejects_invalid_or_mismatched_frozen_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cursor = configured_source(tmp_path)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid")),
    )
    with pytest.raises(PopulationTransferIntegrityError, match="frozen manifest"):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle")

    manifest, cursor = configured_source(tmp_path / "second")
    evidence = list(cursor.evidence)
    evidence[16] = "0" * 64
    cursor.evidence = tuple(evidence)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )
    with pytest.raises(PopulationTransferIntegrityError, match="manifest binding"):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle2")


def test_export_rejects_wrong_row_count_and_fixture_contamination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, cursor = configured_source(tmp_path)
    cursor.rows.pop()
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )
    with pytest.raises(PopulationTransferIntegrityError, match="10,000"):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle")

    manifest, cursor = configured_source(tmp_path / "second")
    contaminated = list(cursor.rows[0])
    contaminated[17] = UUID(int=99)
    cursor.rows[0] = tuple(contaminated)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )
    with pytest.raises(PopulationTransferIntegrityError, match="serving model"):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundle2")


@pytest.mark.parametrize(
    ("position", "value", "message"),
    [
        (21, UUID(int=99), "embedding creator"),
        (22, "0" * 64, "embedding content hash"),
    ],
)
def test_export_rejects_embedding_row_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    position: int,
    value: object,
    message: str,
) -> None:
    manifest, cursor = configured_source(tmp_path)
    drifted = list(cursor.rows[0])
    drifted[position] = value
    cursor.rows[0] = tuple(drifted)
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: RecordingConnection(cursor),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.load_frozen_population", lambda _path: manifest
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        export_population("postgresql://secret", ORIGIN_TRIAL_ID, tmp_path / "bundles")
