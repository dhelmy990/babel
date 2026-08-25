from __future__ import annotations

import json

from babel_online.feedback.bus import OffsetRange, TopicPartition
from babel_online.feedback.kafka import KafkaFeedbackConsumer, KafkaFeedbackProducer

from .test_bus import feedback_event


class _Metadata:
    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self._topic = topic
        self._partition = partition
        self._offset = offset

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset


class _Producer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, bytes]] = []

    def produce(self, topic, *, key, value, on_delivery) -> None:
        self.calls.append((topic, key, value))
        on_delivery(None, _Metadata(topic, 0, 7))

    def flush(self, timeout=None) -> int:
        return 0


class _Message(_Metadata):
    def __init__(self, event) -> None:
        super().__init__("babel.feedback.v1", 0, 7)
        self._event = event

    def error(self):
        return None

    def key(self) -> bytes:
        return str(self._event.creatorId).encode()

    def value(self) -> bytes:
        return self._event.model_dump_json().encode()


class _Consumer:
    def __init__(self, event) -> None:
        self.event = event
        self.subscriptions = []
        self.commits = []
        self.seeks = []
        self.closed = False
        self._polled = False

    def subscribe(self, topics) -> None:
        self.subscriptions.append(list(topics))

    def poll(self, timeout):
        if self._polled:
            return None
        self._polled = True
        return _Message(self.event)

    def assignment(self):
        return [_Metadata("babel.feedback.v1", 0, 0)]

    def position(self, partitions):
        return [_Metadata("babel.feedback.v1", 0, 8)]

    def committed(self, partitions, timeout=None):
        return [_Metadata("babel.feedback.v1", 0, 4)]

    def commit(self, *, offsets, asynchronous):
        self.commits.append((offsets, asynchronous))

    def seek(self, partition):
        self.seeks.append(partition)

    def get_watermark_offsets(self, partition, timeout=None):
        return (0, 9)

    def close(self):
        self.closed = True


class _DelayedAssignmentConsumer(_Consumer):
    def __init__(self, event) -> None:
        super().__init__(event)
        self.assigned = False

    def assignment(self):
        return super().assignment() if self.assigned else []

    def poll(self, timeout):
        self.assigned = True
        return None

    def seek(self, partition):
        if not self.assigned:
            raise RuntimeError("partition is not assigned")
        super().seek(partition)


class _AssignmentWithMessageConsumer(_DelayedAssignmentConsumer):
    def poll(self, timeout):
        self.assigned = True
        return _Message(self.event)


def _partition_factory(topic: str, partition: int, offset: int = -1001):
    return _Metadata(topic, partition, offset)


def test_kafka_producer_keys_creator_and_waits_for_delivery() -> None:
    client = _Producer()
    producer = KafkaFeedbackProducer("unused", client=client)
    event = feedback_event()

    record = producer.publish(key=str(event.creatorId), event=event)

    assert record.offset == 7
    assert client.calls[0][0] == "babel.feedback.v1"
    assert client.calls[0][1] == str(event.creatorId).encode()
    assert json.loads(client.calls[0][2]) == event.model_dump(mode="json")


def test_kafka_consumer_is_manual_commit_and_uses_next_offsets() -> None:
    event = feedback_event()
    client = _Consumer(event)
    consumer = KafkaFeedbackConsumer(
        "unused",
        group_id="trainer",
        client=client,
        topic_partition_factory=_partition_factory,
    )
    partition = TopicPartition("babel.feedback.v1", 0)

    record = consumer.poll()

    assert client.subscriptions == [["babel.feedback.v1"]]
    assert record is not None and record.event == event
    assert consumer.position() == {partition: 8}
    assert consumer.committed() == {partition: 4}
    assert consumer.high_watermarks() == {partition: 9}
    consumer.seek({partition: 5})
    consumer.commit({partition: 5})
    assert client.seeks[-1].offset() == 5
    assert client.commits[-1][1] is False


def test_kafka_consumer_reads_an_exact_bounded_export_range() -> None:
    event = feedback_event()
    consumer = KafkaFeedbackConsumer(
        "unused",
        group_id="exporter",
        client=_Consumer(event),
        topic_partition_factory=_partition_factory,
    )
    partition = TopicPartition("babel.feedback.v1", 0)

    records = consumer.records(OffsetRange(partition, 7, 8))

    assert len(records) == 1
    assert records[0].offset == 7
    assert records[0].event == event


def test_seek_waits_for_subscription_assignment() -> None:
    client = _DelayedAssignmentConsumer(feedback_event())
    consumer = KafkaFeedbackConsumer(
        "unused",
        group_id="trainer",
        client=client,
        topic_partition_factory=_partition_factory,
    )
    partition = TopicPartition("babel.feedback.v1", 0)

    consumer.seek({partition: 4})

    assert client.seeks[-1].offset() == 4


def test_position_does_not_advance_past_assignment_prefetch() -> None:
    event = feedback_event()
    consumer = KafkaFeedbackConsumer(
        "unused",
        group_id="trainer",
        client=_AssignmentWithMessageConsumer(event),
        topic_partition_factory=_partition_factory,
    )
    partition = TopicPartition("babel.feedback.v1", 0)

    assert consumer.position() == {partition: 7}
    assert consumer.poll().offset == 7
