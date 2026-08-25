from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from babel_data.monthly import (
    HIDDEN_ARTICLE_FIELDS,
    canonical_jsonl,
    extract_profile_manifest_assignments,
    normalize_backend_title,
)
from babel_data.monthly.fixture import build_demo_fixture, verify_demo_fixture

from .test_builders import source_rows


EXPECTED_RELEASE_FILES = {
    "2016/articles.jsonl",
    "june/articles.jsonl",
    "june/edges.jsonl",
    "june/clickstream.jsonl",
    "june/hidden-archetypes.jsonl",
    "june/resolved-catalog-v3.jsonl",
    "july/articles.jsonl",
    "july/edges.jsonl",
    "july/clickstream.jsonl",
    "july/hidden-archetypes.jsonl",
    "july/resolved-catalog-v3.jsonl",
}


def write_source(path: Path) -> None:
    path.write_bytes(canonical_jsonl(source_rows()))


def write_profile_source(path: Path) -> Path:
    manifest = (
        Path(__file__).resolve().parents[3]
        / "backend/src/application/profile_manifest.cpp"
    )
    titles = sorted(
        {
            normalize_backend_title(row.declared_title)
            for row in extract_profile_manifest_assignments(manifest)
        }
    )
    rows = [
        {
            "page_id": index,
            "canonical_title": title,
            "redirect_titles": [],
            "lead_text": f"Lead for {title}.",
            "article_text": f"Lead for {title}.\n\nPrepared body for {title}.",
            "source_revision_id": 20_000 + index,
            "wikidata_id": None,
        }
        for index, title in enumerate(titles, 1)
    ]
    path.write_bytes(canonical_jsonl(rows))
    return manifest


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_build_is_deterministic_and_provenance_ready(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    profile_source = tmp_path / "profile-source.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_source(source)
    profile_manifest = write_profile_source(profile_source)

    first_manifest = build_demo_fixture(
        source, first, profile_source_path=profile_source,
        profile_manifest_path=profile_manifest,
    )
    second_manifest = build_demo_fixture(
        source, second, profile_source_path=profile_source,
        profile_manifest_path=profile_manifest,
    )

    assert first_manifest == second_manifest
    assert first_manifest.keys() == {
        "manifest_version",
        "release_scope",
        "snapshot_claim",
        "readiness",
        "source",
        "periods",
    }
    assert first_manifest["release_scope"] == "friday_demo_fixture"
    assert first_manifest["snapshot_claim"] == (
        "representative_fixture_not_official_monthly_snapshot"
    )
    assert first_manifest["readiness"] == "fixture_ready"
    assert first_manifest["source"] == {
        "repo_id": "dhelmy990/babel-wikipedia-experiment",
        "config": "distillation_2016",
        "revision": "c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b",
    }
    assert first_manifest["periods"].keys() == {"2016", "2026-06", "2026-07"}
    assert first_manifest["periods"]["2016"]["artifacts"].keys() == {"articles"}
    expected_monthly = {
        "articles", "edges", "clickstream", "hidden_archetypes", "backend_seed_catalog"
    }
    assert first_manifest["periods"]["2026-06"]["artifacts"].keys() == expected_monthly
    assert first_manifest["periods"]["2026-07"]["artifacts"].keys() == expected_monthly

    descriptors = [
        descriptor
        for period in first_manifest["periods"].values()
        for descriptor in period["artifacts"].values()
    ]
    assert {descriptor["path"] for descriptor in descriptors} == EXPECTED_RELEASE_FILES
    for descriptor in descriptors:
        assert descriptor.keys() == {"path", "sha256", "rows"}
        artifact = first / descriptor["path"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == descriptor["sha256"]
        assert artifact.read_bytes() == (second / descriptor["path"]).read_bytes()


def test_checked_in_fixture_verifies_counts_boundary_and_limitations() -> None:
    root = Path(__file__).resolve().parents[3] / "fixtures" / "monthly" / "demo"

    result = verify_demo_fixture(root)

    assert result == {
        "readiness": "fixture_ready",
        "article_rows": {"2016": 80, "2026-06": 80, "2026-07": 80},
        "seed_assignments": {"2026-06": 78, "2026-07": 78},
        "ambiguities": 1,
    }
    for period_dir in ("2016", "june", "july"):
        for row in read_jsonl(root / period_dir / "articles.jsonl"):
            assert not set(row) & HIDDEN_ARTICLE_FIELDS
    warning = "not an official June or July Wikipedia snapshot"
    assert warning in (root / "README.md").read_text(encoding="utf-8")
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["snapshot_claim"] == (
        "representative_fixture_not_official_monthly_snapshot"
    )


def test_backend_seed_catalogs_have_dashboard_compatible_checksum_companions() -> None:
    root = Path(__file__).resolve().parents[3] / "fixtures" / "monthly" / "demo"
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))

    for period, directory in (("2026-06", "june"), ("2026-07", "july")):
        descriptor = provenance["periods"][period]["artifacts"]["backend_seed_catalog"]
        companion = root / directory / "resolved-catalog-v3.jsonl.sha256"
        assert companion.read_text(encoding="ascii") == (
            f"{descriptor['sha256']}  resolved-catalog-v3.jsonl\n"
        )
        catalog = read_jsonl(root / descriptor["path"])
        assert len(catalog) == 78
        assert [row["page_id"] for row in catalog] == sorted(
            row["page_id"] for row in catalog
        )
        assert all(
            {
                "snapshot", "article_key", "page_id", "canonical_title",
                "article_text", "redirect_titles", "content_hash", "source_revision_id",
            } <= set(row)
            for row in catalog
        )
        profile_manifest = (
            Path(__file__).resolve().parents[3]
            / "backend/src/application/profile_manifest.cpp"
        )
        assignments = extract_profile_manifest_assignments(profile_manifest)
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


def test_verifier_rejects_artifact_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    profile_source = tmp_path / "profile-source.jsonl"
    fixture = tmp_path / "fixture"
    write_source(source)
    profile_manifest = write_profile_source(profile_source)
    build_demo_fixture(
        source, fixture, profile_source_path=profile_source,
        profile_manifest_path=profile_manifest,
    )
    with (fixture / "june" / "articles.jsonl").open("ab") as output:
        output.write(b"{}\n")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_demo_fixture(fixture)
