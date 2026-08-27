from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_data.crosswalk import SnapshotIdentity, build_crosswalk  # noqa: E402


PERIODS = ("2016", "2026-06", "2026-07")


def snap(
    period: str,
    page_id: int,
    title: str,
    qid: str | None,
    *,
    key: str | None = None,
) -> SnapshotIdentity:
    return SnapshotIdentity(
        period=period,
        article_key=key or f"{period}:{page_id}",
        page_id=page_id,
        canonical_title=title,
        wikidata_id=qid,
    )


def test_qid_tracks_title_move_across_periods() -> None:
    result = build_crosswalk(
        [snap("2016", 1, "Old", "Q1"), snap("2026-06", 1, "New", "Q1")],
        period_order=PERIODS,
    )

    assert {row.lineage_id for row in result.rows} == {"qid:Q1"}
    assert {row.change_kind for row in result.rows} == {"moved"}
    assert {row.match_basis for row in result.rows} == {"qid"}
    assert result.ambiguities == ()


def test_title_reuse_without_matching_qid_is_not_same_identity() -> None:
    result = build_crosswalk(
        [
            snap("2026-06", 1, "Name", "Q1"),
            snap("2026-07", 2, "Name", "Q2"),
        ],
        period_order=PERIODS,
    )

    assert {row.lineage_id for row in result.rows} == {"qid:Q1", "qid:Q2"}
    assert {row.change_kind for row in result.rows} == {"deleted", "created"}


def test_page_id_fallback_requires_a_missing_qid() -> None:
    result = build_crosswalk(
        [
            snap("2026-06", 9, "Before", None),
            snap("2026-07", 9, "After", "Q9"),
        ],
        period_order=PERIODS,
    )

    assert {row.lineage_id for row in result.rows} == {"qid:Q9"}
    assert {row.match_basis for row in result.rows} == {"page_id"}
    assert {row.change_kind for row in result.rows} == {"moved"}


def test_conflicting_qids_on_reused_page_id_are_explicitly_ambiguous() -> None:
    result = build_crosswalk(
        [
            snap("2026-06", 5, "First", "Q1"),
            snap("2026-07", 5, "Second", "Q2"),
        ],
        period_order=PERIODS,
    )

    assert {row.lineage_id for row in result.rows} == {"qid:Q1", "qid:Q2"}
    assert {row.change_kind for row in result.rows} == {"ambiguous"}
    assert [finding.code for finding in result.ambiguities] == [
        "page_id_conflicting_qids"
    ]


def test_same_title_without_qid_or_page_continuity_stays_separate() -> None:
    result = build_crosswalk(
        [
            snap("2026-06", 11, "Shared", None),
            snap("2026-07", 12, "Shared", None),
        ],
        period_order=PERIODS,
    )

    assert len({row.lineage_id for row in result.rows}) == 2
    assert {row.change_kind for row in result.rows} == {"deleted", "created"}
    assert result.ambiguities == ()


def test_qid_reappearance_with_new_page_id_is_recreated() -> None:
    result = build_crosswalk(
        [
            snap("2016", 20, "Article", "Q20"),
            snap("2026-07", 21, "Article", "Q20"),
        ],
        period_order=PERIODS,
    )

    assert {row.change_kind for row in result.rows} == {"recreated"}


def test_duplicate_qid_within_one_period_is_explicitly_ambiguous() -> None:
    result = build_crosswalk(
        [
            snap("2026-06", 30, "A", "Q30"),
            snap("2026-06", 31, "B", "Q30"),
            snap("2026-07", 30, "A", "Q30"),
        ],
        period_order=PERIODS,
    )

    assert {row.change_kind for row in result.rows} == {"ambiguous"}
    assert [finding.code for finding in result.ambiguities] == [
        "qid_not_unique_within_period"
    ]
