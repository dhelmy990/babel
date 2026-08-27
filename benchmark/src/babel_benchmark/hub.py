"""Immutable one-operator publication for accepted scalability run bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


REQUIRED_DATA_FILES = (
    "feedback.parquet",
    "edges.parquet",
    "requests.parquet",
    "resources.parquet",
)
REQUIRED_BUNDLE_FILES = (
    "manifest.json",
    *REQUIRED_DATA_FILES,
    "summary.json",
    "report.md",
    "checksums.json",
    "model-manifest.json",
)


class AcceptedRunExists(FileExistsError):
    """The immutable local or remote run path has already been accepted."""


class SecretBearingFile(ValueError):
    """A candidate artifact contains a credential marker or secret filename."""


@dataclass(frozen=True, slots=True)
class RunBundle:
    root: Path
    run_id: UUID
    manifest_path: Path
    checksums_path: Path


@dataclass(frozen=True, slots=True)
class RunBundleReceipt:
    repository: str
    commit_sha: str
    bundle_path: str
    artifact_sha256: str
    verified_parquet_rows: dict[str, int]
    model_artifact_path: str
    verified_model_files: dict[str, str]


@dataclass(frozen=True, slots=True)
class UploadOperation:
    path_in_repo: str
    path_or_fileobj: str


class HubApi(Protocol):
    def list_repo_files(self, **kwargs: object) -> list[str]: ...

    def create_commit(self, **kwargs: object) -> object: ...


_SECRET_NAMES = re.compile(
    r"(^|[._-])(\.env|credentials?|secrets?|private[._-]?key)([._-]|$)",
    re.IGNORECASE,
)
_SECRET_MARKERS = (
    b"HF_TOKEN=",
    b"HUGGING_FACE_HUB_TOKEN=",
    b"AUTHORIZATION: BEARER ",
    b"AWS_SECRET_ACCESS_KEY=",
    b"BEGIN PRIVATE KEY",
    b"BEGIN OPENSSH PRIVATE KEY",
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_secret(path: Path) -> None:
    if _SECRET_NAMES.search(path.name) or path.suffix.casefold() in {".pem", ".key"}:
        raise SecretBearingFile(f"secret-bearing filename rejected: {path.name}")
    previous = b""
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            folded = (previous + block).upper()
            if any(marker in folded for marker in _SECRET_MARKERS):
                raise SecretBearingFile(f"secret marker rejected in {path.name}")
            previous = folded[-64:]


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be readable JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_evidence(
    *,
    progress: dict[str, object],
    topology: str,
    placement: dict[str, object],
    hardware: dict[str, object],
    model_ledger: list[dict[str, object]],
    vector_snapshots: list[dict[str, object]],
    acceptance_label: str,
) -> None:
    if acceptance_label not in {"smoke", "formal"}:
        raise ValueError("acceptance_label must be smoke or formal")
    if topology not in {
        "same_process",
        "same_host_split",
        "same_host_isolated",
        "cross_host",
    }:
        raise ValueError("topology is not supported")
    if not progress or not isinstance(placement, dict) or not isinstance(hardware, dict):
        raise ValueError("progress, placement, and hardware evidence are required")
    if not model_ledger or any(row.get("immutable") is not True for row in model_ledger):
        raise ValueError("model ledger must retain immutable selectable models")
    roles = [row.get("role") for row in model_ledger]
    if roles.count("original") != 1 or any(
        role not in {"original", "child"} for role in roles
    ):
        raise ValueError("model ledger must contain one original and optional children")
    if not vector_snapshots:
        raise ValueError("at least one vector snapshot is required")
    for snapshot in vector_snapshots:
        digest = str(snapshot.get("sha256", ""))
        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or int(snapshot.get("rows", 0)) <= 0
            or int(snapshot.get("dimension", 0)) != 100
        ):
            raise ValueError("vector snapshots require a 100d row count and SHA-256")


def _model_artifact_files(
    root: Path, model_manifest: dict[str, Any]
) -> tuple[list[Path], dict[str, Any]]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("model artifact root must be a real directory")
    descriptor_path = root / "state-descriptor.json"
    descriptor = _json_object(descriptor_path, "model state descriptor")
    if descriptor.get("immutable") is not True:
        raise ValueError("model state descriptor must be immutable")
    if descriptor.get("childManifest") != model_manifest:
        raise ValueError("model manifest differs from the reusable child descriptor")
    files = descriptor.get("files")
    online_state = descriptor.get("onlineStatePath")
    if not isinstance(files, dict) or not files or online_state not in files:
        raise ValueError("model descriptor must checksum its online serving state")
    artifact_files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in artifact_files:
        if path.is_symlink():
            raise ValueError("model artifacts cannot contain symbolic links")
        relative = path.relative_to(root).as_posix()
        _reject_secret(path)
        if relative == "state-descriptor.json":
            continue
        expected = files.get(relative)
        if not isinstance(expected, str) or _sha256(path) != expected:
            raise ValueError(f"model descriptor checksum mismatch: {relative}")
    if set(files) != {
        path.relative_to(root).as_posix()
        for path in artifact_files
        if path.name != "state-descriptor.json"
    }:
        raise ValueError("model descriptor file inventory is incomplete")
    return artifact_files, descriptor


def build_run_bundle(
    output_root: str | Path,
    *,
    run_id: UUID,
    feedback_parquet: str | Path,
    edges_parquet: str | Path,
    requests_parquet: str | Path,
    resources_parquet: str | Path,
    summary_json: str | Path,
    report_markdown: str | Path,
    model_manifest: str | Path,
    model_artifact_root: str | Path,
    progress: dict[str, object],
    topology: str,
    placement: dict[str, object],
    hardware: dict[str, object],
    model_ledger: list[dict[str, object]],
    vector_snapshots: list[dict[str, object]],
    acceptance_label: str,
) -> RunBundle:
    """Build one complete immutable run directory by atomic rename."""
    _validate_evidence(
        progress=progress,
        topology=topology,
        placement=placement,
        hardware=hardware,
        model_ledger=model_ledger,
        vector_snapshots=vector_snapshots,
        acceptance_label=acceptance_label,
    )
    sources = {
        "feedback.parquet": Path(feedback_parquet),
        "edges.parquet": Path(edges_parquet),
        "requests.parquet": Path(requests_parquet),
        "resources.parquet": Path(resources_parquet),
        "summary.json": Path(summary_json),
        "report.md": Path(report_markdown),
        "model-manifest.json": Path(model_manifest),
    }
    for path in sources.values():
        if not path.is_file():
            raise FileNotFoundError(path)
        _reject_secret(path)
    _json_object(sources["summary.json"], "summary")
    model_document = _json_object(sources["model-manifest.json"], "model manifest")
    model_artifact_files, _descriptor = _model_artifact_files(
        Path(model_artifact_root), model_document
    )
    if not sources["report.md"].read_text(encoding="utf-8").strip():
        raise ValueError("report must not be empty")

    import pyarrow.parquet as pq

    for name in REQUIRED_DATA_FILES:
        if pq.ParquetFile(sources[name]).metadata.num_rows < 1:
            raise ValueError(f"{name} must contain at least one row")

    runs_root = Path(output_root) / "runs"
    final = runs_root / str(run_id)
    partial = runs_root / f"{run_id}.partial"
    if final.exists():
        raise AcceptedRunExists(f"accepted local run already exists: {run_id}")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        for name, source in sources.items():
            shutil.copyfile(source, partial / name)
        artifact_destination = partial / "model-artifact"
        artifact_destination.mkdir()
        for source in model_artifact_files:
            relative = source.relative_to(Path(model_artifact_root))
            destination = artifact_destination / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        artifact_names = [
            f"model-artifact/{path.relative_to(Path(model_artifact_root)).as_posix()}"
            for path in model_artifact_files
        ]
        manifest_document = {
            "schemaVersion": 1,
            "runId": str(run_id),
            "acceptanceLabel": acceptance_label,
            "progress": progress,
            "topology": topology,
            "placement": placement,
            "hardware": hardware,
            "modelLedger": model_ledger,
            "vectorSnapshots": vector_snapshots,
            "files": sorted(
                name for name in REQUIRED_BUNDLE_FILES if name != "checksums.json"
            ),
            "modelArtifact": {
                "descriptorPath": "model-artifact/state-descriptor.json",
                "files": artifact_names,
            },
        }
        manifest = partial / "manifest.json"
        manifest.write_bytes(_canonical_json(manifest_document))
        checksums = {
            path.relative_to(partial).as_posix(): _sha256(path)
            for path in sorted(
                (item for item in partial.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(partial).as_posix(),
            )
        }
        checksums_path = partial / "checksums.json"
        checksums_path.write_bytes(_canonical_json(checksums))
        for path in (item for item in partial.rglob("*") if item.is_file()):
            _reject_secret(path)
            with path.open("rb") as source:
                os.fsync(source.fileno())
        os.replace(partial, final)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
    return RunBundle(
        root=final,
        run_id=run_id,
        manifest_path=final / "manifest.json",
        checksums_path=final / "checksums.json",
    )


def _hub_operations(api: HubApi, bundle: RunBundle) -> list[object]:
    prefix = f"runs/{bundle.run_id}"
    paths = sorted(
        (path for path in bundle.root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(bundle.root).as_posix(),
    )
    if type(api).__module__.startswith("huggingface_hub"):
        from huggingface_hub import CommitOperationAdd

        return [
            CommitOperationAdd(
                path_in_repo=f"{prefix}/{path.relative_to(bundle.root).as_posix()}",
                path_or_fileobj=path,
            )
            for path in paths
        ]
    return [
        UploadOperation(
            f"{prefix}/{path.relative_to(bundle.root).as_posix()}", str(path)
        )
        for path in paths
    ]


def _download(
    api: HubApi,
    *,
    repo_id: str,
    filename: str,
    revision: str,
    token: str | None,
) -> Path:
    downloader = getattr(api, "hf_hub_download", None)
    if downloader is None:
        from huggingface_hub import hf_hub_download

        downloader = hf_hub_download
    return Path(
        downloader(
            repo_id=repo_id,
            filename=filename,
            revision=revision,
            repo_type="dataset",
            token=token,
        )
    )


def publish_run_bundle(
    api: HubApi,
    bundle: RunBundle,
    *,
    repo_id: str,
    token: str | None,
    revision: str = "main",
) -> RunBundleReceipt:
    """Upload once, then verify the returned immutable commit by remote reload."""
    prefix = f"runs/{bundle.run_id}"
    remote_files = api.list_repo_files(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    if any(path == prefix or path.startswith(prefix + "/") for path in remote_files):
        raise AcceptedRunExists(f"accepted remote run already exists: {prefix}")
    for path in (item for item in bundle.root.rglob("*") if item.is_file()):
        _reject_secret(path)
    result = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        operations=_hub_operations(api, bundle),
        commit_message=f"Publish immutable scalability run {bundle.run_id}",
        token=token,
    )
    commit_sha = str(
        getattr(result, "oid", None)
        or getattr(result, "commit_id", None)
        or getattr(result, "commit_sha", "")
    )
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise ValueError("Hugging Face did not return an immutable commit SHA")

    checksums_path = _download(
        api,
        repo_id=repo_id,
        filename=f"{prefix}/checksums.json",
        revision=commit_sha,
        token=token,
    )
    if _sha256(checksums_path) != _sha256(bundle.checksums_path):
        raise ValueError("remote checksum inventory differs from the accepted bundle")
    checksums = _json_object(checksums_path, "remote checksums")
    required_checksums = set(REQUIRED_BUNDLE_FILES) - {"checksums.json"}
    if (
        not required_checksums.issubset(checksums)
        or "model-artifact/state-descriptor.json" not in checksums
    ):
        raise ValueError("remote bundle checksum inventory is incomplete")
    loaded: dict[str, Path] = {"checksums.json": checksums_path}
    for name in checksums:
        loaded[name] = _download(
            api,
            repo_id=repo_id,
            filename=f"{prefix}/{name}",
            revision=commit_sha,
            token=token,
        )
    for name, digest in checksums.items():
        if name not in loaded or _sha256(loaded[name]) != digest:
            raise ValueError(f"remote checksum mismatch: {name}")
    manifest = _json_object(loaded["manifest.json"], "remote manifest")
    summary = _json_object(loaded["summary.json"], "remote summary")
    model_manifest = _json_object(loaded["model-manifest.json"], "remote model manifest")
    if manifest.get("runId") != str(bundle.run_id) or not summary or not model_manifest:
        raise ValueError("remote run identity or JSON evidence is incomplete")
    descriptor = _json_object(
        loaded["model-artifact/state-descriptor.json"], "remote model state descriptor"
    )
    if (
        descriptor.get("childManifest") != model_manifest
        or descriptor.get("immutable") is not True
    ):
        raise ValueError("remote model state is not the selected immutable child")
    descriptor_files = descriptor.get("files")
    if (
        not isinstance(descriptor_files, dict)
        or descriptor.get("onlineStatePath") not in descriptor_files
    ):
        raise ValueError("remote model state descriptor is incomplete")
    verified_model_files = {
        name: str(checksums[name])
        for name in checksums
        if name.startswith("model-artifact/")
    }
    for relative, digest in descriptor_files.items():
        name = f"model-artifact/{relative}"
        if name not in loaded or _sha256(loaded[name]) != digest:
            raise ValueError(f"remote model state checksum mismatch: {relative}")

    import pyarrow.parquet as pq

    row_counts: dict[str, int] = {}
    for name in REQUIRED_DATA_FILES:
        table = pq.read_table(loaded[name], columns=None).slice(0, 1)
        if table.num_rows != 1:
            raise ValueError(f"remote {name} did not reload one evidence row")
        row_counts[name] = table.num_rows
    return RunBundleReceipt(
        repository=repo_id,
        commit_sha=commit_sha,
        bundle_path=prefix,
        artifact_sha256=_sha256(bundle.checksums_path),
        verified_parquet_rows=row_counts,
        model_artifact_path=f"{prefix}/model-artifact/state-descriptor.json",
        verified_model_files=verified_model_files,
    )


__all__ = [
    "AcceptedRunExists",
    "RunBundle",
    "RunBundleReceipt",
    "SecretBearingFile",
    "build_run_bundle",
    "publish_run_bundle",
]
