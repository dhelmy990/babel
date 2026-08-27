"""Pure canonical planning for the frozen June/July created-Babel population."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID, uuid5

from ..contracts import RunConfigV2
from ..observable import CreatedBabel
from ..runtime.dataset_bundle import (
    DEMO_DATASET_REPOSITORY,
    SCALE_DATASET_CONFIG,
    SCALE_DATASET_REVISION,
    DatasetBundle,
)
from .decisions import deterministic_draw
from .scheduler import ScheduledSession, ScheduledWork, deterministic_schedule

Period = Literal["2026-06", "2026-07"]


@dataclass(frozen=True, slots=True)
class PlannedBabel:
    """One immutable root, its observable source, and its scheduled identity."""

    ordinal: int
    period: Period
    creator_slot: int
    source_row: Mapping[str, Any]
    babel: CreatedBabel
    catalog_content_hash: str
    event_number: int
    scheduled: ScheduledSession

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.creator_slot < 0 or self.event_number < 0:
            raise ValueError("planned population indexes must be nonnegative")
        if self.scheduled.schedule_index != self.ordinal:
            raise ValueError("planned ordinal differs from schedule index")
        if (
            self.scheduled.run_id != self.babel.runId
            or self.scheduled.creator_id != self.babel.creatorId
            or self.scheduled.root_babel_id != self.babel.babelId
            or self.scheduled.source_article_key != self.babel.sourceArticleKey
            or self.scheduled.period != self.period
        ):
            raise ValueError("planned Babel and schedule identities differ")
        if self.source_row.get("article_key") != self.babel.sourceArticleKey:
            raise ValueError("planned source row differs from CreatedBabel")
        if self.source_row.get("content_hash") != self.catalog_content_hash:
            raise ValueError("planned source content hash differs")


@dataclass(frozen=True, slots=True)
class PopulationPlan:
    """One exact formal-cohort, 5k June plus 5k July population."""

    run_id: UUID
    dataset_revision: str
    babels: tuple[PlannedBabel, ...]

    def __post_init__(self) -> None:
        if len(self.babels) != 10_000:
            raise ValueError("formal population plan requires exactly 10,000 Babels")
        if [row.ordinal for row in self.babels] != list(range(10_000)):
            raise ValueError("population ordinals must be contiguous")
        if any(row.babel.runId != self.run_id for row in self.babels):
            raise ValueError("population plan crossed a run boundary")
        period_counts = Counter(row.period for row in self.babels)
        if period_counts != {"2026-06": 5_000, "2026-07": 5_000}:
            raise ValueError(
                "formal population requires 5,000 June and 5,000 July Babels"
            )
        creators = {row.babel.creatorId for row in self.babels}
        if len(creators) not in {50, 100, 500}:
            raise ValueError("formal population requires 50, 100, or 500 creators")
        creator_sources = [
            (row.babel.creatorId, row.babel.sourceArticleKey) for row in self.babels
        ]
        if len(set(creator_sources)) != len(creator_sources):
            raise ValueError("a creator cannot reuse a source across population months")
        if [row.event_number for row in self.babels] != list(range(10_000)):
            raise ValueError("population event numbers must be globally contiguous")

    @property
    def schedule(self) -> tuple[ScheduledSession, ...]:
        return tuple(row.scheduled for row in self.babels)

    @property
    def creator_ids(self) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(row.babel.creatorId for row in self.babels))

    @property
    def period_counts(self) -> dict[str, int]:
        return dict(Counter(row.period for row in self.babels))


def schedule_planned_roots(
    run_id: UUID,
    roots: Sequence[tuple[Period, UUID, Mapping[str, Any], UUID]],
) -> tuple[ScheduledSession, ...]:
    """Apply the one canonical creator-local numbering and schedule algorithm."""
    creator_events: dict[UUID, int] = {}
    work: list[ScheduledWork] = []
    for period, creator_id, article, babel_id in roots:
        event_number = creator_events.get(creator_id, 0)
        creator_events[creator_id] = event_number + 1
        work.append(
            ScheduledWork(
                creator_id=creator_id,
                creator_event_number=event_number,
                period=period,
                source_article_key=str(article["article_key"]),
                root_babel_id=babel_id,
            )
        )
    return deterministic_schedule(run_id, work)


def _validate_formal_inputs(config: RunConfigV2, bundle: DatasetBundle) -> None:
    if not isinstance(config, RunConfigV2):
        raise TypeError("formal population planning requires RunConfigV2")
    if not isinstance(bundle, DatasetBundle):
        raise TypeError("formal population planning requires a real DatasetBundle")
    if (
        config.datasetRepo != DEMO_DATASET_REPOSITORY
        or config.datasetConfig != SCALE_DATASET_CONFIG
        or config.datasetRevision != SCALE_DATASET_REVISION
        or bundle.dataset_repository != config.datasetRepo
        or bundle.dataset_config != config.datasetConfig
        or bundle.dataset_revision != config.datasetRevision
        or bundle.release_scope != "timeboxed_engineering_snapshot"
    ):
        raise ValueError("formal population requires the real scale dataset identity")
    if (
        config.creatorCount not in {50, 100, 500}
        or config.concurrentUsers != config.creatorCount
    ):
        raise ValueError(
            "formal population requires 50, 100, or 500 concurrent creators"
        )
    if (
        config.environmentSequence != ["2026-06", "2026-07"]
        or config.perMonthEventBudget != {"2026-06": 5_000, "2026-07": 5_000}
        or config.targetCreatedBabels != 10_000
        or config.sourceArticlesPerMonth != 5_000
    ):
        raise ValueError(
            "formal population requires exactly 10,000 roots (5,000 per month)"
        )


def plan_population(config: RunConfigV2, bundle: DatasetBundle) -> PopulationPlan:
    """Build the canonical population without database, encoder, clock, or network I/O."""
    _validate_formal_inputs(config, bundle)
    catalogs: dict[Period, tuple[Mapping[str, Any], ...]] = {
        "2026-06": bundle.configs["catalog_2026_06"],
        "2026-07": bundle.configs["catalog_2026_07"],
    }
    creators = tuple(
        uuid5(config.runId, f"creator:{index}") for index in range(config.creatorCount)
    )
    used: dict[UUID, set[str]] = {creator: set() for creator in creators}
    roots: list[tuple[Period, UUID, Mapping[str, Any], UUID]] = []
    sequence = 0
    for period in config.environmentSequence:
        rows = catalogs[period]
        if not rows:
            raise ValueError(f"population catalog is empty: {period}")
        for month_index in range(config.perMonthEventBudget[period]):
            creator_slot = month_index % len(creators)
            creator = creators[creator_slot]
            start = int(
                deterministic_draw(
                    config.runSeed,
                    "source",
                    period,
                    month_index,
                    creator_slot,
                    "",
                    0,
                )
                * len(rows)
            )
            chosen: Mapping[str, Any] | None = None
            for shift in range(len(rows)):
                candidate = rows[(start + shift) % len(rows)]
                source_key = str(candidate.get("article_key", ""))
                if source_key and source_key not in used[creator]:
                    chosen = candidate
                    break
            if chosen is None:
                raise ValueError("creator source support exhausted without replacement")
            used[creator].add(str(chosen["article_key"]))
            babel_id = uuid5(config.runId, f"babel:{period}:{sequence}:{creator}")
            roots.append((period, creator, MappingProxyType(dict(chosen)), babel_id))
            sequence += 1

    schedule = schedule_planned_roots(config.runId, roots)
    planned: list[PlannedBabel] = []
    for ordinal, ((period, creator, article, babel_id), scheduled) in enumerate(
        zip(roots, schedule, strict=True)
    ):
        text = str(article.get("lead_text") or article["article_text"])
        babel = CreatedBabel(
            babelId=babel_id,
            runId=config.runId,
            creatorId=creator,
            sourceArticleKey=str(article["article_key"]),
            title=str(article["canonical_title"]),
            text=text,
            # PostgreSQL timestamps have microsecond precision; bind a unique,
            # exactly round-trippable value without consulting the wall clock.
            createdAtNs=ordinal * 1_000,
        )
        planned.append(
            PlannedBabel(
                ordinal=ordinal,
                period=period,
                creator_slot=ordinal % config.creatorCount,
                source_row=article,
                babel=babel,
                catalog_content_hash=str(article["content_hash"]),
                event_number=ordinal,
                scheduled=scheduled,
            )
        )
    return PopulationPlan(
        run_id=config.runId,
        dataset_revision=config.datasetRevision,
        babels=tuple(planned),
    )


__all__ = [
    "PlannedBabel",
    "PopulationPlan",
    "plan_population",
    "schedule_planned_roots",
]
