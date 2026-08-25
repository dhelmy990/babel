"""Deterministic identity continuity across representative snapshot periods."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    period: str
    article_key: str
    page_id: int
    canonical_title: str
    wikidata_id: str | None

    def __post_init__(self) -> None:
        if not self.period or not self.article_key or not self.canonical_title:
            raise ValueError("identity strings must be nonblank")
        if not isinstance(self.page_id, int) or isinstance(self.page_id, bool):
            raise ValueError("page_id must be an integer")
        if self.wikidata_id is not None and not self.wikidata_id:
            raise ValueError("wikidata_id must be nonblank or null")


@dataclass(frozen=True, slots=True)
class CrosswalkRow:
    lineage_id: str
    period: str
    article_key: str
    page_id: int
    canonical_title: str
    wikidata_id: str | None
    change_kind: str
    match_basis: str


@dataclass(frozen=True, slots=True)
class AmbiguityFinding:
    code: str
    periods: tuple[str, ...]
    article_keys: tuple[str, ...]
    page_ids: tuple[int, ...]
    wikidata_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CrosswalkResult(Sequence[CrosswalkRow]):
    rows: tuple[CrosswalkRow, ...]
    ambiguities: tuple[AmbiguityFinding, ...]

    def __getitem__(self, index: int | slice) -> CrosswalkRow | tuple[CrosswalkRow, ...]:
        return self.rows[index]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[CrosswalkRow]:
        return iter(self.rows)


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _finding(
    code: str, indexes: Iterable[int], rows: list[SnapshotIdentity]
) -> AmbiguityFinding:
    selected = sorted(
        (rows[index] for index in indexes),
        key=lambda row: (row.period, row.article_key),
    )
    return AmbiguityFinding(
        code=code,
        periods=tuple(row.period for row in selected),
        article_keys=tuple(row.article_key for row in selected),
        page_ids=tuple(row.page_id for row in selected),
        wikidata_ids=tuple(sorted({row.wikidata_id for row in selected if row.wikidata_id})),
    )


def build_crosswalk(
    identities: Iterable[SnapshotIdentity],
    *,
    period_order: Sequence[str],
) -> CrosswalkResult:
    """Build lineages using unique QIDs, then page IDs only for missing QIDs."""
    rows = list(identities)
    if not rows:
        return CrosswalkResult((), ())
    if len(set(period_order)) != len(period_order):
        raise ValueError("period_order must contain unique periods")
    period_rank = {period: index for index, period in enumerate(period_order)}
    if any(row.period not in period_rank for row in rows):
        raise ValueError("identity period is absent from period_order")
    identities_seen = {(row.period, row.article_key) for row in rows}
    if len(identities_seen) != len(rows):
        raise ValueError("period/article_key identities must be unique")

    links = _DisjointSet(len(rows))
    ambiguous: set[int] = set()
    findings: list[AmbiguityFinding] = []
    by_qid: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row.wikidata_id is not None:
            by_qid[row.wikidata_id].append(index)
    for indexes in by_qid.values():
        periods = [rows[index].period for index in indexes]
        if len(set(periods)) != len(periods):
            ambiguous.update(indexes)
            findings.append(_finding("qid_not_unique_within_period", indexes, rows))
            continue
        for index in indexes[1:]:
            links.union(indexes[0], index)

    page_linked: set[int] = set()
    by_page: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_page[row.page_id].append(index)
    for indexes in by_page.values():
        if len(indexes) < 2 or any(index in ambiguous for index in indexes):
            continue
        periods = [rows[index].period for index in indexes]
        if len(set(periods)) != len(periods):
            ambiguous.update(indexes)
            findings.append(_finding("page_id_not_unique_within_period", indexes, rows))
            continue
        qids = {rows[index].wikidata_id for index in indexes if rows[index].wikidata_id}
        if len(qids) > 1:
            ambiguous.update(indexes)
            findings.append(_finding("page_id_conflicting_qids", indexes, rows))
            continue
        if any(rows[index].wikidata_id is None for index in indexes):
            for index in indexes[1:]:
                links.union(indexes[0], index)
            page_linked.update(indexes)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        root = index if index in ambiguous else links.find(index)
        groups[root].append(index)
    observed_ranks = [period_rank[row.period] for row in rows]
    observed_min = min(observed_ranks)
    observed_max = max(observed_ranks)
    output: list[CrosswalkRow] = []
    for indexes in groups.values():
        members = sorted(
            indexes,
            key=lambda index: (
                period_rank[rows[index].period],
                rows[index].article_key,
            ),
        )
        member_rows = [rows[index] for index in members]
        member_ranks = [period_rank[row.period] for row in member_rows]
        is_ambiguous = any(index in ambiguous for index in members)
        qids = sorted({row.wikidata_id for row in member_rows if row.wikidata_id})
        if is_ambiguous:
            lineage_id = (
                f"qid:{qids[0]}"
                if len(qids) == 1 and len(by_qid[qids[0]]) == 1
                else f"ambiguous:{member_rows[0].period}:{member_rows[0].article_key}"
            )
            match_basis = "ambiguous"
            change_kind = "ambiguous"
        else:
            lineage_id = (
                f"qid:{qids[0]}"
                if qids
                else f"page:{member_rows[0].page_id}"
                if len(member_rows) > 1
                else f"article:{member_rows[0].period}:{member_rows[0].article_key}"
            )
            match_basis = (
                "page_id"
                if any(index in page_linked for index in members)
                else "qid"
                if qids
                else "new"
            )
            has_gap = member_ranks != list(range(member_ranks[0], member_ranks[-1] + 1))
            if has_gap or len({row.page_id for row in member_rows}) > 1:
                change_kind = "recreated"
            elif member_ranks[-1] < observed_max:
                change_kind = "deleted"
            elif member_ranks[0] > observed_min:
                change_kind = "created"
            elif len({row.canonical_title for row in member_rows}) > 1:
                change_kind = "moved"
            else:
                change_kind = "unchanged"
        output.extend(
            CrosswalkRow(
                lineage_id=lineage_id,
                period=row.period,
                article_key=row.article_key,
                page_id=row.page_id,
                canonical_title=row.canonical_title,
                wikidata_id=row.wikidata_id,
                change_kind=change_kind,
                match_basis=match_basis,
            )
            for row in member_rows
        )
    output.sort(key=lambda row: (period_rank[row.period], row.article_key, row.lineage_id))
    findings.sort(key=lambda finding: (finding.code, finding.periods, finding.article_keys))
    return CrosswalkResult(tuple(output), tuple(findings))


__all__ = [
    "AmbiguityFinding",
    "CrosswalkResult",
    "CrosswalkRow",
    "SnapshotIdentity",
    "build_crosswalk",
]
