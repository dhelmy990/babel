from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from babel_online.contracts import (
    ActivityLogV1,
    CandidateActionV1,
    FeedbackEventV2,
    ModelManifestV1,
    RunConfigV1,
    RunConfigV2,
)
from babel_online.model.candidate_index import MaterializedServingState
from babel_online.model.population import PopulationIdentity, PopulationIntegrityError
from babel_online.model.registry import ModelRegistry
from babel_online.model.source_vector_cache import VectorCacheKey
from babel_online.model.state_distributor import (
    KnownVectorProbeV1,
    export_real_qwen_child,
)
from babel_online.runtime.database import (
    ArtifactConfigurationError,
    canonical_json_sha256,
    load_configured_model_artifact,
)
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule
from babel_online.simulation.walk import WalkRollEvidence

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
        sourceVectorOrigin="qwen_encode",
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

    def executemany(self, query, parameters):
        self.queries.append((" ".join(str(query).split()), list(parameters)))

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


def test_performance_transition_clears_a_stale_failure_on_retry() -> None:
    from babel_online.runtime.database import RuntimeDatabase

    cursor = RecordingCursor(rows=[(UUID(int=1),)])
    database = RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(cursor)
    )

    database.transition_performance(UUID(int=1), "running")

    query, parameters = cursor.queries[0]
    assert "failure=%s" in query
    assert "COALESCE" not in query
    assert parameters == ("running", None, UUID(int=1))


def scaled_run_config() -> RunConfigV2:
    return RunConfigV2(
        schemaVersion=2,
        runId=UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa"),
        datasetRepo="owner/dataset",
        datasetConfig="scaled",
        datasetRevision="b" * 40,
        startingModelId=UUID("cccccccc-cccc-5ccc-8ccc-cccccccccccc"),
        retrievalBackend="pgvector",
        creatorCount=50,
        embeddingDimension=100,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 2500, "2026-07": 2500},
        runSeed=19,
        recommendationK=20,
        topL=200,
        kafkaTopic="scaled.feedback",
        kafkaGroup="scaled-trainer",
        checkpointEveryEvents=80,
        syncEverySteps=12,
        artifactRoot="artifacts/scaled",
        stateRoot="state/scaled",
        sourceArticlesPerMonth=5000,
        targetCreatedBabels=5000,
        concurrentUsers=40,
        recommendationStartProbability=0.3,
        continuationProbability=0.2,
        maximumTraversalDepth=2,
        maximumRequestsPerTraversal=8,
        interleaveCreationAndRecommendations=False,
    )


def test_create_scaled_run_persists_canonical_v2_launch_and_all_scaled_columns() -> None:
    config = scaled_run_config()
    cursor = RecordingCursor()
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    created = database.create_scaled_run(config)

    query, parameters = cursor.queries[0]
    document = config.model_dump(mode="json")
    assert "INSERT INTO experiment_runs" in query
    assert "contract_version" in query
    assert "source_articles_per_month" in query
    assert "target_created_babels" in query
    assert "concurrent_users" in query
    assert "recommendation_start_probability" in query
    assert "continuation_probability" in query
    assert "maximum_traversal_depth" in query
    assert "maximum_requests_per_traversal" in query
    assert "interleave_creation_and_recommendations" in query
    assert json.loads(parameters[-2]) == document
    assert parameters[-1] == canonical_json_sha256(document)
    assert created.config == config
    assert created.status == "starting"


def test_performance_execution_bindings_are_identically_idempotent() -> None:
    run_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    cursor = RecordingCursor(rows=[("bound",), ("bound",)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    database.bind_performance_population("trial", run_id, "d" * 64, "runs/trial")
    database.bind_performance_condition("trial", "condition", run_id)

    assert "population_manifest_sha256" in cursor.queries[0][0]
    assert "population_bundle_path" in cursor.queries[0][0]
    assert cursor.queries[0][1] == (
        run_id, "d" * 64, "runs/trial", "trial", run_id, "d" * 64, "runs/trial"
    )
    assert "run_id" in cursor.queries[1][0]
    assert cursor.queries[1][1] == (run_id, "trial", "condition", run_id)


def test_performance_execution_bindings_reject_conflicting_identity() -> None:
    database_module = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    )
    cursor = RecordingCursor(rows=[])
    database = database_module.RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(cursor)
    )

    with pytest.raises(database_module.PerformanceBindingConflict):
        database.bind_performance_population(
            "trial", UUID(int=1), "d" * 64, "runs/trial"
        )
    with pytest.raises(database_module.PerformanceBindingConflict):
        database.bind_performance_condition("trial", "condition", UUID(int=2))


def test_database_loads_saved_performance_trial_and_exact_3x3_conditions() -> None:
    experiment_id = UUID(int=101)
    model_id = UUID(int=102)
    condition_rows = []
    index = 0
    for topology in ("same_process", "same_host_split", "same_host_isolated"):
        for training, activation in ((False, False), (True, False), (True, True)):
            index += 1
            condition_rows.append(
                (UUID(int=200 + index), index, topology, training, activation, None, "pending")
            )
    trial_row = (
        experiment_id,
        "population_pending",
        model_id,
        "dhelmy990/babel-qwen-navigation-2016-interview",
        "1" * 40,
        "dhelmy990/babel-wikipedia-experiment",
        "2" * 40,
        50,
        10_000,
        50,
        0.4,
        0.4,
        2,
        10,
        True,
        30,
        120,
        5.0,
        8,
        10,
        False,
        False,
        None,
        None,
        None,
    )
    cursor = RecordingCursor(rows=[trial_row, *condition_rows])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    trial = database.load_performance_experiment(experiment_id)

    assert trial.id == experiment_id
    assert trial.dataset_config == "crosswalk_2026_06_07"
    assert trial.warmup_seconds == 30
    assert len(trial.conditions) == 9
    trial.validate_formal_defaults()
    assert "FROM performance_experiments" in cursor.queries[0][0]
    assert "ORDER BY condition_index" in cursor.queries[1][0]


def test_database_persists_performance_progress_and_result_ratios() -> None:
    database_module = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    )
    worker_module = __import__(
        "babel_online.runtime.performance_worker", fromlist=["LiveConditionEvidence"]
    )
    cursor = RecordingCursor()
    database = database_module.RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(cursor)
    )
    experiment_id, condition_id, run_id = UUID(int=1), UUID(int=2), UUID(int=3)
    database.append_performance_progress(
        experiment_id,
        phase="matrix",
        condition_index=1,
        condition_count=9,
        seeded_articles=10_000,
        created_babels=10_000,
        indexed_babels=10_000,
        requested=20,
        completed=20,
        elapsed_seconds=2.0,
        recent_rate=10.0,
        draining=False,
        telemetry={"ok": True},
    )
    database.save_performance_condition_result(
        experiment_id,
        worker_module.LiveConditionEvidence(
            condition_id=condition_id,
            run_id=run_id,
            request_count=20,
            p95_ms=12.0,
            raw_evidence={"kind": "live"},
        ),
        serving_p95_ms=10.0,
        training_p95_ms=12.0,
        full_p95_ms=15.0,
    )

    assert "performance_progress_snapshots" in cursor.queries[0][0]
    result_query, result_parameters = cursor.queries[1]
    assert "performance_results" in result_query
    assert result_parameters[-3:] == (1.2, 1.5, 1.25)


def test_database_exposes_persisted_trainer_serving_health() -> None:
    cursor = RecordingCursor(rows=[(12, 7, 3, "checkpoint.json", True)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    health = database.performance_runtime_health(UUID(int=91))

    assert health == {
        "kafka_lag": 12,
        "trainer_version": 7,
        "serving_version": 3,
        "checkpoint_version": 7,
        "activation_version": 3,
    }


def test_database_verifies_live_identity_against_run_scoped_model_lineage() -> None:
    cursor = RecordingCursor(rows=[(True,)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))
    values = {
        "run_id": UUID(int=1),
        "starting_model_id": UUID(int=2),
        "model_id": UUID(int=3),
        "model_version": 4,
        "embedding_space_id": UUID(int=5),
        "pgvector_sha256": "a" * 64,
        "backend_sha256": "b" * 64,
    }

    assert database.verify_live_serving_identity(**values) is True
    query, parameters = cursor.queries[0]
    assert "WITH RECURSIVE lineage" in query
    assert "producing_run_id" in query
    assert "run_embedding_states" in query
    assert parameters == (
        values["starting_model_id"],
        values["run_id"],
        values["run_id"],
        values["model_id"],
        values["model_version"],
        values["embedding_space_id"],
        values["pgvector_sha256"],
        values["backend_sha256"],
    )


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


def test_database_loads_the_exact_persisted_work_schedule() -> None:
    run_id = UUID(int=1)
    expected = deterministic_schedule(
        run_id,
        [ScheduledWork(
            creator_id=UUID(int=2),
            creator_event_number=0,
            period="2026-07",
            source_article_key="enwiki:7",
            root_babel_id=UUID(int=3),
        )],
    )[0]
    cursor = RecordingCursor(rows=[(
        expected.schedule_index,
        expected.creator_id,
        expected.creator_event_number,
        expected.period,
        expected.source_article_key,
        expected.root_babel_id,
        expected.traversal_session_id,
        expected.work_id,
        expected.workload_sha256,
    )])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    loaded = database.load_work_schedule(run_id)

    assert loaded == (expected,)
    assert "ORDER BY schedule_index" in cursor.queries[0][0]
    assert cursor.queries[0][1] == (run_id,)


def population_identity() -> PopulationIdentity:
    return PopulationIdentity(
        run_id=UUID(int=1),
        dataset_revision="b" * 40,
        model_id=UUID(int=2),
        model_version=0,
        model_manifest_sha256="c" * 64,
        artifact_manifest_sha256="d" * 64,
        artifact_repo="dhelmy990/babel-qwen-navigation-2016-interview",
        artifact_revision="1" * 40,
        artifact_id="2" * 64,
        training_dataset_revision="3" * 40,
        embedding_space_id=UUID(int=3),
        embedding_space_version="babel-qwen-100d-v1",
    )


def test_database_decodes_exact_pgvector_wire_payload_to_f32le() -> None:
    values = __import__("numpy").linspace(-1, 1, 100, dtype="<f4")
    wire = struct.pack(">hh", 100, 0) + values.astype(">f4").tobytes()
    database_type = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase

    actual_wire, f32le = database_type._decode_vector_send(wire)

    assert actual_wire == wire
    assert f32le == values.tobytes()
    with pytest.raises(PopulationIntegrityError, match="wire"):
        database_type._decode_vector_send(wire[:-4])


def test_clone_population_is_insert_select_and_rejects_any_relational_difference() -> None:
    source = population_identity()
    state_row = (
        source.model_id,
        source.model_version,
        source.embedding_space_id,
        "a" * 64,
        "a" * 64,
    )
    cursor = RecordingCursor(rows=[(0, 0, 0), state_row])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    state = database.clone_population_transaction(source, UUID(int=9))

    assert state.run_id == UUID(int=9)
    inserts = [query for query, _ in cursor.queries[:4]]
    assert all("INSERT INTO" in query and "SELECT" in query for query in inserts)
    assert "vector_send(embedding)" in cursor.queries[4][0]
    assert cursor.queries[4][0].count("serving_model_id=%s") == 4
    assert cursor.queries[4][0].count("materialized_model_version=%s") == 4
    assert cursor.queries[4][0].count("embedding_space_id=%s") == 4
    assert cursor.queries[4][0].count("%s") == len(cursor.queries[4][1])

    conflict = RecordingCursor(rows=[(0, 1, 0)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(conflict))
    with pytest.raises(PopulationIntegrityError, match="bytes or schedule"):
        database.clone_population_transaction(source, UUID(int=10))


def test_database_persists_and_loads_exact_traversal_roll_evidence() -> None:
    run_id, session_id = UUID(int=1), UUID(int=2)
    evidence = (
        WalkRollEvidence(
            draw_index=0,
            kind="start",
            source_babel_id=UUID(int=3),
            target_babel_id=None,
            target_rank=None,
            source_depth=0,
            draw_value=0.91,
            probability=0.4,
            roll_succeeded=False,
            outcome="start_skipped",
        ),
    )
    write_cursor = RecordingCursor()
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(write_cursor))

    database.persist_traversal_rolls(run_id, session_id, evidence)

    query, parameters = write_cursor.queries[0]
    assert "INSERT INTO experiment_traversal_rolls" in query
    assert "ON CONFLICT" in query and "DO NOTHING" in query
    assert "DO UPDATE" not in query
    assert parameters == (
        run_id, session_id, 0, "start", UUID(int=3), None, None, 0,
        0.91, 0.4, False, "start_skipped",
    )

    read_cursor = RecordingCursor(rows=[parameters[2:]])
    reader = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(read_cursor))

    assert reader.load_traversal_rolls(run_id, session_id) == evidence

    exact_retry_cursor = RecordingCursor(rows=[parameters[3:]])
    exact_retry_cursor.rowcount = 0
    exact_retry = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(exact_retry_cursor)
    )
    exact_retry.persist_traversal_rolls(run_id, session_id, evidence)
    assert "SELECT kind,source_babel_id" in exact_retry_cursor.queries[1][0]

    conflicting_cursor = RecordingCursor(rows=[("continuation", *parameters[4:])])
    conflicting_cursor.rowcount = 0
    conflicting = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase(
        "unused", connect=lambda: RecordingConnection(conflicting_cursor)
    )
    with pytest.raises(PopulationIntegrityError, match="traversal roll retry"):
        conflicting.persist_traversal_rolls(run_id, session_id, evidence)


def test_database_registers_and_reloads_real_qwen_child_descriptor(
    tmp_path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    child = export_real_qwen_child(
        tmp_path,
        parent=real_model_manifest,
        run_id=UUID(int=71),
        child_model_id=UUID(int=72),
        label="real child",
        online_state=b'{"state":"real"}\n',
        processed_feedback_events=9,
        model_version=3,
        vector_snapshot_sha256="b" * 64,
        probe=KnownVectorProbeV1(
            schemaVersion=1,
            inputVector=[1.0] + [0.0] * 99,
            expectedSemanticSha256="c" * 64,
        ),
        registry=registry,
    )
    cursor = RecordingCursor(rows=[(
        child.descriptor.childManifest.parentModelId,
        child.descriptor.childManifest.encoderRevision,
        child.descriptor.childManifest.embeddingSpace.model_dump(mode="json"),
        child.descriptor_sha256,
    )])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    database.register_real_child(child.descriptor, child.descriptor_path)

    insert, parameters = cursor.queries[0]
    assert "INSERT INTO recommender_models" in insert
    assert child.descriptor.childManifest.parentModelId in parameters
    assert str(child.descriptor_path) in parameters
    assert child.descriptor_sha256 in parameters

    loader_cursor = RecordingCursor(rows=[(
        str(child.descriptor_path), child.descriptor_sha256,
    )])
    loader = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(loader_cursor))
    loaded = loader.load_real_child_descriptor(child.descriptor.childManifest.modelId)
    assert loaded == child.descriptor


def test_database_loads_exact_active_embedding_state() -> None:
    run_id, model_id, space_id = UUID(int=1), UUID(int=2), UUID(int=3)
    cursor = RecordingCursor(rows=[(model_id, 7, space_id, "a" * 64, "b" * 64)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))

    state = database.load_active_embedding_state(run_id)

    assert state == MaterializedServingState(
        run_id=run_id,
        model_id=model_id,
        model_version=7,
        embedding_space_id=space_id,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="b" * 64,
    )


def test_database_explains_the_measured_retrieval_query_shape() -> None:
    run_id, model_id, space_id, creator_id = (
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
        UUID(int=4),
    )
    plan = [{"Plan": {"Index Name": "babel_embeddings_cosine_hnsw"}}]
    cursor = RecordingCursor(rows=[("[1,0]", "a" * 64), (plan,)])
    database = __import__(
        "babel_online.runtime.database", fromlist=["RuntimeDatabase"]
    ).RuntimeDatabase("unused", connect=lambda: RecordingConnection(cursor))
    vector = [1.0] + [0.0] * 99

    observed = database.explain_population_query(
        SimpleNamespace(
            run_id=run_id,
            model_id=model_id,
            model_version=0,
            embedding_space_id=space_id,
        ),
        query_vector=vector,
        exclude_creator_id=creator_id,
        limit=50,
    )

    assert observed == plan
    explain_sql, parameters = cursor.queries[-1]
    assert "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" in explain_sql
    assert parameters["query"].startswith("[1,")
    assert parameters["exclude_creator_id"] == creator_id
    assert parameters["limit"] == 50
