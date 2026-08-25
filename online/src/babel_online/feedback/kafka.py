"""Minimal confluent-kafka adapter for the single observable feedback topic."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from .bus import (
    FEEDBACK_TOPIC,
    FeedbackRecord,
    OffsetRange,
    TopicPartition,
    validate_feedback_event,
)


def _metadata_value(row: Any, name: str) -> Any:
    value = getattr(row, name)
    return value() if callable(value) else value


def _load_clients() -> tuple[type[Any], type[Any], Callable[..., Any]]:
    try:
        from confluent_kafka import Consumer, Producer
        from confluent_kafka import TopicPartition as KafkaTopicPartition
    except ImportError as error:  # pragma: no cover - exercised by deployment setup
        raise RuntimeError(
            "Kafka support requires the babel-online[kafka] optional dependency"
        ) from error
    return Producer, Consumer, KafkaTopicPartition


class KafkaFeedbackProducer:
    def __init__(self, bootstrap_servers: str, *, client: Any | None = None) -> None:
        if not bootstrap_servers:
            raise ValueError("Kafka bootstrap servers must be nonblank")
        if client is None:
            producer_type, _, _ = _load_clients()
            client = producer_type(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "enable.idempotence": True,
                    "acks": "all",
                }
            )
        self._client = client
        self._closed = False

    def publish(self, *, key: str, event: Any) -> FeedbackRecord:
        if self._closed:
            raise RuntimeError("feedback producer is closed")
        checked = validate_feedback_event(event)
        if key != str(checked.creatorId):
            raise ValueError("feedback key must equal the creator ID")
        delivered: list[tuple[Any, Any]] = []

        def acknowledge(error: Any, message: Any) -> None:
            delivered.append((error, message))

        payload = (
            json.dumps(
                checked.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self._client.produce(
            FEEDBACK_TOPIC,
            key=key.encode("utf-8"),
            value=payload,
            on_delivery=acknowledge,
        )
        remaining = self._client.flush()
        if remaining or not delivered:
            raise TimeoutError("Kafka feedback delivery was not acknowledged")
        error, message = delivered[0]
        if error is not None:
            raise RuntimeError(f"Kafka feedback delivery failed: {error}")
        return FeedbackRecord(
            topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            key=key,
            event=checked,
        )

    def flush(self) -> None:
        if self._client.flush() != 0:
            raise TimeoutError("Kafka feedback producer did not fully drain")

    def close(self) -> None:
        if not self._closed:
            self.flush()
            self._closed = True


class KafkaFeedbackConsumer:
    def __init__(
        self,
        bootstrap_servers: str,
        *,
        group_id: str,
        client: Any | None = None,
        topic_partition_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not bootstrap_servers or not group_id:
            raise ValueError("Kafka bootstrap servers and group ID must be nonblank")
        if client is None:
            _, consumer_type, kafka_partition = _load_clients()
            client = consumer_type(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "group.id": group_id,
                    "enable.auto.commit": False,
                    "enable.auto.offset.store": False,
                    "auto.offset.reset": "earliest",
                }
            )
            topic_partition_factory = kafka_partition
        if topic_partition_factory is None:
            _, _, topic_partition_factory = _load_clients()
        self._client = client
        self._partition = topic_partition_factory
        self._closed = False
        self._pending: list[FeedbackRecord] = []
        self._client.subscribe([FEEDBACK_TOPIC])

    def poll(self, timeout_seconds: float = 0.0) -> FeedbackRecord | None:
        if self._closed:
            raise RuntimeError("feedback consumer is closed")
        if self._pending:
            return self._pending.pop(0)
        message = self._client.poll(timeout_seconds)
        if message is None:
            return None
        return self._decode_message(message)

    def _decode_message(self, message: Any) -> FeedbackRecord:
        if message.error() is not None:
            raise RuntimeError(f"Kafka feedback poll failed: {message.error()}")
        event = validate_feedback_event(json.loads(message.value()))
        key_bytes = message.key()
        key = key_bytes.decode("utf-8") if key_bytes is not None else ""
        if key != str(event.creatorId):
            raise ValueError("feedback key must equal the creator ID")
        return FeedbackRecord(
            topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            key=key,
            event=event,
        )

    def _assignment(self) -> list[Any]:
        return list(self._client.assignment())

    def _await_assignment(self, timeout_seconds: float = 10.0) -> list[Any]:
        deadline = time.monotonic() + timeout_seconds
        while not (assignment := self._assignment()):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("Kafka feedback partition assignment timed out")
            message = self._client.poll(min(0.1, remaining))
            if message is not None:
                self._pending.append(self._decode_message(message))
        return assignment

    @staticmethod
    def _as_offsets(rows: list[Any]) -> dict[TopicPartition, int]:
        return {
            TopicPartition(
                str(_metadata_value(row, "topic")),
                int(_metadata_value(row, "partition")),
            ): max(0, int(_metadata_value(row, "offset")))
            for row in rows
        }

    def position(self) -> dict[TopicPartition, int]:
        assignment = self._await_assignment()
        offsets = self._as_offsets(self._client.position(assignment))
        for record in self._pending:
            partition = record.topic_partition
            offsets[partition] = min(offsets.get(partition, record.offset), record.offset)
        return offsets

    def committed(self) -> dict[TopicPartition, int]:
        assignment = self._await_assignment()
        return self._as_offsets(self._client.committed(assignment))

    def commit(self, next_offsets: Mapping[TopicPartition, int]) -> None:
        rows = [
            self._partition(partition.topic, partition.partition, offset)
            for partition, offset in sorted(next_offsets.items())
        ]
        self._client.commit(offsets=rows, asynchronous=False)

    def seek(self, next_offsets: Mapping[TopicPartition, int]) -> None:
        self._await_assignment()
        self._pending.clear()
        for partition, offset in sorted(next_offsets.items()):
            self._client.seek(
                self._partition(partition.topic, partition.partition, offset)
            )

    def high_watermarks(self) -> dict[TopicPartition, int]:
        return {
            TopicPartition(
                str(_metadata_value(row, "topic")),
                int(_metadata_value(row, "partition")),
            ): int(
                self._client.get_watermark_offsets(row)[1]
            )
            for row in self._await_assignment()
        }

    def records(self, offset_range: OffsetRange) -> tuple[FeedbackRecord, ...]:
        """Read one fixed range after the trainer has durably committed it."""
        if offset_range.start == offset_range.end_exclusive:
            return ()
        self.seek({offset_range.topic_partition: offset_range.start})
        expected = offset_range.start
        records: list[FeedbackRecord] = []
        while expected < offset_range.end_exclusive:
            record = self.poll(5.0)
            if record is None:
                raise TimeoutError("Kafka bounded feedback export did not reach its end")
            if record.topic_partition != offset_range.topic_partition:
                raise ValueError("Kafka bounded feedback export crossed partitions")
            if record.offset != expected:
                raise ValueError("Kafka bounded feedback export is not contiguous")
            records.append(record)
            expected += 1
        return tuple(records)

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True


__all__ = ["KafkaFeedbackConsumer", "KafkaFeedbackProducer"]
