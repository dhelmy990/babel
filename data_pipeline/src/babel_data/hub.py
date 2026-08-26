"""Append-only publication and exact-revision verification for the private Hub."""

from __future__ import annotations

import gc
import hashlib
import heapq
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .interview_export import (
    INTERVIEW_CONFIG,
    INTERVIEW_COUNTS,
    INTERVIEW_SEED,
    _rank_identity,
)
from .release import (
    EMPTY_TEST_PATH,
    MANIFEST_PATH,
    METADATA_PATHS,
    README_PATH,
    READINESS_PATH,
    canonical_json,
    identity_rows_sha256,
    merge_dataset_card,
    render_dataset_card,
    validate_manifest_bytes as _validate_manifest_bytes,
    validate_manifest_extension as _validate_release_extension,
    validate_readiness_alignment,
    validate_readiness_extension,
)
from .shard import PARQUET_SCHEMA, validate_distillation_row


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


def _metadata_sha256(info: object) -> str | None:
    lfs = getattr(info, "lfs", None)
    if isinstance(lfs, Mapping):
        value = lfs.get("sha256")
    else:
        value = getattr(lfs, "sha256", None)
    return value if isinstance(value, str) else None


def _remote_shard_chunks(
    api: object,
    repo_id: str,
    path_in_repo: str,
    revision: str,
    token: str,
) -> Iterable[bytes]:
    streamer = getattr(api, "iter_file_bytes", None)
    if callable(streamer):
        yield from streamer(
            repo_id=repo_id,
            path_in_repo=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        return
    downloader = getattr(api, "hf_hub_download", None)
    if callable(downloader):
        downloaded = downloader(
            repo_id=repo_id,
            filename=path_in_repo,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        with Path(downloaded).open("rb") as source:
            yield from iter(lambda: source.read(1024 * 1024), b"")
        return
    if (
        callable(getattr(api, "get_file_bytes", None))
        and not type(api).__module__.startswith("huggingface_hub")
    ):
        raise RemoteVerificationError(
            "custom remote shard access requires a streaming adapter"
        )
    from huggingface_hub import hf_hub_download

    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=path_in_repo,
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    with Path(downloaded).open("rb") as source:
        yield from iter(lambda: source.read(1024 * 1024), b"")


def _verify_remote_shards(
    api: object,
    repo_id: str,
    revision: str,
    token: str,
    shards: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    paths = [str(item["path"]) for item in shards]
    metadata_getter = getattr(api, "get_paths_info", None)
    if callable(metadata_getter):
        infos = list(
            metadata_getter(
                repo_id=repo_id,
                paths=paths,
                repo_type="dataset",
                revision=revision,
                token=token,
            )
        )
        by_path = {
            str(getattr(info, "path", getattr(info, "rfilename", ""))): info
            for info in infos
        }
        if (
            len(infos) != len(paths)
            or len(by_path) != len(infos)
            or set(by_path) != set(paths)
        ):
            raise RemoteVerificationError("remote shard metadata is incomplete")
        metadata_complete = all(_metadata_sha256(by_path[path]) for path in paths)
        if metadata_complete:
            for item in shards:
                path = str(item["path"])
                info = by_path[path]
                if (
                    getattr(info, "size", None) != int(item["bytes"])
                    or _metadata_sha256(info) != item["sha256"]
                ):
                    raise RemoteVerificationError(
                        f"remote shard metadata does not match manifest: {path}"
                    )
            return frozenset(paths)

    verified: set[str] = set()
    for item in shards:
        path = str(item["path"])
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in _remote_shard_chunks(
                api, repo_id, path, revision, token
            ):
                if not isinstance(chunk, bytes):
                    raise TypeError("remote shard stream yielded a non-bytes chunk")
                digest.update(chunk)
                size += len(chunk)
        except RemoteVerificationError:
            raise
        except BaseException as error:
            raise RemoteVerificationError(f"unable to read remote shard: {path}") from error
        if size != int(item["bytes"]) or digest.hexdigest() != item["sha256"]:
            raise RemoteVerificationError(
                f"remote shard bytes do not match manifest: {path}"
            )
        verified.add(path)
    return frozenset(verified)


def _private_info(info: object) -> object:
    if getattr(info, "private", None) is not True:
        raise RemoteVerificationError(
            "dataset repository privacy could not be proved private"
        )
    return info


def _manifest_document(value: bytes, *, label: str) -> dict[str, object]:
    return _validate_manifest_bytes(value, label=label)


def _validate_manifest_extension(
    old_bytes: bytes,
    new_bytes: bytes,
    *,
    expected_predecessor_sha: str | None = None,
) -> None:
    _validate_release_extension(
        old_bytes,
        new_bytes,
        expected_predecessor_sha=expected_predecessor_sha,
    )


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
        if relative in {README_PATH, READINESS_PATH}:
            pass
        elif relative == "manifest.json":
            relative = f"{DEFAULT_CONFIG}/manifest.json"
        elif not relative.startswith(f"{DEFAULT_CONFIG}/"):
            relative = f"{DEFAULT_CONFIG}/{relative}"
        if relative in seen:
            raise ValueError(f"duplicate remote path: {relative}")
        seen.add(relative)
        uploads.append((relative, absolute))
    missing_metadata = METADATA_PATHS - seen
    if missing_metadata:
        raise ValueError(
            "files must include manifest, readiness, and README exactly: "
            + ", ".join(sorted(missing_metadata))
        )
    return sorted(uploads, key=lambda item: (item[0].endswith("/manifest.json"), item[0]))


def _validate_local_uploads(
    uploads: Sequence[tuple[str, Path]],
    config_name: str,
    *,
    allow_legacy_card: bool = False,
) -> None:
    by_remote_path = dict(uploads)
    manifest_path = by_remote_path[f"{config_name}/manifest.json"]
    manifest = _manifest_document(manifest_path.read_bytes(), label="local")
    if manifest.get("dataset_config") != config_name:
        raise ValueError("local shard manifest configuration does not match upload")
    shards = manifest["shards"]
    expected = {str(item["path"]) for item in shards} | METADATA_PATHS
    supplied = set(by_remote_path)
    if supplied != expected:
        raise ValueError("upload files do not exactly match the local shard manifest")
    empty_test = pq.ParquetFile(by_remote_path[EMPTY_TEST_PATH])
    if (
        not empty_test.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True)
        or empty_test.metadata.num_rows != 0
    ):
        raise ValueError("local empty test sentinel violates the zero-row schema")
    for item in shards:
        local = by_remote_path[str(item["path"])]
        if local.stat().st_size != int(item["bytes"]):
            raise ValueError(f"local shard size does not match manifest: {item['path']}")
        if _file_sha256(local) != item["sha256"]:
            raise ValueError(f"local shard checksum does not match manifest: {item['path']}")
        shard_count = 0
        min_key: str | None = None
        max_key: str | None = None
        min_rank: str | None = None
        max_rank: str | None = None
        prior_order: tuple[str, str] | None = None
        parquet_file = pq.ParquetFile(local)
        if not parquet_file.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True):
            raise ValueError(
                f"local shard physical Parquet schema does not match contract: {item['path']}"
            )

        def checked_identities() -> Iterable[Mapping[str, object]]:
            nonlocal shard_count, min_key, max_key, min_rank, max_rank, prior_order
            for batch in parquet_file.iter_batches(batch_size=4096):
                for raw_row in batch.to_pylist():
                    row = validate_distillation_row(raw_row)
                    if row["split"] != item["split"]:
                        raise ValueError(
                            f"local shard split does not match manifest: {item['path']}"
                        )
                    key = str(row["article_key"])
                    page_id = int(row["page_id"])
                    rank = hashlib.sha256(key.encode("utf-8")).hexdigest()
                    order = (rank, key)
                    if prior_order is not None and order <= prior_order:
                        raise ValueError(
                            f"local shard rows are not in canonical rank order: {item['path']}"
                        )
                    prior_order = order
                    shard_count += 1
                    min_key = key if min_key is None else min(min_key, key)
                    max_key = key if max_key is None else max(max_key, key)
                    min_rank = rank if min_rank is None else min(min_rank, rank)
                    max_rank = rank if max_rank is None else max(max_rank, rank)
                    yield {"article_key": key, "page_id": page_id}

        shard_digest = identity_rows_sha256(checked_identities())
        if shard_count != int(item["rows"]):
            raise ValueError(f"local shard row count does not match manifest: {item['path']}")
        if shard_digest != item["rows_sha256"]:
            raise ValueError(
                f"local shard row identity digest does not match manifest: {item['path']}"
            )
        if (
            item["min_article_key"] != min_key
            or item["max_article_key"] != max_key
            or item["min_rank"] != min_rank
            or item["max_rank"] != max_rank
        ):
            raise ValueError(f"local shard bounds do not match rows: {item['path']}")

    def identity_stream(local: Path) -> Iterable[tuple[str, str, int]]:
        prior: tuple[str, str] | None = None
        parquet = pq.ParquetFile(local)
        for batch in parquet.iter_batches(
            batch_size=4096, columns=["article_key", "page_id"]
        ):
            for row in batch.to_pylist():
                key = str(row["article_key"])
                page_id = int(row["page_id"])
                rank = hashlib.sha256(key.encode("utf-8")).hexdigest()
                order = (rank, key)
                if prior is not None and order <= prior:
                    raise ValueError("local shard identity order changed during validation")
                prior = order
                yield rank, key, page_id

    def merged_identities() -> Iterable[Mapping[str, object]]:
        streams = [
            iter(identity_stream(by_remote_path[str(item["path"])]))
            for item in shards
        ]
        heap: list[tuple[str, str, int, int]] = []
        for index, stream in enumerate(streams):
            try:
                rank, key, page_id = next(stream)
            except StopIteration:
                continue
            heapq.heappush(heap, (rank, key, page_id, index))
        pilot_keys = manifest["pilot_article_keys"]
        emitted = 0
        pilot_matched = 0
        prior_identity: tuple[str, int] | None = None
        while heap:
            rank, key, page_id, index = heapq.heappop(heap)
            identity = (key, page_id)
            if identity == prior_identity:
                raise ValueError("local shards contain an overlapping row identity")
            if pilot_matched < len(pilot_keys):
                if pilot_keys[pilot_matched] != key:
                    raise ValueError("local pilot article keys do not match manifest rows")
                pilot_matched += 1
            emitted += 1
            prior_identity = identity
            yield {"article_key": key, "page_id": page_id}
            try:
                next_rank, next_key, next_page_id = next(streams[index])
            except StopIteration:
                continue
            heapq.heappush(
                heap, (next_rank, next_key, next_page_id, index)
            )
        if pilot_matched != len(pilot_keys):
            raise ValueError("local pilot article keys do not match manifest rows")

    if identity_rows_sha256(merged_identities()) != manifest["rows_sha256"]:
        raise ValueError("local aggregate row identity digest does not match manifest")
    for item in shards:
        local = by_remote_path[str(item["path"])]
        if local.stat().st_size != int(item["bytes"]) or _file_sha256(local) != item["sha256"]:
            raise ValueError(f"local shard changed during validation: {item['path']}")
    try:
        readiness = json.loads(by_remote_path[READINESS_PATH].read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("local readiness is malformed") from error
    if not isinstance(readiness, dict):
        raise ValueError("local readiness is malformed")
    validate_readiness_alignment(readiness, manifest)
    active_root = manifest.get("active_release_root")  # type: ignore[assignment]
    card = by_remote_path[README_PATH].read_bytes()
    merged_card = merge_dataset_card(card, active_root)  # type: ignore[arg-type]
    legacy_card = render_dataset_card(
        active_root, include_interview=False  # type: ignore[arg-type]
    )
    if card != merged_card and not (allow_legacy_card and card == legacy_card):
        raise ValueError("local README does not match the fixed dataset card")


def _snapshot_uploads(
    uploads: Sequence[tuple[str, Path]], snapshot_root: Path
) -> list[tuple[str, Path]]:
    snapshot: list[tuple[str, Path]] = []
    for path_in_repo, source in uploads:
        destination = snapshot_root / path_in_repo
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        snapshot.append((path_in_repo, destination))
    return snapshot


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
    artifact_root = local_manifest_path.parent.parent
    local_readiness_bytes = (artifact_root / READINESS_PATH).read_bytes()
    local_readme_bytes = (artifact_root / README_PATH).read_bytes()
    local_empty_test_bytes = (artifact_root / EMPTY_TEST_PATH).read_bytes()
    try:
        local_readiness = json.loads(local_readiness_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("local readiness is malformed") from error
    if not isinstance(local_readiness, dict):
        raise ValueError("local readiness is malformed")
    validate_readiness_alignment(local_readiness, manifest)
    active_root = manifest.get("active_release_root")  # type: ignore[assignment]
    if merge_dataset_card(local_readme_bytes, active_root) != local_readme_bytes:  # type: ignore[arg-type]
        raise ValueError("local README does not match the fixed dataset card")
    try:
        shards = manifest["shards"]
        pilot_keys = frozenset(manifest["pilot_article_keys"])
    except (KeyError, TypeError) as error:
        raise ValueError("local shard manifest is malformed") from error
    if not isinstance(shards, list) or not all(
        isinstance(item, Mapping) for item in shards
    ):
        raise ValueError("local shard manifest has invalid shards")
    verified_paths = METADATA_PATHS
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
            remote_manifest_document = _manifest_document(
                remote_manifest, label="remote"
            )
            if remote_manifest != local_bytes:
                raise RemoteVerificationError("remote manifest bytes do not match local manifest")
            remote_readiness = _remote_bytes(
                api, repo_id, READINESS_PATH, revision, token
            )
            remote_readme = _remote_bytes(
                api, repo_id, README_PATH, revision, token
            )
            remote_empty_test = _remote_bytes(
                api, repo_id, EMPTY_TEST_PATH, revision, token
            )
            if remote_readiness != local_readiness_bytes:
                raise RemoteVerificationError(
                    "remote readiness bytes do not match local readiness"
                )
            try:
                readiness_document = json.loads(remote_readiness)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RemoteVerificationError("remote readiness is malformed") from error
            if not isinstance(readiness_document, dict):
                raise RemoteVerificationError("remote readiness is malformed")
            validate_readiness_alignment(
                readiness_document, remote_manifest_document
            )
            if remote_readme != local_readme_bytes:
                raise RemoteVerificationError(
                    "remote README bytes do not match the fixed dataset card"
                )
            if remote_empty_test != local_empty_test_bytes:
                raise RemoteVerificationError(
                    "remote empty test sentinel bytes do not match local bytes"
                )
            empty_test = pq.ParquetFile(pa.BufferReader(remote_empty_test))
            if (
                not empty_test.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True)
                or empty_test.metadata.num_rows != 0
            ):
                raise RemoteVerificationError(
                    "remote empty test sentinel violates the zero-row schema"
                )
            verified_paths = METADATA_PATHS | _verify_remote_shards(
                api, repo_id, revision, token, shards
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
    _validate_local_uploads(uploads, config_name, allow_legacy_card=True)
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
    for publication_attempt in range(retries):
        with tempfile.TemporaryDirectory(prefix="babel-upload-snapshot-") as temporary:
            attempt_uploads = _snapshot_uploads(uploads, Path(temporary))
            attempt_by_path = dict(attempt_uploads)
            snapshot_manifest = _manifest_document(
                attempt_by_path[manifest_remote_path].read_bytes(), label="local"
            )
            attempt_by_path[README_PATH].write_bytes(
                merge_dataset_card(
                    attempt_by_path[README_PATH].read_bytes(),
                    snapshot_manifest.get("active_release_root"),  # type: ignore[arg-type]
                )
            )
            _validate_local_uploads(attempt_uploads, config_name)
            by_remote_path = dict(attempt_uploads)
            local_manifest_bytes = by_remote_path[manifest_remote_path].read_bytes()
            local_manifest = _manifest_document(local_manifest_bytes, label="local")
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
            remote_manifest_bytes = _remote_or_missing(
                api,
                repo_id,
                manifest_remote_path,
                parent_sha,
                token,
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
            )
            remote_manifest = (
                _manifest_document(remote_manifest_bytes, label="remote")
                if remote_manifest_bytes is not None
                else None
            )
            if (
                remote_manifest_bytes is not None
                and remote_manifest_bytes != local_manifest_bytes
            ):
                _validate_manifest_extension(
                    remote_manifest_bytes,
                    local_manifest_bytes,
                    expected_predecessor_sha=parent_sha,
                )
            remote_readme = _remote_or_missing(
                api,
                repo_id,
                README_PATH,
                parent_sha,
                token,
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
            )
            if remote_readme is not None:
                by_remote_path[README_PATH].write_bytes(
                    merge_dataset_card(
                        remote_readme,
                        local_manifest.get("active_release_root"),  # type: ignore[arg-type]
                    )
                )
                _validate_local_uploads(attempt_uploads, config_name)
            pending: list[tuple[str, Path]] = []
            for path_in_repo, local in attempt_uploads:
                existing = (
                    remote_manifest_bytes
                    if path_in_repo == manifest_remote_path
                    else remote_readme
                    if path_in_repo == README_PATH
                    else _remote_or_missing(
                        api,
                        repo_id,
                        path_in_repo,
                        parent_sha,
                        token,
                        retries=retries,
                        backoff_seconds=backoff_seconds,
                        sleep=sleep,
                    )
                )
                if existing is None:
                    pending.append((path_in_repo, local))
                    continue
                local_bytes = local.read_bytes()
                if existing != local_bytes:
                    if path_in_repo == manifest_remote_path:
                        pending.append((path_in_repo, local))
                        continue
                    if path_in_repo == READINESS_PATH:
                        if remote_manifest is None:
                            raise ValueError(
                                "refusing readiness update without an existing manifest"
                            )
                        validate_readiness_extension(
                            existing,
                            local_bytes,
                            remote_manifest,
                            local_manifest,
                        )
                        pending.append((path_in_repo, local))
                        continue
                    if (
                        path_in_repo == README_PATH
                        and local_manifest.get("supersedes_commit_sha") == parent_sha
                        and remote_manifest is not None
                        and remote_manifest.get("active_release_root") is None
                    ):
                        pending.append((path_in_repo, local))
                        continue
                    raise ValueError(
                        "refusing to overwrite remote path with a different checksum: "
                        f"{path_in_repo}"
                    )

            returned_sha = parent_sha
            if pending:
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
                        f"after {publication_attempt + 1} attempt(s) "
                        f"({type(error).__name__})"
                    ) from None

            published_info = _private_info(
                _retry(
                    lambda: api.dataset_info(
                        repo_id, revision=returned_sha, token=token
                    ),
                    retries=retries,
                    backoff_seconds=backoff_seconds,
                    sleep=sleep,
                    label="published revision resolution",
                )
            )
            info_sha = _checked_sha(
                getattr(published_info, "sha", None), label="dataset info SHA"
            )
            if info_sha != returned_sha:
                raise RemoteVerificationError(
                    "returned commit SHA does not match dataset info SHA"
                )
            verify_remote(
                api,
                repo_id,
                returned_sha,
                by_remote_path[manifest_remote_path],
                token,
                config_name=config_name,
                load_dataset_fn=load_dataset_fn,
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
            )
            return returned_sha
    raise RemoteVerificationError("atomic Hub commit retries were exhausted")


def _interview_local_uploads(
    root: Path, expected_counts: Mapping[str, int]
) -> tuple[list[tuple[str, Path]], dict[str, object]]:
    config_root = root / INTERVIEW_CONFIG
    manifest_path = config_root / "manifest.json"
    readiness_path = config_root / "readiness.json"
    try:
        manifest = json.loads(manifest_path.read_bytes())
        readiness = json.loads(readiness_path.read_bytes())
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("interview manifest/readiness is missing or malformed") from error
    if not isinstance(manifest, dict) or not isinstance(readiness, dict):
        raise ValueError("interview manifest/readiness must be JSON objects")
    counts = {split: int(expected_counts[split]) for split in ("train", "validation", "test")}
    expected_total = sum(counts.values())
    if manifest.get("dataset_config") != INTERVIEW_CONFIG:
        raise ValueError("interview manifest has the wrong configuration")
    if manifest.get("schema") != "distillation-example-v1":
        raise ValueError("interview manifest has the wrong example schema")
    if manifest.get("counts") != {"total": expected_total, **counts}:
        raise ValueError("interview manifest counts are not exact")
    selection = manifest.get("selection")
    if not isinstance(selection, dict) or selection.get("seed") != INTERVIEW_SEED:
        raise ValueError("interview manifest selection seed is not exact")
    ordered = selection.get("ordered_identities")
    ordered_checksums = selection.get("ordered_identity_sha256")
    if not isinstance(ordered, dict) or not isinstance(ordered_checksums, dict):
        raise ValueError("interview manifest ordered selection is missing")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != 3:
        raise ValueError("interview manifest must contain one shard per split")
    uploads: list[tuple[str, Path]] = [
        (f"{INTERVIEW_CONFIG}/manifest.json", manifest_path),
        (f"{INTERVIEW_CONFIG}/readiness.json", readiness_path),
    ]
    seen_keys: set[str] = set()
    for split in ("train", "validation", "test"):
        matching = [item for item in shards if isinstance(item, dict) and item.get("split") == split]
        if len(matching) != 1:
            raise ValueError(f"interview manifest must contain one {split} shard")
        item = matching[0]
        remote_path = str(item.get("path"))
        if not remote_path.startswith(f"{INTERVIEW_CONFIG}/{split}/") or not remote_path.endswith(".parquet"):
            raise ValueError(f"interview {split} shard path is not config-local")
        local = root / remote_path
        if not local.is_file():
            raise ValueError(f"interview shard is missing: {remote_path}")
        if local.stat().st_size != int(item.get("bytes", -1)) or _file_sha256(local) != item.get("sha256"):
            raise ValueError(f"interview shard checksum disagrees: {remote_path}")
        parquet = pq.ParquetFile(local)
        if not parquet.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True):
            raise ValueError(f"interview shard physical schema disagrees: {remote_path}")
        identities: list[dict[str, object]] = []
        prior: tuple[str, str] | None = None
        for batch in parquet.iter_batches(batch_size=4096):
            for raw in batch.to_pylist():
                row = validate_distillation_row(raw)
                if row["split"] != split:
                    raise ValueError(f"interview {split} shard contains split drift")
                key = str(row["article_key"])
                rank = _rank_identity(key)
                current = (rank, key)
                if prior is not None and current <= prior:
                    raise ValueError(f"interview {split} rows are not rank ordered")
                if key in seen_keys:
                    raise ValueError("interview shards contain duplicate article_key")
                prior = current
                seen_keys.add(key)
                identities.append(
                    {"rank_sha256": rank, "article_key": key, "page_id": int(row["page_id"])}
                )
        if len(identities) != counts[split] or int(item.get("rows", -1)) != counts[split]:
            raise ValueError(f"interview {split} row count is not exact")
        digest = identity_rows_sha256(identities)
        if digest != item.get("rows_sha256") or digest != ordered_checksums.get(split):
            raise ValueError(f"interview {split} ordered checksum disagrees")
        if identities != ordered.get(split):
            raise ValueError(f"interview {split} identities disagree with manifest order")
        uploads.append((remote_path, local))
    aggregate = hashlib.sha256(canonical_json(shards)).hexdigest()
    if manifest.get("aggregate_sha256") != aggregate:
        raise ValueError("interview manifest aggregate checksum disagrees")
    if readiness.get("dataset_config") != INTERVIEW_CONFIG or readiness.get("counts") != {"total": expected_total, **counts}:
        raise ValueError("interview readiness counts/configuration disagree")
    if readiness.get("manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
        raise ValueError("interview readiness manifest checksum disagrees")
    return sorted(uploads), manifest


def publish_interview_configuration(
    api: object,
    repo_id: str,
    input_root: str | os.PathLike[str],
    token: str,
    *,
    load_dataset_fn: Callable[..., object] | None = None,
    expected_counts: Mapping[str, int] = INTERVIEW_COUNTS,
    retries: int = 4,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Atomically append and remotely prove the frozen interview configuration."""
    if not token:
        raise ValueError("a private-Hub token is required")
    if retries <= 0:
        raise ValueError("retries must be positive")
    root = Path(input_root).resolve(strict=True)
    uploads, _ = _interview_local_uploads(root, expected_counts)
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
        label="private interview dataset repository creation",
    )
    loader = load_dataset_fn or _dataset_loader()
    for publication_attempt in range(retries):
        info = _private_info(
            _retry(
                lambda: api.dataset_info(repo_id, revision="main", token=token),
                retries=retries,
                backoff_seconds=backoff_seconds,
                sleep=sleep,
                label="interview main revision resolution",
            )
        )
        parent = _checked_sha(getattr(info, "sha", None), label="main dataset info SHA")
        complete_manifest_path = f"{DEFAULT_CONFIG}/manifest.json"
        complete_manifest = _remote_bytes(api, repo_id, complete_manifest_path, parent, token)
        try:
            complete_document = json.loads(complete_manifest)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteVerificationError("existing complete manifest is malformed") from error
        if not isinstance(complete_document, dict):
            raise RemoteVerificationError("existing complete manifest is malformed")
        active_root = complete_document.get("active_release_root")
        if active_root is not None and not isinstance(active_root, str):
            raise RemoteVerificationError("existing complete manifest release root is malformed")
        remote_card = _remote_or_missing(
            api,
            repo_id,
            README_PATH,
            parent,
            token,
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
        expected_card = (
            render_dataset_card(active_root)
            if remote_card is None
            else merge_dataset_card(remote_card, active_root)
        )
        with tempfile.TemporaryDirectory(prefix="babel-interview-upload-") as temporary:
            snapshot_root = Path(temporary)
            snapshot_uploads = _snapshot_uploads(uploads, snapshot_root)
            card_path = snapshot_root / README_PATH
            card_path.write_bytes(expected_card)
            snapshot_uploads.append((README_PATH, card_path))
            _interview_local_uploads(snapshot_root, expected_counts)
            pending: list[tuple[str, Path]] = []
            for remote_path, local in snapshot_uploads:
                existing = remote_card if remote_path == README_PATH else _remote_or_missing(
                    api,
                    repo_id,
                    remote_path,
                    parent,
                    token,
                    retries=retries,
                    backoff_seconds=backoff_seconds,
                    sleep=sleep,
                )
                local_bytes = local.read_bytes()
                if existing is None:
                    pending.append((remote_path, local))
                elif existing != local_bytes:
                    if remote_path == README_PATH:
                        pending.append((remote_path, local))
                    else:
                        raise ValueError(
                            "refusing to overwrite nonidentical interview path: " + remote_path
                        )
            returned = parent
            if pending:
                try:
                    result = api.create_commit(
                        repo_id=repo_id,
                        repo_type="dataset",
                        revision="main",
                        parent_commit=parent,
                        operations=[_add_operation(api, path, local) for path, local in pending],
                        commit_message="Publish frozen 2016 interview configuration",
                        token=token,
                    )
                    returned = _commit_sha(result)
                except BaseException as error:
                    if (_is_parent_conflict(error) or _is_transient(error)) and publication_attempt + 1 < retries:
                        sleep(backoff_seconds * (2**publication_attempt))
                        continue
                    raise RemoteVerificationError(
                        f"atomic interview commit failed ({type(error).__name__})"
                    ) from None
            published = _private_info(api.dataset_info(repo_id, revision=returned, token=token))
            if _checked_sha(getattr(published, "sha", None)) != returned:
                raise RemoteVerificationError("interview commit identity disagrees")
            if _remote_bytes(api, repo_id, complete_manifest_path, returned, token) != complete_manifest:
                raise RemoteVerificationError("existing complete manifest changed during interview publication")
            for remote_path, local in snapshot_uploads:
                if _remote_bytes(api, repo_id, remote_path, returned, token) != local.read_bytes():
                    raise RemoteVerificationError(f"remote interview bytes disagree: {remote_path}")
            for split in ("train", "validation", "test"):
                streamed = loader(
                    repo_id,
                    name=INTERVIEW_CONFIG,
                    split=split,
                    revision=returned,
                    streaming=True,
                    token=token,
                )
                iterator = iter(streamed)
                try:
                    checked = validate_distillation_row(next(iterator))
                except StopIteration as error:
                    raise RemoteVerificationError(f"remote interview {split} split is empty") from error
                finally:
                    close_iterator = getattr(iterator, "close", None)
                    if callable(close_iterator):
                        close_iterator()
                    if streamed is not iterator:
                        close_stream = getattr(streamed, "close", None)
                        if callable(close_stream):
                            close_stream()
                if checked["split"] != split:
                    raise RemoteVerificationError(f"remote interview {split} split drifted")
                del checked, iterator, streamed
                gc.collect()
            return returned
    raise RemoteVerificationError("atomic interview publication retries were exhausted")


def stage_versioned_release_shards(
    api: object,
    repo_id: str,
    manifest_path: str | os.PathLike[str],
    token: str,
    *,
    load_dataset_fn: Callable[..., object] | None = None,
) -> tuple[str, ...]:
    """Append and prove one inactive versioned shard per main commit."""
    if not token:
        raise ValueError("a private-Hub token is required")
    local_manifest_path = Path(manifest_path)
    manifest = _manifest_document(local_manifest_path.read_bytes(), label="local")
    active_root = manifest.get("active_release_root")
    predecessor = manifest.get("supersedes_commit_sha")
    if not isinstance(active_root, str) or not isinstance(predecessor, str):
        raise ValueError("rolling staging requires a versioned superseding release")
    artifact_root = local_manifest_path.parent.parent
    journal_path = artifact_root / "publication-commits.jsonl"
    journal: dict[str, dict[str, object]] = {}
    if journal_path.exists():
        with journal_path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError("rolling publication journal is malformed") from error
                if not isinstance(entry, dict) or set(entry) != {
                    "path", "sha256", "commit_sha", "remote_stream_verified"
                }:
                    raise ValueError("rolling publication journal has invalid fields")
                path = str(entry["path"])
                if path in journal or entry["remote_stream_verified"] is not True:
                    raise ValueError(
                        f"rolling publication journal entry {line_number} is invalid"
                    )
                _checked_sha(entry["commit_sha"], label="journal commit SHA")
                journal[path] = entry
    loader = load_dataset_fn or _dataset_loader()
    info = _private_info(
        api.dataset_info(repo_id, revision="main", token=token)
    )
    parent = _checked_sha(getattr(info, "sha", None), label="main dataset info SHA")
    if parent != predecessor:
        predecessor_manifest = _remote_bytes(
            api,
            repo_id,
            f"{DEFAULT_CONFIG}/manifest.json",
            predecessor,
            token,
        )
        current_manifest = _remote_bytes(
            api,
            repo_id,
            f"{DEFAULT_CONFIG}/manifest.json",
            parent,
            token,
        )
        if predecessor_manifest != current_manifest:
            raise ValueError("rolling staging predecessor does not match remote main")
        current_document = _manifest_document(current_manifest, label="remote")
        if current_document.get("active_release_root") is not None:
            raise ValueError("rolling staging cannot replace an active release")
    commits: list[str] = []
    for item in manifest["shards"]:
        path = str(item["path"])
        if not path.startswith(f"{active_root}/{item['split']}/"):
            raise ValueError("rolling shard is outside the active release root")
        local = artifact_root / path
        existing = _remote_or_missing(
            api,
            repo_id,
            path,
            parent,
            token,
            retries=4,
            backoff_seconds=0.5,
            sleep=time.sleep,
        )
        recorded = journal.get(path)
        if recorded is not None and recorded.get("sha256") != item["sha256"]:
            raise ValueError("rolling publication journal shard checksum disagrees")
        if existing is not None:
            if existing != local.read_bytes():
                raise ValueError("inactive release shard path already has different bytes")
            commit_sha = (
                str(recorded["commit_sha"]) if recorded is not None else parent
            )
        else:
            result = api.create_commit(
                repo_id=repo_id,
                repo_type="dataset",
                revision="main",
                parent_commit=parent,
                operations=[_add_operation(api, path, local)],
                commit_message=f"Stage complete 2016 shard {Path(path).name}",
                token=token,
            )
            commit_sha = _commit_sha(result)
            published = _private_info(
                api.dataset_info(repo_id, revision=commit_sha, token=token)
            )
            if _checked_sha(getattr(published, "sha", None)) != commit_sha:
                raise RemoteVerificationError("staged shard commit identity disagrees")
        if path not in _verify_remote_shards(
            api, repo_id, commit_sha, token, [item]
        ):
            raise RemoteVerificationError("staged shard remote bytes disagree")
        streamed = loader(
            repo_id,
            name=DEFAULT_CONFIG,
            data_files={str(item["split"]): path},
            split=str(item["split"]),
            revision=commit_sha,
            streaming=True,
            token=token,
        )
        try:
            row = next(iter(streamed))
        except StopIteration as error:
            raise RemoteVerificationError("staged shard remote stream is empty") from error
        checked = validate_distillation_row(row)
        if checked["split"] != item["split"]:
            raise RemoteVerificationError("staged shard remote stream has wrong split")
        if recorded is None:
            entry = {
                "path": path,
                "sha256": item["sha256"],
                "commit_sha": commit_sha,
                "remote_stream_verified": True,
            }
            payload = json.dumps(
                entry, sort_keys=True, separators=(",", ":")
            ).encode("utf-8") + b"\n"
            with journal_path.open("ab") as destination:
                destination.write(payload)
                destination.flush()
                os.fsync(destination.fileno())
            journal[path] = entry
        commits.append(commit_sha)
        if existing is None:
            parent = commit_sha
    return tuple(commits)


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
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            except BaseException as rollback_error:
                raise RemoteVerificationError(
                    "revision rollback directory fsync failed"
                ) from rollback_error
            finally:
                os.close(directory)
        raise
    finally:
        removed = False
        try:
            os.unlink(temporary_name)
            removed = True
        except FileNotFoundError:
            pass
        if removed:
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
