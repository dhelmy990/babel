"""Deterministic, time-boxed selection for real monthly engineering snapshots."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass


PERIODS = ("2026-06", "2026-07")


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    """One namespace-zero candidate resolved through a monthly dump index."""

    period: str
    page_id: int
    canonical_title: str
    traffic: int
    priority: int = 3

    def __post_init__(self) -> None:
        if self.period not in PERIODS:
            raise ValueError("candidate period must be 2026-06 or 2026-07")
        if not isinstance(self.page_id, int) or isinstance(self.page_id, bool) or self.page_id <= 0:
            raise ValueError("candidate page_id must be a positive integer")
        if not self.canonical_title.strip():
            raise ValueError("candidate title must be nonblank")
        if self.traffic < 0:
            raise ValueError("candidate traffic must be nonnegative")
        if self.priority not in range(4):
            raise ValueError("candidate priority must be 0..3")


@dataclass(frozen=True, slots=True)
class EngineeringSnapshotPolicyV1:
    target_rows: int = 10_000
    minimum_rows: int = 5_000
    shared_numerator: int = 4
    shared_denominator: int = 5
    deadline_seconds: float = 45 * 60
    seed: str = "babel-monthly-engineering-v1"
    relation_cap: int = 250_000

    def __post_init__(self) -> None:
        if self.target_rows < self.minimum_rows or self.minimum_rows <= 0:
            raise ValueError("policy row targets are invalid")
        if self.target_rows % self.shared_denominator or self.minimum_rows % self.shared_denominator:
            raise ValueError("row targets must be divisible by five")
        if (self.shared_numerator, self.shared_denominator) != (4, 5):
            raise ValueError("policy v1 requires a four-fifths shared catalog")
        if self.deadline_seconds <= 0 or self.relation_cap <= 0:
            raise ValueError("policy limits must be positive")


@dataclass(frozen=True, slots=True)
class JointSelection:
    rows_per_month: int
    shared_page_ids: tuple[int, ...]
    june_supplement_page_ids: tuple[int, ...]
    july_supplement_page_ids: tuple[int, ...]
    june_elapsed_seconds: float
    july_elapsed_seconds: float
    ordered_identity_sha256: str
    policy_version: int = 1
    identity_basis: str = "page_id"

    @property
    def union_page_ids(self) -> frozenset[int]:
        return frozenset(
            (*self.shared_page_ids, *self.june_supplement_page_ids, *self.july_supplement_page_ids)
        )

    def page_ids_for(self, period: str) -> tuple[int, ...]:
        if period == "2026-06":
            supplement = self.june_supplement_page_ids
        elif period == "2026-07":
            supplement = self.july_supplement_page_ids
        else:
            raise ValueError("period must be 2026-06 or 2026-07")
        return (*self.shared_page_ids, *supplement)

    def to_document(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "identity_basis": self.identity_basis,
            "rows_per_month": self.rows_per_month,
            "shared_page_ids": list(self.shared_page_ids),
            "june_supplement_page_ids": list(self.june_supplement_page_ids),
            "july_supplement_page_ids": list(self.july_supplement_page_ids),
            "june_elapsed_seconds": self.june_elapsed_seconds,
            "july_elapsed_seconds": self.july_elapsed_seconds,
            "ordered_identity_sha256": self.ordered_identity_sha256,
        }


def _collect(
    rows: Iterable[CandidateIdentity],
    period: str,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[dict[int, CandidateIdentity], float]:
    start = clock()
    last = start
    selected: dict[int, CandidateIdentity] = {}
    for row in rows:
        if row.period != period:
            raise ValueError(f"candidate stream for {period} contains period drift")
        prior = selected.get(row.page_id)
        if prior is not None and prior.canonical_title != row.canonical_title:
            raise ValueError(f"duplicate page_id has conflicting titles: {row.page_id}")
        if prior is None or (row.priority, -row.traffic, row.canonical_title) < (
            prior.priority,
            -prior.traffic,
            prior.canonical_title,
        ):
            selected[row.page_id] = row
        last = clock()
        if last < start:
            raise ValueError("monotonic clock moved backwards")
        if last - start >= deadline:
            break
    elapsed = last - start
    if elapsed < 0:
        raise ValueError("monotonic clock moved backwards")
    # The first row observed at or beyond the deadline closes the frontier.
    # Packaging decides whether that frontier clears the proportional floor.
    return selected, min(elapsed, deadline)


def _rank(row: CandidateIdentity, seed: str) -> tuple[int, int, str, int]:
    digest = hashlib.sha256(f"{seed}\0{row.page_id}".encode()).hexdigest()
    return row.priority, -row.traffic, digest, row.page_id


def freeze_joint_selection(
    june: Iterable[CandidateIdentity],
    july: Iterable[CandidateIdentity],
    *,
    policy: EngineeringSnapshotPolicyV1 | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> JointSelection:
    """Freeze the largest feasible 4:1/4:1 selection under independent timers."""
    policy = policy or EngineeringSnapshotPolicyV1()
    june_rows, june_elapsed = _collect(
        june, "2026-06", deadline=policy.deadline_seconds, clock=clock
    )
    july_rows, july_elapsed = _collect(
        july, "2026-07", deadline=policy.deadline_seconds, clock=clock
    )
    shared_ids = set(june_rows) & set(july_rows)
    june_only = set(june_rows) - shared_ids
    july_only = set(july_rows) - shared_ids

    feasible = min(
        policy.target_rows,
        (len(shared_ids) * policy.shared_denominator) // policy.shared_numerator,
        len(june_only) * policy.shared_denominator,
        len(july_only) * policy.shared_denominator,
    )
    feasible -= feasible % policy.shared_denominator
    if feasible < policy.minimum_rows:
        raise ValueError(
            "monthly source frontier is below the 5,000-row emergency floor; "
            "fixture fallback is forbidden"
        )
    shared_count = feasible * policy.shared_numerator // policy.shared_denominator
    supplement_count = feasible // policy.shared_denominator
    shared = tuple(
        sorted(
            shared_ids,
            key=lambda page_id: min(
                _rank(june_rows[page_id], policy.seed),
                _rank(july_rows[page_id], policy.seed),
            ),
        )[:shared_count]
    )
    june_supplement = tuple(
        sorted(june_only, key=lambda page_id: _rank(june_rows[page_id], policy.seed))[
            :supplement_count
        ]
    )
    july_supplement = tuple(
        sorted(july_only, key=lambda page_id: _rank(july_rows[page_id], policy.seed))[
            :supplement_count
        ]
    )
    identity_document: Mapping[str, object] = {
        "policy_version": 1,
        "seed": policy.seed,
        "shared": shared,
        "june_supplement": june_supplement,
        "july_supplement": july_supplement,
    }
    digest = hashlib.sha256(
        json.dumps(identity_document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return JointSelection(
        rows_per_month=feasible,
        shared_page_ids=shared,
        june_supplement_page_ids=june_supplement,
        july_supplement_page_ids=july_supplement,
        june_elapsed_seconds=june_elapsed,
        july_elapsed_seconds=july_elapsed,
        ordered_identity_sha256=digest,
    )


__all__ = [
    "CandidateIdentity",
    "EngineeringSnapshotPolicyV1",
    "JointSelection",
    "freeze_joint_selection",
]
