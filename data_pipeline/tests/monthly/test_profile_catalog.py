from __future__ import annotations

import hashlib
import bz2
from collections import Counter
from pathlib import Path

from babel_data.monthly.profiles import (
    build_profile_catalog,
    extract_profile_manifest_assignments,
    normalize_backend_title,
    plan_multistream_ranges,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROFILE_MANIFEST = (
    REPOSITORY_ROOT / "backend" / "src" / "application" / "profile_manifest.cpp"
)


def profile_sources() -> list[dict[str, object]]:
    assignments = extract_profile_manifest_assignments(PROFILE_MANIFEST)
    titles = sorted({assignment.declared_title for assignment in assignments})
    return [
        {
            "page_id": index,
            "canonical_title": title,
            "redirect_titles": [],
            "lead_text": "" if title == "Corporate finance" else f"Lead for {title}.",
            "article_text": f"Lead for {title}.\n\nPrepared body for {title}.",
            "source_revision_id": 20_000 + index,
            "wikidata_id": None,
        }
        for index, title in enumerate(titles, 1)
    ]


def test_independent_profile_manifest_extraction_has_80_assignments_78_titles() -> None:
    assignments = extract_profile_manifest_assignments(PROFILE_MANIFEST)

    assert len(assignments) == 80
    normalized = [normalize_backend_title(row.declared_title) for row in assignments]
    assert len(set(normalized)) == 78
    assert Counter(normalized).most_common(2) == [
        ("Artificial neural network", 2),
        ("Regulation", 2),
    ]
    assert len({(row.creator_slug, row.declared_title) for row in assignments}) == 80


def test_profile_catalog_resolves_all_80_assignments_through_78_real_rows() -> None:
    assignments = extract_profile_manifest_assignments(PROFILE_MANIFEST)

    catalog = build_profile_catalog(profile_sources(), assignments, period="2026-06")

    assert len(catalog) == 78
    assert [row["page_id"] for row in catalog] == sorted(
        row["page_id"] for row in catalog
    )
    assert len({row["page_id"] for row in catalog}) == 78
    lookup = {
        normalize_backend_title(title): row["page_id"]
        for row in catalog
        for title in (row["canonical_title"], *row["redirect_titles"])
    }
    resolved = [
        lookup[normalize_backend_title(row.declared_title)] for row in assignments
    ]
    assert len(resolved) == 80
    assert len(set(resolved)) == 78
    assert set(resolved) == {row["page_id"] for row in catalog}
    assert all(
        row["content_hash"]
        == hashlib.sha256(row["article_text"].encode("utf-8")).hexdigest()
        for row in catalog
    )
    corporate = next(
        row for row in catalog if row["canonical_title"] == "Corporate finance"
    )
    assert corporate["lead_text"] == "Lead for Corporate finance."
    assert corporate["lead_derivation"] == "first_nonempty_paragraph_fallback"


def test_multistream_plan_groups_titles_and_uses_next_offset(tmp_path: Path) -> None:
    index = tmp_path / "index.txt.bz2"
    index.write_bytes(
        bz2.compress(
            b"10:1:Other\n20:2:Acting\n20:3:Animation\n40:4:Later\n"
        )
    )

    ranges = plan_multistream_ranges(
        index, {"Acting", "Animation"}, dump_size=100
    )

    assert [(row.start, row.end_exclusive, row.titles) for row in ranges] == [
        (20, 40, ("Acting", "Animation"))
    ]
