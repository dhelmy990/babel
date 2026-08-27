"""Persistable deterministic schedule and bounded creator execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5


@dataclass(frozen=True, slots=True)
class ScheduledWork:
    creator_id: UUID
    creator_event_number: int
    period: Literal["2026-06", "2026-07"]
    source_article_key: str
    root_babel_id: UUID

    def __post_init__(self) -> None:
        if self.creator_event_number < 0:
            raise ValueError("creator event number must be nonnegative")
        if not self.source_article_key.startswith("enwiki:"):
            raise ValueError("scheduled source must use a canonical article key")


@dataclass(frozen=True, slots=True)
class ScheduledSession:
    run_id: UUID
    schedule_index: int
    creator_id: UUID
    creator_event_number: int
    traversal_session_id: UUID
    period: Literal["2026-06", "2026-07"]
    source_article_key: str
    root_babel_id: UUID
    work_id: UUID
    workload_sha256: str


def deterministic_schedule(
    run_id: UUID,
    work_items: Iterable[ScheduledWork],
) -> tuple[ScheduledSession, ...]:
    items = list(work_items)
    if not items:
        raise ValueError("schedule needs at least one work item")
    identities = [(row.creator_id, row.creator_event_number) for row in items]
    if len(set(identities)) != len(identities):
        raise ValueError("creator event identity must be unique")
    sources = [(row.creator_id, row.source_article_key) for row in items]
    if len(set(sources)) != len(sources):
        raise ValueError("a creator cannot schedule the same source twice")
    rows = []
    for item in items:
        schedule_index = len(rows)
        work_id = uuid5(
            run_id, f"work:{item.creator_id}:{item.creator_event_number}"
        )
        payload = {
            "creatorEventNumber": item.creator_event_number,
            "creatorId": str(item.creator_id),
            "period": item.period,
            "rootBabelId": str(item.root_babel_id),
            "runId": str(run_id),
            "sourceArticleKey": item.source_article_key,
            "workId": str(work_id),
        }
        checksum = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        rows.append(
            ScheduledSession(
                run_id=run_id,
                schedule_index=schedule_index,
                creator_id=item.creator_id,
                creator_event_number=item.creator_event_number,
                traversal_session_id=uuid5(
                    run_id,
                    f"traversal:{item.creator_id}:{item.creator_event_number}",
                ),
                period=item.period,
                source_article_key=item.source_article_key,
                root_babel_id=item.root_babel_id,
                work_id=work_id,
                workload_sha256=checksum,
            )
        )
    return tuple(rows)


class BoundedCreatorScheduler:
    """Overlap sessions without ever overlapping one creator with itself."""

    def __init__(self, *, concurrent_users: int) -> None:
        if concurrent_users <= 0:
            raise ValueError("concurrent users must be positive")
        self.concurrent_users = concurrent_users

    def run(
        self,
        schedule: Sequence[ScheduledSession],
        execute: Callable[[ScheduledSession], None],
        *,
        after_wave: Callable[[tuple[ScheduledSession, ...]], None] | None = None,
    ) -> tuple[ScheduledSession, ...]:
        waves = deterministic_waves(
            schedule, concurrent_users=self.concurrent_users
        )
        dispatched: list[ScheduledSession] = []
        with ThreadPoolExecutor(max_workers=self.concurrent_users) as pool:
            for wave in waves:
                futures = [pool.submit(execute, row) for row in wave]
                dispatched.extend(wave)
                for future in futures:
                    future.result()
                if after_wave is not None:
                    after_wave(wave)
        return tuple(dispatched)


def deterministic_waves(
    schedule: Sequence[ScheduledSession], *, concurrent_users: int
) -> tuple[tuple[ScheduledSession, ...], ...]:
    """Freeze dispatch batches without making completion timing part of the schedule."""
    if concurrent_users <= 0:
        raise ValueError("concurrent users must be positive")
    if [row.schedule_index for row in schedule] != list(range(len(schedule))):
        raise ValueError("persisted schedule indexes must be contiguous")
    next_event: dict[UUID, int] = {}
    for row in schedule:
        expected = next_event.get(row.creator_id, 0)
        if row.creator_event_number != expected:
            raise ValueError("creator-local event numbers must be contiguous")
        next_event[row.creator_id] = expected + 1

    waves: list[tuple[ScheduledSession, ...]] = []
    wave: list[ScheduledSession] = []
    creators: set[UUID] = set()
    for row in schedule:
        if len(wave) == concurrent_users or row.creator_id in creators:
            waves.append(tuple(wave))
            wave = []
            creators = set()
        wave.append(row)
        creators.add(row.creator_id)
    if wave:
        waves.append(tuple(wave))
    return tuple(waves)


__all__ = [
    "BoundedCreatorScheduler",
    "ScheduledSession",
    "ScheduledWork",
    "deterministic_schedule",
    "deterministic_waves",
]
