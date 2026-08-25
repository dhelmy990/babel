from __future__ import annotations

import hashlib
import json
from collections import Counter

from babel_data.contracts import validate_document
from babel_data.monthly import (
    HIDDEN_ARTICLE_FIELDS,
    build_archetypes,
    build_clickstream,
    build_crosswalk_expectations,
    build_graph,
    build_period_articles,
    build_seed_catalog,
    canonical_jsonl,
    content_sha256,
)


def source_rows() -> list[dict[str, object]]:
    return [
        {
            "article_key": f"enwiki:2016-10-01:{index}",
            "page_id": index,
            "canonical_title": f"Article {index:03d}",
            "wikidata_id": f"Q{index}",
            "lead_text": f"Lead {index}.",
            "article_text": f"Lead {index}.\n\nPrepared body {index}.",
            "source_revision_id": 10_000 + index,
        }
        for index in range(1, 81)
    ]


def test_period_catalogs_are_deterministic_safe_and_observable_only() -> None:
    source = source_rows()

    june = build_period_articles(source, "2026-06")
    repeated = build_period_articles(list(reversed(source)), "2026-06")

    assert canonical_jsonl(june) == canonical_jsonl(repeated)
    assert len(june) == 80
    assert len({row["article_key"] for row in june}) == 80
    assert all(row["namespace"] == 0 for row in june)
    assert all(row["redirect_titles"] == sorted(row["redirect_titles"]) for row in june)
    assert all(
        row["content_hash"] == content_sha256(str(row["article_text"]))
        for row in june
    )
    assert not any(set(row) & HIDDEN_ARTICLE_FIELDS for row in june)
    for row in june:
        validate_document("monthly-article-v1", row)


def test_scenario_periods_exercise_identity_outcomes_without_title_matching() -> None:
    june = build_period_articles(source_rows(), "2026-06")
    july = build_period_articles(source_rows(), "2026-07")

    rows, ambiguities = build_crosswalk_expectations(june, july)
    kinds_by_lineage = Counter(
        row["change_kind"] for row in rows if row["period"] == "2026-07"
    )

    assert kinds_by_lineage == {
        "unchanged": 76,
        "moved": 1,
        "created": 2,
        "ambiguous": 1,
    }
    assert sum(row["change_kind"] == "deleted" for row in rows) == 1
    assert len(ambiguities) == 1
    assert ambiguities[0]["code"] == "qid_not_unique_within_period"
    assert all(row["match_basis"] != "title" for row in rows)
    for row in rows:
        validate_document("article-crosswalk-v1", row)


def test_hidden_graph_and_clickstream_are_deterministic_and_valid() -> None:
    articles = build_period_articles(source_rows(), "2026-06")

    graph = build_graph(articles)
    clickstream = build_clickstream(graph)

    assert len(graph) == 160
    assert graph == sorted(
        graph, key=lambda row: (row["source_article_key"], row["target_article_key"])
    )
    pairs = {
        (row["source_article_key"], row["target_article_key"]) for row in graph
    }
    assert len(pairs) == len(graph)
    assert all(source != target for source, target in pairs)
    assert {
        (row["source_article_key"], row["target_article_key"])
        for row in clickstream
    } <= pairs
    assert all(row["type"] == "link" and row["n"] > 0 for row in clickstream)
    assert max(row["normalized_weight"] for row in clickstream) == 1.0
    for row in graph:
        validate_document("monthly-edge-v1", row)
    for row in clickstream:
        validate_document("clickstream-edge-v1", row)


def test_hidden_archetypes_and_seed_catalog_match_backend_roster() -> None:
    articles = build_period_articles(source_rows(), "2026-06")

    archetypes = build_archetypes(articles)
    catalog = build_seed_catalog(archetypes, articles)

    assert len(archetypes) == 20
    assert len(catalog) == 80
    assert all(len(archetype["seeds"]) == 4 for archetype in archetypes)
    assert all(
        tuple(seed["weight"] for seed in archetype["seeds"])
        == (0.4, 0.3, 0.2, 0.1)
        for archetype in archetypes
    )
    assert len({row["assignment_id"] for row in catalog}) == 80
    assert [row["page_id"] for row in catalog] == sorted(
        row["page_id"] for row in catalog
    )
    required_article_fields = {
        "snapshot",
        "article_key",
        "page_id",
        "canonical_title",
        "article_text",
        "redirect_titles",
        "content_hash",
        "source_revision_id",
    }
    assert all(required_article_fields <= set(row) for row in catalog)
    articles_by_page = {row["page_id"]: row for row in articles}
    assert all(
        row["article_text"] == articles_by_page[row["page_id"]]["article_text"]
        and row["snapshot"] == "2026-06"
        for row in catalog
    )
    assert catalog[0]["declared_title"] == "Distributed computing"
    assert catalog[-1]["declared_title"] == "Regulation"
    payload = canonical_jsonl(catalog)
    assert hashlib.sha256(payload).hexdigest() == hashlib.sha256(
        canonical_jsonl(build_seed_catalog(build_archetypes(articles), articles))
    ).hexdigest()
    assert json.loads(payload.splitlines()[0])["weight"] == 0.4
