from __future__ import annotations

import os
import time
from uuid import uuid4

import numpy as np
import pytest

from babel_online.feedback.kafka import KafkaFeedbackConsumer, KafkaFeedbackProducer
from babel_online.feedback.bus import OffsetRange
from babel_online.feedback.export import export_offset_ranges
from babel_online.training import NumpyWorkingModel, OnlineTrainer

from .test_bus import feedback_event


@pytest.mark.kafka
def test_real_kafka_publish_manual_commit_and_restart(tmp_path) -> None:
    bootstrap = os.environ.get("BABEL_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("set BABEL_KAFKA_BOOTSTRAP to run the real Kafka smoke")
    group_id = f"babel-online-smoke-{uuid4()}"
    event = feedback_event()
    producer = KafkaFeedbackProducer(bootstrap)
    produced = producer.publish(key=str(event.creatorId), event=event)
    producer.close()

    consumer = KafkaFeedbackConsumer(bootstrap, group_id=group_id)
    vector = np.zeros(100, dtype=np.float32)
    vector[0] = 1.0
    model = NumpyWorkingModel(
        {event.candidateActions[0].babelId: vector},
        query_vector=vector,
    )
    trainer = OnlineTrainer(
        model=model,
        consumer=consumer,
        checkpoint_root=tmp_path / "checkpoints",
    )
    deadline = time.monotonic() + 20.0
    received = None
    while time.monotonic() < deadline:
        if trainer.process_available(max_records=1, poll_timeout_seconds=1.0):
            if trainer.next_offsets.get(produced.topic_partition) == produced.offset + 1:
                received = produced
                break
    assert received is not None and received.event == event
    checkpoint = trainer.checkpoint_and_commit()
    assert (checkpoint / "manifest.json").is_file()
    assert consumer.committed()[received.topic_partition] == received.offset + 1
    exported = export_offset_ranges(
        consumer,
        [OffsetRange(received.topic_partition, received.offset, received.offset + 1)],
        tmp_path,
    )
    assert exported.jsonl_path.is_file()
    assert exported.parquet_path.is_file()
    consumer.close()

    restarted = KafkaFeedbackConsumer(bootstrap, group_id=group_id)
    restored_model = NumpyWorkingModel(
        {event.candidateActions[0].babelId: vector},
        query_vector=vector,
    )
    restored = OnlineTrainer(
        model=restored_model,
        consumer=restarted,
        checkpoint_root=tmp_path / "checkpoints",
    )
    restored.restore_latest()
    assert restored.process_available(max_records=1, poll_timeout_seconds=3.0) == 0
    assert restarted.committed()[received.topic_partition] == received.offset + 1
    restarted.close()
