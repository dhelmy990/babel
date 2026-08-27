from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from babel_online.contracts import (
    ActivityLogV1,
    ModelManifestV1,
    RunConfigV1,
)
from babel_online.model.source_vector_cache import VectorCacheKey
from babel_online.runtime.database import (
    ArtifactConfigurationError,
    canonical_json_sha256,
    load_configured_model_artifact,
)
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule

from babel_online.contracts import CandidateActionV1, FeedbackEventV2


ROOT = Path(__file__).resolve().parents[3]


def feedback_event_v2(*, event_number: int = 1) -> FeedbackEventV2:
    suffix = f"{event_number:012d}"
    return FeedbackEventV2(
        schemaVersion=2,
        eventId=f"00000000-0000-5000-8000-{suffix}",
        requestId=f"10000000-0000-5000-8000-{suffix}",
        runId=UUID(int=1),
        creatorId=UUID(int=2),
        sourceBabelId=f"20000000-0000-5000-8000-{suffix}",
        sourceArticleKey=f"enwiki:{event_number}",
        traversalSessionId=f"40000000-0000-5000-8000-{suffix}",
        parentRequestId=None,
        traversalDepth=0,
        modelId=UUID(int=3),
        modelVersion=0,
        embeddingSpaceId=UUID(int=4),
        retrievalBackend="pgvector",
        candidateActions=[CandidateActionV1(
            babelId=f"30000000-0000-5000-8000-{suffix}",
            sourceArticleKey=f"enwiki:{event_number + 100}",
            rank=1,
            modelScore=0.5,
            action="include",
        )],
        occurredAtNs=event_number,
    )


def test_launch_config_digest_is_stable_and_validates_the_pinned_run() -> None:
    document = json.loads((ROOT / "fixtures/online/tiny/run.json").read_text())
    digest = canonical_json_sha256(document)
    assert digest == hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run = RunConfigV1.model_validate(document)
    assert run.datasetRevision == "e1acc648fcace8820dd5ee70bae9216ea4334555"


def test_configured_model_artifact_requires_real_checksum_verified_bytes(tmp_path) -> None:
    state = b'{"fixture":"checksum-verified Friday demo model"}\n'
    (tmp_path / "working-state.json").write_bytes(state)
    manifest = json.loads((ROOT / "fixtures/online/demo-model/manifest.json").read_text())
    manifest["checkpointPath"] = "working-state.json"
    manifest["checkpointSha256"] = hashlib.sha256(state).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    loaded = load_configured_model_artifact(tmp_path)

    assert loaded.manifest == ModelManifestV1.model_validate(manifest)
    assert "demo" in loaded.manifest.label.casefold()
    (tmp_path / "working-state.json").write_text("tampered")
    with pytest.raises(ArtifactConfigurationError):
        load_configured_model_artifact(tmp_path)


def test_activity_boundary_rejects_hidden_simulator_fields() -> None:
    with pytest.raises(ValueError):
        ActivityLogV1.model_validate(
            {
                "schemaVersion": 1,
                "runId": str(UUID(int=1)),
                "sequence": 1,
                "occurredAtNs": 1,
                "level": "info",
                "component": "supervisor",
                "event": "hidden",
                "message": "must fail",
                "metrics": {"pprScore": 0.9},
                "details": {"kind": "lifecycle"},
            }
        )


class RecordingCursor:
    def __init__(self, rows=()) -> None:
        self.queries = []
        self.rows = list(rows)
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=()):
        self.queries.append((" ".join(str(query).split()), parameters))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_existing_source_load_uses_exact_snapshot_key_not_moving_active_pointer() -> None:
    vector = "[" + ",".join(["1"] + ["0"] * 99) + "]"
    cursor = RecordingCursor(rows=[(vector,)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))
    key = VectorCacheKey(
        run_id=UUID(int=1),
        babel_id=UUID(int=2),
        model_id=UUID(int=3),
        model_version=7,
        embedding_space_id=UUID(int=4),
    )

    result = database.load_active_source_vector(key)

    query, parameters = cursor.queries[0]
    assert "run_embedding_states" not in query
    assert "serving_model_id=%s" in query
    assert parameters == (
        key.run_id,
        key.babel_id,
        key.model_id,
        key.model_version,
        key.embedding_space_id,
    )
    assert result.tobytes() == __import__("numpy").array(
        [1.0] + [0.0] * 99, dtype="<f4"
    ).tobytes()


def test_database_persists_full_schedule_identity_and_canonical_include_only() -> None:
    run_id = UUID(int=1)
    creator_id = UUID(int=2)
    rows = deterministic_schedule(
        run_id,
        [ScheduledWork(
            creator_id=creator_id,
            creator_event_number=0,
            period="2026-06",
            source_article_key="enwiki:1",
            root_babel_id=UUID(int=3),
        )],
    )
    cursor = RecordingCursor()
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    database.persist_work_schedule(rows)
    event = feedback_event_v2(event_number=4)
    event = event.model_copy(
        update={
            "runId": run_id,
            "creatorId": creator_id,
            "candidateActions": [
                event.candidateActions[0],
                event.candidateActions[0].model_copy(update={"action": "exclude", "babelId": UUID(int=9)}),
                event.candidateActions[0].model_copy(update={"action": "ignore", "babelId": UUID(int=10)}),
            ],
        }
    )
    database.persist_feedback_edges(event)

    schedule_query, schedule_parameters = cursor.queries[0]
    assert "INSERT INTO experiment_work_schedule" in schedule_query
    assert rows[0].workload_sha256 in schedule_parameters
    edge_queries = [query for query, _params in cursor.queries if "INSERT INTO experiment_edges" in query]
    assert len(edge_queries) == 1
    assert "feedback_occurred_at_ns" in edge_queries[0]
    assert "EXCLUDED.feedback_event_id" in edge_queries[0]
