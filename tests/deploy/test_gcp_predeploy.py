from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest


ROOT = Path(__file__).resolve().parents[2]
PREDEPLOY_PATH = ROOT / "deploy" / "gcp" / "predeploy.py"
TRIAL_ID = UUID("4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70")
RUN_ID = uuid5(TRIAL_ID, "population")
MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")
MODEL_REPOSITORY = "dhelmy990/babel-qwen-navigation-2016-interview"
MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIG = "crosswalk_2026_06_07"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
TRAINING_DATASET_REVISION = "b440e98b04ab77afed7caf0455eca3189235fc3b"
ARTIFACT_ID = "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"
ARTIFACT_MANIFEST_SHA = "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
MODEL_MANIFEST_SHA = "174e5109b5f34808b2d3814b12a6b2a452da1f1828f43561d392aa58844a8f09"
DATASET_MANIFEST_SHA = "069c84e32195d7e175968aa0c569fe5bebc3a148247dbf9e7e34918ef3a22c0f"


def _load_predeploy_module():
    spec = importlib.util.spec_from_file_location("babel_gcp_predeploy", PREDEPLOY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def predeploy():
    return _load_predeploy_module()


def _embedding_space() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "embeddingSpaceId": str(SPACE_ID),
        "dimension": 100,
        "distance": "cosine",
        "distilledEncoderArtifact": (
            f"hf://{MODEL_REPOSITORY}@{MODEL_REVISION}/artifacts/{ARTIFACT_ID}"
        ),
        "datasetRevision": TRAINING_DATASET_REVISION,
        "compatibilityVersion": "babel-qwen-100d-v1",
    }


def _wire(values: list[float] | None = None) -> bytes:
    row = values or [1.0] + [0.0] * 99
    return struct.pack(">hh100f", 100, 0, *row)


def _f32le(wire: bytes) -> bytes:
    return b"".join(wire[offset : offset + 4][::-1] for offset in range(4, 404, 4))


def _snapshot_sha(vectors: list[tuple[object, ...]]) -> str:
    digest = hashlib.sha256()
    for row in vectors:
        wire = bytes(row[8])
        value = {
            "babelId": str(UUID(str(row[0]))),
            "catalogContentHash": str(row[5]),
            "creatorId": str(UUID(str(row[1]))),
            "embeddingSpaceId": str(UUID(str(row[3]))),
            "materializedModelVersion": int(row[7]),
            "servingModelId": str(UUID(str(row[4]))),
            "sourceArticleKey": str(row[6]),
            "vectorSha256": hashlib.sha256(_f32le(wire)).hexdigest(),
        }
        digest.update(
            (
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


@pytest.fixture(scope="module")
def valid_snapshot():
    wire = _wire()
    vectors = []
    for index in range(10_000):
        babel_id = UUID(int=index + 1)
        creator_id = UUID(int=10_001 + index % 50)
        vectors.append(
            (
                babel_id,
                creator_id,
                creator_id,
                SPACE_ID,
                MODEL_ID,
                hashlib.sha256(f"content-{index}".encode()).hexdigest(),
                f"enwiki:{index + 1}",
                0,
                wire,
            )
        )
    vector_sha = hashlib.sha256(_f32le(wire) * 10_000).hexdigest()
    snapshot_sha = _snapshot_sha(vectors)
    metadata = {
        "trial_row_count": 1,
        "trial_id": TRIAL_ID,
        "trial_status": "population_ready",
        "trial_population_ready": True,
        "trial_run_id": RUN_ID,
        "trial_starting_model_id": MODEL_ID,
        "trial_model_repository": MODEL_REPOSITORY,
        "trial_model_revision": MODEL_REVISION,
        "trial_dataset_repository": DATASET_REPOSITORY,
        "trial_dataset_revision": DATASET_REVISION,
        "trial_retrieval_backend": "pgvector",
        "trial_target_count": 10_000,
        "trial_population_count": 10_000,
        "trial_population_vector_sha256": vector_sha,
        "trial_population_model_repository": MODEL_REPOSITORY,
        "trial_population_model_revision": MODEL_REVISION,
        "trial_population_model_sha256": MODEL_MANIFEST_SHA,
        "trial_population_dataset_repository": DATASET_REPOSITORY,
        "trial_population_dataset_revision": DATASET_REVISION,
        "trial_population_dataset_sha256": DATASET_MANIFEST_SHA,
        "trial_population_manifest_sha256": "c" * 64,
        "run_row_count": 1,
        "run_id": RUN_ID,
        "run_status": "completed",
        "run_retrieval_backend": "pgvector",
        "run_contract_version": 2,
        "run_scenario": "june_to_july",
        "run_environment_sequence": ["2026-06", "2026-07"],
        "run_target_count": 10_000,
        "run_created_count": 10_000,
        "run_starting_model_id": MODEL_ID,
        "run_active_model_id": MODEL_ID,
        "run_active_model_version": 0,
        "run_dataset_repository": DATASET_REPOSITORY,
        "run_dataset_config": DATASET_CONFIG,
        "run_dataset_revision": DATASET_REVISION,
        "model_row_count": 1,
        "model_id": MODEL_ID,
        "model_parent_model_id": None,
        "model_producing_run_id": None,
        "model_encoder_repository": MODEL_REPOSITORY,
        "model_encoder_revision": MODEL_REVISION,
        "model_dataset_repository": DATASET_REPOSITORY,
        "model_dataset_revision": TRAINING_DATASET_REVISION,
        "model_training_examples": 50_000,
        "model_checkpoint_sha256": ARTIFACT_MANIFEST_SHA,
        "model_embedding_space": _embedding_space(),
        "model_immutable": True,
        "state_row_count": 1,
        "state_active_model_id": MODEL_ID,
        "state_active_model_version": 0,
        "state_embedding_space_id": SPACE_ID,
        "state_pgvector_snapshot_sha256": snapshot_sha,
        "state_backend_snapshot_sha256": snapshot_sha,
        "catalog_count": 10_000,
        "catalog_unique_babel_count": 10_000,
        "catalog_valid_count": 10_000,
        "embedding_count": 10_000,
        "embedding_unique_babel_count": 10_000,
        "embedding_exact_active_count": 10_000,
        "embedding_valid_vector_count": 10_000,
        "embedding_catalog_match_count": 10_000,
        "hnsw_index_count": 1,
        "hnsw_index_method": "hnsw",
        "hnsw_index_valid": True,
        "hnsw_index_ready": True,
        "hnsw_plan_uses_named_index": True,
    }
    return metadata, vectors, vector_sha, snapshot_sha


def test_valid_snapshot_emits_canonical_nonsecret_evidence(
    predeploy, valid_snapshot
) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    evidence = predeploy.validate_snapshot(
        metadata,
        vectors,
        trial_id=TRIAL_ID,
        run_id=RUN_ID,
        expected_vector_sha256=vector_sha,
        expected_snapshot_sha256=snapshot_sha,
    )

    assert evidence == {
        "schemaVersion": 1,
        "verified": True,
        "trialId": str(TRIAL_ID),
        "runId": str(RUN_ID),
        "sampleCreatorId": str(UUID(int=10_001)),
        "catalogCount": 10_000,
        "activeEmbeddingCount": 10_000,
        "embeddingDimension": 100,
        "modelId": str(MODEL_ID),
        "modelRepository": MODEL_REPOSITORY,
        "modelRevision": MODEL_REVISION,
        "datasetRepository": DATASET_REPOSITORY,
        "datasetConfig": DATASET_CONFIG,
        "datasetRevision": DATASET_REVISION,
        "embeddingSpaceId": str(SPACE_ID),
        "materializedModelVersion": 0,
        "orderedVectorSha256": vector_sha,
        "pgvectorSnapshotSha256": snapshot_sha,
        "hnswIndexName": "babel_embeddings_cosine_hnsw",
        "hnswIndexValid": True,
        "hnswIndexReady": True,
    }
    encoded = predeploy.canonical_json(evidence)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == evidence
    assert "DATABASE" not in encoded.decode()


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("trial_row_count", 0),
        ("trial_status", "failed"),
        ("trial_population_ready", False),
        ("trial_population_count", 9_999),
        ("run_status", "running"),
        ("run_created_count", 9_999),
        ("catalog_count", 10_001),
        ("catalog_unique_babel_count", 9_999),
        ("catalog_valid_count", 9_999),
        ("embedding_count", 10_001),
        ("embedding_unique_babel_count", 9_999),
        ("embedding_exact_active_count", 9_999),
        ("embedding_valid_vector_count", 9_999),
        ("embedding_catalog_match_count", 9_999),
        ("state_active_model_version", 1),
        ("state_embedding_space_id", uuid4()),
        ("model_encoder_revision", "f" * 40),
        ("run_dataset_config", "fixture"),
        ("hnsw_index_count", 0),
        ("hnsw_index_method", "btree"),
        ("hnsw_index_valid", False),
        ("hnsw_index_ready", False),
        ("hnsw_plan_uses_named_index", False),
    ],
)
def test_each_metadata_mismatch_class_fails_closed(
    predeploy, valid_snapshot, field: str, invalid: object
) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    with pytest.raises(predeploy.PredeployValidationError, match=field):
        predeploy.validate_snapshot(
            metadata | {field: invalid},
            vectors,
            trial_id=TRIAL_ID,
            run_id=RUN_ID,
            expected_vector_sha256=vector_sha,
            expected_snapshot_sha256=snapshot_sha,
        )


def test_ids_must_be_fresh_uuid4_and_uuid5_population(predeploy, valid_snapshot) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    cases = (
        (
            UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43"),
            UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9"),
        ),
        (uuid5(UUID(int=99), "trial"), RUN_ID),
        (TRIAL_ID, uuid4()),
    )
    for trial_id, run_id in cases:
        with pytest.raises(predeploy.PredeployValidationError):
            predeploy.validate_snapshot(
                metadata,
                vectors,
                trial_id=trial_id,
                run_id=run_id,
                expected_vector_sha256=vector_sha,
                expected_snapshot_sha256=snapshot_sha,
            )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: [rows[0][:-1] + (b"short",), *rows[1:]],
        lambda rows: [
            rows[0][:-1] + (_wire([math.nan] + [0.0] * 99),),
            *rows[1:],
        ],
        lambda rows: [rows[0], rows[0], *rows[2:]],
        lambda rows: [
            rows[0][:2] + (uuid4(),) + rows[0][3:],
            *rows[1:],
        ],
    ],
)
def test_vector_shape_finiteness_uniqueness_and_creator_binding_fail_closed(
    predeploy, valid_snapshot, mutate
) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    with pytest.raises(predeploy.PredeployValidationError):
        predeploy.validate_snapshot(
            metadata,
            mutate(vectors),
            trial_id=TRIAL_ID,
            run_id=RUN_ID,
            expected_vector_sha256=vector_sha,
            expected_snapshot_sha256=snapshot_sha,
        )


def test_recomputed_ordered_vector_and_snapshot_hashes_must_match(
    predeploy, valid_snapshot
) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    with pytest.raises(predeploy.PredeployValidationError, match="ordered vector"):
        predeploy.validate_snapshot(
            metadata | {"trial_population_vector_sha256": "f" * 64},
            vectors,
            trial_id=TRIAL_ID,
            run_id=RUN_ID,
            expected_vector_sha256="f" * 64,
            expected_snapshot_sha256=snapshot_sha,
        )
    with pytest.raises(predeploy.PredeployValidationError, match="snapshot"):
        predeploy.validate_snapshot(
            metadata
            | {
                "state_pgvector_snapshot_sha256": "e" * 64,
                "state_backend_snapshot_sha256": "e" * 64,
            },
            vectors,
            trial_id=TRIAL_ID,
            run_id=RUN_ID,
            expected_vector_sha256=vector_sha,
            expected_snapshot_sha256="e" * 64,
        )


class _FakeCursor:
    def __init__(self, metadata: dict[str, object], vectors: list[tuple[object, ...]]):
        self.metadata = metadata
        self.vectors = vectors
        self.description = None
        self.executions: list[tuple[str, object]] = []
        self._result: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement: str, parameters=None) -> None:
        self.executions.append((statement, parameters))
        if "WITH expected" in statement:
            self.description = [SimpleNamespace(name=key) for key in self.metadata]
            self._result = [tuple(self.metadata.values())]
        elif "public.vector_send" in statement:
            self.description = []
            self._result = self.vectors
        elif "EXPLAIN (FORMAT JSON)" in statement:
            self.description = []
            self._result = [
                ([{"Plan": {"Index Name": "babel_embeddings_cosine_hnsw"}}],)
            ]
        else:
            self.description = []
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.fake_cursor


def test_database_boundary_is_read_only_and_parameterized(predeploy, valid_snapshot) -> None:
    metadata, vectors, _vector_sha, _snapshot_sha_value = valid_snapshot
    cursor = _FakeCursor(metadata, vectors)
    connection = _FakeConnection(cursor)
    snapshot = predeploy.read_database_snapshot(
        "postgresql://not-logged",
        trial_id=TRIAL_ID,
        run_id=RUN_ID,
        connect=lambda dsn: connection,
    )

    assert snapshot == (metadata, vectors)
    assert cursor.executions[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        None,
    )
    assert cursor.executions[1][1] == (TRIAL_ID, RUN_ID)
    assert cursor.executions[2][1] == (RUN_ID,)
    assert cursor.executions[3] == ("SET LOCAL enable_seqscan=off", None)
    explain, explain_parameters = cursor.executions[4]
    assert "EXPLAIN (FORMAT JSON)" in explain
    assert explain_parameters[:4] == (RUN_ID, MODEL_ID, 0, SPACE_ID)
    assert isinstance(explain_parameters[4], str)
    assert explain_parameters[4].startswith("[")
    assert str(TRIAL_ID) not in cursor.executions[1][0]
    assert str(RUN_ID) not in cursor.executions[1][0]
    assert str(RUN_ID) not in explain


def test_cli_uses_database_url_only_from_env_and_emits_canonical_json(
    predeploy, valid_snapshot, monkeypatch, capsys
) -> None:
    metadata, vectors, vector_sha, snapshot_sha = valid_snapshot
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://secret-value")
    monkeypatch.setattr(
        predeploy,
        "read_database_snapshot",
        lambda *_args, **_kwargs: (metadata, vectors),
    )
    result = predeploy.main(
        [
            "--trial-id",
            str(TRIAL_ID),
            "--run-id",
            str(RUN_ID),
            "--population-vector-sha256",
            vector_sha,
            "--population-snapshot-sha256",
            snapshot_sha,
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out)["sampleCreatorId"] == str(UUID(int=10_001))
    assert captured.out.encode() == predeploy.canonical_json(json.loads(captured.out))
    assert "secret-value" not in captured.out + captured.err


def _ready_import_receipt(vector_sha: str, snapshot_sha: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "state": "ready",
        "importAttemptId": str(uuid4()),
        "originTrialId": "ce8e54ff-e317-4a89-b7db-90327e02dc43",
        "originRunId": "7f4ad291-e6d0-5bb9-9658-3605c634a3a9",
        "freshTrialId": str(TRIAL_ID),
        "freshPopulationRunId": str(RUN_ID),
        "rowCount": 10_000,
        "sampleCount": 100,
        "orderedVectorSha256": vector_sha,
        "snapshotSha256": snapshot_sha,
        "hnswIndex": "babel_embeddings_cosine_hnsw",
        "bundleDigest": "c" * 64,
        "frozenManifestSha256": "d" * 64,
        "modelCheckpointRoot": "/var/lib/babel-online/cache/model-artifact",
        "modelArtifactManifestPath": "/var/lib/babel-online/cache/model-artifact/artifact_manifest.json",
    }


def test_reuse_validation_checks_receipt_and_metadata_without_vector_rows(
    predeploy, valid_snapshot, tmp_path: Path
) -> None:
    metadata, _vectors, vector_sha, snapshot_sha = valid_snapshot
    metadata = metadata | {
        "sample_creator_id": UUID(int=10_001),
        "trial_population_manifest_sha256": "d" * 64,
    }
    receipt_path = tmp_path / "import-receipt.json"
    receipt_path.write_bytes(
        predeploy.canonical_json(_ready_import_receipt(vector_sha, snapshot_sha))
    )

    evidence = predeploy.validate_reuse_snapshot(
        metadata,
        import_receipt_path=receipt_path,
        trial_id=TRIAL_ID,
        run_id=RUN_ID,
        expected_vector_sha256=vector_sha,
        expected_snapshot_sha256=snapshot_sha,
    )

    assert evidence["verified"] is True
    assert evidence["validationMode"] == "reuse_without_vector_rehash"
    assert evidence["activeEmbeddingCount"] == 10_000
    assert evidence["orderedVectorSha256"] == vector_sha
    assert evidence["pgvectorSnapshotSha256"] == snapshot_sha
    assert len(evidence["importReceiptSha256"]) == 64


def test_reuse_cli_never_invokes_full_vector_scan(
    predeploy, valid_snapshot, tmp_path: Path, monkeypatch, capsys
) -> None:
    metadata, _vectors, vector_sha, snapshot_sha = valid_snapshot
    metadata = metadata | {
        "sample_creator_id": UUID(int=10_001),
        "trial_population_manifest_sha256": "d" * 64,
    }
    receipt_path = tmp_path / "import-receipt.json"
    receipt_path.write_bytes(
        predeploy.canonical_json(_ready_import_receipt(vector_sha, snapshot_sha))
    )
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://secret-value")
    monkeypatch.setattr(
        predeploy,
        "read_database_snapshot",
        lambda *_args, **_kwargs: pytest.fail("reuse mode must not scan vectors"),
    )
    monkeypatch.setattr(
        predeploy,
        "read_database_reuse_snapshot",
        lambda *_args, **_kwargs: metadata,
    )

    result = predeploy.main(
        [
            "--trial-id",
            str(TRIAL_ID),
            "--run-id",
            str(RUN_ID),
            "--population-vector-sha256",
            vector_sha,
            "--population-snapshot-sha256",
            snapshot_sha,
            "--reuse-import-receipt",
            str(receipt_path),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["validationMode"] == (
        "reuse_without_vector_rehash"
    )


def test_reuse_database_boundary_never_reads_vector_payloads(
    predeploy, valid_snapshot
) -> None:
    metadata, vectors, _vector_sha, _snapshot_sha_value = valid_snapshot
    cursor = _FakeCursor(metadata | {"sample_creator_id": UUID(int=10_001)}, vectors)
    connection = _FakeConnection(cursor)

    snapshot = predeploy.read_database_reuse_snapshot(
        "postgresql://not-logged",
        trial_id=TRIAL_ID,
        run_id=RUN_ID,
        connect=lambda _dsn: connection,
    )

    assert snapshot["embedding_count"] == 10_000
    assert len(cursor.executions) == 2
    query, parameters = cursor.executions[1]
    assert parameters == (TRIAL_ID, RUN_ID)
    assert "vector_send" not in query
    assert "embedding::" not in query
    assert "eb.embedding" not in query


def test_cli_fails_closed_without_database_url(predeploy, monkeypatch, capsys) -> None:
    monkeypatch.delenv("BABEL_DATABASE_URL", raising=False)
    result = predeploy.main(
        [
            "--trial-id",
            str(TRIAL_ID),
            "--run-id",
            str(RUN_ID),
            "--population-vector-sha256",
            "a" * 64,
            "--population-snapshot-sha256",
            "b" * 64,
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert "BABEL_DATABASE_URL" in captured.err
