from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4
from pathlib import Path

import pytest

from babel_online.feedback import InMemoryFeedbackBus
from babel_online.runtime.worker import RunScopedConsumer, WorkerManager, isolate_new_run_offsets
from babel_online.runtime.worker import FridayDemoRuntime
from babel_online.config import default_run_config
from babel_online.contracts import FeedbackEventV1


ROOT = Path(__file__).resolve().parents[3]


def event_with_three_actions():
    return FeedbackEventV1.model_validate_json(
        (ROOT / "fixtures/online/tiny/observable/feedback-event.json").read_text()
    )


def test_new_run_starts_at_current_high_watermark_and_rejects_cross_run_feedback() -> None:
    bus = InMemoryFeedbackBus()
    old = event_with_three_actions()
    bus.publish(key=str(old.creatorId), event=old)
    consumer = bus.consumer(group_id=f"trainer.{uuid4()}", auto_commit=False)

    start = isolate_new_run_offsets(consumer)

    assert next(iter(start.values())) == 1
    target = old.model_copy(update={"runId": uuid4(), "eventId": uuid4()})
    bus.publish(key=str(target.creatorId), event=target)
    scoped = RunScopedConsumer(consumer, run_id=target.runId)
    assert scoped.poll().event.runId == target.runId
    wrong = target.model_copy(update={"runId": uuid4(), "eventId": uuid4()})
    bus.publish(key=str(wrong.creatorId), event=wrong)
    with pytest.raises(RuntimeError, match="cross-run"):
        scoped.poll()


def test_worker_refuses_a_partial_catalog_pin_before_starting_thread() -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    ).model_copy(update={"datasetConfig": "demo_catalog_2026_06"})

    class Database:
        def load_run(self, requested_run_id):
            assert requested_run_id == run_id
            return SimpleNamespace(config=config, status="starting")

    manager = WorkerManager(database=Database(), runtime_factory=lambda *_args: None)

    with pytest.raises(RuntimeError, match="demo_crosswalk"):
        manager.start(run_id)


def test_new_babel_materialization_keeps_serving_at_last_sync_version() -> None:
    runtime = object.__new__(FridayDemoRuntime)
    runtime._serving_version = 3
    runtime.trainer = SimpleNamespace(training_version=9)
    calls = []
    runtime._persist_and_activate = (  # type: ignore[method-assign]
        lambda version, *, synchronize: calls.append((version, synchronize))
    )

    runtime._materialize_new_babel()

    assert runtime.trainer.training_version == 9
    assert runtime._serving_version == 3
    assert calls == [(3, False)]
