"""Exact, auditable reconciliation of teacher vectors to snapshot articles."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from .teacher import DEFAULT_DIMENSION, TeacherRecord
from .wikipedia import WikipediaPage, _page_index, is_non_article_title, normalize_title


SNAPSHOT_DATE = "2016-10-01"
MAX_REDIRECT_CACHE = 100_000


@dataclass(frozen=True, slots=True)
class ReconciledRow:
    """One accepted exact match, retaining the teacher-owned read-only vector."""

    teacher_title: str
    teacher_normalized_title: str
    teacher_vector: np.ndarray
    teacher_norm: float
    page_id: int
    source_revision_id: int | None
    canonical_title: str
    lead_text: str
    article_text: str
    article_key: str
    snapshot_date: str
    split: str
    reconciliation_status: str

    @property
    def model_text(self) -> str:
        return self.canonical_title + "\n\n" + self.lead_text

    def to_document(self) -> dict[str, object]:
        """Return the JSON-compatible ``distillation-example-v1`` document."""
        return {
            "article_key": self.article_key,
            "page_id": self.page_id,
            "canonical_title": self.canonical_title,
            "wikidata_id": None,
            "lead_text": self.lead_text,
            "article_text": self.article_text,
            "teacher_vector": self.teacher_vector.tolist(),
            "teacher_norm": self.teacher_norm,
            "source_revision_id": self.source_revision_id,
            "snapshot_date": self.snapshot_date,
            "split": self.split,
            "reconciliation_status": self.reconciliation_status,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationExclusion:
    teacher_title: str
    normalized_title: str
    reason: str
    detail: str


@dataclass(slots=True)
class ReconciliationResult:
    rows: list[ReconciledRow] = field(default_factory=list)
    exclusions: list[ReconciliationExclusion] = field(default_factory=list)
    input_count: int = 0


@dataclass(frozen=True, slots=True)
class _Resolution:
    status: str
    page: WikipediaPage | None
    hops: int = 0


@dataclass(frozen=True, slots=True)
class _Candidate:
    record: TeacherRecord
    normalized_title: str
    teacher_norm: float
    page: WikipediaPage


@dataclass(frozen=True, slots=True)
class _TeacherInput:
    record: TeacherRecord
    normalized_title: str
    teacher_norm: float


def split_for(article_key: str) -> str:
    """Assign a stable 98/1/1 split using the contracted SHA-256 bucket."""
    if not isinstance(article_key, str) or not article_key:
        raise ValueError("article_key must be a nonempty string")
    bucket = int.from_bytes(
        hashlib.sha256(article_key.encode("utf-8")).digest()[:8], "big"
    ) % 100
    return "train" if bucket < 98 else "validation" if bucket == 98 else "test"


def _resolve_from_index(
    start: str,
    index: dict[str, WikipediaPage],
    ambiguous: set[str],
    invalid: dict[str, str],
    cache: dict[str, _Resolution],
    *,
    max_depth: int,
) -> _Resolution:
    current = start
    path: list[str] = []
    visited: set[str] = set()
    depth = 0
    total_hops = 0
    while True:
        if current in ambiguous:
            resolution = _Resolution("duplicate/ambiguous_title", None)
            break
        if current in invalid:
            resolution = _Resolution("invalid_wikipedia_page", None)
            break
        cached = cache.get(current)
        if cached is not None:
            total_hops = depth + cached.hops
            resolution = (
                cached
                if total_hops <= max_depth
                else _Resolution("redirect_depth_exceeded", None)
            )
            break
        page = index.get(current)
        if page is None:
            resolution = _Resolution(
                "title_not_found" if depth == 0 else "redirect_target_missing",
                None,
            )
            break
        if page.redirect_target is None:
            total_hops = depth
            resolution = _Resolution("resolved", page, hops=0)
            break
        if current in visited:
            resolution = _Resolution("redirect_cycle", None)
            break
        visited.add(current)
        path.append(current)
        if depth >= max_depth:
            resolution = _Resolution("redirect_depth_exceeded", None)
            break
        current = normalize_title(page.redirect_target)
        depth += 1

    if resolution.status == "resolved":
        cache_entries: dict[str, _Resolution] = {
            key: _Resolution("resolved", resolution.page, total_hops - offset)
            for offset, key in enumerate(path)
        }
        if not path:
            cache_entries[start] = resolution
    else:
        cache_entries = {}
    if len(cache) + len(cache_entries) > MAX_REDIRECT_CACHE:
        cache.clear()
    for key, cached_resolution in cache_entries.items():
        if len(cache) >= MAX_REDIRECT_CACHE:
            break
        cache[key] = cached_resolution
    return resolution


def _valid_teacher(record: object) -> tuple[bool, str, float]:
    if not isinstance(record, TeacherRecord):
        return False, "record is not a TeacherRecord", 0.0
    if not isinstance(record.title, str):
        return False, "teacher title is not text", 0.0
    key = normalize_title(record.title)
    vector = record.vector
    if not key:
        return False, "teacher title has an empty normalized identity", 0.0
    if (
        not isinstance(vector, np.ndarray)
        or vector.shape != (DEFAULT_DIMENSION,)
        or vector.dtype != np.dtype(np.float32)
        or not vector.flags.c_contiguous
        or not vector.flags.owndata
        or vector.flags.writeable
        or not np.isfinite(vector).all()
    ):
        return False, "teacher vector violates the owned read-only 100d contract", 0.0
    norm = math.sqrt(math.fsum(float(value) * float(value) for value in vector))
    if not math.isfinite(norm) or norm <= 0.0:
        return False, "teacher vector norm is not positive and finite", 0.0
    return True, "", norm


def validate_teacher_record(record: object) -> tuple[str, float]:
    """Return the normalized title and stable norm for one valid teacher row."""
    valid, detail, norm = _valid_teacher(record)
    if not valid:
        raise ValueError(detail)
    assert isinstance(record, TeacherRecord)
    return normalize_title(record.title), norm


def _exclude(
    result: ReconciliationResult,
    record: object,
    normalized_title: str,
    reason: str,
    detail: str,
) -> None:
    title = (
        record.title
        if isinstance(record, TeacherRecord) and isinstance(record.title, str)
        else repr(record)
    )
    result.exclusions.append(
        ReconciliationExclusion(title, normalized_title, reason, detail)
    )


def reconcile(
    teacher: Iterable[TeacherRecord],
    pages: Iterable[WikipediaPage],
    *,
    max_redirect_depth: int = 16,
) -> ReconciliationResult:
    """Join every teacher input to one row or one explicit exclusion.

    Callers processing the full dump should pass the normalized teacher titles
    to :func:`babel_data.wikipedia.iter_wikipedia_pages` as ``title_filter``;
    reconciliation then retains only those candidate/redirect-closure pages.
    """
    if (
        not isinstance(max_redirect_depth, int)
        or isinstance(max_redirect_depth, bool)
        or max_redirect_depth < 0
    ):
        raise ValueError("max_redirect_depth must be a nonnegative integer")
    index, ambiguous, invalid = _page_index(pages)
    cache: dict[str, _Resolution] = {}
    result = ReconciliationResult()
    candidates: list[_Candidate] = []
    teacher_groups: dict[str, list[_TeacherInput]] = {}

    for record in teacher:
        result.input_count += 1
        valid, invalid_detail, teacher_norm = _valid_teacher(record)
        normalized = (
            normalize_title(record.title)
            if isinstance(record, TeacherRecord) and isinstance(record.title, str)
            else ""
        )
        if not valid:
            _exclude(
                result,
                record,
                normalized,
                "invalid_teacher_source",
                invalid_detail,
            )
            continue
        teacher_groups.setdefault(normalized, []).append(
            _TeacherInput(record, normalized, teacher_norm)
        )

    selected_teachers: list[_TeacherInput] = []
    for normalized, duplicates in teacher_groups.items():
        winner = min(
            duplicates,
            key=lambda item: (
                item.record.title != normalized,
                item.record.title,
                item.record.vector.tobytes(),
            ),
        )
        selected_teachers.append(winner)
        for duplicate in duplicates:
            if duplicate is winner:
                continue
            _exclude(
                result,
                duplicate.record,
                normalized,
                "duplicate/ambiguous_title",
                f"normalized teacher identity was assigned to "
                f"{winner.record.title!r}",
            )

    for selected in sorted(
        selected_teachers,
        key=lambda item: (item.normalized_title, item.record.title),
    ):
        record = selected.record
        normalized = selected.normalized_title
        teacher_norm = selected.teacher_norm

        if normalized not in index and is_non_article_title(normalized):
            _exclude(
                result,
                record,
                normalized,
                "non_article_namespace",
                "title names a namespace excluded from article extraction",
            )
            continue
        resolution = _resolve_from_index(
            normalized,
            index,
            ambiguous,
            invalid,
            cache,
            max_depth=max_redirect_depth,
        )
        if resolution.page is None:
            _exclude(
                result,
                record,
                normalized,
                resolution.status,
                "exact normalized snapshot identity did not resolve to an article",
            )
            continue
        page = resolution.page
        if not page.article_text.strip():
            _exclude(
                result,
                record,
                normalized,
                "empty_text",
                "resolved snapshot page has no useful article text",
            )
            continue
        if not page.lead_text.strip():
            _exclude(
                result,
                record,
                normalized,
                "empty_lead",
                "resolved snapshot page has no useful lead text",
            )
            continue

        candidates.append(_Candidate(record, normalized, teacher_norm, page))

    by_article_key: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        article_key = f"enwiki:{SNAPSHOT_DATE}:{candidate.page.page_id}"
        by_article_key.setdefault(article_key, []).append(candidate)

    for article_key, collisions in by_article_key.items():
        winner = min(
            collisions,
            key=lambda candidate: (
                candidate.normalized_title
                != normalize_title(candidate.page.canonical_title),
                candidate.normalized_title,
                candidate.record.title,
            ),
        )
        for loser in collisions:
            if loser is winner:
                continue
            _exclude(
                result,
                loser.record,
                loser.normalized_title,
                "canonical_identity_collision",
                f"canonical snapshot identity {article_key} for "
                f"{winner.page.canonical_title!r} was assigned to teacher "
                f"{winner.record.title!r}",
            )

        page = winner.page
        result.rows.append(
            ReconciledRow(
                teacher_title=winner.record.title,
                teacher_normalized_title=winner.normalized_title,
                teacher_vector=winner.record.vector,
                teacher_norm=winner.teacher_norm,
                page_id=page.page_id,
                source_revision_id=page.revision_id,
                canonical_title=page.canonical_title,
                lead_text=page.lead_text,
                article_text=page.article_text,
                article_key=article_key,
                snapshot_date=SNAPSHOT_DATE,
                split=split_for(article_key),
                reconciliation_status=(
                    "matched"
                    if winner.normalized_title
                    == normalize_title(page.canonical_title)
                    else "redirect_resolved"
                ),
            )
        )

    result.rows.sort(
        key=lambda row: (row.article_key, row.teacher_normalized_title, row.teacher_title)
    )
    result.exclusions.sort(
        key=lambda item: (
            item.normalized_title,
            item.teacher_title,
            item.reason,
            item.detail,
        )
    )
    return result


__all__ = [
    "ReconciledRow",
    "ReconciliationExclusion",
    "ReconciliationResult",
    "reconcile",
    "split_for",
    "validate_teacher_record",
]
