from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from babel_online.runtime import performance_condition as condition_module
from babel_online.runtime.performance_condition import (
    LatencyTraceSink,
    RealWorkloadFreezer,
    VerifiedLiveIdentityLedger,
    build_live_condition_plan,
    execute_live_condition,
    _select_replay_subset,
)
from babel_online.runtime.performance_worker import PerformanceCondition
from babel_online.contracts import CandidateActionV1, FeedbackEventV2, RecommendationRequestV2
from babel_online.feedback import FeedbackRecord
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


def test_isolated_plan_uses_actual_affinity_cpu_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(condition_module.os, "sched_getaffinity", lambda _pid: {2, 4, 7, 9})

    plan = build_live_condition_plan(
        _condition("same_host_isolated", True, False),
        run_id=UUID(int=2),
        state_root=tmp_path,
        serving_port=8791,
        python_executable="/venv/bin/python",
        cpu_count=64,
    )

    serving = set(plan.resources["serving"].cpuAffinity)
    trainer = set(plan.resources["trainer"].cpuAffinity)
    assert serving | trainer == {2, 4, 7, 9}
    assert serving.isdisjoint(trainer)


def test_host_lifecycle_stops_partially_started_host():
    calls = []

    class Host:
        def start(self):
            calls.append("start")
            raise RuntimeError("partial startup")

        def stop(self):
            calls.append("stop")

    with pytest.raises(RuntimeError, match="partial startup"):
        with condition_module._host_lifecycle(Host(), lambda host: host.stop()):
            pytest.fail("partially started host must not enter the condition body")

    assert calls == ["start", "stop"]


def test_feedback_publisher_abort_is_safe_before_start():
    publisher = condition_module._OrderedFeedbackPublisher(
        {}, {}, SimpleNamespace(), SimpleNamespace()
    )

    publisher.abort()


def test_feedback_publisher_captures_ordered_records_and_exact_offset_ranges():
    request_ids = [uuid4(), uuid4(), uuid4()]
    creator = uuid4()
    events = [
        SimpleNamespace(requestId=request_id, eventId=uuid4(), creatorId=creator)
        for request_id in request_ids
    ]
    returned = iter(((0, 10), (0, 12), (1, 4)))
    published = []

    class Producer:
        def publish(self, *, key, event):
            published.append(event.requestId)
            partition, offset = next(returned)
            return FeedbackRecord(
                topic="babel.feedback.v1",
                partition=partition,
                offset=offset,
                key=key,
                event=event,
            )

    publisher = condition_module._OrderedFeedbackPublisher(
        dict(zip(request_ids, events, strict=True)),
        {request_id: index for index, request_id in enumerate(request_ids)},
        Producer(),
        SimpleNamespace(persist_feedback_edges=lambda _event: None),
    )
    rows = [
        SimpleNamespace(request=SimpleNamespace(requestId=value, creatorId=creator))
        for value in request_ids
    ]
    publisher.start()
    publisher.callback(rows[2], None, None)
    publisher.callback(rows[0], None, None)
    publisher.callback(rows[1], None, None)
    publisher.finish(3)

    assert published == request_ids
    assert [record.event.requestId for record in publisher.records] == request_ids
    assert publisher.offset_range_documents() == [
        {
            "topic": "babel.feedback.v1",
            "partition": 0,
            "startInclusive": 10,
            "endExclusive": 11,
        },
        {
            "topic": "babel.feedback.v1",
            "partition": 0,
            "startInclusive": 12,
            "endExclusive": 13,
        },
        {
            "topic": "babel.feedback.v1",
            "partition": 1,
            "startInclusive": 4,
            "endExclusive": 5,
        },
    ]


def test_split_host_rejects_stale_activation_receipt(tmp_path, monkeypatch):
    activation_dir = tmp_path / "activations"
    activation_dir.mkdir()
    (activation_dir / "receipt-v00000003.json").write_text(
        json.dumps({"modelId": str(UUID(int=3)), "modelVersion": 3})
    )
    stopped = []
    running = SimpleNamespace(
        graceful_stop_trainer=lambda **_values: stopped.append("trainer"),
        stop_serving=lambda: stopped.append("serving"),
    )
    host = condition_module._SplitProcessHost(
        SimpleNamespace(state_root=tmp_path / "topology"),
        activation_dir=activation_dir,
        starting_model_version=3,
    )
    host.running = running
    moments = iter((0.0, 31.0))
    monkeypatch.setattr(condition_module.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(condition_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="acknowledge model activation"):
        host.stop(wait_for_activation=True)

    assert stopped == ["trainer", "serving"]


def test_split_host_removes_stale_trainer_ready_marker(tmp_path, monkeypatch):
    run_id = UUID(int=41)
    plan = build_live_condition_plan(
        _condition("same_host_split", True, False),
        run_id=run_id,
        state_root=tmp_path / "topology",
        serving_port=8791,
        python_executable="/venv/bin/python",
        cpu_count=4,
    )
    ready_path = Path(plan.commands["trainer"].environment["BABEL_TRAINER_READY_PATH"])
    ready_path.parent.mkdir(parents=True)
    ready_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runId": str(UUID(int=99)),
                "consumerGroup": "stale",
                "readyAtNs": 1,
            }
        )
    )
    running = SimpleNamespace(
        serving_status=lambda: 200,
        process_alive=lambda role: True,
    )
    host = condition_module._SplitProcessHost(plan)
    host._supervisor = SimpleNamespace(
        launch=lambda **_values: running,
    )
    moments = iter((0.0, 181.0))
    monkeypatch.setattr(condition_module.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(condition_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="healthy"):
        host.start()

    assert not ready_path.exists()


def test_split_host_rejects_ready_marker_when_trainer_exited(tmp_path):
    run_id = UUID(int=42)
    plan = build_live_condition_plan(
        _condition("same_host_split", True, False),
        run_id=run_id,
        state_root=tmp_path / "topology",
        serving_port=8791,
        python_executable="/venv/bin/python",
        cpu_count=4,
    )
    ready_path = Path(plan.commands["trainer"].environment["BABEL_TRAINER_READY_PATH"])
    running = SimpleNamespace(
        serving_status=lambda: 200,
        process_alive=lambda role: role == "serving",
    )

    def launch(**_values):
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "runId": str(run_id),
                    "consumerGroup": "fresh",
                    "readyAtNs": (1 << 63) - 1,
                }
            )
        )
        return running

    host = condition_module._SplitProcessHost(plan)
    host._supervisor = SimpleNamespace(launch=launch)

    with pytest.raises(RuntimeError, match="trainer exited"):
        host.start()


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
        performance_runtime_health=lambda run_id: {
            "kafka_lag": 0,
            "trainer_version": 1,
            "serving_version": 0,
        },
    )
    host = SimpleNamespace(
        services={"serving": 1},
        placement={"actualTopology": "same_process"},
        start=lambda: None,
        stop=lambda: None,
    )
    published = []
    def publish(**values):
        published.append(values)
        return FeedbackRecord(
            topic="babel.feedback.v1",
            partition=0,
            offset=17,
            key=values["key"],
            event=values["event"],
        )

    producer = SimpleNamespace(publish=publish, close=lambda: None)
    progress = []

    async def runner(manifest, spec, replay, universe, **options):
        assert replay.rows[0].request.runId == condition_run
        assert spec.requestCorpusSha256 == replay.sha256
        assert manifest.timeoutSeconds == 120.0
        options["success_callback"](
            replay.rows[0], SimpleNamespace(), SimpleNamespace()
        )
        options["progress_callback"](
            SimpleNamespace(
                phase="draining",
                total=1,
                submitted=1,
                completed=1,
                errors=0,
                in_flight=0,
                elapsed_seconds=0.1,
                recent_rate=10.0,
            )
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
        progress_sink=progress.append,
    )

    assert evidence["requestCount"] == 1
    assert evidence["p95Ms"] == 2.0
    assert len(published) == 1
    assert published[0]["event"].runId == condition_run
    assert published[0]["event"].candidateActions == feedback.candidateActions
    assert evidence["rawEvidence"]["feedbackKafka"] == {
        "recordCount": 1,
        "records": [
            {
                "topic": "babel.feedback.v1",
                "partition": 0,
                "offset": 17,
                "key": str(creator),
                "eventId": str(feedback.eventId),
                "requestId": str(feedback.requestId),
            }
        ],
        "offsetRanges": [
            {
                "topic": "babel.feedback.v1",
                "partition": 0,
                "startInclusive": 17,
                "endExclusive": 18,
            }
        ],
        "finalTrainerState": {
            "available": True,
            "kafkaLag": 0,
            "trainerVersion": 1,
            "servingVersion": 0,
        },
    }
    assert progress[-1].submitted == progress[-1].completed == 1


def test_representative_replay_subset_is_bounded_and_request_aligned(tmp_path: Path):
    root = tmp_path / "workload"
    root.mkdir()
    requests = [
        {"scheduleOffsetNs": index, "request": {"requestId": str(UUID(int=index + 1))}}
        for index in range(4)
    ]
    feedback = [
        {"requestId": str(UUID(int=index + 1)), "value": index}
        for index in range(4)
    ]
    (root / "requests.template.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in requests), encoding="utf-8"
    )
    (root / "feedback.template.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in feedback), encoding="utf-8"
    )

    source_snapshot = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()
    }
    selected_path, selected_feedback, source_count = _select_replay_subset(
        root, tmp_path / "condition-replay", 2
    )

    assert source_count == 4
    assert len(selected_path.read_text().splitlines()) == 2
    assert [row["requestId"] for row in selected_feedback] == [
        str(UUID(int=1)),
        str(UUID(int=2)),
    ]
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()
    } == source_snapshot
    assert selected_path.parent == tmp_path / "condition-replay"


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
            client = self.values["client_factory"]()
            assert client.timeout_seconds == 120.0
            client.close()
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


def test_real_workload_freezer_reuses_declared_source_without_capture(
    tmp_path: Path, monkeypatch
):
    identity = tuple(str(index) * 64 for index in range(6))
    source = tmp_path / "source-workload"
    source.mkdir()
    (source / "manifest.json").write_text("{}\n", encoding="utf-8")
    loaded = SimpleNamespace(path=source, identity=identity)
    monkeypatch.setattr(
        "babel_benchmark.workload.load_frozen_workload", lambda path: loaded
    )

    class Database:
        def __getattr__(self, name):
            raise AssertionError(f"reuse must not touch database population: {name}")

    freezer = RealWorkloadFreezer(
        database=Database(), bundle=object(), output_root=tmp_path / "output"
    )
    result = freezer(
        SimpleNamespace(
            id=uuid4(),
            evidence_scope="representative_same_host_split",
            source_workload_path=str(source),
            source_workload_identity=identity,
        ),
        object(),
        tmp_path / "population",
        lambda: False,
    )

    assert result.path == source
    assert result.identity == identity


def test_real_workload_freezer_rejects_reused_workload_identity_drift(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source-workload"
    source.mkdir()
    monkeypatch.setattr(
        "babel_benchmark.workload.load_frozen_workload",
        lambda _path: SimpleNamespace(path=source, identity=("a" * 64,) * 6),
    )
    freezer = RealWorkloadFreezer(
        database=object(), bundle=object(), output_root=tmp_path / "output"
    )

    with pytest.raises(ValueError, match="identity differs"):
        freezer(
            SimpleNamespace(
                id=uuid4(),
                evidence_scope="representative_same_host_split",
                source_workload_path=str(source),
                source_workload_identity=("b" * 64,) * 6,
            ),
            object(),
            tmp_path / "population",
            lambda: False,
        )
