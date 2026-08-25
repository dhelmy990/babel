"""Bounded graceful shutdown for the continuously running online worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from babel_online.feedback.bus import OffsetRange, TopicPartition, capture_high_watermarks
from babel_online.feedback.export import FeedbackExport, export_offset_ranges


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    checkpoint_path: Path
    next_offsets: dict[TopicPartition, int]
    feedback_export: FeedbackExport
    sync_artifact: Any
    child_artifact: Any


class OnlineDemoSupervisor:
    """Own shutdown ordering; the simulator must stop calling publish first."""

    def __init__(
        self,
        *,
        producer: Any,
        trainer: Any,
        feedback_source: Any,
        export_root: str | Path,
        publish_sync: Callable[[], Any],
        export_child: Callable[[], Any],
    ) -> None:
        self.producer = producer
        self.trainer = trainer
        self.feedback_source = feedback_source
        self.export_root = Path(export_root)
        self.publish_sync = publish_sync
        self.export_child = export_child
        self._start_offsets = trainer.consumer.committed()
        self._stopped = False

    def graceful_stop(self) -> ShutdownResult:
        if self._stopped:
            raise RuntimeError("online demo worker is already stopped")
        self.producer.flush()
        end_offsets = capture_high_watermarks(self.trainer.consumer)
        self.trainer.drain_to(end_offsets)
        checkpoint = self.trainer.checkpoint_and_commit()
        ranges = [
            OffsetRange(
                partition,
                self._start_offsets.get(partition, 0),
                end_offset,
            )
            for partition, end_offset in sorted(end_offsets.items())
        ]
        feedback_export = export_offset_ranges(
            self.feedback_source, ranges, self.export_root
        )
        sync_artifact = self.publish_sync()
        child_artifact = self.export_child()
        self.trainer.consumer.close()
        self.producer.close()
        self._stopped = True
        return ShutdownResult(
            checkpoint,
            dict(self.trainer.next_offsets),
            feedback_export,
            sync_artifact,
            child_artifact,
        )


__all__ = ["OnlineDemoSupervisor", "ShutdownResult"]
