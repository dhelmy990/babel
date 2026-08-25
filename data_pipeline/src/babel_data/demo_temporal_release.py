"""Manifest-driven preparation and append-only publication for the Friday demo."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .crosswalk import SnapshotIdentity, build_crosswalk


DEMO_CONFIGS = (
    "demo_catalog_2026_06",
    "demo_simulator_2026_06_hidden",
    "demo_catalog_2026_07",
    "demo_simulator_2026_07_hidden",
    "demo_crosswalk",
)
_PERIODS = ("2016", "2026-06", "2026-07")
_PERIOD_DIRECTORIES = {"2016": "2016", "2026-06": "june", "2026-07": "july"}
_MONTH_CONFIGS = {
    "2026-06": ("demo_catalog_2026_06", "demo_simulator_2026_06_hidden"),
    "2026-07": ("demo_catalog_2026_07", "demo_simulator_2026_07_hidden"),
}
_MONTH_ARTIFACTS = {
    "articles",
    "edges",
    "clickstream",
    "hidden_archetypes",
    "backend_seed_catalog",
}
_HIDDEN_ARTIFACTS = ("edges", "clickstream", "hidden_archetypes")
_HIDDEN_FIELD_NAMES = frozenset(
    {
        "archetype",
        "archetypes",
        "clickstream",
        "edges",
        "graph",
        "hidden_archetypes",
        "outgoing_edges",
        "payload_json",
        "profile_id",
        "record_type",
        "seed_page_ids",
        "source_page_id",
        "target_page_id",
        "transition_probability",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA40 = re.compile(r"[0-9a-f]{40}")


class HiddenFieldLeakage(ValueError):
    """An observable catalog exposed simulator-only data."""


@dataclass(frozen=True, slots=True)
class PreparedDemoRelease:
    root: Path
    config_paths: Mapping[str, Path]
    config_counts: Mapping[str, int]
    backend_seed_paths: Mapping[str, tuple[Path, Path]]
    source_revision: str
    source_manifest_sha256: str
    snapshot_claim: str
    release_manifest_path: Path


@dataclass(frozen=True, slots=True)
class PublishedDemoRelease:
    commit_sha: str
    parent_sha: str
    verified_counts: Mapping[str, int]
    preserved_distillation_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _AddOperation:
    path_in_repo: str
    path_or_fileobj: str


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest(fixture_root: Path) -> tuple[dict[str, object], str]:
    path = fixture_root / "provenance.json"
    raw = path.read_bytes()
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("fixture provenance manifest is malformed") from error
    if not isinstance(document, dict):
        raise ValueError("fixture provenance manifest must be an object")
    required = {
        "manifest_version",
        "release_scope",
        "snapshot_claim",
        "readiness",
        "source",
        "periods",
    }
    if not required <= set(document):
        raise ValueError("fixture provenance manifest has missing fields")
    if (
        document["manifest_version"] != 1
        or document["release_scope"] != "friday_demo_fixture"
        or document["snapshot_claim"]
        != "representative_fixture_not_official_monthly_snapshot"
        or document["readiness"] != "fixture_ready"
    ):
        raise ValueError("fixture provenance manifest is not release-ready")
    source = document["source"]
    if not isinstance(source, Mapping) or not {
        "repo_id",
        "config",
        "revision",
    } <= set(source):
        raise ValueError("fixture source identity is incomplete")
    if (
        source["repo_id"] != "dhelmy990/babel-wikipedia-experiment"
        or source["config"] != "distillation_2016"
        or not isinstance(source["revision"], str)
        or _SHA40.fullmatch(source["revision"]) is None
    ):
        raise ValueError("fixture source identity is invalid")
    periods = document["periods"]
    if not isinstance(periods, Mapping) or set(periods) != set(_PERIODS):
        raise ValueError("fixture periods must be exactly 2016, 2026-06, and 2026-07")
    return document, hashlib.sha256(raw).hexdigest()


def _read_artifact(
    fixture_root: Path, descriptor: object, *, label: str
) -> list[dict[str, object]]:
    if not isinstance(descriptor, Mapping) or set(descriptor) != {
        "path",
        "sha256",
        "rows",
    }:
        raise ValueError(f"{label} artifact descriptor is invalid")
    relative = descriptor["path"]
    expected_sha = descriptor["sha256"]
    expected_rows = descriptor["rows"]
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not isinstance(expected_sha, str)
        or _SHA256.fullmatch(expected_sha) is None
        or not isinstance(expected_rows, int)
        or isinstance(expected_rows, bool)
        or expected_rows < 0
    ):
        raise ValueError(f"{label} artifact descriptor values are invalid")
    path = fixture_root / relative
    if _sha256(path) != expected_sha:
        raise ValueError(f"{label} artifact checksum mismatch")
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{label} row {line_number} is malformed") from error
            if not isinstance(value, dict):
                raise ValueError(f"{label} row {line_number} must be an object")
            rows.append(value)
    if len(rows) != expected_rows:
        raise ValueError(f"{label} artifact row count mismatch")
    return rows


def _hidden_key(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _HIDDEN_FIELD_NAMES:
                return str(key)
            found = _hidden_key(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _hidden_key(nested)
            if found is not None:
                return found
    return None


def _scan_observable(rows: Iterable[Mapping[str, object]], *, label: str) -> None:
    for row in rows:
        hidden = _hidden_key(row)
        if hidden is not None:
            raise HiddenFieldLeakage(f"{label} exposes hidden field {hidden}")


def _identity(period: str, row: Mapping[str, object]) -> SnapshotIdentity:
    required = {"article_key", "page_id", "canonical_title", "wikidata_id"}
    if not required <= set(row):
        raise ValueError(f"{period} article row lacks crosswalk identity fields")
    return SnapshotIdentity(
        period=period,
        article_key=row["article_key"],  # type: ignore[arg-type]
        page_id=row["page_id"],  # type: ignore[arg-type]
        canonical_title=row["canonical_title"],  # type: ignore[arg-type]
        wikidata_id=row["wikidata_id"],  # type: ignore[arg-type]
    )


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"demo config cannot be empty: {path.parent.parent.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def _stage_backend_seed(
    fixture: Path,
    output: Path,
    *,
    period: str,
    descriptor: Mapping[str, object],
) -> tuple[Path, Path]:
    relative = descriptor["path"]
    expected_sha = descriptor["sha256"]
    assert isinstance(relative, str)
    assert isinstance(expected_sha, str)
    source = fixture / relative
    source_checksum = source.with_name(f"{source.name}.sha256")
    artifact_name = "resolved-catalog-v1.jsonl"
    expected_checksum = f"{expected_sha}  {artifact_name}\n".encode("ascii")
    if source_checksum.read_bytes() != expected_checksum:
        raise ValueError(f"{period} backend seed checksum companion mismatch")
    target = output / "backend-seed" / period / artifact_name
    checksum_target = target.with_name(f"{target.name}.sha256")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    checksum_target.write_bytes(source_checksum.read_bytes())
    return target, checksum_target


def prepare_demo_temporal_release(
    fixture_root: str | Path, output_root: str | Path
) -> PreparedDemoRelease:
    """Validate one provenance-ready fixture and stage five demo configs."""
    fixture = Path(fixture_root)
    output = Path(output_root)
    document, manifest_sha = _read_manifest(fixture)
    periods = document["periods"]
    assert isinstance(periods, Mapping)
    artifacts_by_period: dict[str, dict[str, list[dict[str, object]]]] = {}
    backend_seed_paths: dict[str, tuple[Path, Path]] = {}
    identities: list[SnapshotIdentity] = []
    for period in _PERIODS:
        period_document = periods[period]
        if not isinstance(period_document, Mapping) or set(period_document) != {
            "artifacts"
        }:
            raise ValueError(f"{period} period descriptor is invalid")
        descriptors = period_document["artifacts"]
        expected = {"articles"} if period == "2016" else _MONTH_ARTIFACTS
        if not isinstance(descriptors, Mapping) or set(descriptors) != expected:
            raise ValueError(f"{period} artifact set is invalid")
        loaded = {
            name: _read_artifact(fixture, descriptor, label=f"{period} {name}")
            for name, descriptor in descriptors.items()
        }
        _scan_observable(loaded["articles"], label=f"{period} articles")
        identities.extend(_identity(period, row) for row in loaded["articles"])
        artifacts_by_period[period] = loaded
        if period != "2016":
            seed_descriptor = descriptors["backend_seed_catalog"]
            assert isinstance(seed_descriptor, Mapping)
            backend_seed_paths[period] = _stage_backend_seed(
                fixture,
                output,
                period=period,
                descriptor=seed_descriptor,
            )

    crosswalk = build_crosswalk(identities, period_order=_PERIODS)
    config_rows: dict[str, list[dict[str, object]]] = {}
    for period, (catalog_config, hidden_config) in _MONTH_CONFIGS.items():
        config_rows[catalog_config] = artifacts_by_period[period]["articles"]
        hidden_rows: list[dict[str, object]] = []
        for artifact_name in _HIDDEN_ARTIFACTS:
            hidden_rows.extend(
                {
                    "record_type": artifact_name,
                    "period": period,
                    "payload_json": _canonical_json(row).decode("utf-8").rstrip("\n"),
                }
                for row in artifacts_by_period[period][artifact_name]
            )
        config_rows[hidden_config] = hidden_rows
    crosswalk_rows = [
        {
            "record_type": "membership",
            "lineage_id": row.lineage_id,
            "period": row.period,
            "article_key": row.article_key,
            "page_id": row.page_id,
            "canonical_title": row.canonical_title,
            "wikidata_id": row.wikidata_id or "",
            "change_kind": row.change_kind,
            "match_basis": row.match_basis,
            "ambiguity_code": "",
            "ambiguity_payload_json": "",
        }
        for row in crosswalk.rows
    ]
    crosswalk_rows.extend(
        {
            "record_type": "ambiguity",
            "lineage_id": "",
            "period": "",
            "article_key": "",
            "page_id": 0,
            "canonical_title": "",
            "wikidata_id": "",
            "change_kind": "ambiguous",
            "match_basis": "ambiguous",
            "ambiguity_code": finding.code,
            "ambiguity_payload_json": _canonical_json(
                {
                    "periods": finding.periods,
                    "article_keys": finding.article_keys,
                    "page_ids": finding.page_ids,
                    "wikidata_ids": finding.wikidata_ids,
                }
            ).decode("utf-8").rstrip("\n"),
        }
        for finding in crosswalk.ambiguities
    )
    config_rows["demo_crosswalk"] = crosswalk_rows

    config_paths: dict[str, Path] = {}
    config_counts: dict[str, int] = {}
    for config in DEMO_CONFIGS:
        path = output / config / "train" / "part-00000.parquet"
        _write_parquet(path, config_rows[config])
        config_paths[config] = path
        config_counts[config] = len(config_rows[config])
    source = document["source"]
    assert isinstance(source, Mapping)
    release_manifest = {
        "release_version": 1,
        "release_scope": "friday_demo_fixture",
        "snapshot_claim": document["snapshot_claim"],
        "source": dict(source),
        "source_manifest_sha256": manifest_sha,
        "fixture_provenance": document,
        "configs": {
            config: {
                "path": str(config_paths[config].relative_to(output)),
                "sha256": _sha256(config_paths[config]),
                "rows": config_counts[config],
            }
            for config in DEMO_CONFIGS
        },
        "backend_seed": {
            period: {
                "catalog_path": str(paths[0].relative_to(output)),
                "catalog_sha256": _sha256(paths[0]),
                "checksum_path": str(paths[1].relative_to(output)),
                "checksum_sha256": _sha256(paths[1]),
            }
            for period, paths in backend_seed_paths.items()
        },
    }
    release_manifest_path = output / "demo_crosswalk" / "release.json"
    release_manifest_path.write_bytes(_canonical_json(release_manifest))
    return PreparedDemoRelease(
        root=output,
        config_paths=config_paths,
        config_counts=config_counts,
        backend_seed_paths=backend_seed_paths,
        source_revision=str(source["revision"]),
        source_manifest_sha256=manifest_sha,
        snapshot_claim=str(document["snapshot_claim"]),
        release_manifest_path=release_manifest_path,
    )


def _remote_bytes(
    api: object, repo_id: str, path: str, revision: str, token: str
) -> bytes:
    getter = getattr(api, "get_file_bytes", None)
    if callable(getter):
        value = getter(
            repo_id=repo_id,
            path_in_repo=path,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        if not isinstance(value, bytes):
            raise TypeError("remote byte getter returned non-bytes")
        return value
    from huggingface_hub import hf_hub_download

    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=path,
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    return Path(downloaded).read_bytes()


def _render_card(existing: bytes) -> bytes:
    text = existing.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("remote dataset card lacks YAML front matter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("remote dataset card front matter is not closed") from error
    for config in DEMO_CONFIGS:
        if any(line.strip() == f"config_name: {config}" for line in lines[:closing]):
            raise ValueError(f"remote dataset card already contains {config}")
    additions: list[str] = []
    for config in DEMO_CONFIGS:
        additions.extend(
            [
                f"- config_name: {config}",
                "  data_files:",
                "  - split: train",
                f"    path: {config}/train/*.parquet",
            ]
        )
    merged = lines[:closing] + additions + lines[closing:]
    return ("\n".join(merged) + "\n").encode("utf-8")


def _default_add_operation(path_in_repo: str, local: Path) -> object:
    from huggingface_hub import CommitOperationAdd

    return CommitOperationAdd(path_in_repo=path_in_repo, path_or_fileobj=str(local))


def _commit_sha(result: object) -> str:
    candidates = (
        result.get("oid") if isinstance(result, Mapping) else getattr(result, "oid", None),
        result.get("commit_id")
        if isinstance(result, Mapping)
        else getattr(result, "commit_id", None),
        result.get("sha") if isinstance(result, Mapping) else getattr(result, "sha", None),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and _SHA40.fullmatch(candidate):
            return candidate
    raise ValueError("Hub commit did not return an exact SHA")


def verify_demo_configs(
    *,
    repo_id: str,
    revision: str,
    token: str,
    expected_counts: Mapping[str, int],
    load_dataset_fn: Callable[..., Iterable[Mapping[str, object]]] | None = None,
) -> dict[str, int]:
    """Stream every pinned demo config and scan observable rows for hidden fields."""
    if load_dataset_fn is None:
        from datasets import load_dataset as load_dataset_fn
    verified: dict[str, int] = {}
    for config in DEMO_CONFIGS:
        dataset = load_dataset_fn(
            repo_id,
            config,
            split="train",
            revision=revision,
            token=token,
            streaming=True,
        )
        count = 0
        for row in dataset:
            if config in {"demo_catalog_2026_06", "demo_catalog_2026_07"}:
                _scan_observable([row], label=f"remote {config}")
            count += 1
        if count != expected_counts[config]:
            raise ValueError(f"remote {config} row count mismatch")
        verified[config] = count
    return verified


def publish_demo_temporal_release(
    prepared: PreparedDemoRelease,
    *,
    api: object,
    repo_id: str,
    token: str,
    add_operation_factory: Callable[[str, Path], object] | None = None,
    load_dataset_fn: Callable[..., Iterable[Mapping[str, object]]] | None = None,
) -> PublishedDemoRelease:
    """Append the five configs in one commit and verify the pinned result."""
    if repo_id != "dhelmy990/babel-wikipedia-experiment":
        raise ValueError("Friday demo repository identity is fixed")
    info = api.dataset_info(repo_id, revision="main", token=token)
    parent_sha = getattr(info, "sha", None)
    if not isinstance(parent_sha, str) or _SHA40.fullmatch(parent_sha) is None:
        raise ValueError("remote main did not resolve to an exact SHA")
    remote_paths = api.list_repo_files(
        repo_id=repo_id, repo_type="dataset", revision=parent_sha, token=token
    )
    distillation_paths = sorted(
        path for path in remote_paths if path.startswith("distillation_2016/")
    )
    before = {
        path: hashlib.sha256(
            _remote_bytes(api, repo_id, path, parent_sha, token)
        ).hexdigest()
        for path in distillation_paths
    }
    card_path = prepared.root / "README.demo.md"
    card_path.write_bytes(
        _render_card(_remote_bytes(api, repo_id, "README.md", parent_sha, token))
    )
    factory = add_operation_factory or _default_add_operation
    operations = [
        factory(
            str(prepared.config_paths[config].relative_to(prepared.root)),
            prepared.config_paths[config],
        )
        for config in DEMO_CONFIGS
    ]
    operations.extend(
        factory(str(path.relative_to(prepared.root)), path)
        for period in ("2026-06", "2026-07")
        for path in prepared.backend_seed_paths[period]
    )
    operations.extend(
        [
            factory(
                str(prepared.release_manifest_path.relative_to(prepared.root)),
                prepared.release_manifest_path,
            ),
            factory("README.md", card_path),
        ]
    )
    result = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        revision="main",
        parent_commit=parent_sha,
        operations=operations,
        commit_message="data: append Friday demo temporal configs",
        token=token,
    )
    commit_sha = _commit_sha(result)
    after_paths = api.list_repo_files(
        repo_id=repo_id, repo_type="dataset", revision=commit_sha, token=token
    )
    if any(path not in after_paths for path in distillation_paths):
        raise ValueError("published revision removed a distillation_2016 path")
    after = {
        path: hashlib.sha256(
            _remote_bytes(api, repo_id, path, commit_sha, token)
        ).hexdigest()
        for path in distillation_paths
    }
    if after != before:
        raise ValueError("published revision changed distillation_2016 bytes")
    verified = verify_demo_configs(
        repo_id=repo_id,
        revision=commit_sha,
        token=token,
        expected_counts=prepared.config_counts,
        load_dataset_fn=load_dataset_fn,
    )
    return PublishedDemoRelease(commit_sha, parent_sha, verified, after)


__all__ = [
    "DEMO_CONFIGS",
    "HiddenFieldLeakage",
    "PreparedDemoRelease",
    "PublishedDemoRelease",
    "prepare_demo_temporal_release",
    "publish_demo_temporal_release",
    "verify_demo_configs",
]
