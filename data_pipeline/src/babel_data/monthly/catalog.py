"""Observable catalog construction for the representative monthly fixture."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence


RELEASE_SCOPE = "friday_demo_fixture"
SOURCE_SNAPSHOT = "2016-10-01"
PERIODS = frozenset({"2016", "2026-06", "2026-07"})
DEMO_AMBIGUITY_QID = "Q999999999999999901"
HIDDEN_ARTICLE_FIELDS = frozenset(
    {
        "archetype",
        "archetype_slug",
        "assignment_id",
        "clickstream_n",
        "graph_neighbors",
        "hidden_relevance",
        "normalized_weight",
        "ppr_rank",
        "ppr_score",
        "seed_weight",
        "simulator_seed",
    }
)


def content_sha256(text: str) -> str:
    """Hash the exact safe prepared article text encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_jsonl(rows: Iterable[Mapping[str, object]]) -> bytes:
    """Serialize mappings as deterministic compact UTF-8 JSONL."""
    return b"".join(
        (
            json.dumps(
                dict(row),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def _require_source_rows(
    source_rows: Iterable[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    rows = sorted(source_rows, key=lambda row: int(row["page_id"]))
    if len(rows) != 80:
        raise ValueError("the Friday demo fixture requires exactly 80 source rows")
    page_ids = [row.get("page_id") for row in rows]
    if any(not isinstance(page_id, int) or isinstance(page_id, bool) or page_id <= 0 for page_id in page_ids):
        raise ValueError("source page IDs must be positive integers")
    if len(set(page_ids)) != len(page_ids):
        raise ValueError("source page IDs must be unique")
    return rows


def _observable_row(source: Mapping[str, object], period: str) -> dict[str, object]:
    article_text = source.get("article_text")
    lead_text = source.get("lead_text")
    canonical_title = source.get("canonical_title")
    if not isinstance(article_text, str) or not article_text.strip():
        raise ValueError("source article_text must be nonblank")
    if not isinstance(lead_text, str) or not lead_text.strip():
        raise ValueError("source lead_text must be nonblank")
    if not isinstance(canonical_title, str) or not canonical_title.strip():
        raise ValueError("source canonical_title must be nonblank")
    page_id = int(source["page_id"])
    wikidata_id = source.get("wikidata_id")
    if wikidata_id is not None and not isinstance(wikidata_id, str):
        raise ValueError("source wikidata_id must be a string or null")
    revision_id = source.get("source_revision_id")
    if revision_id is not None and (
        not isinstance(revision_id, int) or isinstance(revision_id, bool) or revision_id <= 0
    ):
        raise ValueError("source_revision_id must be a positive integer or null")
    redirects = source.get("redirect_titles", [])
    if not isinstance(redirects, Sequence) or isinstance(redirects, (str, bytes)):
        raise ValueError("redirect_titles must be an array")
    redirect_titles = sorted({str(title) for title in redirects})
    return {
        "article_key": f"enwiki:{page_id}",
        "period": period,
        "release_scope": RELEASE_SCOPE,
        "source_snapshot": SOURCE_SNAPSHOT,
        "namespace": 0,
        "page_id": page_id,
        "canonical_title": canonical_title,
        "wikidata_id": wikidata_id,
        "lead_text": lead_text,
        "article_text": article_text,
        "redirect_titles": redirect_titles,
        "content_hash": content_sha256(article_text),
        "source_revision_id": revision_id,
    }


def build_period_articles(
    source_rows: Iterable[Mapping[str, object]], period: str
) -> list[dict[str, object]]:
    """Build one deterministic catalog, including declared demo transitions."""
    if period not in PERIODS:
        raise ValueError(f"unsupported representative fixture period: {period!r}")
    sources = _require_source_rows(source_rows)
    rows = [_observable_row(source, period) for source in sources]
    if period == "2026-06":
        rows[78]["wikidata_id"] = DEMO_AMBIGUITY_QID
        rows[79]["wikidata_id"] = DEMO_AMBIGUITY_QID
    elif period == "2026-07":
        maximum_page_id = max(int(row["page_id"]) for row in rows)
        rows[76]["canonical_title"] = f"{rows[76]['canonical_title']} (demo move)"
        rows[77]["page_id"] = maximum_page_id + 1_001
        rows[77]["article_key"] = f"enwiki:{rows[77]['page_id']}"
        rows[77]["wikidata_id"] = None
        rows[78]["wikidata_id"] = DEMO_AMBIGUITY_QID
        rows[79]["page_id"] = maximum_page_id + 1_002
        rows[79]["article_key"] = f"enwiki:{rows[79]['page_id']}"
        rows[79]["wikidata_id"] = None
        rows[79]["canonical_title"] = f"{rows[79]['canonical_title']} (demo created)"
    rows.sort(key=lambda row: (int(row["page_id"]), str(row["article_key"])))
    return rows
