from __future__ import annotations

from pathlib import Path

import numpy as np

from babel_online.feedback.bus import InMemoryFeedbackBus, TopicPartition
from babel_online.runtime.supervisor import OnlineDemoSupervisor
from babel_online.training.consumer import OnlineTrainer

from .test_checkpoint import working_model
from .test_pairs import INCLUDED, event_with_three_actions


class _TransientEmptyPoll:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.empty_once = True

    def poll(self, timeout_seconds=0.0):
        if self.empty_once:
            self.empty_once = False
            return None
        return self.inner.poll(timeout_seconds)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_graceful_stop_drains_checkpoints_exports_syncs_and_restarts(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    model = working_model()
    before = model.residual(INCLUDED)
    consumer = _TransientEmptyPoll(
        bus.consumer(group_id="trainer", auto_commit=False)
    )
    trainer = OnlineTrainer(
        model=model,
        consumer=consumer,
        checkpoint_root=tmp_path / "checkpoints",
    )
    calls: list[str] = []
    supervisor = OnlineDemoSupervisor(
        producer=bus,
        trainer=trainer,
        feedback_source=bus,
        export_root=tmp_path / "exports",
        publish_sync=lambda: calls.append("sync") or "sync-v1",
        export_child=lambda: calls.append("child") or "child-v1",
    )

    result = supervisor.graceful_stop()

    partition = TopicPartition("babel.feedback.v1", 0)
    assert not np.array_equal(model.residual(INCLUDED), before)
    assert result.next_offsets == {partition: 1}
    assert result.feedback_export.manifest_path.is_file()
    assert result.sync_artifact == "sync-v1"
    assert result.child_artifact == "child-v1"
    assert calls == ["sync", "child"]

    restarted = OnlineTrainer(
        model=working_model(),
        consumer=bus.consumer(group_id="trainer", auto_commit=False),
        checkpoint_root=tmp_path / "checkpoints",
    )
    restarted.restore_latest()
    np.testing.assert_array_equal(restarted.model.residual(INCLUDED), model.residual(INCLUDED))
    assert restarted.process_available() == 0
