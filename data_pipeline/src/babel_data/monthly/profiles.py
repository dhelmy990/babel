"""Build the dashboard source catalog from the backend profile manifest."""

from __future__ import annotations

import bz2
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProfileAssignment:
    """One authoritative creator/title assignment declared by the backend."""

    creator_slug: str
    declared_title: str


@dataclass(frozen=True)
class MultistreamRange:
    """One complete bzip2 stream selected through its index entries."""

    start: int
    end_exclusive: int
    titles: tuple[str, ...]
    page_ids: tuple[int, ...]


_CREATOR_PATTERN = re.compile(
    r'\{\s*"(?P<slug>[^"]+)",\s*"[^"]+",\s*"#[0-9A-Fa-f]{6}",'
    r'\s*\{(?P<titles>[^}]*)\}\s*\}',
    re.DOTALL,
)
_QUOTED_PATTERN = re.compile(r'"([^"]+)"')


def normalize_backend_title(value: str) -> str:
    """Apply the exact lookup-key normalization used by the dashboard adapter."""
    normalized = " ".join(value.replace("_", " ").split())
    if normalized and "a" <= normalized[0] <= "z":
        normalized = normalized[0].upper() + normalized[1:]
    return normalized


def extract_profile_manifest_assignments(path: Path) -> list[ProfileAssignment]:
    """Extract generated assignments from ``ProfileManifest`` source text."""
    source = path.read_text(encoding="utf-8")
    assignments: list[ProfileAssignment] = []
    for match in _CREATOR_PATTERN.finditer(source):
        titles = _QUOTED_PATTERN.findall(match.group("titles"))
        if len(titles) != 4:
            raise ValueError(
                f"profile {match.group('slug')!r} must declare exactly four titles"
            )
        assignments.extend(
            ProfileAssignment(match.group("slug"), title) for title in titles
        )
    if len(assignments) != 80:
        raise ValueError(
            "ProfileManifest must declare exactly 80 assignments, "
            f"found {len(assignments)}"
        )
    return assignments


def plan_multistream_ranges(
    index_path: Path, titles: set[str], *, dump_size: int
) -> list[MultistreamRange]:
    """Locate exact title keys and group them by complete multistream range."""
    wanted = {normalize_backend_title(title) for title in titles}
    matches: dict[str, tuple[int, int, str]] = {}
    selected_by_offset: dict[int, list[tuple[str, int]]] = {}
    ends: dict[int, int] = {}
    prior_offset: int | None = None
    with bz2.open(index_path, "rt", encoding="utf-8", errors="strict") as source:
        for line_number, raw_line in enumerate(source, 1):
            fields = raw_line.rstrip("\n").split(":", 2)
            if len(fields) != 3:
                raise ValueError(f"malformed multistream index line {line_number}")
            try:
                offset = int(fields[0])
                page_id = int(fields[1])
            except ValueError as error:
                raise ValueError(
                    f"malformed multistream index identity at line {line_number}"
                ) from error
            if prior_offset is not None and offset != prior_offset:
                if prior_offset in selected_by_offset:
                    ends[prior_offset] = offset
            prior_offset = offset
            title = normalize_backend_title(fields[2])
            if title not in wanted:
                continue
            if title in matches:
                raise ValueError(f"duplicate exact multistream index title: {title}")
            matches[title] = (offset, page_id, fields[2])
            selected_by_offset.setdefault(offset, []).append((fields[2], page_id))
    missing = sorted(wanted - set(matches))
    if missing:
        raise ValueError(f"multistream index title not found: {missing[0]}")
    for offset in selected_by_offset:
        ends.setdefault(offset, dump_size)
    return [
        MultistreamRange(
            start=offset,
            end_exclusive=ends[offset],
            titles=tuple(sorted(title for title, _page_id in entries)),
            page_ids=tuple(page_id for _title, page_id in sorted(entries)),
        )
        for offset, entries in sorted(selected_by_offset.items())
    ]


def build_profile_catalog(
    source_rows: Sequence[Mapping[str, object]],
    assignments: Sequence[ProfileAssignment],
    *,
    period: str,
) -> list[dict[str, object]]:
    """Build the 78-row article catalog resolving all 80 backend assignments."""
    if period not in {"2026-06", "2026-07"}:
        raise ValueError("profile catalog period must be 2026-06 or 2026-07")
    authoritative = {
        normalize_backend_title(assignment.declared_title) for assignment in assignments
    }
    if len(assignments) != 80 or len(authoritative) != 78:
        raise ValueError("profile catalog requires 80 assignments and 78 title keys")

    catalog: list[dict[str, object]] = []
    lookup: dict[str, int] = {}
    for source in source_rows:
        page_id = source.get("page_id")
        canonical_title = source.get("canonical_title")
        redirects = source.get("redirect_titles")
        article_text = source.get("article_text")
        revision_id = source.get("source_revision_id")
        if (
            not isinstance(page_id, int)
            or isinstance(page_id, bool)
            or page_id <= 0
            or not isinstance(canonical_title, str)
            or not canonical_title
            or not isinstance(redirects, list)
            or any(not isinstance(title, str) or not title for title in redirects)
            or not isinstance(article_text, str)
            or not article_text
            or not isinstance(revision_id, int)
            or isinstance(revision_id, bool)
            or revision_id <= 0
        ):
            raise ValueError("profile source row has invalid article fields")
        row: dict[str, object] = {
            "article_key": f"enwiki:{page_id}",
            "snapshot": period,
            "page_id": page_id,
            "canonical_title": canonical_title,
            "redirect_titles": sorted(set(redirects)),
            "article_text": article_text,
            "lead_text": source.get("lead_text", ""),
            "content_hash": hashlib.sha256(article_text.encode("utf-8")).hexdigest(),
            "source_revision_id": revision_id,
            "wikidata_id": source.get("wikidata_id"),
            "release_scope": "friday_demo_fixture",
            "source_snapshot": "enwiki-20161001",
        }
        if not row["lead_text"]:
            lead = next(
                (
                    paragraph.strip()
                    for paragraph in article_text.split("\n\n")
                    if paragraph.strip()
                ),
                "",
            )
            if not lead:
                raise ValueError("profile source row has no usable prepared lead")
            row["lead_text"] = lead
            row["lead_derivation"] = "first_nonempty_paragraph_fallback"
        row_index = len(catalog)
        for title in (canonical_title, *row["redirect_titles"]):
            key = normalize_backend_title(title)
            previous = lookup.get(key)
            if previous is not None and previous != row_index:
                raise ValueError(f"ambiguous profile title key: {key}")
            lookup[key] = row_index
        catalog.append(row)

    if len(catalog) != 78 or len({row["page_id"] for row in catalog}) != 78:
        raise ValueError("profile catalog must contain 78 unique page IDs")
    missing = sorted(authoritative - set(lookup))
    if missing:
        raise ValueError(f"authoritative profile title does not resolve: {missing[0]}")
    resolved = {lookup[title] for title in authoritative}
    if resolved != set(range(78)):
        raise ValueError(
            "authoritative profile titles must cover every catalog row once"
        )
    return sorted(catalog, key=lambda row: int(row["page_id"]))


__all__ = [
    "ProfileAssignment",
    "MultistreamRange",
    "build_profile_catalog",
    "extract_profile_manifest_assignments",
    "normalize_backend_title",
    "plan_multistream_ranges",
]
