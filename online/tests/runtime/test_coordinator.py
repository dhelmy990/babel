from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from babel_online.observable import CreatedBabel
from babel_online.runtime.coordinator import StandaloneCoordinator
from babel_online.simulation.decisions import deterministic_draw
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule


def test_simulator_only_coordinator_posts_recommendations_and_publishes_feedback() -> None:
    run_id, creator_id = uuid4(), uuid4()
    root = CreatedBabel(
        babelId=uuid4(), runId=run_id, creatorId=creator_id,
        sourceArticleKey="enwiki:1", title="Root", text="Root lead", createdAtNs=1,
    )
    target = CreatedBabel(
        babelId=uuid4(), runId=run_id, creatorId=uuid4(),
        sourceArticleKey="enwiki:2", title="Target", text="Target lead", createdAtNs=2,
    )
    schedule = deterministic_schedule(run_id, [ScheduledWork(
        creator_id=creator_id,
        creator_event_number=0,
        period="2026-06",
        source_article_key=root.sourceArticleKey,
        root_babel_id=root.babelId,
    )])
    requests, feedback, rolls, edges = [], [], [], []

    class Client:
        def recommend(self, request):
            requests.append(request)
            candidates = [] if request.traversalDepth == 1 else [SimpleNamespace(
                babelId=target.babelId,
                sourceArticleKey=target.sourceArticleKey,
                rank=1,
                modelScore=0.9,
            )]
            return SimpleNamespace(
                requestId=request.requestId,
                runId=run_id,
                modelId=uuid4(),
                modelVersion=2,
                embeddingSpaceId=uuid4(),
                retrievalBackend="pgvector",
                sourceVectorOrigin="pgvector_load" if request.traversalDepth else "qwen_encode",
                candidates=candidates,
            )

        def close(self):
            pass

    class Producer:
        def publish(self, *, key, event):
            feedback.append(event)
            return SimpleNamespace(offset=len(feedback) - 1)

        def close(self):
            pass

    metrics = []
    database = SimpleNamespace(
        stop_requested=lambda _run_id: False,
        persist_feedback_edges=lambda event: edges.append(event),
        persist_traversal_rolls=lambda _run, _session, evidence: rolls.extend(evidence),
        append_activity=lambda _activity: None,
        update_metrics=lambda *_args, **kwargs: metrics.append(kwargs),
    )
    config = SimpleNamespace(
        runId=run_id,
        runSeed=7,
        recommendationK=10,
        recommendationStartProbability=1.0,
        continuationProbability=1.0,
        maximumTraversalDepth=2,
        maximumRequestsPerTraversal=10,
        concurrentUsers=1,
    )
    coordinator = StandaloneCoordinator(
        config=config,
        database=database,
        schedule=schedule,
        babels={root.babelId: root, target.babelId: target},
        hidden_edges={"2026-06": {(root.sourceArticleKey, target.sourceArticleKey)}},
        producer=Producer(),
        client_factory=Client,
        stop_event=SimpleNamespace(is_set=lambda: False),
        decide=lambda *_args, **_kwargs: "include",
    )

    coordinator.run()

    assert len(requests) == 2
    assert len(feedback) == 2
    assert edges == feedback
    assert rolls[0].kind == "start"
    assert any(row.kind == "continuation" for row in rolls)
    assert metrics[-1]["kafka_offset"] == 2
    assert metrics[-1]["event_rate"] > 0


def test_decision_draw_identity_matches_task8_replay_contract(monkeypatch) -> None:
    run_id, creator_id = uuid4(), uuid4()
    root_id, target_id = uuid4(), uuid4()
    scheduled = deterministic_schedule(run_id, [ScheduledWork(
        creator_id=creator_id,
        creator_event_number=0,
        period="2026-06",
        source_article_key="enwiki:1",
        root_babel_id=root_id,
    )])[0]
    calls = []

    def capture(*identity):
        calls.append(identity)
        return 0.1

    monkeypatch.setattr("babel_online.runtime.coordinator.deterministic_draw", capture)
    coordinator = StandaloneCoordinator(
        config=SimpleNamespace(runId=run_id, runSeed=17, concurrentUsers=1),
        database=SimpleNamespace(),
        schedule=[scheduled],
        babels={},
        hidden_edges={"2026-06": {("enwiki:1", "enwiki:2")}},
        producer=SimpleNamespace(),
        client_factory=lambda: None,
        stop_event=SimpleNamespace(),
    )
    source = SimpleNamespace(babel_id=root_id, source_article_key="enwiki:1")
    candidate = SimpleNamespace(
        babelId=target_id, sourceArticleKey="enwiki:2", rank=3
    )

    coordinator._default_decision(scheduled, source, 2, candidate, "2026-06")

    expected_tail = (
        run_id,
        creator_id,
        scheduled.traversal_session_id,
        root_id,
        2,
        target_id,
        "enwiki:2",
        3,
    )
    assert calls == [
        (17, "preference", *expected_tail),
        (17, "action", *expected_tail),
    ]


def test_creator_history_uses_prior_persisted_schedule_roots() -> None:
    run_id, creator_id = uuid4(), uuid4()
    roots = [
        CreatedBabel(
            babelId=uuid4(), runId=run_id, creatorId=creator_id,
            sourceArticleKey=f"enwiki:{index}", title=f"Root {index}",
            text="Lead", createdAtNs=index,
        )
        for index in (1, 2)
    ]
    schedule = deterministic_schedule(run_id, [
        ScheduledWork(
            creator_id=creator_id,
            creator_event_number=index,
            period="2026-06",
            source_article_key=root.sourceArticleKey,
            root_babel_id=root.babelId,
        )
        for index, root in enumerate(roots)
    ])
    requests = []

    class Client:
        def recommend(self, request):
            requests.append(request)
            return SimpleNamespace(
                requestId=request.requestId, runId=run_id, modelId=uuid4(),
                modelVersion=0, embeddingSpaceId=uuid4(),
                retrievalBackend="pgvector", sourceVectorOrigin="qwen_encode",
                candidates=[],
            )

        def close(self):
            pass

    class Producer:
        def publish(self, **_kwargs):
            return SimpleNamespace(offset=len(requests) - 1)

        def close(self):
            pass

    coordinator = StandaloneCoordinator(
        config=SimpleNamespace(
            runId=run_id, runSeed=7, recommendationK=10,
            recommendationStartProbability=1.0, continuationProbability=0.0,
            maximumTraversalDepth=2, maximumRequestsPerTraversal=10,
            concurrentUsers=1,
        ),
        database=SimpleNamespace(
            stop_requested=lambda _run_id: False,
            persist_feedback_edges=lambda _event: None,
            persist_traversal_rolls=lambda *_args: None,
            append_activity=lambda _activity: None,
            update_metrics=lambda *_args, **_kwargs: None,
        ),
        schedule=schedule,
        babels={row.babelId: row for row in roots},
        hidden_edges={"2026-06": set()},
        producer=Producer(),
        client_factory=Client,
        stop_event=SimpleNamespace(is_set=lambda: False),
    )

    coordinator.run()

    assert requests[0].historyBabelIds == []
    assert requests[1].historyBabelIds == [roots[0].babelId]
