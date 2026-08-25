"""One continuously running manual-offset online trainer."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from babel_online.feedback.bus import FeedbackConsumer, TopicPartition

from .checkpoint import (
    CheckpointState,
    load_latest_checkpoint,
    restore_rng,
    save_online_checkpoint,
)
from .pairs import pairs_from_event


class OnlineTrainer:
    def __init__(
        self,
        *,
        model: Any,
        consumer: FeedbackConsumer,
        checkpoint_root: str | Path,
    ) -> None:
        self.model = model
        self.consumer = consumer
        self.checkpoint_root = Path(checkpoint_root)
        self.global_step = 0
        self.training_version = 0
        self.processed_events = 0
        self.losses: list[float] = []
        self.next_offsets = consumer.position()

    @property
    def metrics(self) -> dict[str, float | int]:
        return {
            "processedEvents": self.processed_events,
            "optimizerSteps": self.global_step,
            "rollingRankLoss": (
                float(sum(self.losses[-20:]) / len(self.losses[-20:]))
                if self.losses
                else 0.0
            ),
        }

    def process_available(
        self,
        *,
        max_records: int | None = None,
        end_offsets: dict[TopicPartition, int] | None = None,
        poll_timeout_seconds: float = 0.0,
    ) -> int:
        processed = 0
        while max_records is None or processed < max_records:
            if end_offsets is not None and all(
                self.next_offsets.get(partition, 0) >= bound
                for partition, bound in end_offsets.items()
            ):
                break
            record = self.consumer.poll(poll_timeout_seconds)
            if record is None:
                break
            partition = record.topic_partition
            if end_offsets is not None and record.offset >= end_offsets[partition]:
                self.consumer.seek({partition: record.offset})
                break
            pairs = pairs_from_event(record.event)
            if pairs:
                loss = float(self.model.train_pairs(pairs))
                self.losses.append(loss)
                self.global_step += 1
                self.training_version += 1
            self.next_offsets[partition] = record.offset + 1
            self.processed_events += 1
            processed += 1
        return processed

    def run_until_stopped(
        self,
        *,
        stop_requested: Callable[[], bool],
        checkpoint_every_events: int,
        poll_timeout_seconds: float = 0.1,
    ) -> None:
        if checkpoint_every_events <= 0:
            raise ValueError("checkpoint interval must be positive")
        while not stop_requested():
            processed = self.process_available(
                max_records=1,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if processed and self.processed_events % checkpoint_every_events == 0:
                self.checkpoint_and_commit()

    def drain_to(
        self,
        end_offsets: dict[TopicPartition, int],
        *,
        timeout_seconds: float = 30.0,
        poll_timeout_seconds: float = 0.25,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while any(
            self.next_offsets.get(partition, 0) < bound
            for partition, bound in end_offsets.items()
        ):
            self.process_available(
                max_records=1,
                end_offsets=end_offsets,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if time.monotonic() >= deadline:
                raise TimeoutError("online trainer did not drain to captured offsets")

    def checkpoint_and_commit(self) -> Path:
        latest = load_latest_checkpoint(self.checkpoint_root)
        if (
            latest is not None
            and latest.step == self.processed_events
            and latest.version == self.training_version
            and latest.next_offsets == self.next_offsets
        ):
            self.consumer.commit(self.next_offsets)
            return latest.path
        checkpoint = save_online_checkpoint(
            self.checkpoint_root,
            step=self.processed_events,
            version=self.training_version,
            next_offsets=self.next_offsets,
            metrics=self.metrics,
            model_state=self.model.state_dict(),
        )
        self.consumer.commit(self.next_offsets)
        return checkpoint

    def restore_latest(self) -> CheckpointState | None:
        checkpoint = load_latest_checkpoint(self.checkpoint_root)
        if checkpoint is None:
            return None
        self.model.load_state_dict(checkpoint.model_state)
        self.global_step = int(checkpoint.metrics["optimizerSteps"])
        self.training_version = checkpoint.version
        self.processed_events = int(checkpoint.metrics["processedEvents"])
        self.losses = []
        self.next_offsets = dict(checkpoint.next_offsets)
        restore_rng(checkpoint.rng_state)
        self.consumer.seek(self.next_offsets)
        return checkpoint


__all__ = ["OnlineTrainer"]
