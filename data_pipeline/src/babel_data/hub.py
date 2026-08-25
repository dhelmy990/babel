"""Append-only publication and exact-revision verification for the private Hub."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .contracts import validate_document


DEFAULT_REPO_ID = "dhelmy990/babel-wikipedia-experiment"
DEFAULT_CONFIG = "distillation_2016"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RemoteVerificationError(RuntimeError):
    """Raised when a pinned remote commit cannot be proved equivalent locally."""


@dataclass(frozen=True, slots=True)
class VerifiedRemote:
    commit_sha: str
    manifest_sha256: str
    verified_paths: frozenset[str]
    split_examples: dict[str, int]


@dataclass(frozen=True, slots=True)
class _AddOperation:
    path_in_repo: str
    path_or_fileobj: str


def _checked_sha(value: object, *, label: str = "commit SHA") -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise RemoteVerificationError(
            f"{label} must be a lowercase 40-character hexadecimal value"
        )
    return value


def _commit_sha(result: object) -> str:
    candidates: list[object] = []
    if isinstance(result, Mapping):
        candidates.extend(result.get(name) for name in ("oid", "commit_id", "sha"))
    else:
        candidates.extend(
            getattr(result, name, None) for name in ("oid", "commit_id", "sha")
        )
        if isinstance(result, str):
            candidates.append(result.rsplit("/", 1)[-1])
    for candidate in candidates:
        if isinstance(candidate, str):
            return _checked_sha(candidate, label="returned commit SHA")
    raise RemoteVerificationError("upload did not return a 40-character commit SHA")


def _add_operation(api: object, path_in_repo: str, local: Path) -> object:
    if not type(api).__module__.startswith("huggingface_hub"):
        return _AddOperation(path_in_repo, str(local))
    try:
        from huggingface_hub import CommitOperationAdd
    except (ImportError, ModuleNotFoundError):
        return _AddOperation(path_in_repo, str(local))
    return CommitOperationAdd(path_in_repo=path_in_repo, path_or_fileobj=str(local))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _remote_bytes(
    api: object,
    repo_id: str,
    path_in_repo: str,
    revision: str,
    token: str,
) -> bytes:
    getter = getattr(api, "get_file_bytes", None)
    if callable(getter):
        value = getter(
            repo_id=repo_id,
            path_in_repo=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        if not isinstance(value, bytes):
            raise TypeError("remote byte getter returned a non-bytes value")
        return value
    downloader = getattr(api, "hf_hub_download", None)
    if callable(downloader):
        downloaded = downloader(
            repo_id=repo_id,
            filename=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
    else:
        from huggingface_hub import hf_hub_download

        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
    return Path(downloaded).read_bytes()


def _dataset_loader() -> Callable[..., object]:
    from datasets import load_dataset

    return load_dataset


def _is_remote_not_found(error: BaseException) -> bool:
    if "EntryNotFound" in type(error).__name__:
        return True
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 404


def _relative_uploads(
    files: Iterable[str | os.PathLike[str]], root: Path | None
) -> list[tuple[str, Path]]:
    resolved = [Path(file) for file in files]
    if not resolved:
        raise ValueError("files must contain shards and a manifest")
    if root is None:
        manifests = [path for path in resolved if path.name == "manifest.json"]
        if len(manifests) != 1:
            raise ValueError("exactly one manifest.json is required when root is omitted")
        manifest = manifests[0]
        root = (
            manifest.parent.parent
            if manifest.parent.name == DEFAULT_CONFIG
            else manifest.parent
        )
    root = root.resolve()
    uploads: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in resolved:
        absolute = path.resolve(strict=True)
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError(f"upload file is outside root: {path}") from error
        if relative == "manifest.json":
            relative = f"{DEFAULT_CONFIG}/manifest.json"
        elif not relative.startswith(f"{DEFAULT_CONFIG}/"):
            relative = f"{DEFAULT_CONFIG}/{relative}"
        if relative in seen:
            raise ValueError(f"duplicate remote path: {relative}")
        seen.add(relative)
        uploads.append((relative, absolute))
    if f"{DEFAULT_CONFIG}/manifest.json" not in seen:
        raise ValueError("files must include the config manifest")
    return sorted(uploads, key=lambda item: (item[0].endswith("/manifest.json"), item[0]))


def _validate_local_uploads(
    uploads: Sequence[tuple[str, Path]], config_name: str
) -> None:
    by_remote_path = dict(uploads)
    manifest_path = by_remote_path[f"{config_name}/manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shards = manifest["shards"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("local shard manifest is malformed") from error
    expected = {str(item["path"]) for item in shards}
    supplied = set(by_remote_path) - {f"{config_name}/manifest.json"}
    if supplied != expected:
        raise ValueError("upload files do not exactly match the local shard manifest")
    for item in shards:
        local = by_remote_path[str(item["path"])]
        if local.stat().st_size != int(item["bytes"]):
            raise ValueError(f"local shard size does not match manifest: {item['path']}")
        if _file_sha256(local) != item["sha256"]:
            raise ValueError(f"local shard checksum does not match manifest: {item['path']}")


def verify_remote(
    api: object,
    repo_id: str,
    revision: str,
    manifest_path: str | os.PathLike[str],
    token: str,
    *,
    config_name: str = DEFAULT_CONFIG,
    load_dataset_fn: Callable[..., object] | None = None,
    retries: int = 4,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> VerifiedRemote:
    """Verify manifest bytes and representative rows at one immutable commit."""
    revision = _checked_sha(revision)
    if not token:
        raise ValueError("a private-Hub token is required")
    if retries <= 0:
        raise ValueError("retries must be positive")
    local_manifest_path = Path(manifest_path)
    local_bytes = local_manifest_path.read_bytes()
    try:
        manifest = json.loads(local_bytes)
        shards = manifest["shards"]
        pilot_keys = frozenset(manifest["pilot_article_keys"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("local shard manifest is malformed") from error
    if not isinstance(shards, list) or not all(
        isinstance(item, Mapping) for item in shards
    ):
        raise ValueError("local shard manifest has invalid shards")
    verified_paths = frozenset(str(item["path"]) for item in shards)
    split_counts = {
        split: sum(int(item["rows"]) for item in shards if item["split"] == split)
        for split in ("train", "validation", "test")
    }
    loader = load_dataset_fn or _dataset_loader()
    last_error: BaseException | None = None
    for attempt in range(retries):
        try:
            info = api.dataset_info(repo_id, revision=revision, token=token)
            info_sha = _checked_sha(getattr(info, "sha", None), label="dataset info SHA")
            if info_sha != revision:
                raise RemoteVerificationError(
                    "dataset info SHA does not match the exact requested revision"
                )
            remote_manifest = _remote_bytes(
                api, repo_id, f"{config_name}/manifest.json", revision, token
            )
            if remote_manifest != local_bytes:
                raise RemoteVerificationError("remote manifest bytes do not match local manifest")
            # Validate all advertised digests before trusting the manifest as evidence.
            for item in shards:
                checksum = item.get("sha256")
                if (
                    not isinstance(checksum, str)
                    or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
                ):
                    raise RemoteVerificationError(
                        "remote manifest contains an invalid shard checksum"
                    )
            dataset = loader(
                repo_id,
                name=config_name,
                revision=revision,
                streaming=True,
                token=token,
            )
            verified_examples: dict[str, int] = {}
            for split, expected_count in split_counts.items():
                if expected_count == 0:
                    continue
                if not isinstance(dataset, Mapping) or split not in dataset:
                    raise RemoteVerificationError(
                        f"remote dataset is missing nonempty split {split}"
                    )
                try:
                    row = next(iter(dataset[split]))
                except StopIteration as error:
                    raise RemoteVerificationError(f"remote split {split} is empty") from error
                if not isinstance(row, Mapping):
                    raise RemoteVerificationError(f"remote split {split} yielded an invalid row")
                try:
                    validate_document("distillation-example-v1", row)
                except Exception as error:
                    raise RemoteVerificationError(
                        f"remote split {split} yielded a schema-invalid row"
                    ) from error
                if row["split"] != split or row["article_key"] not in pilot_keys:
                    raise RemoteVerificationError(
                        f"remote split {split} row is not represented by the exact manifest"
                    )
                verified_examples[split] = 1
            return VerifiedRemote(
                commit_sha=revision,
                manifest_sha256=hashlib.sha256(local_bytes).hexdigest(),
                verified_paths=verified_paths,
                split_examples=verified_examples,
            )
        except BaseException as error:
            last_error = error
            if attempt + 1 < retries:
                sleep(backoff_seconds * (2**attempt))
    if isinstance(last_error, RemoteVerificationError):
        raise last_error
    raise RemoteVerificationError(
        f"remote verification failed after {retries} attempt(s) ({type(last_error).__name__})"
    ) from None


def publish_verified_shards(
    api: object,
    repo_id: str,
    files: Sequence[str | os.PathLike[str]],
    token: str,
    *,
    root: str | os.PathLike[str] | None = None,
    config_name: str = DEFAULT_CONFIG,
    load_dataset_fn: Callable[..., object] | None = None,
    retries: int = 4,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Upload append-only files to ``main`` and return the verified commit SHA."""
    if not token:
        raise ValueError("a private-Hub token is required")
    uploads = _relative_uploads(files, Path(root) if root is not None else None)
    _validate_local_uploads(uploads, config_name)
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        private=True,
        exist_ok=True,
        token=token,
    )
    pending: list[tuple[str, Path]] = []
    for path_in_repo, local in uploads:
        try:
            existing = _remote_bytes(api, repo_id, path_in_repo, "main", token)
        except Exception as error:
            if isinstance(error, (FileNotFoundError, OSError)) or _is_remote_not_found(error):
                pending.append((path_in_repo, local))
                continue
            raise RemoteVerificationError(
                f"unable to inspect existing remote path ({type(error).__name__})"
            ) from None
        if hashlib.sha256(existing).digest() != hashlib.sha256(
            local.read_bytes()
        ).digest():
            raise ValueError(
                "refusing to overwrite remote path with a different checksum: "
                f"{path_in_repo}"
            )

    if pending and callable(getattr(api, "create_commit", None)):
        result = api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            revision="main",
            operations=[_add_operation(api, path, local) for path, local in pending],
            commit_message=f"Publish verified {config_name} shards",
            token=token,
        )
        returned_sha = _commit_sha(result)
    elif pending:
        returned_sha = ""
        for path_in_repo, local in pending:
            result = api.upload_file(
                path_or_fileobj=local,
                path_in_repo=path_in_repo,
                repo_id=repo_id,
                repo_type="dataset",
                revision="main",
                token=token,
                commit_message=f"Publish verified {config_name} file",
            )
            returned_sha = _commit_sha(result)
    else:
        returned_sha = _checked_sha(
            getattr(
                api.dataset_info(repo_id, revision="main", token=token), "sha", None
            ),
            label="dataset info SHA",
        )

    info = api.dataset_info(repo_id, revision=returned_sha, token=token)
    info_sha = _checked_sha(getattr(info, "sha", None), label="dataset info SHA")
    if info_sha != returned_sha:
        raise RemoteVerificationError("returned commit SHA does not match dataset info SHA")
    manifest = next(local for path, local in uploads if path == f"{config_name}/manifest.json")
    verify_remote(
        api,
        repo_id,
        returned_sha,
        manifest,
        token,
        config_name=config_name,
        load_dataset_fn=load_dataset_fn,
        retries=retries,
        backoff_seconds=backoff_seconds,
        sleep=sleep,
    )
    return returned_sha


def write_revision_file(path: str | os.PathLike[str], revision: str) -> None:
    """Atomically create a mode-0600 revision handoff without clobbering."""
    revision = _checked_sha(revision)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write((revision + "\n").encode("ascii"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_name, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
