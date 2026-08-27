"""One continuously running manual-offset online trainer."""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from babel_online.feedback.bus import FeedbackConsumer, TopicPartition

from .checkpoint import (
    CheckpointIdentity,
    CheckpointState,
    load_latest_checkpoint,
    restore_rng,
    save_online_checkpoint,
)
from .pairs import pairs_from_event


@dataclass(frozen=True, slots=True)
class SyncTrainingState:
    version: int
    materialized_vectors: dict[Any, Any]
    model_state: dict[str, Any]


class OnlineTrainer:
    def __init__(
        self,
        *,
        model: Any,
        consumer: FeedbackConsumer,
        checkpoint_root: str | Path,
        identity: CheckpointIdentity | None = None,
        micro_batch_size: int = 1,
        batch_fill_timeout_seconds: float = 2.0,
    ) -> None:
        if micro_batch_size <= 0:
            raise ValueError("training micro-batch size must be positive")
        if (
            batch_fill_timeout_seconds <= 0
            or not math.isfinite(batch_fill_timeout_seconds)
        ):
            raise ValueError("batch fill timeout must be positive and finite")
        self.model = model
        self.consumer = consumer
        self.checkpoint_root = Path(checkpoint_root)
        self.identity = identity
        self.micro_batch_size = int(micro_batch_size)
        self.batch_fill_timeout_seconds = float(batch_fill_timeout_seconds)
        self.global_step = 0
        self.training_version = 0
        self.processed_events = 0
        self.losses: list[float] = []
        self.next_offsets = consumer.position()
        self.last_step_time_ms: float | None = None
        self._lock = RLock()

    @property
    def metrics(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "processedEvents": self.processed_events,
                "optimizerSteps": self.global_step,
                "rollingRankLoss": (
                    float(sum(self.losses[-20:]) / len(self.losses[-20:]))
                    if self.losses
                    else 0.0
                ),
            }

    def capture_sync_state(self) -> SyncTrainingState:
        """Capture one version and its exact vectors/model bytes atomically."""
        with self._lock:
            return SyncTrainingState(
                version=self.training_version,
                materialized_vectors={
                    item_id: vector.copy()
                    for item_id, vector in self.model.materialized_vectors().items()
                },
                model_state=copy.deepcopy(self.model.state_dict()),
            )

    def process_available(
        self,
        *,
        max_records: int | None = None,
        end_offsets: dict[TopicPartition, int] | None = None,
        poll_timeout_seconds: float = 0.0,
    ) -> int:
        processed = 0
        while max_records is None or processed < max_records:
            batch = []
            remaining = (
                self.micro_batch_size
                if max_records is None
                else min(self.micro_batch_size, max_records - processed)
            )
            fill_deadline: float | None = None
            while len(batch) < remaining:
                observed_offsets = dict(self.next_offsets)
                for pending in batch:
                    observed_offsets[pending.topic_partition] = pending.offset + 1
                if end_offsets is not None and all(
                    observed_offsets.get(partition, 0) >= bound
                    for partition, bound in end_offsets.items()
                ):
                    break
                if batch:
                    if fill_deadline is None:
                        fill_deadline = time.monotonic() + self.batch_fill_timeout_seconds
                    wait_seconds = max(0.0, fill_deadline - time.monotonic())
                    if wait_seconds == 0.0:
                        break
                else:
                    wait_seconds = poll_timeout_seconds
                record = self.consumer.poll(wait_seconds)
                if record is None:
                    break
                partition = record.topic_partition
                if end_offsets is not None and record.offset >= end_offsets[partition]:
                    self.consumer.seek({partition: record.offset})
                    break
                batch.append(record)
            if not batch:
                break
            with self._lock:
                batch_events = [record.event for record in batch]
                has_pairs = any(pairs_from_event(event) for event in batch_events)
                event_aware = hasattr(self.model, "train_events")
                if event_aware or has_pairs:
                    step_started = time.perf_counter_ns()
                    if event_aware:
                        trained = self.model.train_events(batch_events)
                    else:
                        pairs = tuple(
                            pair
                            for event in batch_events
                            for pair in pairs_from_event(event)
                        )
                        trained = self.model.train_pairs(pairs)
                    if trained is None and has_pairs:
                        raise RuntimeError("pair-bearing batch did not produce a loss")
                    if trained is not None:
                        loss = float(trained)
                        self.last_step_time_ms = (
                            time.perf_counter_ns() - step_started
                        ) / 1_000_000
                        self.losses.append(loss)
                        self.global_step += 1
                        self.training_version += 1
                for record in batch:
                    self.next_offsets[record.topic_partition] = record.offset + 1
                self.processed_events += len(batch)
            processed += len(batch)
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
                max_records=self.micro_batch_size,
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
                max_records=self.micro_batch_size,
                end_offsets=end_offsets,
                poll_timeout_seconds=poll_timeout_seconds,
            )
            if time.monotonic() >= deadline:
                raise TimeoutError("online trainer did not drain to captured offsets")

    def checkpoint_and_commit(self) -> Path:
        with self._lock:
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
                identity=self.identity,
            )
            self.consumer.commit(self.next_offsets)
        return checkpoint

    def restore_latest(self) -> CheckpointState | None:
        checkpoint = load_latest_checkpoint(self.checkpoint_root)
        if checkpoint is None:
            return None
        if self.identity is not None and checkpoint.identity != self.identity:
            raise ValueError("online checkpoint identity does not match this run")
        with self._lock:
            self.model.load_state_dict(checkpoint.model_state)
            self.global_step = int(checkpoint.metrics["optimizerSteps"])
            self.training_version = checkpoint.version
            self.processed_events = int(checkpoint.metrics["processedEvents"])
            self.losses = []
            self.next_offsets = dict(checkpoint.next_offsets)
            restore_rng(checkpoint.rng_state)
            self.consumer.seek(self.next_offsets)
        return checkpoint


__all__ = ["OnlineTrainer", "SyncTrainingState"]
