from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest

from babel_online.transfer.database import (
    ImportReceiptV1,
    _acquire_initial_import_receipt,
    _advance_import_receipt,
    _build_rebound_frozen_manifest,
    _deterministic_sample_ordinals,
    _exclusive_import_receipt_lock,
    _import_advisory_lock_key,
    _import_verified_bundle,
    _load_verified_transfer_rows,
    _ready_database_state_matches,
    _validate_import_presence,
    _validate_quarantined_catalog_rows,
    import_population,
)
from babel_online.transfer.parquet_bundle import PopulationTransferIntegrityError


FILES = (
    "SHA256SUMS",
    "babel_catalog.parquet",
    "babel_embeddings.parquet",
    "import_population.py",
    "manifest.json",
)


def _operator_receipt(root: Path, digest: str) -> Path:
    objects = {}
    for name in FILES:
        payload = (root / name).read_bytes()
        objects[name] = {
            "generation": "123",
            "gsUrl": f"gs://private/bundle/{name}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    path = root / "operator-receipt.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "bundleDigest": digest,
                "objects": objects,
            },
            sort_keys=True,
        )
    )
    return path


def _bundle(tmp_path: Path, digest: str = "a" * 64) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    for index, name in enumerate(FILES):
        (root / name).write_bytes(f"payload-{index}".encode())
    return root


def _planned_import_receipt(attempt_id: UUID) -> ImportReceiptV1:
    return ImportReceiptV1(
        schemaVersion=1,
        state="planned",
        bundleDigest="a" * 64,
        originTrialId=UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43"),
        originRunId=UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9"),
        freshTrialId=UUID("4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70"),
        freshPopulationRunId=UUID("15a452ee-f560-59bb-80e9-ff21e8a82c44"),
        importAttemptId=attempt_id,
        rowCount=10_000,
        orderedVectorSha256="b" * 64,
        snapshotSha256="c" * 64,
        frozenManifestSha256="d" * 64,
        sampleCount=100,
        hnswIndex="babel_embeddings_cosine_hnsw",
        modelArtifactManifestPath="/models/artifact_manifest.json",
        modelCheckpointRoot="/models/checkpoint",
    )


def test_deterministic_samples_are_unique_bounded_and_digest_stable() -> None:
    first = _deterministic_sample_ordinals("a" * 64, row_count=10_000, count=100)
    second = _deterministic_sample_ordinals("a" * 64, row_count=10_000, count=100)

    assert first == second
    assert len(first) == len(set(first)) == 100
    assert all(0 <= ordinal < 10_000 for ordinal in first)
    assert first != _deterministic_sample_ordinals(
        "b" * 64, row_count=10_000, count=100
    )


def test_concurrent_initial_receipt_acquisition_converges_without_overwrite(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "receipts" / "import.json"
    candidates = [_planned_import_receipt(uuid4()) for _ in range(2)]
    barrier = threading.Barrier(2)
    results: list[tuple[ImportReceiptV1, bool]] = []

    def contend(candidate: ImportReceiptV1) -> None:
        barrier.wait()
        results.append(_acquire_initial_import_receipt(candidate, destination))

    threads = [
        threading.Thread(target=contend, args=(candidate,))
        for candidate in candidates
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert sum(created for _receipt, created in results) == 1
    assert results[0][0] == results[1][0]
    winner = next(receipt for receipt, created in results if created)
    assert ImportReceiptV1.model_validate_json(destination.read_text()) == winner


def test_import_advisory_lock_key_is_stable_and_scoped_to_both_ids() -> None:
    trial_id = uuid4()
    run_id = uuid5(trial_id, "population")

    assert _import_advisory_lock_key(trial_id, run_id) == _import_advisory_lock_key(
        trial_id, run_id
    )
    assert _import_advisory_lock_key(
        trial_id, run_id
    ) != _import_advisory_lock_key(uuid4(), run_id)
    assert _import_advisory_lock_key(
        trial_id, run_id
    ) != _import_advisory_lock_key(trial_id, uuid4())
    assert -(1 << 63) <= _import_advisory_lock_key(trial_id, run_id) < (1 << 63)


def test_receipt_lock_serializes_contenders_without_service_mutation(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "receipts" / "import.json"
    first_entered = threading.Event()
    second_attempted = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with _exclusive_import_receipt_lock(destination):
            first_entered.set()
            assert release_first.wait(2)

    def second() -> None:
        assert first_entered.wait(2)
        second_attempted.set()
        with _exclusive_import_receipt_lock(destination):
            second_entered.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    assert second_attempted.wait(2)
    assert not second_entered.wait(0.05)
    release_first.set()
    for thread in threads:
        thread.join(timeout=2)

    assert second_entered.is_set()
    assert all(not thread.is_alive() for thread in threads)


def test_receipt_advance_does_not_rewrite_an_already_won_state(tmp_path: Path) -> None:
    destination = tmp_path / "receipts" / "import.json"
    planned = _planned_import_receipt(uuid4())
    acquired, created = _acquire_initial_import_receipt(planned, destination)
    assert created and acquired == planned
    ready = planned.model_copy(update={"state": "ready"})

    assert _advance_import_receipt(ready, destination) == ready
    won_stat = destination.stat()
    assert _advance_import_receipt(ready, destination) == ready
    unchanged_stat = destination.stat()

    assert (unchanged_stat.st_ino, unchanged_stat.st_mtime_ns) == (
        won_stat.st_ino,
        won_stat.st_mtime_ns,
    )


def test_initial_receipt_acquisition_rejects_lexical_symlink(tmp_path: Path) -> None:
    private = tmp_path / "receipts"
    private.mkdir(mode=0o700)
    target = private / "target.json"
    target.write_text("operator-owned\n")
    destination = private / "import.json"
    destination.symlink_to(target)

    with pytest.raises(PopulationTransferIntegrityError, match="receipt"):
        _acquire_initial_import_receipt(_planned_import_receipt(uuid4()), destination)

    assert target.read_text() == "operator-owned\n"


@pytest.mark.parametrize(
    ("trial_id", "run_factory", "message"),
    [
        (UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa"), lambda value: uuid5(value, "population"), "UUIDv4"),
        (uuid4(), lambda _value: uuid4(), "uuid5"),
    ],
)
def test_import_rejects_invalid_fresh_identity_before_bundle_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trial_id, run_factory, message
) -> None:
    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle",
        lambda *_args: pytest.fail("identity must fail before bundle verification"),
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        import_population(
            "postgresql://unused",
            tmp_path,
            "a" * 64,
            tmp_path / "operator.json",
            trial_id,
            run_factory(trial_id),
            tmp_path / "artifact_manifest.json",
            tmp_path / "checkpoint",
            tmp_path / "frozen",
            tmp_path / "import.json",
        )


@pytest.mark.parametrize(
    ("origin_trial", "origin_run", "message"),
    [
        ("fresh_trial", "different_run", "trial ID reuses bundle origin"),
        ("different_trial", "fresh_run", "run ID reuses bundle origin"),
    ],
)
def test_import_rejects_origin_identity_after_trust_verification_before_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    origin_trial: str,
    origin_run: str,
    message: str,
) -> None:
    digest = "a" * 64
    root = _bundle(tmp_path, digest)
    operator_receipt = _operator_receipt(root, digest)
    fresh_trial = uuid4()
    fresh_run = uuid5(fresh_trial, "population")
    verified = SimpleNamespace(
        manifest_contract=SimpleNamespace(
            originTrialId=(fresh_trial if origin_trial == "fresh_trial" else uuid4()),
            originRunId=(fresh_run if origin_run == "fresh_run" else uuid4()),
        )
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle", lambda *_args: verified
    )
    monkeypatch.setattr(
        "babel_online.transfer.database._import_verified_bundle",
        lambda **_values: pytest.fail("origin reuse must fail before adapter"),
    )

    with pytest.raises(PopulationTransferIntegrityError, match=message):
        import_population(
            "postgresql://unused",
            root,
            digest,
            operator_receipt,
            fresh_trial,
            fresh_run,
            tmp_path / "artifact_manifest.json",
            tmp_path / "checkpoint",
            tmp_path / "frozen",
            tmp_path / "import.json",
        )


def test_two_invocation_collision_never_adopts_rows_without_attempt_binding() -> None:
    attempt_id = uuid4()

    for _invocation in range(2):
        with pytest.raises(
            PopulationTransferIntegrityError, match="import attempt binding"
        ):
            _validate_import_presence(
                (True, True, None), expected_attempt_id=attempt_id
            )

    assert (
        _validate_import_presence(
            (False, False, None), expected_attempt_id=attempt_id
        )
        is False
    )
    assert (
        _validate_import_presence(
            (True, True, str(attempt_id)), expected_attempt_id=attempt_id
        )
        is True
    )
    with pytest.raises(PopulationTransferIntegrityError, match="partial quarantined"):
        _validate_import_presence(
            (True, False, str(attempt_id)), expected_attempt_id=attempt_id
        )


def test_two_import_invocations_reject_foreign_rows_after_planned_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_manifest = tmp_path / "artifact-manifest.json"
    artifact_manifest.write_bytes(b"artifact")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    receipt_path = tmp_path / "import-receipt.json"
    fresh_trial = uuid4()
    fresh_run = uuid5(fresh_trial, "population")
    transfer = SimpleNamespace(
        originTrialId=UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43"),
        originRunId=UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9"),
        datasetRepository="dhelmy990/babel-wikipedia-experiment",
        datasetConfiguration="20231101.en",
        datasetRevision="0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        servingModelId=UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67"),
        orderedPopulationSha256="b" * 64,
        snapshotSha256="c" * 64,
    )
    verified = SimpleNamespace(
        digest="a" * 64,
        manifest_contract=transfer,
    )
    statements: list[str] = []

    class CollisionCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _parameters):
            statements.append(statement)
            assert (
                "pg_advisory_xact_lock" in statement
                or "telemetry->>'importAttemptId'" in statement
            )

        def fetchone(self):
            return (True, True, None)

    class CollisionConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return CollisionCursor()

    monkeypatch.setattr(
        "babel_online.transfer.database._ARTIFACT_MANIFEST_SHA256",
        hashlib.sha256(artifact_manifest.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database._load_verified_transfer_rows", lambda _verified: []
    )
    monkeypatch.setattr(
        "babel_online.transfer.database.materialize_rebound_frozen_population",
        lambda *_args: (object(), tmp_path / "frozen", "d" * 64),
    )
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: CollisionConnection(),
    )
    monkeypatch.setattr("pgvector.psycopg.register_vector", lambda _connection: None)

    arguments = dict(
        database_url="postgresql://collision",
        verified=verified,
        fresh_trial_id=fresh_trial,
        fresh_run_id=fresh_run,
        model_artifact_manifest=artifact_manifest,
        model_checkpoint_root=checkpoint,
        frozen_output_root=tmp_path / "output",
        import_receipt_path=receipt_path,
    )
    for _invocation in range(2):
        with pytest.raises(
            PopulationTransferIntegrityError, match="import attempt binding"
        ):
            _import_verified_bundle(**arguments)

    protected = ImportReceiptV1.model_validate_json(receipt_path.read_text())
    assert protected.state == "planned"
    assert protected.importAttemptId.version == 4
    assert sum("pg_advisory_xact_lock" in statement for statement in statements) == 2
    for recovery_state in ("quarantined", "ready"):
        receipt_path.write_text(
            protected.model_copy(update={"state": recovery_state}).model_dump_json()
        )
        with pytest.raises(
            PopulationTransferIntegrityError, match="import attempt binding"
        ):
            _import_verified_bundle(**arguments)


def test_import_rejects_operator_receipt_or_downloaded_object_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    root = _bundle(tmp_path, digest)
    receipt = _operator_receipt(root, digest)
    document = json.loads(receipt.read_text())
    document["objects"]["manifest.json"]["sha256"] = "0" * 64
    receipt.write_text(json.dumps(document))
    trial_id = uuid4()
    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle",
        lambda *_args: pytest.fail("operator receipt must fail first"),
    )

    with pytest.raises(PopulationTransferIntegrityError, match="operator receipt"):
        import_population(
            "postgresql://unused",
            root,
            digest,
            receipt,
            trial_id,
            uuid5(trial_id, "population"),
            tmp_path / "artifact_manifest.json",
            tmp_path / "checkpoint",
            tmp_path / "frozen",
            tmp_path / "import.json",
        )


def test_import_verifies_trust_root_before_calling_database_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    root = _bundle(tmp_path, digest)
    operator_receipt = _operator_receipt(root, digest)
    artifact_manifest = tmp_path / "artifact_manifest.json"
    artifact_manifest.write_bytes(b"artifact")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    frozen = tmp_path / "frozen"
    destination_receipt = tmp_path / "receipts" / "import.json"
    trial_id = uuid4()
    run_id = uuid5(trial_id, "population")
    verified = SimpleNamespace(
        root=root,
        digest=digest,
        manifest_contract=SimpleNamespace(
            originTrialId=UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43"),
            originRunId=UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9"),
        ),
    )
    observed = {}

    monkeypatch.setattr(
        "babel_online.transfer.database.verify_bundle", lambda path, trusted: verified
    )

    def adapter(**values):
        observed.update(values)
        return ImportReceiptV1(
            schemaVersion=1,
            state="ready",
            bundleDigest=digest,
            originTrialId=UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43"),
            originRunId=UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9"),
            freshTrialId=trial_id,
            freshPopulationRunId=run_id,
            importAttemptId=uuid4(),
            rowCount=10_000,
            orderedVectorSha256="b" * 64,
            snapshotSha256="c" * 64,
            frozenManifestSha256="d" * 64,
            sampleCount=100,
            hnswIndex="babel_embeddings_cosine_hnsw",
            modelArtifactManifestPath=str(artifact_manifest.resolve()),
            modelCheckpointRoot=str(checkpoint.resolve()),
        )

    monkeypatch.setattr("babel_online.transfer.database._import_verified_bundle", adapter)

    result = import_population(
        "postgresql://db",
        root,
        digest,
        operator_receipt,
        trial_id,
        run_id,
        artifact_manifest,
        checkpoint,
        frozen,
        destination_receipt,
    )

    assert result.state == "ready"
    assert observed["verified"] is verified
    assert observed["fresh_trial_id"] == trial_id
    assert observed["fresh_run_id"] == run_id
    assert observed["import_receipt_path"] == destination_receipt.absolute()
    assert not destination_receipt.exists()


def test_verified_parquet_rows_merge_by_identity_and_keep_float32_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np
    import pyarrow.parquet as pq

    vector = np.concatenate(
        (np.ones(1, dtype=np.float32), np.zeros(99, dtype=np.float32))
    )
    vectors = []
    catalog = []
    for ordinal in range(10_000):
        babel_id = str(uuid5(UUID(int=1), f"babel:{ordinal}"))
        creator_id = str(uuid5(UUID(int=1), f"creator:{ordinal % 50}"))
        content_hash = hashlib.sha256(f"content:{ordinal}".encode()).hexdigest()
        vectors.append(
            {
                "babel_id": babel_id,
                "creator_id": creator_id,
                "serving_model_id": "2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67",
                "materialized_model_version": 0,
                "embedding_space_id": "f3665769-b470-5228-8df4-08004e252aa4",
                "catalog_content_hash": content_hash,
                "model_artifact_id": "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
                "dataset_revision": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
                "vector": vector,
                "vector_sha256": hashlib.sha256(vector.tobytes()).hexdigest(),
            }
        )
        catalog.append(
            {
                "babel_id": babel_id,
                "creator_id": creator_id,
                "source_article_key": f"enwiki:{ordinal + 1}",
                "title": f"Title {ordinal}",
                "article_text": f"Content {ordinal}",
                "catalog_content_hash": content_hash,
                "event_number": ordinal,
                "created_at_ns": ordinal * 1000 + 10,
                "finalized_at_ns": ordinal * 1000 + 20,
                "schedule_index": ordinal,
                "creator_event_number": ordinal // 50,
                "period": "2026-06" if ordinal < 5000 else "2026-07",
                "root_babel_id": babel_id,
                "traversal_session_id": str(uuid5(UUID(int=1), f"traversal:{ordinal}")),
                "work_id": str(uuid5(UUID(int=1), f"work:{ordinal}")),
                "workload_sha256": "e" * 64,
                "schedule_created_at_ns": ordinal * 1000,
                "dataset_repository": "dhelmy990/babel-wikipedia-experiment",
                "dataset_configuration": "crosswalk_2026_06_07",
                "dataset_revision": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
                "dataset_row_reference": "ignored-after-verified-bundle",
            }
        )
    vectors.sort(key=lambda row: row["babel_id"])
    catalog.sort(key=lambda row: row["babel_id"])

    class Table:
        def __init__(self, rows):
            self.rows = rows

        def to_pylist(self):
            return self.rows

    monkeypatch.setattr(
        pq,
        "read_table",
        lambda path: Table(vectors if str(path).endswith("embeddings") else catalog),
    )
    verified = SimpleNamespace(embeddings=Path("embeddings"), catalog=Path("catalog"))

    rows = _load_verified_transfer_rows(verified)

    assert len(rows) == 10_000
    assert rows[0].babel_id == vectors[0]["babel_id"]
    assert rows[0].vector.dtype == np.dtype("<f4")
    assert rows[0].vector.shape == (100,)


def test_rebound_frozen_manifest_changes_only_trial_and_population_run_identities() -> None:
    trial_id = uuid4()
    run_id = uuid5(trial_id, "population")
    transfer = SimpleNamespace(
        creatorCount=50,
        periodCounts={"2026-06": 5_000, "2026-07": 5_000},
        modelArtifactId="3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
        modelRepository="dhelmy990/babel-qwen-navigation-2016-interview",
        modelRevision="57d949cd634b920cc1a46f27c9b21df094b5240e",
        servingModelId=UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67"),
        materializedModelVersion=0,
        embeddingSpaceId=UUID("f3665769-b470-5228-8df4-08004e252aa4"),
        embeddingSpaceVersion="babel-qwen-100d-v1",
        datasetRepository="dhelmy990/babel-wikipedia-experiment",
        datasetConfiguration="crosswalk_2026_06_07",
        datasetRevision="0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        contentSha256="1" * 64,
        orderedPopulationSha256="2" * 64,
        snapshotSha256="3" * 64,
        scheduleSha256="4" * 64,
    )

    manifest = _build_rebound_frozen_manifest(
        transfer,
        fresh_trial_id=trial_id,
        fresh_run_id=run_id,
        babels_bytes=123,
        schedule_bytes=456,
    )

    assert manifest.experimentId == str(trial_id)
    assert manifest.sourcePopulationRunId == run_id
    assert manifest.babelsSha256 == transfer.contentSha256
    assert manifest.vectorsSha256 == transfer.orderedPopulationSha256
    assert manifest.pgvectorSnapshotSha256 == transfer.snapshotSha256
    assert manifest.scheduleSha256 == transfer.scheduleSha256
    assert manifest.vectorBytes == 4_000_000
    assert manifest.modelManifestSha256 == "174e5109b5f34808b2d3814b12a6b2a452da1f1828f43561d392aa58844a8f09"
    assert manifest.datasetManifestSha256 == "069c84e32195d7e175968aa0c569fe5bebc3a148247dbf9e7e34918ef3a22c0f"


def test_database_adapter_rejects_unverified_model_paths_before_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_manifest = tmp_path / "artifact_manifest.json"
    artifact_manifest.write_bytes(b"corrupt")
    checkpoint_root = tmp_path / "checkpoint"
    checkpoint_root.mkdir()
    monkeypatch.setattr(
        "babel_online.transfer.database._connect_psycopg",
        lambda _url: pytest.fail("model trust must fail before database access"),
    )
    trial_id = uuid4()

    with pytest.raises(PopulationTransferIntegrityError, match="artifact manifest"):
        _import_verified_bundle(
            database_url="postgresql://unused",
            verified=SimpleNamespace(digest="a" * 64),
            fresh_trial_id=trial_id,
            fresh_run_id=uuid5(trial_id, "population"),
            model_artifact_manifest=artifact_manifest,
            model_checkpoint_root=checkpoint_root,
            frozen_output_root=tmp_path / "frozen",
            import_receipt_path=tmp_path / "import.json",
        )


def test_ready_database_state_recovery_requires_every_immutable_binding() -> None:
    trial_id = uuid4()
    run_id = uuid5(trial_id, "population")
    expected = (
        "completed",
        "population_ready",
        True,
        run_id,
        "a" * 64,
        "/frozen/population",
        10_000,
        "b" * 64,
        "dhelmy990/babel-qwen-navigation-2016-interview",
        "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "174e5109b5f34808b2d3814b12a6b2a452da1f1828f43561d392aa58844a8f09",
        "dhelmy990/babel-wikipedia-experiment",
        "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "069c84e32195d7e175968aa0c569fe5bebc3a148247dbf9e7e34918ef3a22c0f",
    )
    assert _ready_database_state_matches(
        expected,
        fresh_run_id=run_id,
        frozen_manifest_sha="a" * 64,
        frozen_directory=Path("/frozen/population"),
        ordered_vector_sha="b" * 64,
    )
    drifted = list(expected)
    drifted[7] = "c" * 64
    assert not _ready_database_state_matches(
        tuple(drifted),
        fresh_run_id=run_id,
        frozen_manifest_sha="a" * 64,
        frozen_directory=Path("/frozen/population"),
        ordered_vector_sha="b" * 64,
    )


def test_quarantine_catalog_validation_covers_catalog_and_schedule_identity() -> None:
    expected = SimpleNamespace(
        babel_id="00000000-0000-0000-0000-000000000001",
        creator_id="00000000-0000-0000-0000-000000000002",
        source_article_key="enwiki:1",
        title="Title",
        article_text="Text",
        catalog_content_hash="a" * 64,
        event_number=0,
        created_at_ns=1000,
        finalized_at_ns=2000,
        schedule_index=0,
        creator_event_number=0,
        period="2026-06",
        root_babel_id="00000000-0000-0000-0000-000000000001",
        traversal_session_id="00000000-0000-0000-0000-000000000003",
        work_id="00000000-0000-0000-0000-000000000004",
        workload_sha256="b" * 64,
        schedule_created_at_ns=0,
    )
    database_row = (
        UUID(expected.babel_id),
        UUID(expected.creator_id),
        expected.source_article_key,
        expected.title,
        expected.article_text,
        expected.catalog_content_hash,
        expected.event_number,
        expected.created_at_ns,
        expected.finalized_at_ns,
        expected.schedule_index,
        expected.creator_event_number,
        expected.period,
        UUID(expected.root_babel_id),
        UUID(expected.traversal_session_id),
        UUID(expected.work_id),
        expected.workload_sha256,
        expected.schedule_created_at_ns,
    )

    _validate_quarantined_catalog_rows([expected], [database_row])
    drifted = list(database_row)
    drifted[15] = "c" * 64
    with pytest.raises(PopulationTransferIntegrityError, match="catalog or schedule"):
        _validate_quarantined_catalog_rows([expected], [tuple(drifted)])
