from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.contracts import validate_document  # noqa: E402
from babel_data.reconcile import reconcile, split_for  # noqa: E402
from babel_data.teacher import TeacherRecord  # noqa: E402
from babel_data.wikipedia import WikipediaPage  # noqa: E402


def teacher(title: str, value: float = 1.0) -> TeacherRecord:
    vector = np.full(100, value, dtype=np.float32)
    vector.setflags(write=False)
    return TeacherRecord(title, vector)


def article(
    title: str = "Virtual memory",
    *,
    page_id: int = 10,
    article_text: str = "Lead sentence.\n\nHistory\nLater",
    lead_text: str = "Lead sentence.",
    redirect_target: str | None = None,
) -> WikipediaPage:
    return WikipediaPage(
        page_id=page_id,
        canonical_title=title,
        revision_id=100 + page_id,
        article_text=article_text,
        lead_text=lead_text,
        redirect_target=redirect_target,
    )


def test_teacher_underscore_resolves_to_canonical_snapshot_identity() -> None:
    source = teacher("Virtual_memory")
    page = article()

    result = reconcile([source], [page])

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.page_id == 10
    assert row.canonical_title == "Virtual memory"
    assert row.article_key == "enwiki:2016-10-01:10"
    assert row.teacher_vector is source.vector
    assert row.teacher_norm == 10.0
    assert row.reconciliation_status == "matched"
    assert result.exclusions == []


def test_redirect_source_resolves_to_target_content_and_identity() -> None:
    result = reconcile(
        [teacher("VM_alias")],
        [
            article("Virtual memory", page_id=10),
            article(
                "VM overview",
                page_id=11,
                article_text="",
                lead_text="",
                redirect_target="Virtual memory",
            ),
            article(
                "VM alias",
                page_id=12,
                article_text="",
                lead_text="",
                redirect_target="VM overview",
            ),
        ],
    )

    [row] = result.rows
    assert row.page_id == 10
    assert row.canonical_title == "Virtual memory"
    assert row.article_text.startswith("Lead sentence.")
    assert row.reconciliation_status == "redirect_resolved"


def test_exact_only_matching_never_uses_fuzzy_title() -> None:
    result = reconcile([teacher("Virtul_memory")], [article()])

    assert result.rows == []
    assert [item.reason for item in result.exclusions] == ["title_not_found"]


def test_every_teacher_input_is_accepted_or_explicitly_excluded_in_order() -> None:
    teachers = [
        teacher("Virtual_memory"),
        teacher("Unknown"),
        teacher("Talk:Virtual_memory"),
        teacher("Virtual memory", 2.0),
    ]
    result = reconcile(teachers, [article()])

    assert [row.teacher_title for row in result.rows] == ["Virtual_memory"]
    assert [item.teacher_title for item in result.exclusions] == [
        "Unknown",
        "Talk:Virtual_memory",
        "Virtual memory",
    ]
    assert [item.reason for item in result.exclusions] == [
        "title_not_found",
        "non_article_namespace",
        "duplicate/ambiguous_title",
    ]
    assert result.input_count == len(teachers)
    assert len(result.rows) + len(result.exclusions) == result.input_count


def test_page_ambiguity_and_redirect_failures_are_explicit() -> None:
    pages = [
        article("Same", page_id=1),
        article("same", page_id=2),
        article("Cycle A", page_id=3, article_text="", lead_text="", redirect_target="Cycle A"),
        article("Missing", page_id=5, article_text="", lead_text="", redirect_target="Absent"),
        article("Depth A", page_id=6, article_text="", lead_text="", redirect_target="Depth B"),
        article("Depth B", page_id=7, article_text="", lead_text="", redirect_target="Depth C"),
        article("Depth C", page_id=8),
    ]
    result = reconcile(
        [teacher("Same"), teacher("Cycle A"), teacher("Missing"), teacher("Depth A")],
        pages,
        max_redirect_depth=1,
    )

    assert [item.reason for item in result.exclusions] == [
        "duplicate/ambiguous_title",
        "redirect_cycle",
        "redirect_target_missing",
        "redirect_depth_exceeded",
    ]


def test_empty_target_text_and_lead_have_distinct_reasons() -> None:
    result = reconcile(
        [teacher("Empty text"), teacher("Empty lead")],
        [
            article("Empty text", page_id=1, article_text="", lead_text=""),
            article("Empty lead", page_id=2, article_text="Body", lead_text=""),
        ],
    )

    assert [item.reason for item in result.exclusions] == ["empty_text", "empty_lead"]


def test_accepted_row_converts_to_exact_v1_schema_document() -> None:
    source = teacher("Virtual_memory")
    [row] = reconcile([source], [article()]).rows
    document = row.to_document()

    validate_document("distillation-example-v1", document)
    assert document["teacher_vector"] == [1.0] * 100
    assert document["wikidata_id"] is None
    assert document["source_revision_id"] == 110
    assert document["snapshot_date"] == "2016-10-01"


def test_split_uses_sha256_first_eight_bytes_and_is_stable() -> None:
    keys = [f"enwiki:2016-10-01:{page_id}" for page_id in range(1, 250)]
    expected = []
    for key in keys:
        bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % 100
        expected.append("train" if bucket < 98 else "validation" if bucket == 98 else "test")

    assert [split_for(key) for key in keys] == expected
    assert {"train", "validation", "test"}.issubset(expected)


def test_result_order_is_deterministic_for_generator_inputs() -> None:
    titles = ["Third", "First", "Second"]
    pages = (article(title, page_id=index) for index, title in enumerate(titles, 1))
    teachers = (teacher(title) for title in titles)

    result = reconcile(teachers, pages)

    assert [row.teacher_title for row in result.rows] == titles


def test_result_is_stable_across_page_permutations_and_repeated_runs() -> None:
    pages = [article("First", page_id=1), article("Second", page_id=2)]
    teachers = [teacher("Second"), teacher("First")]

    forward = reconcile(iter(teachers), iter(pages))
    reverse = reconcile(iter(teachers), iter(reversed(pages)))
    repeated = reconcile(iter(teachers), iter(pages))

    assert [row.to_document() for row in forward.rows] == [
        row.to_document() for row in reverse.rows
    ]
    assert [row.to_document() for row in forward.rows] == [
        row.to_document() for row in repeated.rows
    ]


def test_structurally_invalid_teacher_record_is_explicitly_accounted() -> None:
    malformed = TeacherRecord(None, np.ones(100, dtype=np.float32))  # type: ignore[arg-type]

    result = reconcile([malformed], [article()])

    assert result.rows == []
    assert result.input_count == 1
    assert [item.reason for item in result.exclusions] == ["invalid_teacher_source"]


def invalid_page(**overrides: object) -> WikipediaPage:
    values: dict[str, object] = {
        "page_id": 9,
        "canonical_title": "Invalid page",
        "revision_id": 10,
        "article_text": "Useful body.",
        "lead_text": "Useful lead.",
        "redirect_target": None,
    }
    values.update(overrides)
    return WikipediaPage(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "page",
    [
        invalid_page(page_id=0),
        invalid_page(page_id=True),
        invalid_page(page_id=2**100),
        invalid_page(revision_id=0),
        invalid_page(revision_id=True),
        invalid_page(revision_id=2**100),
        invalid_page(canonical_title="invalid_page"),
        invalid_page(article_text=None),
        invalid_page(lead_text=None),
        invalid_page(redirect_target=""),
        invalid_page(redirect_target="\x7f"),
        invalid_page(redirect_target="Target", article_text="not empty"),
    ],
)
def test_direct_malformed_wikipedia_pages_are_explicitly_excluded(
    page: WikipediaPage,
) -> None:
    result = reconcile([teacher("Invalid_page")], [page])

    assert result.rows == []
    assert result.input_count == 1
    assert [item.reason for item in result.exclusions] == [
        "invalid_wikipedia_page"
    ]


def test_invalid_page_cannot_emit_schema_invalid_row_and_valid_peer_still_emits() -> None:
    result = reconcile(
        [teacher("Valid"), teacher("Invalid page")],
        [article("Valid", page_id=1), invalid_page(page_id=0)],
    )

    assert len(result.rows) == 1
    validate_document("distillation-example-v1", result.rows[0].to_document())
    assert result.rows[0].page_id == 1
    assert [item.reason for item in result.exclusions] == [
        "invalid_wikipedia_page"
    ]
    assert len(result.rows) + len(result.exclusions) == result.input_count == 2
