from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from babel_online.runtime.dataset_bundle import (
    DEMO_DATASET_CONFIG,
    DEMO_DATASET_REPOSITORY,
    DEMO_DATASET_REVISION,
    DatasetBundleIntegrityError,
    acquire_pinned_bundle,
    load_demo_dataset_bundle,
)


def test_full_bundle_requires_both_observable_hidden_months_and_crosswalk(tmp_path) -> None:
    configs = {}
    for name in (
        "demo_catalog_2026_06",
        "demo_simulator_2026_06_hidden",
        "demo_catalog_2026_07",
        "demo_simulator_2026_07_hidden",
        "demo_crosswalk",
    ):
        path = Path(name) / "train" / "part-00000.parquet"
        payload = name.encode()
        target = tmp_path / path
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        configs[name] = {
            "path": str(path),
            "rows": 1,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    release = {
        "release_scope": "friday_demo_fixture",
        "snapshot_claim": "representative_fixture_not_official_monthly_snapshot",
        "configs": configs,
    }
    manifest = tmp_path / "demo_crosswalk" / "release.json"
    manifest.write_text(json.dumps(release))

    bundle = load_demo_dataset_bundle(
        tmp_path,
        dataset_repository=DEMO_DATASET_REPOSITORY,
        dataset_config=DEMO_DATASET_CONFIG,
        dataset_revision=DEMO_DATASET_REVISION,
        read_parquet=lambda path: [{"path": str(path)}],
    )

    assert set(bundle.configs) == set(configs)
    assert bundle.release_scope == "friday_demo_fixture"
    assert bundle.dataset_repository == DEMO_DATASET_REPOSITORY
    assert bundle.dataset_config == DEMO_DATASET_CONFIG


def test_bundle_rejects_missing_hidden_month_or_wrong_pin(tmp_path) -> None:
    (tmp_path / "demo_crosswalk").mkdir(parents=True)
    (tmp_path / "demo_crosswalk" / "release.json").write_text(
        json.dumps({"release_scope": "friday_demo_fixture", "configs": {}})
    )
    with pytest.raises(DatasetBundleIntegrityError):
        load_demo_dataset_bundle(
            tmp_path,
            dataset_repository=DEMO_DATASET_REPOSITORY,
            dataset_config=DEMO_DATASET_CONFIG,
            dataset_revision="0" * 40,
            read_parquet=lambda _path: [],
        )


def test_production_acquisition_uses_one_exact_private_hub_snapshot(tmp_path) -> None:
    calls = []

    def download(**values):
        calls.append(values)
        return str(tmp_path)

    acquired = acquire_pinned_bundle(
        repo_id="dhelmy990/babel-wikipedia-experiment",
        revision=DEMO_DATASET_REVISION,
        token="secret",
        cache_dir=tmp_path / "cache",
        snapshot_download=download,
    )

    assert acquired == tmp_path
    assert calls[0]["revision"] == DEMO_DATASET_REVISION
    assert calls[0]["repo_type"] == "dataset"
    assert set(calls[0]["allow_patterns"]) == {
        "demo_catalog_2026_06/**",
        "demo_simulator_2026_06_hidden/**",
        "demo_catalog_2026_07/**",
        "demo_simulator_2026_07_hidden/**",
        "demo_crosswalk/**",
    }


def test_loader_accepts_checksum_pinned_hub_cache_symlinks(tmp_path) -> None:
    snapshot = tmp_path / "snapshots" / DEMO_DATASET_REVISION
    blobs = tmp_path / "blobs"
    blobs.mkdir(parents=True)
    configs = {}
    for index, name in enumerate(sorted({
        "demo_catalog_2026_06",
        "demo_simulator_2026_06_hidden",
        "demo_catalog_2026_07",
        "demo_simulator_2026_07_hidden",
        "demo_crosswalk",
    })):
        blob = blobs / f"blob-{index}"
        blob.write_bytes(name.encode())
        relative = Path(name) / "train" / "part-00000.parquet"
        link = snapshot / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(blob)
        configs[name] = {
            "path": str(relative),
            "rows": 1,
            "sha256": hashlib.sha256(blob.read_bytes()).hexdigest(),
        }
    release = snapshot / "demo_crosswalk" / "release.json"
    release.write_text(json.dumps({
        "release_scope": "friday_demo_fixture",
        "snapshot_claim": "representative_fixture_not_official_monthly_snapshot",
        "configs": configs,
    }))

    loaded = load_demo_dataset_bundle(
        snapshot,
        dataset_repository=DEMO_DATASET_REPOSITORY,
        dataset_config=DEMO_DATASET_CONFIG,
        dataset_revision=DEMO_DATASET_REVISION,
        read_parquet=lambda _path: [{}],
    )

    assert set(loaded.configs) == set(configs)
