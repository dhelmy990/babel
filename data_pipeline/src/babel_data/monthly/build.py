"""Pure builders for real monthly observed catalogs and hidden relations."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from babel_data.wikipedia import extract_lead, wikitext_to_plain_text

from .catalog import HIDDEN_ARTICLE_FIELDS
from .sources import normalize_dump_title


RELEASE_SCOPE = "10k_timeboxed_engineering_snapshot"
_SECTION = re.compile(r"(?m)^\s*={2,6}\s*.*?\s*={2,6}\s*$")
_MAX_PAGE_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HiddenRelations:
    edges: tuple[dict[str, object], ...]
    clickstream: tuple[dict[str, object], ...]
    edge_available: int
    clickstream_available: int
    edge_cap: int
    clickstream_cap: int


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _iter_page_elements(path: Path) -> Iterable[ET.Element]:
    buffer = bytearray()
    with bz2.open(path, "rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if block:
                buffer.extend(block)
            while True:
                start = buffer.find(b"<page>")
                if start < 0:
                    if len(buffer) > _MAX_PAGE_BYTES:
                        del buffer[:-32]
                    break
                end = buffer.find(b"</page>", start)
                if end < 0:
                    if len(buffer) - start > _MAX_PAGE_BYTES:
                        raise ValueError("monthly XML page exceeds the bounded parser limit")
                    if start:
                        del buffer[:start]
                    break
                end += len(b"</page>")
                payload = bytes(buffer[start:end])
                del buffer[:end]
                try:
                    yield ET.fromstring(payload)
                except ET.ParseError as error:
                    raise ValueError(f"malformed selected monthly XML page: {error}") from error
            if not block:
                break


def _first_useful_section(wikitext: str) -> str:
    headings = list(_SECTION.finditer(wikitext))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(wikitext)
        section = wikitext_to_plain_text(wikitext[heading.end() : end])
        if section:
            return section
    return ""


def extract_selected_articles(
    selected_ranges_path: Path,
    page_ids: set[int],
    *,
    period: str,
) -> list[dict[str, object]]:
    """Extract bounded prepared text from previously mirrored BZ2 members."""
    if period not in {"2026-06", "2026-07"}:
        raise ValueError("period must be 2026-06 or 2026-07")
    found: list[dict[str, object]] = []
    for page in _iter_page_elements(selected_ranges_path):
        namespace = _child(page, "ns")
        page_id_element = _child(page, "id")
        title_element = _child(page, "title")
        if namespace is None or namespace.text != "0" or page_id_element is None:
            continue
        try:
            page_id = int(page_id_element.text or "")
        except ValueError:
            continue
        if page_id not in page_ids:
            continue
        if _child(page, "redirect") is not None:
            continue
        revision = _child(page, "revision")
        if revision is None or title_element is None or not title_element.text:
            continue
        text_element = _child(revision, "text")
        if text_element is None:
            continue
        wikitext = text_element.text or ""
        lead = extract_lead(wikitext)
        section = _first_useful_section(wikitext)
        if not lead or not section:
            continue
        revision_element = _child(revision, "id")
        revision_id = None
        if revision_element is not None and revision_element.text:
            try:
                revision_id = int(revision_element.text)
            except ValueError:
                revision_id = None
        found.append(
            {
                "period": period,
                "page_id": page_id,
                "canonical_title": normalize_dump_title(title_element.text),
                "wikidata_id": None,
                "lead_text": lead,
                "first_section_text": section,
                "source_revision_id": revision_id,
                "redirect_titles": [],
            }
        )
    found.sort(key=lambda row: int(row["page_id"]))
    if len({int(row["page_id"]) for row in found}) != len(found):
        raise ValueError("selected XML ranges contain duplicate page IDs")
    return found


def read_induced_clickstream(
    path: Path, title_to_page_id: Mapping[str, int]
) -> list[tuple[int, int, int]]:
    """Read real internal-link transitions whose endpoints are selected."""
    lookup = {normalize_dump_title(title): page_id for title, page_id in title_to_page_id.items()}
    counts: dict[tuple[int, int], int] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as source:
        for line_number, line in enumerate(source, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                raise ValueError(f"malformed Clickstream line {line_number}")
            previous, current, kind, raw_count = fields
            if kind != "link":
                continue
            source_id = lookup.get(normalize_dump_title(previous))
            target_id = lookup.get(normalize_dump_title(current))
            if source_id is None or target_id is None or source_id == target_id:
                continue
            try:
                count = int(raw_count)
            except ValueError as error:
                raise ValueError(f"malformed Clickstream count at line {line_number}") from error
            if count > 0:
                pair = (source_id, target_id)
                counts[pair] = counts.get(pair, 0) + count
    return [(source, target, count) for (source, target), count in sorted(counts.items())]


def build_observable_catalog(
    source_rows: Iterable[Mapping[str, object]], *, source_snapshot: str
) -> list[dict[str, object]]:
    """Keep identity plus lead/first section, excluding every hidden field."""
    output: list[dict[str, object]] = []
    seen: set[int] = set()
    period: str | None = None
    for source in source_rows:
        leaked = set(source) & HIDDEN_ARTICLE_FIELDS
        if leaked:
            raise ValueError(f"source row contains hidden field: {sorted(leaked)[0]}")
        page_id = source.get("page_id")
        row_period = source.get("period")
        title = source.get("canonical_title")
        lead = source.get("lead_text")
        section = source.get("first_section_text", "")
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id <= 0:
            raise ValueError("page_id must be a positive integer")
        if page_id in seen:
            raise ValueError(f"duplicate page_id: {page_id}")
        if row_period not in {"2026-06", "2026-07"}:
            raise ValueError("catalog row has unsupported period")
        if period is None:
            period = str(row_period)
        elif row_period != period:
            raise ValueError("catalog rows must belong to one period")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("canonical_title must be nonblank")
        if not isinstance(lead, str) or not lead.strip():
            raise ValueError("lead_text must be nonblank")
        if not isinstance(section, str):
            raise ValueError("first_section_text must be a string")
        article_text = lead.strip()
        if section.strip() and section.strip() != article_text:
            article_text += "\n\n" + section.strip()
        redirects = source.get("redirect_titles", [])
        if not isinstance(redirects, list) or any(
            not isinstance(value, str) or not value.strip() for value in redirects
        ):
            raise ValueError("redirect_titles must be an array of nonblank strings")
        revision_id = source.get("source_revision_id")
        if revision_id is not None and (
            not isinstance(revision_id, int) or isinstance(revision_id, bool) or revision_id <= 0
        ):
            raise ValueError("source_revision_id must be positive or null")
        qid = source.get("wikidata_id")
        if qid is not None and (not isinstance(qid, str) or not qid.startswith("Q")):
            raise ValueError("wikidata_id must be a QID or null")
        output.append(
            {
                "article_key": f"enwiki:{page_id}",
                "period": row_period,
                "release_scope": RELEASE_SCOPE,
                "source_snapshot": source_snapshot,
                "namespace": 0,
                "page_id": page_id,
                "canonical_title": title.strip(),
                "wikidata_id": qid,
                "lead_text": lead.strip(),
                "article_text": article_text,
                "redirect_titles": sorted(set(redirects)),
                "content_hash": hashlib.sha256(article_text.encode()).hexdigest(),
                "source_revision_id": revision_id,
            }
        )
        seen.add(page_id)
    output.sort(key=lambda row: int(row["page_id"]))
    return output


def build_hidden_relations(
    catalog: Iterable[Mapping[str, object]],
    pagelinks: Iterable[tuple[int, int]],
    clickstream: Iterable[tuple[int, int, int]],
    *,
    cap: int = 250_000,
) -> HiddenRelations:
    """Create deterministic induced graph/clickstream artifacts."""
    if cap <= 0:
        raise ValueError("relation cap must be positive")
    rows = list(catalog)
    if not rows:
        raise ValueError("catalog must be nonempty")
    periods = {str(row["period"]) for row in rows}
    if len(periods) != 1:
        raise ValueError("catalog must belong to one period")
    period = periods.pop()
    page_ids = {int(row["page_id"]) for row in rows}
    induced_edges = sorted(
        {
            (source, target)
            for source, target in pagelinks
            if source in page_ids and target in page_ids and source != target
        }
    )
    click_counts: dict[tuple[int, int], int] = {}
    for source, target, count in clickstream:
        if source in page_ids and target in page_ids and source != target and count > 0:
            click_counts[(source, target)] = click_counts.get((source, target), 0) + count
    ordered_click = sorted(click_counts.items(), key=lambda item: (-item[1], item[0]))
    retained_click = ordered_click[:cap]
    scale = max((math.log1p(count) for _pair, count in retained_click), default=1.0)
    return HiddenRelations(
        edges=tuple(
            {
                "period": period,
                "source_article_key": f"enwiki:{source}",
                "target_article_key": f"enwiki:{target}",
            }
            for source, target in induced_edges[:cap]
        ),
        clickstream=tuple(
            {
                "period": period,
                "source_article_key": f"enwiki:{source}",
                "target_article_key": f"enwiki:{target}",
                "type": "link",
                "n": count,
                "normalized_weight": math.log1p(count) / scale,
            }
            for (source, target), count in retained_click
        ),
        edge_available=len(induced_edges),
        clickstream_available=len(ordered_click),
        edge_cap=min(cap, len(induced_edges)),
        clickstream_cap=min(cap, len(ordered_click)),
    )


__all__ = [
    "HiddenRelations",
    "RELEASE_SCOPE",
    "build_hidden_relations",
    "build_observable_catalog",
    "extract_selected_articles",
    "read_induced_clickstream",
]
