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
from .shard import validate_distillation_row


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
    error_name = type(error).__name__
    if "LocalEntryNotFound" in error_name:
        return False
    if "EntryNotFound" in error_name:
        return True
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 404


def _status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _is_parent_conflict(error: BaseException) -> bool:
    status = _status_code(error)
    return status in {409, 412} or "ParentCommit" in type(error).__name__


def _is_transient(error: BaseException) -> bool:
    status = _status_code(error)
    return (
        "LocalEntryNotFound" in type(error).__name__
        or isinstance(error, (TimeoutError, ConnectionError))
        or (isinstance(error, OSError) and not isinstance(error, FileNotFoundError))
        or status in {408, 429}
        or (status is not None and 500 <= status <= 599)
    )


def _retry(
    operation: Callable[[], object],
    *,
    retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None],
    label: str,
) -> object:
    last_error: BaseException | None = None
    for attempt in range(retries):
        try:
            return operation()
        except BaseException as error:
            last_error = error
            if not _is_transient(error) or attempt + 1 == retries:
                break
            sleep(backoff_seconds * (2**attempt))
    raise RemoteVerificationError(
        f"{label} failed after {retries} attempt(s) ({type(last_error).__name__})"
    ) from None


def _remote_or_missing(
    api: object,
    repo_id: str,
    path_in_repo: str,
    revision: str,
    token: str,
    *,
    retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None],
) -> bytes | None:
    last_error: BaseException | None = None
    for attempt in range(retries):
        try:
            return _remote_bytes(api, repo_id, path_in_repo, revision, token)
        except BaseException as error:
            if _is_remote_not_found(error):
                return None
            last_error = error
            if not _is_transient(error) or attempt + 1 == retries:
                break
            sleep(backoff_seconds * (2**attempt))
    raise RemoteVerificationError(
        "unable to inspect existing remote path "
        f"after {retries} attempt(s) ({type(last_error).__name__})"
    ) from None


def _private_info(info: object) -> object:
    if getattr(info, "private", None) is not True:
        raise RemoteVerificationError(
            "dataset repository privacy could not be proved private"
        )
    return info


def _manifest_document(value: bytes, *, label: str) -> dict[str, object]:
    try:
        document = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} manifest is malformed") from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} manifest is malformed")
    try:
        shards = document["shards"]
        counts = document["counts"]
        provenance = document["provenance"]
        provenance_document = provenance["document"]  # type: ignore[index]
        identifiers = provenance["identifiers"]  # type: ignore[index]
    except (KeyError, TypeError) as error:
        raise ValueError(f"{label} manifest is malformed") from error
    if not isinstance(shards, list) or not all(isinstance(item, dict) for item in shards):
        raise ValueError(f"{label} manifest has invalid shards")
    if not isinstance(counts, dict) or not isinstance(provenance, dict):
        raise ValueError(f"{label} manifest is malformed")
    validate_document("provenance-v1", provenance_document)
    if provenance.get("schema") != "provenance-v1" or identifiers != {
        "dataset_config": DEFAULT_CONFIG,
        "example_schema": "distillation-example-v1",
        "snapshot_date": "2016-10-01",
        "teacher_dimension": 100,
    }:
        raise ValueError(f"{label} manifest has invalid fixed provenance identifiers")
    expected_counts = {
        split: sum(int(item["rows"]) for item in shards if item.get("split") == split)
        for split in ("train", "validation", "test")
    }
    if counts != {"total": sum(expected_counts.values()), **expected_counts}:
        raise ValueError(f"{label} manifest counts do not match shards")
    aggregate = hashlib.sha256(
        (json.dumps(shards, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    if document.get("aggregate_sha256") != aggregate:
        raise ValueError(f"{label} manifest aggregate checksum is invalid")
    return document


def _is_monotonic_value(old: object, new: object) -> bool:
    if isinstance(old, dict):
        return isinstance(new, dict) and all(
            key in new and _is_monotonic_value(value, new[key])
            for key, value in old.items()
        )
    if isinstance(old, list):
        return isinstance(new, list) and len(new) >= len(old) and all(
            _is_monotonic_value(value, new[index])
            for index, value in enumerate(old)
        )
    if (
        isinstance(old, (int, float))
        and not isinstance(old, bool)
        and isinstance(new, (int, float))
        and not isinstance(new, bool)
    ):
        return new >= old
    return new == old


def _is_monotonic_provenance(old: object, new: object) -> bool:
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if old.get("schema") != new.get("schema") or old.get("identifiers") != new.get(
        "identifiers"
    ):
        return False
    old_document = old.get("document")
    new_document = new.get("document")
    if not isinstance(old_document, dict) or not isinstance(new_document, dict):
        return False
    old_sources = old_document.get("sources")
    new_sources = new_document.get("sources")
    if not (
        isinstance(old_sources, list)
        and isinstance(new_sources, list)
        and new_sources[: len(old_sources)] == old_sources
    ):
        return False
    old_artifacts = old_document.get("artifacts")
    new_artifacts = new_document.get("artifacts")
    if not isinstance(old_artifacts, dict) or not isinstance(new_artifacts, dict):
        return False
    if any(
        new_artifacts.get(name) != evidence
        for name, evidence in old_artifacts.items()
    ):
        return False
    return (
        old_document.get("schema_version") == new_document.get("schema_version")
        and _is_monotonic_value(
            old_document.get("reports"), new_document.get("reports")
        )
    )


def _validate_manifest_extension(old_bytes: bytes, new_bytes: bytes) -> None:
    old = _manifest_document(old_bytes, label="remote")
    new = _manifest_document(new_bytes, label="local")
    for field in ("manifest_version", "schema", "dataset_config"):
        if old.get(field) != new.get(field):
            raise ValueError(f"manifest extension is not monotonic: changed {field}")
    old_shard_items = old["shards"]  # type: ignore[assignment]
    new_shard_items = new["shards"]  # type: ignore[assignment]
    old_shards = {str(item["path"]): item for item in old_shard_items}
    new_shards = {str(item["path"]): item for item in new_shard_items}
    if len(old_shards) != len(old_shard_items) or len(new_shards) != len(
        new_shard_items
    ):
        raise ValueError("manifest extension is not monotonic: duplicate shard path")
    if new_shard_items[: len(old_shard_items)] != old_shard_items:
        raise ValueError("manifest extension is not monotonic: prior shard changed")
    old_pilot = old.get("pilot_article_keys")
    new_pilot = new.get("pilot_article_keys")
    if not (
        isinstance(old_pilot, list)
        and isinstance(new_pilot, list)
        and new_pilot[: len(old_pilot)] == old_pilot
    ):
        raise ValueError("manifest extension is not monotonic: pilot keys changed")
    old_provenance = old["provenance"]  # type: ignore[index]
    new_provenance = new["provenance"]  # type: ignore[index]
    if not _is_monotonic_provenance(old_provenance, new_provenance):
        raise ValueError("manifest extension is not monotonic: provenance regressed")


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
    manifest = _manifest_document(manifest_path.read_bytes(), label="local")
    if manifest.get("dataset_config") != config_name:
        raise ValueError("local shard manifest configuration does not match upload")
    shards = manifest["shards"]
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
    if config_name != DEFAULT_CONFIG:
        raise ValueError(f"dataset configuration is fixed to {DEFAULT_CONFIG}")
    if not token:
        raise ValueError("a private-Hub token is required")
    if retries <= 0:
        raise ValueError("retries must be positive")
    local_manifest_path = Path(manifest_path)
    local_bytes = local_manifest_path.read_bytes()
    manifest = _manifest_document(local_bytes, label="local")
    try:
        shards = manifest["shards"]
        pilot_keys = frozenset(manifest["pilot_article_keys"])
    except (KeyError, TypeError) as error:
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
            info = _private_info(
                api.dataset_info(repo_id, revision=revision, token=token)
            )
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
                    checked_row = validate_distillation_row(row)
                except Exception as error:
                    raise RemoteVerificationError(
                        f"remote split {split} yielded a semantic-invalid row"
                    ) from error
                if (
                    checked_row["split"] != split
                    or checked_row["article_key"] not in pilot_keys
                ):
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
    if config_name != DEFAULT_CONFIG:
        raise ValueError(f"dataset configuration is fixed to {DEFAULT_CONFIG}")
    if retries <= 0:
        raise ValueError("retries must be positive")
    uploads = _relative_uploads(files, Path(root) if root is not None else None)
    _validate_local_uploads(uploads, config_name)
    _retry(
        lambda: api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=True,
            exist_ok=True,
            token=token,
        ),
        retries=retries,
        backoff_seconds=backoff_seconds,
        sleep=sleep,
        label="private dataset repository creation",
    )
    manifest_remote_path = f"{config_name}/manifest.json"
    returned_sha: str | None = None
    for publication_attempt in range(retries):
        _validate_local_uploads(uploads, config_name)
        info = _private_info(
            _retry(
                lambda: api.dataset_info(
                    repo_id, revision="main", token=token
                ),
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
                label="main revision resolution",
            )
        )
        parent_sha = _checked_sha(
            getattr(info, "sha", None), label="main dataset info SHA"
        )
        pending: list[tuple[str, Path]] = []
        for path_in_repo, local in uploads:
            existing = _remote_or_missing(
                api,
                repo_id,
                path_in_repo,
                parent_sha,
                token,
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
            )
            if existing is None:
                pending.append((path_in_repo, local))
                continue
            local_bytes = local.read_bytes()
            if existing != local_bytes:
                if path_in_repo == manifest_remote_path:
                    _validate_manifest_extension(existing, local_bytes)
                    pending.append((path_in_repo, local))
                    continue
                raise ValueError(
                    "refusing to overwrite remote path with a different checksum: "
                    f"{path_in_repo}"
                )

        if not pending:
            returned_sha = parent_sha
            break
        if not callable(getattr(api, "create_commit", None)):
            raise RemoteVerificationError(
                "atomic append-only publication requires create_commit support"
            )
        try:
            result = api.create_commit(
                repo_id=repo_id,
                repo_type="dataset",
                revision="main",
                parent_commit=parent_sha,
                operations=[
                    _add_operation(api, path, local) for path, local in pending
                ],
                commit_message=f"Publish verified {config_name} shards",
                token=token,
            )
            returned_sha = _commit_sha(result)
            break
        except BaseException as error:
            if isinstance(error, RemoteVerificationError):
                raise
            if (
                (_is_parent_conflict(error) or _is_transient(error))
                and publication_attempt + 1 < retries
            ):
                sleep(backoff_seconds * (2**publication_attempt))
                continue
            raise RemoteVerificationError(
                "atomic Hub commit failed "
                f"after {publication_attempt + 1} attempt(s) ({type(error).__name__})"
            ) from None
    if returned_sha is None:
        raise RemoteVerificationError("atomic Hub commit retries were exhausted")

    info = _private_info(
        _retry(
            lambda: api.dataset_info(repo_id, revision=returned_sha, token=token),
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
            label="published revision resolution",
        )
    )
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
    linked = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write((revision + "\n").encode("ascii"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_name, destination)
        linked = True
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if linked:
            os.unlink(destination)
        raise
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
