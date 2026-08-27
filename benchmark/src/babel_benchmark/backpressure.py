"""Persisted latency-aware trainer backpressure bounded by dashboard limits."""

import json
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BackpressureState:
    configured_micro_batch: int
    latency_threshold_ms: float
    micro_batch: int
    delay_ms: int
    consecutive_high_windows: int = 0
    consecutive_low_windows: int = 0
    transition_count: int = 0

    @property
    def maximum_backpressure_verified(self) -> bool:
        return self.micro_batch == 2 and self.delay_ms == 500

    def as_document(self) -> dict[str, int | float | bool]:
        return {
            **asdict(self),
            "maximum_backpressure_verified": self.maximum_backpressure_verified,
        }


class PersistedBackpressureController:
    """Use two high windows to tighten and five low windows to recover."""

    minimum_micro_batch = 2
    dashboard_maximum_micro_batch = 1024
    delay_step_ms = 25
    maximum_delay_ms = 500

    def __init__(
        self,
        *,
        state_path: str | Path,
        transitions_path: str | Path,
        configured_micro_batch: int,
        latency_threshold_ms: float,
    ) -> None:
        if not (
            self.minimum_micro_batch
            <= configured_micro_batch
            <= self.dashboard_maximum_micro_batch
        ):
            raise ValueError("micro-batch is outside dashboard limits")
        if latency_threshold_ms <= 0:
            raise ValueError("latency threshold must be positive")
        self.state_path = Path(state_path)
        self.transitions_path = Path(transitions_path)
        if self.state_path.is_file():
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
            document.pop("maximum_backpressure_verified", None)
            self.state = BackpressureState(**document)
            if (
                self.state.configured_micro_batch != configured_micro_batch
                or self.state.latency_threshold_ms != latency_threshold_ms
            ):
                raise ValueError("persisted backpressure identity differs from launch")
        else:
            self.state = BackpressureState(
                configured_micro_batch=configured_micro_batch,
                latency_threshold_ms=latency_threshold_ms,
                micro_batch=configured_micro_batch,
                delay_ms=0,
            )
            self._persist_state()

    @property
    def maximum_backpressure_verified(self) -> bool:
        return self.state.maximum_backpressure_verified

    def _persist_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.state_path.with_suffix(self.state_path.suffix + ".partial")
        partial.write_text(
            json.dumps(
                self.state.as_document(), sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(partial, self.state_path)

    def _record_transition(
        self,
        *,
        previous: BackpressureState,
        p95_ms: float,
        kafka_lag: int,
    ) -> None:
        self.transitions_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "sequence": self.state.transition_count,
            "observed_p95_ms": p95_ms,
            "kafka_lag": kafka_lag,
            "previous_micro_batch": previous.micro_batch,
            "micro_batch": self.state.micro_batch,
            "previous_delay_ms": previous.delay_ms,
            "delay_ms": self.state.delay_ms,
            "maximum_backpressure_verified": self.maximum_backpressure_verified,
        }
        with self.transitions_path.open("a", encoding="utf-8") as target:
            target.write(
                json.dumps(document, sort_keys=True, separators=(",", ":"))
                + "\n"
            )

    def observe(self, *, p95_ms: float, kafka_lag: int) -> BackpressureState:
        if p95_ms < 0 or kafka_lag < 0:
            raise ValueError("backpressure observations cannot be negative")
        previous = self.state
        high = previous.consecutive_high_windows
        low = previous.consecutive_low_windows
        if p95_ms > previous.latency_threshold_ms:
            high += 1
            low = 0
        elif p95_ms < 0.90 * previous.latency_threshold_ms:
            low += 1
            high = 0
        else:
            high = low = 0
        micro_batch = previous.micro_batch
        delay_ms = previous.delay_ms
        changed = False
        if high >= 2:
            high = 0
            if micro_batch > self.minimum_micro_batch:
                micro_batch = max(self.minimum_micro_batch, micro_batch // 2)
                changed = True
            elif delay_ms < self.maximum_delay_ms:
                delay_ms = min(self.maximum_delay_ms, delay_ms + self.delay_step_ms)
                changed = True
        elif low >= 5:
            low = 0
            if delay_ms > 0:
                delay_ms = max(0, delay_ms - self.delay_step_ms)
                changed = True
            elif micro_batch < previous.configured_micro_batch:
                micro_batch = min(previous.configured_micro_batch, micro_batch * 2)
                changed = True
        self.state = replace(
            previous,
            micro_batch=micro_batch,
            delay_ms=delay_ms,
            consecutive_high_windows=high,
            consecutive_low_windows=low,
            transition_count=previous.transition_count + int(changed),
        )
        self._persist_state()
        if changed:
            self._record_transition(
                previous=previous,
                p95_ms=p95_ms,
                kafka_lag=kafka_lag,
            )
        return self.state


class TrainerBackpressureHooks(Protocol):
    def apply_backpressure(self, *, micro_batch: int, delay_ms: int) -> None: ...


class BackpressureOrchestrator:
    """Apply each persisted state to the live trainer at a window boundary."""

    def __init__(
        self,
        controller: PersistedBackpressureController,
        trainer: TrainerBackpressureHooks,
    ) -> None:
        self.controller = controller
        self.trainer = trainer

    def observe_window(self, *, p95_ms: float, kafka_lag: int) -> BackpressureState:
        state = self.controller.observe(p95_ms=p95_ms, kafka_lag=kafka_lag)
        self.trainer.apply_backpressure(
            micro_batch=state.micro_batch,
            delay_ms=state.delay_ms,
        )
        return state


class OnlineTrainerPacingAdapter:
    """Apply benchmark pacing to an OnlineTrainer-compatible consumer loop."""

    def __init__(self, trainer, *, sleep=time.sleep) -> None:
        self.trainer = trainer
        self._sleep = sleep
        self.micro_batch = 2
        self.delay_ms = 0

    def apply_backpressure(self, *, micro_batch: int, delay_ms: int) -> None:
        if not 2 <= micro_batch <= 1024:
            raise ValueError("micro-batch is outside dashboard limits")
        if not 0 <= delay_ms <= 500 or delay_ms % 25:
            raise ValueError("trainer delay is outside benchmark limits")
        self.micro_batch = micro_batch
        self.delay_ms = delay_ms

    def process_once(self, *, poll_timeout_seconds: float = 0.1) -> int:
        processed = int(
            self.trainer.process_available(
                max_records=self.micro_batch,
                poll_timeout_seconds=poll_timeout_seconds,
            )
        )
        if processed and self.delay_ms:
            self._sleep(self.delay_ms / 1_000)
        return processed


__all__ = [
    "BackpressureOrchestrator",
    "BackpressureState",
    "OnlineTrainerPacingAdapter",
    "PersistedBackpressureController",
    "TrainerBackpressureHooks",
]
