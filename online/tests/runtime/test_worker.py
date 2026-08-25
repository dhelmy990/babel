from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4
from pathlib import Path

import pytest

from babel_online.feedback import InMemoryFeedbackBus
from babel_online.runtime.worker import RunScopedConsumer, WorkerManager, isolate_new_run_offsets
from babel_online.runtime.worker import FridayDemoRuntime
from babel_online.runtime.dataset_bundle import (
    DEMO_DATASET_CONFIG,
    DEMO_DATASET_REPOSITORY,
    DEMO_DATASET_REVISION,
)
from babel_online.config import default_run_config
from babel_online.contracts import FeedbackEventV1
from babel_online.training.consumer import SyncTrainingState
import numpy as np
import threading


ROOT = Path(__file__).resolve().parents[3]


def bundle_identity(**updates):
    values = {
        "dataset_repository": DEMO_DATASET_REPOSITORY,
        "dataset_config": DEMO_DATASET_CONFIG,
        "dataset_revision": DEMO_DATASET_REVISION,
    }
    values.update(updates)
    return SimpleNamespace(**values)


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

    manager = WorkerManager(
        database=Database(),
        dataset_bundle=bundle_identity(),
        runtime_factory=lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="identity"):
        manager.start(run_id)


@pytest.mark.parametrize(
    "bundle_update",
    [
        {"dataset_repository": "another/repository"},
        {"dataset_revision": "0" * 40},
    ],
)
def test_worker_rejects_loaded_bundle_identity_mismatch_before_claim(bundle_update) -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision=DEMO_DATASET_REVISION,
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    )

    class Database:
        claimed = False

        def load_run(self, _run_id):
            return SimpleNamespace(config=config, status="starting")

        def claim_run(self, _run_id):
            self.claimed = True

    database = Database()
    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle_identity(**bundle_update),
        runtime_factory=lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="identity"):
        manager.start(run_id)
    assert database.claimed is False


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


def test_worker_sync_uses_one_locked_training_capture_for_version_vectors_and_state() -> None:
    babel_id = uuid4()
    vector = np.eye(100, dtype=np.float32)[4]
    captured = SyncTrainingState(
        version=11,
        materialized_vectors={babel_id: vector},
        model_state={"captured": 11},
    )
    runtime = object.__new__(FridayDemoRuntime)
    runtime.trainer = SimpleNamespace(capture_sync_state=lambda: captured)
    runtime._created = [SimpleNamespace(babelId=babel_id)]

    version, vectors, model_state = runtime._capture_training_sync()

    assert version == 11
    np.testing.assert_array_equal(vectors[babel_id], vector)
    assert model_state == {"captured": 11}


def test_same_run_seed_reproduces_sources_and_decision_draws_across_run_ids() -> None:
    def runtime(run_id, seed):
        value = object.__new__(FridayDemoRuntime)
        value.config = default_run_config(
            run_id=run_id,
            dataset_revision="e1acc648fcace8820dd5ee70bae9216ea4334555",
            starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
            creator_count=2,
        ).model_copy(
            update={
                "runSeed": seed,
                "environmentSequence": ["2026-06"],
                "perMonthEventBudget": {"2026-06": 4},
            }
        )
        value.bundle = SimpleNamespace(
            configs={
                "demo_catalog_2026_06": tuple(
                    {"article_key": f"enwiki:{number}"} for number in range(1, 9)
                ),
                "demo_catalog_2026_07": (),
            }
        )
        return value

    first = runtime(uuid4(), 73)
    second = runtime(uuid4(), 73)
    different = runtime(uuid4(), 99)
    first_plan = first._plan()
    second_plan = second._plan()

    assert [row[2]["article_key"] for row in first_plan] == [
        row[2]["article_key"] for row in second_plan
    ]
    assert [row[2]["article_key"] for row in first_plan] != [
        row[2]["article_key"] for row in different._plan()
    ]
    assert [row[1] for row in first_plan] != [row[1] for row in second_plan]
    assert [row[3] for row in first_plan] != [row[3] for row in second_plan]
    assert first._simulation_draw("action", "2026-06", 2, 0, "enwiki:4", 1) == (
        second._simulation_draw("action", "2026-06", 2, 0, "enwiki:4", 1)
    )


def test_start_claims_run_before_return_so_immediate_stop_finishes_gracefully() -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision=DEMO_DATASET_REVISION,
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    )
    release = threading.Event()
    finished = threading.Event()
    observed_stop = []

    class Database:
        claimed = False
        transitions = []

        def load_run(self, _run_id):
            return SimpleNamespace(config=config, status="starting")

        def claim_run(self, _run_id):
            self.claimed = True

        def transition(self, _run_id, status, **_values):
            self.transitions.append(status)

        def append_activity(self, _activity):
            pass

    class Runtime:
        def __init__(self, stop_event):
            self.stop_event = stop_event

        def request_stop(self):
            self.stop_event.set()

        def stop_serving(self):
            pass

        def run(self):
            assert release.wait(1.0)
            observed_stop.append(self.stop_event.is_set())
            finished.set()

    database = Database()
    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle_identity(),
        runtime_factory=lambda _config, stop_event: Runtime(stop_event),
    )

    manager.start(run_id)
    assert database.claimed is True
    manager.request_stop(run_id)
    release.set()

    assert finished.wait(1.0)
    assert observed_stop == [True]
    assert "failed" not in database.transitions
