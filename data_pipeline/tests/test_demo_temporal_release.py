from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_data.demo_temporal_release import (  # noqa: E402
    DEMO_CONFIGS,
    HiddenFieldLeakage,
    prepare_demo_temporal_release,
    publish_demo_temporal_release,
    verify_demo_configs,
)


SOURCE_REVISION = "c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b"
PUBLISHED_REVISION = "d" * 40


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"".join(canonical_json(row) for row in rows)
    path.write_bytes(content)
    return {
        "path": str(path.relative_to(path.parents[1])),
        "sha256": hashlib.sha256(content).hexdigest(),
        "rows": len(rows),
    }


def fixture(root: Path, *, leak: bool = False) -> Path:
    fixture_root = root / "fixtures" / "monthly" / "demo"
    article_rows = {
        "2016": [
            {
                "article_key": "enwiki:2016:1",
                "page_id": 1,
                "canonical_title": "Alpha",
                "wikidata_id": "Q1",
            }
        ],
        "2026-06": [
            {
                "article_key": "enwiki:2026-06:1",
                "page_id": 1,
                "canonical_title": "Alpha",
                "wikidata_id": "Q1",
                **({"outgoing_edges": [2]} if leak else {}),
            },
            {
                "article_key": "enwiki:2026-06:2",
                "page_id": 2,
                "canonical_title": "Reused",
                "wikidata_id": "Q2",
            },
        ],
        "2026-07": [
            {
                "article_key": "enwiki:2026-07:1",
                "page_id": 1,
                "canonical_title": "Alpha Renamed",
                "wikidata_id": "Q1",
            },
            {
                "article_key": "enwiki:2026-07:3",
                "page_id": 3,
                "canonical_title": "Reused",
                "wikidata_id": "Q3",
            },
        ],
    }
    periods: dict[str, object] = {}
    for period, directory in (("2016", "2016"), ("2026-06", "june"), ("2026-07", "july")):
        artifacts: dict[str, object] = {}
        articles = fixture_root / directory / "articles.jsonl"
        artifacts["articles"] = write_jsonl(articles, article_rows[period])
        if period != "2016":
            for name, rows in {
                "edges": [{"source_page_id": 1, "target_page_id": 2}],
                "clickstream": [{"source_page_id": 1, "target_page_id": 2, "n": 7}],
                "hidden_archetypes": [{"profile_id": "curious", "seed_page_ids": [1]}],
                "backend_seed_catalog": [{"article_key": article_rows[period][0]["article_key"]}],
            }.items():
                artifact = fixture_root / directory / f"{name}.jsonl"
                artifacts[name] = write_jsonl(artifact, rows)
                if name == "backend_seed_catalog":
                    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                    artifact.with_name(f"{artifact.name}.sha256").write_text(
                        f"{digest}  resolved-catalog-v1.jsonl\n"
                    )
        periods[period] = {"artifacts": artifacts}
    manifest = {
        "manifest_version": 1,
        "release_scope": "friday_demo_fixture",
        "snapshot_claim": "representative_fixture_not_official_monthly_snapshot",
        "readiness": "fixture_ready",
        "source": {
            "repo_id": "dhelmy990/babel-wikipedia-experiment",
            "config": "distillation_2016",
            "revision": SOURCE_REVISION,
        },
        "periods": periods,
    }
    (fixture_root / "provenance.json").write_bytes(canonical_json(manifest))
    return fixture_root


def test_prepare_builds_only_five_demo_configs_and_crosswalk(tmp_path: Path) -> None:
    prepared = prepare_demo_temporal_release(
        fixture(tmp_path), tmp_path / "prepared"
    )

    assert tuple(prepared.config_counts) == DEMO_CONFIGS
    assert prepared.config_counts == {
        "demo_catalog_2026_06": 2,
        "demo_simulator_2026_06_hidden": 3,
        "demo_catalog_2026_07": 2,
        "demo_simulator_2026_07_hidden": 3,
        "demo_crosswalk": 5,
    }
    assert prepared.source_revision == SOURCE_REVISION
    for period, directory in (("2026-06", "june"), ("2026-07", "july")):
        catalog, checksum = prepared.backend_seed_paths[period]
        source = (
            tmp_path
            / "fixtures"
            / "monthly"
            / "demo"
            / directory
            / "backend_seed_catalog.jsonl"
        )
        assert catalog.read_bytes() == source.read_bytes()
        assert catalog.name == "resolved-catalog-v1.jsonl"
        assert checksum.read_text() == (
            f"{hashlib.sha256(source.read_bytes()).hexdigest()}  "
            "resolved-catalog-v1.jsonl\n"
        )
    release_manifest = json.loads(prepared.release_manifest_path.read_text())
    assert release_manifest["fixture_provenance"]["readiness"] == "fixture_ready"
    assert release_manifest["fixture_provenance"]["snapshot_claim"] == (
        "representative_fixture_not_official_monthly_snapshot"
    )
    crosswalk = pq.read_table(prepared.config_paths["demo_crosswalk"]).to_pylist()
    assert {row["change_kind"] for row in crosswalk if row["record_type"] == "membership"} == {
        "moved",
        "deleted",
        "created",
    }
    assert not any(path.name.startswith("distillation_2016") for path in prepared.root.rglob("*"))


def test_prepare_rejects_a_fixture_checksum_mismatch(tmp_path: Path) -> None:
    fixture_root = fixture(tmp_path)
    with (fixture_root / "june" / "articles.jsonl").open("ab") as output:
        output.write(b"{}\n")

    with pytest.raises(ValueError, match="checksum"):
        prepare_demo_temporal_release(fixture_root, tmp_path / "prepared")


def test_prepare_rejects_a_backend_seed_checksum_companion_mismatch(
    tmp_path: Path,
) -> None:
    fixture_root = fixture(tmp_path)
    companion = fixture_root / "june" / "backend_seed_catalog.jsonl.sha256"
    companion.write_text(f"{'0' * 64}  resolved-catalog-v1.jsonl\n")

    with pytest.raises(ValueError, match="backend seed checksum companion"):
        prepare_demo_temporal_release(fixture_root, tmp_path / "prepared")


def test_prepare_rejects_hidden_fields_in_observable_articles(tmp_path: Path) -> None:
    with pytest.raises(HiddenFieldLeakage, match="outgoing_edges"):
        prepare_demo_temporal_release(fixture(tmp_path, leak=True), tmp_path / "prepared")


@dataclass
class Add:
    path_in_repo: str
    path_or_fileobj: str


class FakeApi:
    __module__ = "tests.fake_hub"

    def __init__(self) -> None:
        self.sha = SOURCE_REVISION
        self.files = {
            "README.md": (
                b"---\nconfigs:\n- config_name: distillation_2016\n"
                b"  data_files:\n  - split: train\n"
                b"    path: distillation_2016/train/*.parquet\n---\n# Existing\n"
            ),
            "distillation_2016/train/part.parquet": b"immutable-distillation-bytes",
        }
        self.commits: list[list[Add]] = []

    def dataset_info(self, repo_id: str, **kwargs: object) -> object:
        assert repo_id == "dhelmy990/babel-wikipedia-experiment"
        return SimpleNamespace(sha=self.sha)

    def list_repo_files(self, **kwargs: object) -> list[str]:
        return sorted(self.files)

    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        return self.files[path_in_repo]

    def create_commit(self, *, operations: list[Add], **kwargs: object) -> object:
        self.commits.append(operations)
        for operation in operations:
            self.files[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
        self.sha = PUBLISHED_REVISION
        return SimpleNamespace(oid=PUBLISHED_REVISION)


def loader_for(prepared, *, leak: bool = False):
    def load_dataset(repo_id: str, config: str, **kwargs: object):
        rows = pq.read_table(prepared.config_paths[config]).to_pylist()
        if leak and config == "demo_catalog_2026_06":
            rows[0]["clickstream"] = [{"n": 7}]
        return rows

    return load_dataset


def test_publish_is_one_append_only_commit_and_preserves_distillation(tmp_path: Path) -> None:
    prepared = prepare_demo_temporal_release(fixture(tmp_path), tmp_path / "prepared")
    api = FakeApi()
    before = api.files["distillation_2016/train/part.parquet"]

    published = publish_demo_temporal_release(
        prepared,
        api=api,
        repo_id="dhelmy990/babel-wikipedia-experiment",
        token="secret",
        add_operation_factory=Add,
        load_dataset_fn=loader_for(prepared),
    )

    assert published.commit_sha == PUBLISHED_REVISION
    assert published.verified_counts == prepared.config_counts
    assert len(api.commits) == 1
    paths = {operation.path_in_repo for operation in api.commits[0]}
    assert all(
        path == "README.md"
        or path.startswith("demo_")
        or path.startswith("backend-seed/")
        for path in paths
    )
    assert {
        "backend-seed/2026-06/resolved-catalog-v1.jsonl",
        "backend-seed/2026-06/resolved-catalog-v1.jsonl.sha256",
        "backend-seed/2026-07/resolved-catalog-v1.jsonl",
        "backend-seed/2026-07/resolved-catalog-v1.jsonl.sha256",
    } <= paths
    assert not any(path.startswith("distillation_2016/") for path in paths)
    assert api.files["distillation_2016/train/part.parquet"] == before
    card = api.files["README.md"].decode()
    assert all(f"config_name: {config}" in card for config in DEMO_CONFIGS)


def test_remote_observable_scan_rejects_hidden_field_leakage(tmp_path: Path) -> None:
    prepared = prepare_demo_temporal_release(fixture(tmp_path), tmp_path / "prepared")

    with pytest.raises(HiddenFieldLeakage, match="clickstream"):
        verify_demo_configs(
            repo_id="dhelmy990/babel-wikipedia-experiment",
            revision=PUBLISHED_REVISION,
            token="secret",
            expected_counts=prepared.config_counts,
            load_dataset_fn=loader_for(prepared, leak=True),
        )
