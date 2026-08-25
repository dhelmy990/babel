from __future__ import annotations

import copy

import pytest
from jsonschema import ValidationError

from babel_data.contracts import validate_document


def article() -> dict[str, object]:
    return {
        "article_key": "enwiki:42",
        "period": "2026-06",
        "release_scope": "friday_demo_fixture",
        "source_snapshot": "2016-10-01",
        "namespace": 0,
        "page_id": 42,
        "canonical_title": "Example",
        "wikidata_id": "Q42",
        "lead_text": "A safe lead.",
        "article_text": "A safe lead.\n\nMore prepared text.",
        "redirect_titles": ["A redirect", "Example redirect"],
        "content_hash": "a" * 64,
        "source_revision_id": 99,
    }


def edge() -> dict[str, object]:
    return {
        "period": "2026-06",
        "source_article_key": "enwiki:42",
        "target_article_key": "enwiki:43",
    }


def clickstream() -> dict[str, object]:
    return {
        **edge(),
        "type": "link",
        "n": 17,
        "normalized_weight": 0.75,
    }


def crosswalk() -> dict[str, object]:
    return {
        "lineage_id": "qid:Q42",
        "period": "2026-06",
        "article_key": "enwiki:42",
        "page_id": 42,
        "canonical_title": "Example",
        "wikidata_id": "Q42",
        "change_kind": "unchanged",
        "match_basis": "qid",
    }


@pytest.mark.parametrize(
    ("schema", "document"),
    [
        ("monthly-article-v1", article()),
        ("monthly-edge-v1", edge()),
        ("clickstream-edge-v1", clickstream()),
        ("article-crosswalk-v1", crosswalk()),
    ],
)
def test_monthly_contracts_accept_valid_rows(
    schema: str, document: dict[str, object]
) -> None:
    validate_document(schema, document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("namespace", 1),
        ("redirect_titles", ["Zulu", "Alpha"]),
        ("redirect_titles", ["Alpha", "Alpha"]),
        ("content_hash", "not-a-sha256"),
        ("release_scope", "official_monthly_snapshot"),
        ("hidden_relevance", 0.9),
        ("seed_weight", 0.4),
        ("clickstream_n", 10),
    ],
)
def test_observable_article_rejects_invalid_or_hidden_fields(
    field: str, value: object
) -> None:
    document = article()
    document[field] = value

    with pytest.raises(ValidationError):
        validate_document("monthly-article-v1", document)


@pytest.mark.parametrize("schema", ["monthly-edge-v1", "clickstream-edge-v1"])
def test_edge_contracts_reject_self_loops(schema: str) -> None:
    document = clickstream() if schema == "clickstream-edge-v1" else edge()
    document["target_article_key"] = document["source_article_key"]

    with pytest.raises(ValidationError):
        validate_document(schema, document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "other"),
        ("n", 0),
        ("normalized_weight", -0.1),
        ("normalized_weight", 1.1),
        ("normalized_weight", float("nan")),
    ],
)
def test_clickstream_rejects_non_link_and_invalid_weights(
    field: str, value: object
) -> None:
    document = clickstream()
    document[field] = value

    with pytest.raises(ValidationError):
        validate_document("clickstream-edge-v1", document)


def test_crosswalk_forbids_title_only_identity_and_unknown_fields() -> None:
    for mutation in (
        lambda value: value.update(match_basis="title"),
        lambda value: value.update(similarity_score=0.99),
    ):
        document = copy.deepcopy(crosswalk())
        mutation(document)
        with pytest.raises(ValidationError):
            validate_document("article-crosswalk-v1", document)
