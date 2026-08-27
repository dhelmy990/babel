from __future__ import annotations

import hashlib
import json
import threading
import time
from uuid import UUID

import numpy as np
import pytest

from babel_online.feedback.bus import InMemoryFeedbackBus, TopicPartition
from babel_online.training.checkpoint import (
    CheckpointIdentity,
    load_latest_checkpoint,
    save_online_checkpoint,
)
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


def test_trainer_uses_configured_event_micro_batches(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    for _ in range(5):
        bus.publish(key=str(event.creatorId), event=event)

    class RecordingBatchModel:
        def __init__(self) -> None:
            self.batch_sizes = []

        def train_events(self, events) -> float:
            self.batch_sizes.append(len(events))
            return 0.25

        def materialized_vectors(self):
            return {}

        def state_dict(self):
            return {"batchSizes": self.batch_sizes}

    model = RecordingBatchModel()
    trainer = OnlineTrainer(
        model=model,
        consumer=bus.consumer(group_id="micro-batch", auto_commit=False),
        checkpoint_root=tmp_path,
        micro_batch_size=4,
    )

    assert trainer.process_available(max_records=5) == 5
    assert model.batch_sizes == [4, 1]
    assert trainer.global_step == 2
    assert trainer.training_version == 2
    assert trainer.processed_events == 5


def test_checkpoint_exposes_complete_torch_optimizer_and_scheduler_state(tmp_path) -> None:
    from babel_online.training.torch_working import TorchOnlineRecommender

    vector = np.eye(100, dtype=np.float32)[0]
    model = TorchOnlineRecommender({INCLUDED: vector}, learning_rate=0.02)
    checkpoint = save_online_checkpoint(
        tmp_path,
        step=0,
        version=0,
        next_offsets={},
        metrics={"processedEvents": 0, "optimizerSteps": 0},
        model_state=model.state_dict(),
    )

    document = json.loads((checkpoint / "state.json").read_text())
    assert document["optimizerState"] == document["modelState"]["optimizerState"]
    assert document["schedulerState"] == document["modelState"]["schedulerState"]


def test_event_aware_model_observes_feedback_batches_without_ranking_pairs(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions().model_copy(update={"candidateActions": []})
    bus.publish(key=str(event.creatorId), event=event)

    class ObservingModel:
        def __init__(self) -> None:
            self.observed = 0

        def train_events(self, events):
            self.observed += len(events)
            return None

        def materialized_vectors(self):
            return {}

        def state_dict(self):
            return {"observed": self.observed}

    model = ObservingModel()
    trainer = OnlineTrainer(
        model=model,
        consumer=bus.consumer(group_id="observation", auto_commit=False),
        checkpoint_root=tmp_path,
    )

    assert trainer.process_available() == 1
    assert model.observed == 1
    assert trainer.global_step == 0


def test_trainer_fills_micro_batch_from_delayed_live_arrivals(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    for _ in range(4):
        bus.publish(key=str(event.creatorId), event=event)
    raw = bus.consumer(group_id="delayed", auto_commit=False)

    class DelayedArrivalConsumer:
        def __init__(self) -> None:
            self.polls = 0

        def poll(self, timeout_seconds=0.0):
            self.polls += 1
            if self.polls > 1 and timeout_seconds <= 0:
                return None
            if self.polls > 1:
                time.sleep(0.002)
            return raw.poll(timeout_seconds)

        def __getattr__(self, name):
            return getattr(raw, name)

    class RecordingModel:
        def __init__(self) -> None:
            self.batches = []

        def train_events(self, events):
            self.batches.append(len(events))
            return 0.25

        def materialized_vectors(self):
            return {}

        def state_dict(self):
            return {"batches": self.batches}

    model = RecordingModel()
    trainer = OnlineTrainer(
        model=model,
        consumer=DelayedArrivalConsumer(),
        checkpoint_root=tmp_path,
        micro_batch_size=4,
        batch_fill_timeout_seconds=0.1,
    )

    assert trainer.process_available(max_records=4, poll_timeout_seconds=0.01) == 4
    assert model.batches == [4]
    assert trainer.global_step == 1
