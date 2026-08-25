from __future__ import annotations

import numpy as np

from babel_online.feedback.bus import InMemoryFeedbackBus, TopicPartition
from babel_online.training.checkpoint import load_latest_checkpoint
from babel_online.training.consumer import OnlineTrainer
from babel_online.training.working import NumpyWorkingModel

from .test_pairs import EXCLUDED, IGNORED, INCLUDED, event_with_three_actions


def _model() -> NumpyWorkingModel:
    vectors = {
        INCLUDED: np.zeros(100, dtype=np.float32),
        EXCLUDED: np.zeros(100, dtype=np.float32),
        IGNORED: np.zeros(100, dtype=np.float32),
    }
    query = np.zeros(100, dtype=np.float32)
    query[0] = 1.0
    return NumpyWorkingModel(vectors, query_vector=query, learning_rate=0.25)


def test_restart_restores_durable_model_and_next_offset(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    first_consumer = bus.consumer(group_id="trainer", auto_commit=False)
    first = OnlineTrainer(
        model=_model(), consumer=first_consumer, checkpoint_root=tmp_path
    )
    first.process_available()
    durable_residual = first.model.residual(INCLUDED).copy()
    first.checkpoint_and_commit()

    # A crash may leave a directory, but it is not a complete checkpoint.
    (tmp_path / "checkpoint-step-00000002.partial").mkdir()

    restarted_consumer = bus.consumer(group_id="trainer", auto_commit=False)
    restarted = OnlineTrainer(
        model=_model(), consumer=restarted_consumer, checkpoint_root=tmp_path
    )
    restarted.model.learning_rate = 9.0
    checkpoint = restarted.restore_latest()

    partition = TopicPartition("babel.feedback.v1", 0)
    assert checkpoint is not None
    assert load_latest_checkpoint(tmp_path) == checkpoint
    np.testing.assert_array_equal(restarted.model.residual(INCLUDED), durable_residual)
    assert restarted.model.learning_rate == 0.25
    assert restarted.next_offsets == {partition: 1}
    assert restarted.process_available() == 0
    bus.publish(key=str(event.creatorId), event=event)
    assert restarted.process_available() == 1
    assert restarted.checkpoint_and_commit().name == "checkpoint-step-00000002"
    assert not list(tmp_path.glob("*.partial"))


def test_uncheckpointed_event_replays_from_committed_offset(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    crashed = OnlineTrainer(
        model=_model(),
        consumer=bus.consumer(group_id="trainer", auto_commit=False),
        checkpoint_root=tmp_path,
    )
    assert crashed.process_available() == 1

    restarted = OnlineTrainer(
        model=_model(),
        consumer=bus.consumer(group_id="trainer", auto_commit=False),
        checkpoint_root=tmp_path,
    )
    assert restarted.restore_latest() is None
    assert restarted.process_available() == 1


def test_continuous_consumer_checkpoints_on_event_interval(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    consumer = bus.consumer(group_id="trainer", auto_commit=False)
    trainer = OnlineTrainer(
        model=_model(), consumer=consumer, checkpoint_root=tmp_path
    )

    trainer.run_until_stopped(
        stop_requested=lambda: trainer.processed_events == 1,
        checkpoint_every_events=1,
    )

    partition = TopicPartition("babel.feedback.v1", 0)
    assert consumer.committed() == {partition: 1}
    assert load_latest_checkpoint(tmp_path).step == 1
