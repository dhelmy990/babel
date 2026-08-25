from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from babel_data.monthly import HIDDEN_ARTICLE_FIELDS, canonical_jsonl
from babel_data.monthly.fixture import build_demo_fixture, verify_demo_fixture

from .test_builders import source_rows


EXPECTED_RELEASE_FILES = {
    "2016/articles.jsonl",
    "june/articles.jsonl",
    "june/edges.jsonl",
    "june/clickstream.jsonl",
    "june/hidden-archetypes.jsonl",
    "june/backend-seed-catalog.jsonl",
    "july/articles.jsonl",
    "july/edges.jsonl",
    "july/clickstream.jsonl",
    "july/hidden-archetypes.jsonl",
    "july/backend-seed-catalog.jsonl",
}


def write_source(path: Path) -> None:
    path.write_bytes(canonical_jsonl(source_rows()))


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_build_is_deterministic_and_provenance_ready(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_source(source)

    first_manifest = build_demo_fixture(source, first)
    second_manifest = build_demo_fixture(source, second)

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
        "seed_assignments": {"2026-06": 80, "2026-07": 80},
        "ambiguities": 1,
    }
    for period_dir in ("2016", "june", "july"):
        for row in read_jsonl(root / period_dir / "articles.jsonl"):
            assert not set(row) & HIDDEN_ARTICLE_FIELDS
    warning = "not an official June or July Wikipedia snapshot"
    assert warning in (root / "README.md").read_text(encoding="utf-8")
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["snapshot_claim"] == "representative_fixture_not_official_monthly_snapshot"


def test_verifier_rejects_artifact_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    fixture = tmp_path / "fixture"
    write_source(source)
    build_demo_fixture(source, fixture)
    with (fixture / "june" / "articles.jsonl").open("ab") as output:
        output.write(b"{}\n")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_demo_fixture(fixture)
