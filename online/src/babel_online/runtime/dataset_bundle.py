"""Checksum-verified full June/July Friday-demo bundle loader."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..observable import reject_hidden_fields


DEMO_DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
DEMO_DATASET_CONFIG = "demo_crosswalk"
DEMO_DATASET_REVISION = "e1acc648fcace8820dd5ee70bae9216ea4334555"
REQUIRED_CONFIGS = frozenset(
    {
        "demo_catalog_2026_06",
        "demo_simulator_2026_06_hidden",
        "demo_catalog_2026_07",
        "demo_simulator_2026_07_hidden",
        "demo_crosswalk",
    }
)


class DatasetBundleIntegrityError(ValueError):
    pass


def acquire_pinned_bundle(
    *,
    repo_id: str,
    revision: str,
    token: str,
    cache_dir: str | Path,
    snapshot_download: Callable[..., str] | None = None,
) -> Path:
    """Download/cache the five-config private snapshot in one pinned Hub call."""
    if (
        repo_id != DEMO_DATASET_REPOSITORY
        or revision != DEMO_DATASET_REVISION
        or not token
    ):
        raise DatasetBundleIntegrityError("private dataset requires its exact revision and token")
    if snapshot_download is None:
        try:
            from huggingface_hub import snapshot_download as hub_download
        except ImportError as error:  # pragma: no cover - deployment setup
            raise RuntimeError("remote dataset acquisition requires huggingface-hub") from error
        snapshot_download = hub_download
    resolved = snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        token=token,
        cache_dir=str(cache_dir),
        allow_patterns=[f"{name}/**" for name in sorted(REQUIRED_CONFIGS)],
    )
    return Path(resolved).resolve()


@dataclass(frozen=True, slots=True)
class DatasetBundle:
    root: Path
    dataset_repository: str
    dataset_config: str
    dataset_revision: str
    release_scope: str
    snapshot_claim: str
    configs: dict[str, tuple[dict[str, Any], ...]]
    manifest_sha256: str


def _default_read_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - deployment setup
        raise RuntimeError("dataset bundle loading requires babel-online[parquet]") from error
    return pq.read_table(path).to_pylist()


def load_demo_dataset_bundle(
    root: str | Path,
    *,
    dataset_repository: str,
    dataset_config: str,
    dataset_revision: str,
    read_parquet: Callable[[Path], list[dict[str, Any]]] = _default_read_parquet,
) -> DatasetBundle:
    root_path = Path(root).resolve()
    if (
        dataset_repository != DEMO_DATASET_REPOSITORY
        or dataset_config != DEMO_DATASET_CONFIG
        or dataset_revision != DEMO_DATASET_REVISION
    ):
        raise DatasetBundleIntegrityError("dataset identity is not the pinned Friday bundle")
    manifest_path = root_path / "demo_crosswalk" / "release.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except Exception as error:
        raise DatasetBundleIntegrityError("dataset release manifest is unavailable") from error
    configs = manifest.get("configs")
    if (
        manifest.get("release_scope") != "friday_demo_fixture"
        or manifest.get("snapshot_claim")
        != "representative_fixture_not_official_monthly_snapshot"
        or not isinstance(configs, dict)
        or set(configs) != REQUIRED_CONFIGS
    ):
        raise DatasetBundleIntegrityError(
            "release manifest must bind the complete observable/hidden/crosswalk bundle"
        )
    loaded: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in sorted(REQUIRED_CONFIGS):
        record = configs[name]
        relative = PurePosixPath(str(record["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise DatasetBundleIntegrityError("dataset config escapes bundle root")
        # Keep the lexical snapshot path: huggingface_hub intentionally links
        # these entries to checksum-addressed blobs outside the snapshot tree.
        path = root_path.joinpath(*relative.parts)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise DatasetBundleIntegrityError(f"dataset config checksum failed: {name}")
        rows = tuple(read_parquet(path))
        if len(rows) != int(record["rows"]):
            raise DatasetBundleIntegrityError(f"dataset config row count failed: {name}")
        if name.startswith("demo_catalog_"):
            for row in rows:
                reject_hidden_fields(row)
        loaded[name] = rows
    return DatasetBundle(
        root=root_path,
        dataset_repository=dataset_repository,
        dataset_config=dataset_config,
        dataset_revision=dataset_revision,
        release_scope=manifest["release_scope"],
        snapshot_claim=manifest["snapshot_claim"],
        configs=loaded,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


__all__ = [
    "DEMO_DATASET_REVISION",
    "DEMO_DATASET_REPOSITORY",
    "DEMO_DATASET_CONFIG",
    "DatasetBundle",
    "DatasetBundleIntegrityError",
    "REQUIRED_CONFIGS",
    "acquire_pinned_bundle",
    "load_demo_dataset_bundle",
]
