"""Fixture-facing adapter for the shared temporal identity implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict

from babel_data.crosswalk import SnapshotIdentity, build_crosswalk


def build_crosswalk_expectations(
    june: Sequence[Mapping[str, object]], july: Sequence[Mapping[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return non-authoritative June/July rows and explicit ambiguity findings."""
    identities = [
        SnapshotIdentity(
            period=str(row["period"]),
            article_key=str(row["article_key"]),
            page_id=int(row["page_id"]),
            canonical_title=str(row["canonical_title"]),
            wikidata_id=None if row["wikidata_id"] is None else str(row["wikidata_id"]),
        )
        for row in (*june, *july)
    ]
    result = build_crosswalk(
        identities,
        period_order=("2026-06", "2026-07"),
    )
    return [asdict(row) for row in result.rows], [asdict(row) for row in result.ambiguities]
