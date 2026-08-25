from __future__ import annotations

import hashlib
import threading
from uuid import UUID

import numpy as np
import pytest

from babel_online.feedback.bus import InMemoryFeedbackBus, TopicPartition
from babel_online.training.checkpoint import CheckpointIdentity, load_latest_checkpoint
from babel_online.training.consumer import OnlineTrainer
from babel_online.training.working import NumpyWorkingModel

from .test_pairs import EXCLUDED, IGNORED, INCLUDED, event_with_three_actions


def working_model() -> NumpyWorkingModel:
    vectors = {
        INCLUDED: np.zeros(100, dtype=np.float32),
        EXCLUDED: np.zeros(100, dtype=np.float32),
        IGNORED: np.zeros(100, dtype=np.float32),
    }
    query = np.zeros(100, dtype=np.float32)
    query[0] = 1.0
    return NumpyWorkingModel(vectors, query_vector=query, learning_rate=0.25)


def test_offset_commits_only_after_complete_atomic_checkpoint(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    consumer = bus.consumer(group_id="trainer", auto_commit=False)
    trainer = OnlineTrainer(
        model=working_model(), consumer=consumer, checkpoint_root=tmp_path
    )
    partition = TopicPartition("babel.feedback.v1", 0)

    trainer.process_available()

    assert consumer.committed() == {partition: 0}
    checkpoint = trainer.checkpoint_and_commit()
    assert consumer.committed() == {partition: 1}
    assert checkpoint.name == "checkpoint-step-00000001"
    assert not list(tmp_path.glob("*.partial"))
    loaded = load_latest_checkpoint(tmp_path)
    assert loaded is not None
    assert loaded.next_offsets == {partition: 1}
    assert loaded.step == 1
    assert loaded.metrics["processedEvents"] == 1
    assert loaded.manifest_sha256 == hashlib.sha256(
        (checkpoint / "state.json").read_bytes()
    ).hexdigest()


def test_checkpoint_binds_run_model_and_embedding_identity(tmp_path) -> None:
    event = event_with_three_actions()
    identity = CheckpointIdentity(
        run_id=event.runId,
        model_id=event.modelId,
        embedding_space_id=event.embeddingSpaceId,
    )
    bus = InMemoryFeedbackBus()
    bus.publish(key=str(event.creatorId), event=event)
    trainer = OnlineTrainer(
        model=working_model(),
        consumer=bus.consumer(group_id=f"trainer.{event.runId}", auto_commit=False),
        checkpoint_root=tmp_path,
        identity=identity,
    )
    trainer.process_available()
    trainer.checkpoint_and_commit()

    assert load_latest_checkpoint(tmp_path).identity == identity
    incompatible = OnlineTrainer(
        model=working_model(),
        consumer=bus.consumer(group_id="different-run", auto_commit=False),
        checkpoint_root=tmp_path,
        identity=CheckpointIdentity(
            run_id=event.runId,
            model_id=event.modelId,
            embedding_space_id=UUID(int=999),
        ),
    )
    with pytest.raises(ValueError, match="identity"):
        incompatible.restore_latest()


def test_sync_capture_cannot_observe_model_update_before_matching_version(tmp_path) -> None:
    event = event_with_three_actions()
    bus = InMemoryFeedbackBus()
    bus.publish(key=str(event.creatorId), event=event)
    update_started = threading.Event()
    release_update = threading.Event()

    class BlockingModel:
        def __init__(self) -> None:
            self.value = 0.0

        def train_pairs(self, _pairs) -> float:
            self.value = 1.0
            update_started.set()
            assert release_update.wait(2.0)
            return 0.5

        def materialized_vectors(self):
            vector = np.zeros(100, dtype=np.float32)
            vector[0] = self.value
            return {INCLUDED: vector}

        def state_dict(self):
            return {"observedVersion": self.value}

    trainer = OnlineTrainer(
        model=BlockingModel(),
        consumer=bus.consumer(group_id="locked-capture", auto_commit=False),
        checkpoint_root=tmp_path,
    )
    training = threading.Thread(target=trainer.process_available)
    training.start()
    assert update_started.wait(1.0)
    captured = []
    capture = threading.Thread(target=lambda: captured.append(trainer.capture_sync_state()))
    capture.start()
    capture.join(0.05)
    assert capture.is_alive()

    release_update.set()
    training.join(1.0)
    capture.join(1.0)

    assert captured[0].version == 1
    assert captured[0].model_state == {"observedVersion": 1.0}
    assert captured[0].materialized_vectors[INCLUDED][0] == 1.0
