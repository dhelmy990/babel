from __future__ import annotations

import threading
import time
from uuid import UUID

from babel_online.simulation.scheduler import (
    BoundedCreatorScheduler,
    ScheduledWork,
    ScheduledSession,
    deterministic_schedule,
    deterministic_waves,
)


RUN = UUID("00000000-0000-5000-8000-000000000001")


def creator(number: int) -> UUID:
    return UUID(f"00000000-0000-5000-8000-{number:012d}")


def work(creators, sessions=3):
    return [
        ScheduledWork(
            creator_id=value,
            creator_event_number=event,
            period="2026-06" if event < 2 else "2026-07",
            source_article_key=f"enwiki:{value.int % 1000 + event + 1}",
            root_babel_id=UUID(
                f"00000000-0000-5000-8000-{100 + slot * 1000 + event:012d}"
            ),
        )
        for event in range(sessions)
        for slot, value in enumerate(creators)
    ]


def test_schedule_is_replayable_and_preserves_creator_local_order() -> None:
    creators = [creator(1), creator(2), creator(3)]
    first = deterministic_schedule(RUN, work(creators))
    second = deterministic_schedule(RUN, work(creators))

    assert first == second
    assert [row.schedule_index for row in first] == list(range(9))
    for value in creators:
        assert [row.creator_event_number for row in first if row.creator_id == value] == [0, 1, 2]
    assert all(row.period in {"2026-06", "2026-07"} for row in first)
    assert all(row.source_article_key.startswith("enwiki:") for row in first)
    assert all(len(row.workload_sha256) == 64 for row in first)


def test_scheduler_bounds_global_and_per_creator_concurrency() -> None:
    schedule = deterministic_schedule(RUN, work([creator(1), creator(2), creator(3)]))
    lock = threading.Lock()
    active = 0
    maximum = 0
    active_creators: set[UUID] = set()
    completed: list[ScheduledSession] = []

    def execute(row: ScheduledSession) -> None:
        nonlocal active, maximum
        with lock:
            assert row.creator_id not in active_creators
            active_creators.add(row.creator_id)
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.005)
        with lock:
            active -= 1
            active_creators.remove(row.creator_id)
            completed.append(row)

    BoundedCreatorScheduler(concurrent_users=2).run(schedule, execute)

    assert maximum == 2
    assert {row.schedule_index for row in completed} == set(range(9))
    for value in {row.creator_id for row in completed}:
        assert [
            row.creator_event_number
            for row in sorted(completed, key=lambda item: item.schedule_index)
            if row.creator_id == value
        ] == [0, 1, 2]


def test_scheduler_dispatches_persisted_work_in_deterministic_waves() -> None:
    schedule = deterministic_schedule(
        RUN, work([creator(1), creator(2), creator(3)], sessions=2)
    )

    waves = deterministic_waves(schedule, concurrent_users=2)

    assert [[row.schedule_index for row in wave] for wave in waves] == [
        [0, 1], [2, 3], [4, 5]
    ]
    assert all(
        len({row.creator_id for row in wave}) == len(wave) for wave in waves
    )


def test_completion_timing_does_not_change_later_dispatch_order() -> None:
    schedule = deterministic_schedule(
        RUN, work([creator(1), creator(2), creator(3)], sessions=2)
    )

    def run_with_delays(reverse: bool) -> list[int]:
        def execute(row: ScheduledSession) -> None:
            delay_slot = 5 - row.schedule_index if reverse else row.schedule_index
            time.sleep(delay_slot * 0.001)

        dispatched = BoundedCreatorScheduler(concurrent_users=2).run(
            schedule, execute
        )
        return [row.schedule_index for row in dispatched]

    assert run_with_delays(False) == list(range(6))
    assert run_with_delays(True) == list(range(6))


def test_wave_boundary_runs_after_every_member_and_before_next_dispatch() -> None:
    schedule = deterministic_schedule(
        RUN, work([creator(1), creator(2), creator(3)], sessions=2)
    )
    lock = threading.Lock()
    events: list[tuple[str, int]] = []

    def execute(row: ScheduledSession) -> None:
        time.sleep((row.schedule_index % 2) * 0.002)
        with lock:
            events.append(("complete", row.schedule_index))

    def after_wave(wave: tuple[ScheduledSession, ...]) -> None:
        with lock:
            completed = {index for kind, index in events if kind == "complete"}
            assert {row.schedule_index for row in wave} <= completed
            events.append(("publish", wave[0].schedule_index))

    BoundedCreatorScheduler(concurrent_users=2).run(
        schedule, execute, after_wave=after_wave
    )

    first_publish = events.index(("publish", 0))
    assert {value for kind, value in events[:first_publish] if kind == "complete"} == {0, 1}
    assert [value for kind, value in events if kind == "publish"] == [0, 2, 4]


def test_ten_thousand_sessions_need_only_two_hundred_wave_publications() -> None:
    creators = [creator(index + 1) for index in range(50)]
    schedule = deterministic_schedule(RUN, work(creators, sessions=200))

    waves = deterministic_waves(schedule, concurrent_users=50)

    assert len(schedule) == 10_000
    assert len(waves) == 200
    assert all(len(wave) == 50 for wave in waves)
