"""Small feedback transport ports and deterministic in-memory implementation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol


FEEDBACK_TOPIC = "babel.feedback.v1"


@dataclass(frozen=True, order=True, slots=True)
class TopicPartition:
    topic: str
    partition: int

    def __post_init__(self) -> None:
        if self.topic != FEEDBACK_TOPIC:
            raise ValueError(f"only topic {FEEDBACK_TOPIC} is supported")
        if self.partition < 0:
            raise ValueError("partition must be nonnegative")


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    topic: str
    partition: int
    offset: int
    key: str
    event: Any

    @property
    def topic_partition(self) -> TopicPartition:
        return TopicPartition(self.topic, self.partition)


@dataclass(frozen=True, slots=True)
class OffsetRange:
    topic_partition: TopicPartition
    start: int
    end_exclusive: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end_exclusive < self.start:
            raise ValueError("offset range must satisfy 0 <= start <= end")


class FeedbackProducer(Protocol):
    def publish(self, *, key: str, event: Any) -> FeedbackRecord: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class FeedbackConsumer(Protocol):
    def poll(self, timeout_seconds: float = 0.0) -> FeedbackRecord | None: ...

    def position(self) -> dict[TopicPartition, int]: ...

    def committed(self) -> dict[TopicPartition, int]: ...

    def commit(self, next_offsets: Mapping[TopicPartition, int]) -> None: ...

    def seek(self, next_offsets: Mapping[TopicPartition, int]) -> None: ...

    def high_watermarks(self) -> dict[TopicPartition, int]: ...

    def close(self) -> None: ...


def validate_feedback_event(event: Any) -> Any:
    """Validate through Lane A's production contract without duplicating it."""
    from babel_online.contracts import FeedbackEventV1, FeedbackEventV2

    if isinstance(event, (FeedbackEventV1, FeedbackEventV2)):
        return event
    if hasattr(event, "model_dump"):
        event = event.model_dump(mode="json")
    if not isinstance(event, Mapping):
        raise ValueError("feedback event must be an object")
    version = event.get("schemaVersion")
    if version == 1:
        return FeedbackEventV1.model_validate(event)
    if version == 2:
        return FeedbackEventV2.model_validate(event)
    raise ValueError("feedback event schemaVersion must be 1 or 2")


class InMemoryFeedbackBus:
    """One-partition acknowledged bus with consumer-group committed offsets."""

    def __init__(self, *, topic: str = FEEDBACK_TOPIC) -> None:
        if topic != FEEDBACK_TOPIC:
            raise ValueError(f"only topic {FEEDBACK_TOPIC} is supported")
        self.topic = topic
        self._partition = TopicPartition(topic, 0)
        self._records: list[FeedbackRecord] = []
        self._committed: dict[str, int] = {}
        self._closed = False
        self._lock = Lock()

    def publish(self, *, key: str, event: Any) -> FeedbackRecord:
        checked = validate_feedback_event(event)
        if key != str(checked.creatorId):
            raise ValueError("feedback key must equal the creator ID")
        with self._lock:
            if self._closed:
                raise RuntimeError("feedback bus is closed")
            record = FeedbackRecord(
                topic=self.topic,
                partition=0,
                offset=len(self._records),
                key=key,
                event=checked,
            )
            self._records.append(record)
            return record

    def consumer(
        self, *, group_id: str, auto_commit: bool = False
    ) -> "InMemoryFeedbackConsumer":
        if auto_commit:
            raise ValueError("automatic offset commits are disabled")
        if not group_id:
            raise ValueError("consumer group ID must be nonblank")
        with self._lock:
            self._committed.setdefault(group_id, 0)
            position = self._committed[group_id]
        return InMemoryFeedbackConsumer(self, group_id=group_id, position=position)

    def high_watermarks(self) -> dict[TopicPartition, int]:
        with self._lock:
            return {self._partition: len(self._records)}

    def records(self, offset_range: OffsetRange) -> tuple[FeedbackRecord, ...]:
        if offset_range.topic_partition != self._partition:
            raise ValueError("offset range is not assigned to this bus")
        with self._lock:
            if offset_range.end_exclusive > len(self._records):
                raise ValueError("offset range exceeds the high watermark")
            return tuple(self._records[offset_range.start : offset_range.end_exclusive])

    def flush(self) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            self._closed = True


class InMemoryFeedbackConsumer:
    def __init__(
        self, bus: InMemoryFeedbackBus, *, group_id: str, position: int
    ) -> None:
        self._bus = bus
        self._group_id = group_id
        self._position = position
        self._closed = False

    @property
    def _partition(self) -> TopicPartition:
        return TopicPartition(self._bus.topic, 0)

    def poll(self, timeout_seconds: float = 0.0) -> FeedbackRecord | None:
        del timeout_seconds
        if self._closed:
            raise RuntimeError("feedback consumer is closed")
        with self._bus._lock:
            if self._position >= len(self._bus._records):
                return None
            record = self._bus._records[self._position]
            self._position += 1
            return record

    def position(self) -> dict[TopicPartition, int]:
        return {self._partition: self._position}

    def committed(self) -> dict[TopicPartition, int]:
        with self._bus._lock:
            offset = self._bus._committed[self._group_id]
        return {self._partition: offset}

    def commit(self, next_offsets: Mapping[TopicPartition, int]) -> None:
        next_offset = self._one_offset(next_offsets)
        if next_offset > self._position:
            raise ValueError("cannot commit beyond the consumer position")
        with self._bus._lock:
            self._bus._committed[self._group_id] = next_offset

    def seek(self, next_offsets: Mapping[TopicPartition, int]) -> None:
        next_offset = self._one_offset(next_offsets)
        high = self._bus.high_watermarks()[self._partition]
        if next_offset > high:
            raise ValueError("cannot seek beyond the high watermark")
        self._position = next_offset

    def high_watermarks(self) -> dict[TopicPartition, int]:
        return self._bus.high_watermarks()

    def close(self) -> None:
        self._closed = True

    def _one_offset(self, offsets: Mapping[TopicPartition, int]) -> int:
        if set(offsets) != {self._partition}:
            raise ValueError("offsets must contain exactly the assigned partition")
        offset = offsets[self._partition]
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("next offset must be a nonnegative integer")
        return offset


def capture_high_watermarks(consumer: FeedbackConsumer) -> dict[TopicPartition, int]:
    return consumer.high_watermarks()


__all__ = [
    "FEEDBACK_TOPIC",
    "FeedbackConsumer",
    "FeedbackProducer",
    "FeedbackRecord",
    "InMemoryFeedbackBus",
    "InMemoryFeedbackConsumer",
    "OffsetRange",
    "TopicPartition",
    "capture_high_watermarks",
    "validate_feedback_event",
]
