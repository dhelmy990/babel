from __future__ import annotations

from uuid import UUID

from babel_online.simulation.walk import (
    IncludedWalkTarget,
    RecommendationWalk,
    WalkExpansion,
    WalkNode,
)


RUN = UUID("00000000-0000-5000-8000-000000000001")
CREATOR = UUID("00000000-0000-5000-8000-000000000002")
SESSION = UUID("00000000-0000-5000-8000-000000000003")


def node(number: int) -> WalkNode:
    return WalkNode(
        babel_id=UUID(f"00000000-0000-5000-8000-{number:012d}"),
        source_article_key=f"enwiki:{number}",
    )


def test_walk_is_breadth_first_requests_only_depth_zero_and_one() -> None:
    root, first, second, leaf_a, leaf_b = [node(value) for value in range(10, 15)]
    calls: list[tuple[WalkNode, int, UUID | None]] = []

    def recommend(source, _session, parent_request_id, depth):
        calls.append((source, depth, parent_request_id))
        request_id = UUID(f"00000000-0000-5000-8000-{100 + len(calls):012d}")
        targets = {
            root: (IncludedWalkTarget(first, 1), IncludedWalkTarget(second, 2)),
            first: (IncludedWalkTarget(leaf_a, 1),),
            second: (IncludedWalkTarget(leaf_b, 1),),
        }.get(source, ())
        return WalkExpansion(request_id=request_id, included=targets)

    trace = RecommendationWalk(
        start_probability=0.4,
        continuation_probability=0.4,
        draw=lambda *_identity: 0.1,
        max_depth=2,
        max_requests=10,
    ).run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=root,
        recommend=recommend,
    )

    assert [(value.babel_id, depth) for value, depth, _parent in calls] == [
        (root.babel_id, 0),
        (first.babel_id, 1),
        (second.babel_id, 1),
    ]
    assert [edge.target_depth for edge in trace.edges] == [1, 1, 2, 2]
    assert all(depth in {0, 1} for _source, depth, _parent in calls)
    assert calls[1][2] == trace.requests[0].request_id
    assert calls[2][2] == trace.requests[0].request_id


def test_start_and_each_continuation_use_separate_rolls_and_cap_actual_posts() -> None:
    root = node(1)
    targets = tuple(IncludedWalkTarget(node(value), value) for value in range(2, 30))
    identities = []
    calls = []

    def draw(*identity):
        identities.append(identity)
        return 0.1

    def recommend(source, _session, _parent, depth):
        calls.append((source, depth))
        return WalkExpansion(
            request_id=UUID(f"00000000-0000-5000-8000-{200 + len(calls):012d}"),
            included=targets if depth == 0 else (),
        )

    trace = RecommendationWalk(
        start_probability=0.4,
        continuation_probability=0.4,
        draw=draw,
        max_requests=10,
    ).run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=root,
        recommend=recommend,
    )

    assert identities[0][0] == "start"
    assert all(identity[0] == "continuation" for identity in identities[1:])
    assert len(identities) == 1 + len(targets)
    assert len(calls) == trace.request_count == 10
    assert len({source.babel_id for source, _depth in calls}) == 10


def test_failed_start_roll_makes_no_post_and_replay_is_identical() -> None:
    calls = []
    walk = RecommendationWalk(
        start_probability=0.4,
        continuation_probability=0.4,
        draw=lambda *_identity: 0.9,
    )
    first = walk.run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=node(1),
        recommend=lambda *args: calls.append(args),
    )
    second = walk.run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=node(1),
        recommend=lambda *args: calls.append(args),
    )

    assert first == second
    assert first.request_count == 0
    assert calls == []
    assert len(first.rolls) == 1
    assert first.rolls[0].kind == "start"
    assert first.rolls[0].draw_value == 0.9
    assert first.rolls[0].probability == 0.4
    assert first.rolls[0].roll_succeeded is False
    assert first.rolls[0].outcome == "start_skipped"


def test_walk_records_each_continuation_roll_and_why_it_did_not_expand() -> None:
    root, queued, skipped, duplicate = node(1), node(2), node(3), node(2)
    draws = iter((0.1, 0.1, 0.8, 0.1, 0.1))
    calls = []

    def recommend(source, _session, _parent, _depth):
        calls.append(source)
        return WalkExpansion(
            request_id=UUID(
                f"00000000-0000-5000-8000-{400 + len(calls):012d}"
            ),
            included=(
                IncludedWalkTarget(queued, 1),
                IncludedWalkTarget(skipped, 2),
                IncludedWalkTarget(duplicate, 3),
            ) if source == root else (IncludedWalkTarget(node(4), 1),),
        )

    trace = RecommendationWalk(
        start_probability=0.4,
        continuation_probability=0.4,
        draw=lambda *_identity: next(draws),
    ).run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=root,
        recommend=recommend,
    )

    assert [roll.draw_index for roll in trace.rolls] == list(range(5))
    assert [roll.kind for roll in trace.rolls] == [
        "start", "continuation", "continuation", "continuation", "continuation"
    ]
    assert [roll.outcome for roll in trace.rolls] == [
        "started", "enqueued", "continuation_skipped", "already_visited", "depth_limit"
    ]
    assert trace.rolls[1].target_babel_id == queued.babel_id
    assert trace.rolls[1].target_rank == 1
    assert trace.rolls[1].source_depth == 0
    assert trace.rolls[-1].source_depth == 1


def test_start_and_continuation_probabilities_are_independent_settings() -> None:
    root, target = node(1), node(2)
    draws = iter((0.1, 0.5))
    calls = []

    def recommend(source, _session, _parent, _depth):
        calls.append(source)
        return WalkExpansion(
            request_id=UUID(f"00000000-0000-5000-8000-{300 + len(calls):012d}"),
            included=(IncludedWalkTarget(target, 1),) if source == root else (),
        )

    trace = RecommendationWalk(
        start_probability=0.2,
        continuation_probability=0.4,
        draw=lambda *_identity: next(draws),
    ).run(
        run_id=RUN,
        creator_id=CREATOR,
        session_id=SESSION,
        root=root,
        recommend=recommend,
    )

    assert trace.started is True
    assert trace.request_count == 1
