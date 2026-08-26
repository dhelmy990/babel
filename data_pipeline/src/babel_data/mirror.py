"""Authenticated mirroring and pinned processing access for source files."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlsplit

from .hub import DEFAULT_REPO_ID
from .release import SourceMirrorReceiptV1
from .sources import (
    SourcePolicyError,
    SourceSpec,
    download_source,
    source_id as derive_source_id,
    verify_file,
)


_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class MirrorVerificationError(RuntimeError):
    """Raised when local and pinned remote source identities do not match."""


@dataclass(frozen=True, slots=True)
class _AddOperation:
    path_in_repo: str
    path_or_fileobj: str


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _checked_commit(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _COMMIT_PATTERN.fullmatch(value) is None:
        raise MirrorVerificationError(
            f"{label} must be a 40-character lowercase hexadecimal value"
        )
    return value


def _returned_commit(result: object) -> str:
    values: list[object]
    if isinstance(result, Mapping):
        values = [result.get(name) for name in ("oid", "commit_id", "sha")]
    else:
        values = [getattr(result, name, None) for name in ("oid", "commit_id", "sha")]
        if isinstance(result, str):
            values.append(result.rsplit("/", 1)[-1])
    for value in values:
        if isinstance(value, str):
            return _checked_commit(value, label="returned commit SHA")
    raise MirrorVerificationError("upload did not return an exact commit SHA")


def _add_operation(api: object, path_in_repo: str, local: Path) -> object:
    if not type(api).__module__.startswith("huggingface_hub"):
        return _AddOperation(path_in_repo, str(local))
    from huggingface_hub import CommitOperationAdd

    return CommitOperationAdd(
        path_in_repo=path_in_repo, path_or_fileobj=str(local)
    )


def _require_repository(repository: str) -> None:
    if urlsplit(repository).scheme in {"http", "https"}:
        raise SourcePolicyError(
            "semantic processing requires an authenticated, pinned Hugging Face mirror"
        )
    if repository != DEFAULT_REPO_ID:
        raise SourcePolicyError(
            f"processing is restricted to private Hugging Face repository {DEFAULT_REPO_ID}"
        )


def validate_data_root(value: str | os.PathLike[str] | None) -> Path:
    """Validate the required external bulk-data root without creating it."""

    if value is None or not str(value):
        raise ValueError("data root is required via --data-root or BABEL_DATA_ROOT")
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("data root must be an absolute path")
    resolved = root.resolve(strict=False)
    repository = _REPOSITORY_ROOT.resolve()
    if resolved == repository or repository in resolved.parents:
        raise ValueError("data root must be outside the repository")
    return resolved


def _resolve_private_revision(
    api: object, repository: str, revision: str, token: str
) -> None:
    try:
        info = api.dataset_info(repository, revision=revision, token=token)  # type: ignore[attr-defined]
    except BaseException as error:
        raise SourcePolicyError(
            "unable to authenticate the exact private Hugging Face revision"
        ) from error
    if getattr(info, "private", None) is not True:
        raise SourcePolicyError("Hugging Face repository could not be proved private")
    resolved = getattr(info, "sha", None)
    if resolved != revision:
        raise SourcePolicyError(
            "Hugging Face resolved revision does not match the exact requested commit"
        )


def _write_bytes(destination: Path, value: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _download_remote(
    api: object,
    repository: str,
    revision: str,
    path_in_repo: str,
    token: str,
    destination: Path,
) -> Path:
    getter = getattr(api, "get_file_bytes", None)
    if callable(getter):
        value = getter(
            repo_id=repository,
            path_in_repo=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        if not isinstance(value, bytes):
            raise MirrorVerificationError("remote source reader returned non-bytes")
        _write_bytes(destination, value)
        return destination

    downloader = getattr(api, "hf_hub_download", None)
    if not callable(downloader):
        from huggingface_hub import hf_hub_download

        downloader = hf_hub_download
    downloaded = Path(
        downloader(
            repo_id=repository,
            filename=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
            local_dir=str(destination.parents[len(PurePosixPath(path_in_repo).parts) - 1]),
            force_download=True,
        )
    )
    if downloaded != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloaded, destination)
    return destination


def receipt_path(
    cache_root: str | os.PathLike[str], receipt: SourceMirrorReceiptV1
) -> Path:
    """Return the deterministic local receipt registry path."""

    return (
        Path(cache_root)
        / receipt.remote_commit_sha
        / ".receipts"
        / f"{receipt.source_id}.json"
    )


def persist_receipt(
    cache_root: str | os.PathLike[str], receipt: SourceMirrorReceiptV1
) -> Path:
    """Persist a receipt without replacing different existing evidence."""

    destination = receipt_path(cache_root, receipt)
    content = receipt.to_json_bytes()
    if destination.exists():
        if destination.read_bytes() != content:
            raise MirrorVerificationError(
                f"existing source mirror receipt differs: {destination}"
            )
        return destination
    _write_bytes(destination, content)
    return destination


def _source_id_from_path(path_in_repo: str) -> str:
    path = PurePosixPath(path_in_repo)
    if (
        path.is_absolute()
        or path.as_posix() != path_in_repo
        or ".." in path.parts
        or len(path.parts) < 3
        or path.parts[0] != "sources"
    ):
        raise SourcePolicyError("processing path must be beneath sources/{source_id}/")
    return path.parts[1]


def mirror_source(
    source: SourceSpec,
    api: object,
    *,
    repository: str = DEFAULT_REPO_ID,
    token: str | None = None,
    data_root: str | os.PathLike[str] | None = None,
    source_identifier: str | None = None,
) -> SourceMirrorReceiptV1:
    """Mirror one manifest source and prove its exact private-Hub bytes."""

    _require_repository(repository)
    effective_token = token or os.environ.get("HF_TOKEN")
    if not effective_token:
        raise ValueError("a private-Hub token is required")
    effective_root = validate_data_root(data_root or os.environ.get("BABEL_DATA_ROOT"))
    identifier = source_identifier or derive_source_id(source)
    staging = effective_root / "raw-mirror-staging"
    local = download_source(source, staging)
    verify_file(local, source)
    local_size, local_sha256 = _file_identity(local)
    if local_size != source.size:
        raise MirrorVerificationError("authoritative local source size changed")
    path_in_repo = f"sources/{identifier}/{source.filename}"

    api.create_repo(  # type: ignore[attr-defined]
        repo_id=repository,
        repo_type="dataset",
        private=True,
        exist_ok=True,
        token=effective_token,
    )
    main_info = api.dataset_info(  # type: ignore[attr-defined]
        repository, revision="main", token=effective_token
    )
    if getattr(main_info, "private", None) is not True:
        raise SourcePolicyError("Hugging Face repository could not be proved private")
    parent = _checked_commit(
        getattr(main_info, "sha", None), label="main dataset commit SHA"
    )
    result = api.create_commit(  # type: ignore[attr-defined]
        repo_id=repository,
        repo_type="dataset",
        revision="main",
        parent_commit=parent,
        operations=[_add_operation(api, path_in_repo, local)],
        commit_message=f"Mirror authoritative source {identifier}",
        token=effective_token,
    )
    revision = _returned_commit(result)
    _resolve_private_revision(api, repository, revision, effective_token)
    remote = effective_root / "hf-cache" / revision / path_in_repo
    _download_remote(
        api, repository, revision, path_in_repo, effective_token, remote
    )
    remote_size, remote_sha256 = _file_identity(remote)
    if remote_size != local_size or not hmac.compare_digest(
        remote_sha256, local_sha256
    ):
        raise MirrorVerificationError(
            "remote source bytes do not match verified authoritative bytes"
        )
    receipt = SourceMirrorReceiptV1(
        source_id=identifier,
        authoritative_url=source.url,
        expected_sha256=local_sha256,
        bytes=local_size,
        repository=repository,
        path_in_repo=path_in_repo,
        remote_commit_sha=revision,
        remote_sha256=remote_sha256,
    )
    persist_receipt(effective_root / "hf-cache", receipt)
    return receipt


def open_processing_source(
    repository: str,
    revision: str | None = None,
    path: str | None = None,
    token: str | None = None,
    cache_root: str | os.PathLike[str] | None = None,
    *,
    api: object | None = None,
) -> Path:
    """Open only a receipt-bound source at an authenticated exact Hub commit."""

    _require_repository(repository)
    if revision is None or _COMMIT_PATTERN.fullmatch(revision) is None:
        raise SourcePolicyError(
            "processing revision must be a 40-character lowercase commit SHA"
        )
    if path is None:
        raise SourcePolicyError("processing requires a repository path")
    identifier = _source_id_from_path(path)
    if not token:
        raise SourcePolicyError("processing requires private Hugging Face authentication")
    if cache_root is None:
        raise SourcePolicyError("processing requires the pinned mirror cache root")
    checked_cache_root = Path(cache_root)
    if not checked_cache_root.is_absolute():
        raise SourcePolicyError("processing cache root must be absolute")
    client = api
    if client is None:
        from huggingface_hub import HfApi

        client = HfApi()
    _resolve_private_revision(client, repository, revision, token)
    registry_path = (
        checked_cache_root / revision / ".receipts" / f"{identifier}.json"
    )
    try:
        receipt = SourceMirrorReceiptV1.from_json_bytes(registry_path.read_bytes())
    except FileNotFoundError as error:
        raise SourcePolicyError(
            "processing requires a remote-verified pinned Hugging Face mirror receipt"
        ) from error
    if (
        receipt.repository != repository
        or receipt.remote_commit_sha != revision
        or receipt.path_in_repo != path
        or receipt.source_id != identifier
    ):
        raise SourcePolicyError("source mirror receipt does not match the pinned source")
    destination = checked_cache_root / revision / path
    if not destination.exists():
        _download_remote(client, repository, revision, path, token, destination)
    size, checksum = _file_identity(destination)
    if size != receipt.bytes:
        raise MirrorVerificationError(
            f"pinned processing source size mismatch: expected {receipt.bytes}, found {size}"
        )
    if not hmac.compare_digest(checksum, receipt.remote_sha256):
        raise MirrorVerificationError("pinned processing source checksum mismatch")
    return destination


__all__ = [
    "MirrorVerificationError",
    "SourceMirrorReceiptV1",
    "mirror_source",
    "open_processing_source",
    "persist_receipt",
    "receipt_path",
    "validate_data_root",
]
