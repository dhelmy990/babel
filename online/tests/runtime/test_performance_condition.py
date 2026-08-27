from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from babel_online.runtime.performance_condition import (
    LatencyTraceSink,
    RealWorkloadFreezer,
    VerifiedLiveIdentityLedger,
    build_live_condition_plan,
    execute_live_condition,
)
from babel_online.runtime.performance_worker import PerformanceCondition
from babel_online.contracts import CandidateActionV1, FeedbackEventV2, RecommendationRequestV2
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule
from babel_online.simulation.walk import WalkRollEvidence
from babel_online.observable import CreatedBabel
from babel_benchmark.workload import WorkloadTraceCollector, freeze_workload


def _condition(topology, training, activation):
    return PerformanceCondition(
        id=UUID(int=1),
        condition_index=1,
        topology=topology,
        training_enabled=training,
        activation_enabled=activation,
        run_id=None,
        status="pending",
    )


def test_split_plan_uses_real_roles_and_explicit_activation_flag(tmp_path):
    plan = build_live_condition_plan(
        _condition("same_host_split", True, False),
        run_id=UUID(int=2),
        state_root=tmp_path,
        serving_port=8791,
        python_executable="/venv/bin/python",
        cpu_count=8,
    )

    assert plan.topology.value == "same_host_split"
    assert "babel-recommendation-server" in plan.commands["serving"].argv[0]
    assert plan.commands["trainer"].argv[-2:] == (
        "--activation-enabled",
        "false",
    )
    assert plan.commands["trainer"].environment[
        "BABEL_TRAINER_READY_PATH"
    ].endswith("trainer-ready.json")
    assert plan.resources["serving"].cpuAffinity == ()
    assert plan.resources["trainer"].cpuAffinity == ()


def test_isolated_plan_assigns_disjoint_cpu_sets_and_serving_only_idle_role(tmp_path):
    plan = build_live_condition_plan(
        _condition("same_host_isolated", False, False),
        run_id=UUID(int=2),
        state_root=tmp_path,
        serving_port=8791,
        python_executable="/venv/bin/python",
        cpu_count=8,
    )

    assert plan.topology.value == "same_host_isolated"
    assert "time.sleep" in plan.commands["trainer"].argv[-1]
    serving = set(plan.resources["serving"].cpuAffinity)
    trainer = set(plan.resources["trainer"].cpuAffinity)
    assert serving and trainer and serving.isdisjoint(trainer)


def test_latency_sink_records_real_response_boundary_and_reports_nearest_rank_p95():
    sink = LatencyTraceSink()
    request = type("Request", (), {"requestId": UUID(int=1), "runId": UUID(int=2)})()
    response = type("Response", (), {"requestId": UUID(int=1), "runId": UUID(int=2)})()
    for latency in range(1, 21):
        sink.record_response(request, response, latency * 1_000_000)

    assert sink.request_count == 20
    assert sink.p95_ms == 19.0
    with pytest.raises(ValueError, match="identity"):
        sink.record_response(
            request,
            type("Response", (), {"requestId": UUID(int=9), "runId": UUID(int=2)})(),
            1,
        )


def test_live_identity_ledger_accepts_only_database_verified_lineage():
    calls = []
    database = type(
        "Database",
        (),
        {
            "verify_live_serving_identity": lambda self, **values: calls.append(values)
            or values["model_version"] in {0, 3}
        },
    )()
    ledger = VerifiedLiveIdentityLedger(
        database=database,
        run_id=UUID(int=1),
        starting_model_id=UUID(int=2),
        embedding_space_id=UUID(int=3),
        initial_state=SimpleNamespace(
            run_id=UUID(int=1),
            model_id=UUID(int=2),
            model_version=0,
            embedding_space_id=UUID(int=3),
            pgvector_snapshot_sha256="0" * 64,
            backend_snapshot_sha256="0" * 64,
        ),
    )
    response = type(
        "Response",
        (),
        {
            "modelId": UUID(int=4),
            "modelVersion": 3,
            "embeddingSpaceId": UUID(int=3),
            "pgvectorSnapshotSha256": "a" * 64,
            "backendSnapshotSha256": "a" * 64,
        },
    )()
    ledger.validate(response)

    assert ledger.observed == (
        (str(UUID(int=4)), 3, "a" * 64, "a" * 64),
    )
    response.modelVersion = 7
    with pytest.raises(ValueError, match="lineage"):
        ledger.validate(response)


def test_live_identity_ledger_accepts_delayed_pre_activation_response():
    original = SimpleNamespace(
        run_id=UUID(int=1),
        model_id=UUID(int=2),
        model_version=0,
        embedding_space_id=UUID(int=3),
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="a" * 64,
    )
    child = SimpleNamespace(
        modelId=UUID(int=4),
        modelVersion=1,
        embeddingSpaceId=UUID(int=3),
        pgvectorSnapshotSha256="b" * 64,
        backendSnapshotSha256="b" * 64,
    )
    active = {
        "identity": (
            original.model_id,
            original.model_version,
            original.pgvector_snapshot_sha256,
            original.backend_snapshot_sha256,
        )
    }

    class Database:
        def verify_live_serving_identity(self, **values):
            return (
                values["model_id"],
                values["model_version"],
                values["pgvector_sha256"],
                values["backend_sha256"],
            ) == active["identity"]

    ledger = VerifiedLiveIdentityLedger(
        database=Database(),
        run_id=original.run_id,
        starting_model_id=original.model_id,
        embedding_space_id=original.embedding_space_id,
        initial_state=original,
    )
    original_response = SimpleNamespace(
        modelId=original.model_id,
        modelVersion=original.model_version,
        embeddingSpaceId=original.embedding_space_id,
        pgvectorSnapshotSha256=original.pgvector_snapshot_sha256,
        backendSnapshotSha256=original.backend_snapshot_sha256,
    )

    # The original request is in flight. Activation commits, and the child
    # response reaches the validator before the older original response.
    active["identity"] = (
        child.modelId,
        child.modelVersion,
        child.pgvectorSnapshotSha256,
        child.backendSnapshotSha256,
    )
    ledger.validate(child)
    ledger.validate(original_response)

    unactivated = SimpleNamespace(
        modelId=UUID(int=5),
        modelVersion=2,
        embeddingSpaceId=original.embedding_space_id,
        pgvectorSnapshotSha256="c" * 64,
        backendSnapshotSha256="c" * 64,
    )
    with pytest.raises(ValueError, match="lineage"):
        ledger.validate(unactivated)

    assert ledger.observed == (
        (str(child.modelId), 1, "b" * 64, "b" * 64),
        (str(original.model_id), 0, "a" * 64, "a" * 64),
    )


def test_live_condition_rebinds_workload_and_publishes_exact_feedback(
    tmp_path: Path, monkeypatch
):
    source_run, condition_run = uuid4(), uuid4()
    creator, other_creator, root, candidate = uuid4(), uuid4(), uuid4(), uuid4()
    schedule = deterministic_schedule(
        source_run,
        [ScheduledWork(creator, 0, "2026-06", "enwiki:1", root)],
    )
    session = schedule[0]
    request = RecommendationRequestV2(
        schemaVersion=2,
        requestId=uuid4(),
        runId=source_run,
        creatorId=creator,
        sourceBabelId=root,
        sourceArticleKey="enwiki:1",
        traversalSessionId=session.traversal_session_id,
        parentRequestId=None,
        traversalDepth=0,
        title="Root",
        text="Root lead",
        historyBabelIds=[],
        candidateCount=10,
    )
    feedback = FeedbackEventV2(
        schemaVersion=2,
        eventId=uuid4(),
        requestId=request.requestId,
        runId=source_run,
        creatorId=creator,
        sourceBabelId=root,
        sourceArticleKey="enwiki:1",
        traversalSessionId=session.traversal_session_id,
        parentRequestId=None,
        traversalDepth=0,
        modelId=UUID(int=20),
        modelVersion=0,
        embeddingSpaceId=UUID(int=21),
        retrievalBackend="pgvector",
        sourceVectorOrigin="qwen_encode",
        candidateActions=[
            CandidateActionV1(
                babelId=candidate,
                sourceArticleKey="enwiki:2",
                rank=1,
                modelScore=0.8,
                action="include",
            )
        ],
        occurredAtNs=123,
    )
    collector = WorkloadTraceCollector(schedule=schedule, target_rps=5.0)
    collector.record_request(request)
    collector.record_feedback(feedback)
    collector.record_rolls(
        session.traversal_session_id,
        (
            WalkRollEvidence(0, "start", root, None, None, 0, 0.1, 0.4, True, "started"),
            WalkRollEvidence(
                1,
                "continuation",
                root,
                candidate,
                1,
                0,
                0.8,
                0.4,
                False,
                "continuation_skipped",
            ),
        ),
    )
    frozen = freeze_workload(collector, tmp_path / "frozen")

    def write_universe(_database, run_id, path):
        path.write_text(
            json.dumps(
                {
                    "babelId": str(candidate),
                    "runId": str(run_id),
                    "creatorId": str(other_creator),
                    "sourceArticleKey": "enwiki:2",
                    "createdBySyntheticCreator": True,
                    "createdInRun": True,
                }
            )
            + "\n"
        )

    monkeypatch.setattr(
        "babel_online.runtime.performance_condition._write_candidate_universe",
        write_universe,
    )
    active = SimpleNamespace(
        model_id=UUID(int=20),
        model_version=0,
        embedding_space_id=UUID(int=21),
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="a" * 64,
    )
    database = SimpleNamespace(
        load_active_embedding_state=lambda run_id: active,
        load_run=lambda run_id: SimpleNamespace(
            config=SimpleNamespace(startingModelId=UUID(int=20))
        ),
        persist_feedback_edges=lambda event: None,
    )
    host = SimpleNamespace(
        services={"serving": 1},
        placement={"actualTopology": "same_process"},
        start=lambda: None,
        stop=lambda: None,
    )
    published = []
    producer = SimpleNamespace(
        publish=lambda **values: published.append(values), close=lambda: None
    )

    async def runner(manifest, spec, replay, universe, **options):
        assert replay.rows[0].request.runId == condition_run
        assert spec.requestCorpusSha256 == replay.sha256
        options["success_callback"](
            replay.rows[0], SimpleNamespace(), SimpleNamespace()
        )
        measurement = SimpleNamespace(
            isWarmup=False,
            outcome="success",
            clientTotalNs=2_000_000,
            model_dump=lambda **_: {"outcome": "success"},
        )
        return SimpleNamespace(measurements=(measurement,), resources=())

    evidence = execute_live_condition(
        database=database,
        trial=SimpleNamespace(
            starting_model_id=UUID(int=20),
            target_rps=5.0,
            concurrent_users=2,
            warmup_seconds=0,
        ),
        condition=_condition("same_process", True, False),
        run_id=condition_run,
        frozen_workload_path=frozen.path,
        evidence_path=tmp_path / "evidence.json",
        host_factory=lambda **_: host,
        transport_factory=lambda *_: SimpleNamespace(),
        producer_factory=lambda *_: producer,
        concurrent_runner=runner,
    )

    assert evidence["requestCount"] == 1
    assert evidence["p95Ms"] == 2.0
    assert len(published) == 1
    assert published[0]["event"].runId == condition_run
    assert published[0]["event"].candidateActions == feedback.candidateActions


def test_real_workload_freezer_runs_reference_host_and_coordinator(tmp_path: Path):
    experiment_id, creator, root, candidate = uuid4(), uuid4(), uuid4(), uuid4()
    calls = []

    class Database:
        def create_condition_run(self, trial, condition, run_id):
            calls.append(("create", run_id))

        def clone_performance_population(self, trial, condition, run_id):
            calls.append(("clone", run_id))

        def load_run(self, run_id):
            return SimpleNamespace(
                config=SimpleNamespace(
                    runId=run_id,
                    environmentSequence=["2026-06", "2026-07"],
                    concurrentUsers=1,
                    recommendationStartProbability=0.4,
                    continuationProbability=0.4,
                    maximumTraversalDepth=2,
                    maximumRequestsPerTraversal=10,
                    recommendationK=10,
                    runSeed=7,
                )
            )

        def load_work_schedule(self, run_id):
            return deterministic_schedule(
                run_id,
                [ScheduledWork(creator, 0, "2026-06", "enwiki:1", root)],
            )

        def created_babels(self, run_id):
            return [
                CreatedBabel(
                    babelId=root,
                    runId=run_id,
                    creatorId=creator,
                    sourceArticleKey="enwiki:1",
                    title="Root",
                    text="Root lead",
                    createdAtNs=1,
                )
            ]

        def transition(self, run_id, status, failure=None):
            calls.append(("transition", status))

    class Host:
        def start(self):
            calls.append(("host", "start"))

        def stop(self, **_values):
            calls.append(("host", "stop"))

    class Coordinator:
        def __init__(self, **values):
            calls.append(("coordinator", "created"))
            self.values = values

        def run(self):
            sink = self.values["trace_sink"]
            scheduled = self.values["schedule"][0]
            request = RecommendationRequestV2(
                schemaVersion=2,
                requestId=uuid4(),
                runId=scheduled.run_id,
                creatorId=creator,
                sourceBabelId=root,
                sourceArticleKey="enwiki:1",
                traversalSessionId=scheduled.traversal_session_id,
                parentRequestId=None,
                traversalDepth=0,
                title="Root",
                text="Root lead",
                historyBabelIds=[],
                candidateCount=10,
            )
            action = CandidateActionV1(
                babelId=candidate,
                sourceArticleKey="enwiki:2",
                rank=1,
                modelScore=0.5,
                action="include",
            )
            sink.record_request(request)
            sink.record_feedback(
                FeedbackEventV2(
                    schemaVersion=2,
                    eventId=uuid4(),
                    requestId=request.requestId,
                    runId=scheduled.run_id,
                    creatorId=creator,
                    sourceBabelId=root,
                    sourceArticleKey="enwiki:1",
                    traversalSessionId=scheduled.traversal_session_id,
                    parentRequestId=None,
                    traversalDepth=0,
                    modelId=UUID(int=20),
                    modelVersion=0,
                    embeddingSpaceId=UUID(int=21),
                    retrievalBackend="pgvector",
                    sourceVectorOrigin="qwen_encode",
                    candidateActions=[action],
                    occurredAtNs=1,
                )
            )
            sink.record_rolls(
                scheduled.traversal_session_id,
                (
                    WalkRollEvidence(
                        0, "start", root, None, None, 0, 0.1, 0.4, True, "started"
                    ),
                    WalkRollEvidence(
                        1,
                        "continuation",
                        root,
                        candidate,
                        1,
                        0,
                        0.8,
                        0.4,
                        False,
                        "continuation_skipped",
                    ),
                ),
            )

    freezer = RealWorkloadFreezer(
        database=Database(),
        bundle=SimpleNamespace(
            configs={
                "simulator_2026_06_hidden": (),
                "simulator_2026_07_hidden": (),
            }
        ),
        output_root=tmp_path,
        host_factory=lambda _plan: Host(),
        coordinator_factory=Coordinator,
        producer_factory=lambda _bootstrap: SimpleNamespace(),
    )
    result = freezer(
        SimpleNamespace(
            id=experiment_id,
            warmup_seconds=0,
            duration_seconds=1,
            target_rps=1.0,
            recommendation_start_probability=0.4,
        ),
        object(),
        tmp_path / "population",
        lambda: False,
    )

    assert (result.path / "manifest.json").is_file()
    assert calls[:4] == [
        ("create", calls[0][1]),
        ("clone", calls[0][1]),
        ("host", "start"),
        ("coordinator", "created"),
    ]
    assert calls[-2:] == [("transition", "completed"), ("host", "stop")]


def test_real_workload_freezer_honors_dashboard_stop_after_boundary(tmp_path: Path):
    creator, root = uuid4(), uuid4()
    stopped = {"value": False}
    transitions = []

    class Database:
        def create_condition_run(self, *_args):
            pass

        def clone_performance_population(self, *_args):
            pass

        def load_run(self, run_id):
            return SimpleNamespace(
                config=SimpleNamespace(
                    runId=run_id,
                    environmentSequence=["2026-06", "2026-07"],
                )
            )

        def load_work_schedule(self, run_id):
            return deterministic_schedule(
                run_id,
                [ScheduledWork(creator, 0, "2026-06", "enwiki:1", root)],
            )

        def created_babels(self, run_id):
            return [
                CreatedBabel(
                    babelId=root,
                    runId=run_id,
                    creatorId=creator,
                    sourceArticleKey="enwiki:1",
                    title="Root",
                    text="Lead",
                    createdAtNs=1,
                )
            ]

        def transition(self, _run_id, status, **_values):
            transitions.append(status)

    class Coordinator:
        def __init__(self, **values):
            self.stop_event = values["stop_event"]

        def run(self):
            assert self.stop_event.is_set() is False
            stopped["value"] = True

    host = SimpleNamespace(
        start=lambda: None,
        stop=lambda **_values: transitions.append("host_stopped"),
    )
    producer = SimpleNamespace(close=lambda: transitions.append("producer_closed"))
    freezer = RealWorkloadFreezer(
        database=Database(),
        bundle=SimpleNamespace(
            configs={
                "simulator_2026_06_hidden": (),
                "simulator_2026_07_hidden": (),
            }
        ),
        output_root=tmp_path,
        host_factory=lambda _plan: host,
        coordinator_factory=Coordinator,
        producer_factory=lambda _bootstrap: producer,
    )

    with pytest.raises(InterruptedError, match="complete traversal boundary"):
        freezer(
            SimpleNamespace(
                id=uuid4(),
                warmup_seconds=0,
                duration_seconds=1,
                target_rps=1.0,
                recommendation_start_probability=0.4,
            ),
            object(),
            tmp_path / "population",
            lambda: stopped["value"],
        )

    assert transitions == ["interrupted", "producer_closed", "host_stopped"]
