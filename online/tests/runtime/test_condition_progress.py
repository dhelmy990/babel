from __future__ import annotations

import threading
from types import SimpleNamespace
from uuid import UUID

from babel_online.runtime.condition_progress import PersistedConditionProgress


def _snapshot(**changes):
    values = {
        "phase": "scheduled",
        "total": 750,
        "submitted": 12,
        "completed": 8,
        "errors": 1,
        "in_flight": 4,
        "elapsed_seconds": 2.5,
        "recent_rate": 3.2,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_condition_progress_persists_latest_snapshot_at_context_close() -> None:
    calls = []
    database = SimpleNamespace(
        append_performance_progress=lambda experiment_id, **values: calls.append(
            (experiment_id, values)
        )
    )
    experiment_id = UUID(int=1)

    with PersistedConditionProgress(
        database=database,
        experiment_id=experiment_id,
        condition_index=4,
        condition_count=6,
        interval_seconds=60,
    ) as reporter:
        reporter.publish(_snapshot())
        reporter.publish(
            _snapshot(
                phase="draining",
                submitted=750,
                completed=749,
                errors=2,
                in_flight=1,
                elapsed_seconds=151.0,
                recent_rate=4.5,
            )
        )

    assert calls[-1] == (
        experiment_id,
        {
            "phase": "draining",
            "condition_index": 4,
            "condition_count": 6,
            "seeded_articles": 10_000,
            "created_babels": 10_000,
            "indexed_babels": 10_000,
            "requested": 750,
            "completed": 749,
            "elapsed_seconds": 151.0,
            "recent_rate": 4.5,
            "draining": True,
            "telemetry": {
                "submitted": 750,
                "errors": 2,
                "inFlight": 1,
                "conditionPhase": "draining",
            },
        },
    )


def test_condition_progress_never_propagates_dashboard_persistence_failure() -> None:
    def fail(*_args, **_values):
        raise RuntimeError("dashboard database unavailable")

    with PersistedConditionProgress(
        database=SimpleNamespace(append_performance_progress=fail),
        experiment_id=UUID(int=1),
        condition_index=1,
        condition_count=9,
        interval_seconds=60,
    ) as reporter:
        reporter.publish(_snapshot())

    assert reporter.failure == "dashboard database unavailable"


def test_condition_progress_coalesces_updates_off_the_request_path() -> None:
    persisted = threading.Event()
    database = SimpleNamespace(
        append_performance_progress=lambda *_args, **_values: persisted.set()
    )

    with PersistedConditionProgress(
        database=database,
        experiment_id=UUID(int=1),
        condition_index=1,
        condition_count=9,
        interval_seconds=1.0,
    ) as reporter:
        reporter.publish(_snapshot())
        assert persisted.wait(0.05) is False

    assert persisted.is_set()
