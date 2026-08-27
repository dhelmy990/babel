"""Non-authoritative dashboard telemetry for live performance conditions."""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID


class PersistedConditionProgress:
    """Coalesce runner observations and persist them off the request event loop."""

    def __init__(
        self,
        *,
        database: Any,
        experiment_id: UUID,
        condition_index: int,
        condition_count: int,
        interval_seconds: float = 1.0,
    ) -> None:
        if condition_index <= 0 or condition_count < condition_index:
            raise ValueError("condition progress matrix position is invalid")
        if interval_seconds <= 0:
            raise ValueError("condition progress interval must be positive")
        self._database = database
        self._experiment_id = experiment_id
        self._condition_index = condition_index
        self._condition_count = condition_count
        self._interval_seconds = interval_seconds
        self._condition = threading.Condition()
        self._latest: Any | None = None
        self._generation = 0
        self._written_generation = 0
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._failure: str | None = None

    def __enter__(self) -> "PersistedConditionProgress":
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"babel-condition-progress-{self._condition_index}",
        )
        self._thread.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self._interval_seconds + 1.0))
            if self._thread.is_alive():
                self._failure = "condition progress reporter did not stop"

    def publish(self, snapshot: Any) -> None:
        """Accept the latest immutable runner snapshot without performing I/O."""
        with self._condition:
            self._latest = snapshot
            self._generation += 1

    def _persist(self, snapshot: Any) -> None:
        self._database.append_performance_progress(
            self._experiment_id,
            phase=str(snapshot.phase),
            condition_index=self._condition_index,
            condition_count=self._condition_count,
            seeded_articles=10_000,
            created_babels=10_000,
            indexed_babels=10_000,
            requested=int(snapshot.total),
            completed=int(snapshot.completed),
            elapsed_seconds=float(snapshot.elapsed_seconds),
            recent_rate=float(snapshot.recent_rate),
            draining=str(snapshot.phase) == "draining",
            telemetry={
                "submitted": int(snapshot.submitted),
                "errors": int(snapshot.errors),
                "inFlight": int(snapshot.in_flight),
                "conditionPhase": str(snapshot.phase),
            },
        )

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait(timeout=self._interval_seconds)
                snapshot = (
                    self._latest
                    if self._generation != self._written_generation
                    else None
                )
                generation = self._generation
                stopping = self._stopping
            if snapshot is not None:
                try:
                    self._persist(snapshot)
                except Exception as error:
                    self._failure = str(error)
                finally:
                    with self._condition:
                        self._written_generation = max(
                            self._written_generation, generation
                        )
            if stopping:
                with self._condition:
                    if self._written_generation == self._generation:
                        return

    @property
    def failure(self) -> str | None:
        return self._failure


__all__ = ["PersistedConditionProgress"]
