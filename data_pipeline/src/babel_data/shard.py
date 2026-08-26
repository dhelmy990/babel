"""Deterministic Parquet materialization for the 2016 distillation dataset.

Production callers should keep ``target_shard_bytes`` in the 256--512 MiB
range.  The boundary is based on canonical row bytes, making it stable across
machines; fixture tests intentionally use much smaller values.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import heapq
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import zlib
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import validate_document
from .reconcile import split_for
from .release import (
    EMPTY_TEST_PATH,
    METADATA_PATHS,
    README_PATH,
    READINESS_PATH,
    identity_rows_sha256,
    render_dataset_card,
    validate_manifest_document,
    validate_manifest_bytes,
    validate_readiness_alignment,
)


DEFAULT_PILOT_SIZE = 10_000
DEFAULT_TARGET_SHARD_BYTES = 384 * 1024 * 1024
DATASET_CONFIG = "distillation_2016"
MANIFEST_VERSION = 1

PARQUET_SCHEMA = pa.schema(
    [
        pa.field("article_key", pa.string(), nullable=False),
        pa.field("page_id", pa.int64(), nullable=False),
        pa.field("canonical_title", pa.string(), nullable=False),
        pa.field("wikidata_id", pa.string(), nullable=True),
        pa.field("lead_text", pa.string(), nullable=False),
        pa.field("article_text", pa.string(), nullable=False),
        pa.field(
            "teacher_vector",
            pa.list_(pa.field("element", pa.float32()), 100),
            nullable=False,
        ),
        pa.field("teacher_norm", pa.float64(), nullable=False),
        pa.field("source_revision_id", pa.int64(), nullable=True),
        pa.field("snapshot_date", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("reconciliation_status", pa.string(), nullable=False),
    ],
    metadata={
        b"babel_schema": b"distillation-example-v1",
        b"babel_manifest_version": b"1",
    },
)


@dataclass(frozen=True, slots=True)
class ShardInfo:
    path: str
    split: str
    rows: int
    bytes: int
    sha256: str
    rows_sha256: str
    schema: str
    version: int
    min_article_key: str
    max_article_key: str
    min_rank: str
    max_rank: str

    def to_document(self) -> dict[str, object]:
        return {
            "path": self.path,
            "split": self.split,
            "rows": self.rows,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "rows_sha256": self.rows_sha256,
            "schema": self.schema,
            "version": self.version,
            "min_article_key": self.min_article_key,
            "max_article_key": self.max_article_key,
            "min_rank": self.min_rank,
            "max_rank": self.max_rank,
        }


@dataclass(frozen=True, slots=True)
class ShardResult:
    output_root: Path
    manifest_path: Path
    readiness_path: Path
    readme_path: Path
    shards: tuple[ShardInfo, ...]
    pilot_article_keys: tuple[str, ...]
    row_count: int
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _PilotCandidate:
    rank: str
    article_key: str
    document: dict[str, object]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _PilotCandidate):
            return NotImplemented
        return (self.rank, self.article_key) > (other.rank, other.article_key)


_STATE_ORDER = {"building": 0, "pilot_ready": 1, "complete": 2}


@dataclass(slots=True)
class Readiness:
    """Validated readiness state plus non-serialized local deletion evidence."""

    state: str
    available_examples: int
    verified_shards: list[dict[str, object]]
    source_checksums: dict[str, str]
    remote_verified: bool = False
    remote_commit_sha: str | None = None
    supersedes_commit_sha: str | None = None
    active_release_root: str | None = None
    _manifest_sha256: str | None = None
    _verified_manifest_sha256: str | None = None
    _local_paths: frozenset[str] = frozenset()
    _verified_paths: frozenset[str] = frozenset()
    _manifest_path: Path | None = None
    _artifact_root: Path | None = None
    _evidence_path: Path | None = None
    _evidence_durable: bool = False

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "state": self.state,
            "schema_version": 1,
            "teacher_dimension": 100,
            "available_examples": self.available_examples,
            "verified_shards": [dict(item) for item in self.verified_shards],
            "source_checksums": dict(self.source_checksums),
            "remote_verified": self.remote_verified,
            "remote_commit_sha": self.remote_commit_sha,
        }
        if self.supersedes_commit_sha is not None:
            document["supersedes_commit_sha"] = self.supersedes_commit_sha
            document["active_release_root"] = self.active_release_root
        validate_document("dataset-readiness-v1", document)
        return document

    @property
    def can_delete_local(self) -> bool:
        if not (
            self.remote_verified
            and self.remote_commit_sha
            and self._manifest_sha256
            and self._verified_manifest_sha256 == self._manifest_sha256
            and self._verified_paths == self._local_paths
            and self._manifest_path is not None
            and self._artifact_root is not None
            and self._evidence_path is not None
            and self._evidence_durable
        ):
            return False
        try:
            evidence = json.loads(self._evidence_path.read_text(encoding="utf-8"))
            if evidence != {
                "commit_sha": self.remote_commit_sha,
                "manifest_sha256": self._verified_manifest_sha256,
                "readiness_sha256": _sha256(
                    Path(
                        str(self._evidence_path).removesuffix(
                            ".remote-verification.json"
                        )
                    )
                ),
                "verified_paths": sorted(self._verified_paths),
            }:
                return False
            manifest_bytes = self._manifest_path.read_bytes()
            if (
                hashlib.sha256(manifest_bytes).hexdigest()
                != self._verified_manifest_sha256
            ):
                return False
            manifest = validate_manifest_bytes(manifest_bytes, label="local")
            manifest_shards = manifest["shards"]
            if not isinstance(manifest_shards, list):
                return False
            manifest_identities = {
                str(item["path"]): (str(item["sha256"]), int(item["rows"]))
                for item in manifest_shards
            }
            readiness_identities = {
                str(item["path"]): (str(item["sha256"]), int(item["examples"]))
                for item in self.verified_shards
            }
            if (
                len(manifest_identities) != len(manifest_shards)
                or manifest_identities != readiness_identities
                or frozenset(manifest_identities)
                != self._local_paths - METADATA_PATHS
            ):
                return False
            artifact_root = self._artifact_root.resolve()
            if (artifact_root / README_PATH).read_bytes() != render_dataset_card(
                self.active_release_root
            ):
                return False
            for shard_path, (checksum, _) in manifest_identities.items():
                local = (artifact_root / shard_path).resolve(strict=True)
                local.relative_to(artifact_root)
                if _sha256(local) != checksum:
                    return False
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return False
        return True

    def mark_remote_verified(
        self,
        commit_sha: str,
        *,
        manifest_sha256: str | None = None,
        verified_paths: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(commit_sha, str) or len(commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in commit_sha
        ):
            raise ValueError(
                "commit_sha must be a lowercase 40-character hexadecimal SHA"
            )
        verified_manifest = manifest_sha256 or self._manifest_sha256
        if verified_manifest != self._manifest_sha256:
            raise ValueError("remote manifest checksum does not match local manifest")
        paths = (
            frozenset(verified_paths)
            if verified_paths is not None
            else self._local_paths
        )
        if paths != self._local_paths:
            raise ValueError("remote verification does not cover every local artifact")
        if (
            self.remote_commit_sha is not None
            and self.remote_commit_sha != commit_sha
            and self._verified_manifest_sha256 == self._manifest_sha256
        ):
            raise ValueError("remote commit identity is immutable once verified")
        self.remote_verified = True
        self.remote_commit_sha = commit_sha
        self._verified_manifest_sha256 = verified_manifest
        self._verified_paths = paths
        self._evidence_durable = False

    def stage_publication(self, state: str) -> None:
        """Prepare the exact non-self-referential readiness bytes to upload."""
        if state not in _STATE_ORDER:
            raise ValueError(f"unknown readiness state: {state!r}")
        if _STATE_ORDER[state] < _STATE_ORDER[self.state]:
            raise ValueError(
                f"readiness state cannot regress from {self.state} to {state}"
            )
        self.state = state
        self.remote_verified = False
        self.remote_commit_sha = None
        self._verified_paths = frozenset()
        self._evidence_durable = False
        self.to_document()

    def save_verification_evidence(
        self,
        path: str | os.PathLike[str],
        *,
        readiness_sha256: str | None = None,
    ) -> None:
        """Persist the verified commit separately from uploaded readiness bytes."""
        if not (
            self.remote_verified
            and self.remote_commit_sha
            and self._verified_manifest_sha256 == self._manifest_sha256
            and self._verified_paths == self._local_paths
        ):
            raise ValueError("remote readiness requires exact verification evidence")
        evidence_path = _verification_evidence_path(Path(path))
        _atomic_write_json(
            evidence_path,
            {
                "commit_sha": self.remote_commit_sha,
                "manifest_sha256": self._verified_manifest_sha256,
                "readiness_sha256": readiness_sha256 or _sha256(Path(path)),
                "verified_paths": sorted(self._verified_paths),
            },
        )
        self._evidence_path = evidence_path
        self._evidence_durable = True

    def transition(self, state: str) -> None:
        if state not in _STATE_ORDER:
            raise ValueError(f"unknown readiness state: {state!r}")
        current = _STATE_ORDER[self.state]
        requested = _STATE_ORDER[state]
        if requested < current:
            raise ValueError(f"readiness state cannot regress from {self.state} to {state}")
        if requested > current + 1:
            raise ValueError(
                "readiness transitions must follow building -> pilot_ready -> complete"
            )
        if requested > current and not self.can_delete_local:
            raise ValueError("remote verification is required before readiness can advance")
        self.state = state
        self.to_document()

    def save(self, path: str | os.PathLike[str]) -> None:
        """Atomically persist state while defending published shard identities."""
        destination = Path(path)
        if destination.exists():
            prior = json.loads(destination.read_text(encoding="utf-8"))
            validate_document("dataset-readiness-v1", prior)
            if _STATE_ORDER[str(prior["state"])] > _STATE_ORDER[self.state]:
                raise ValueError("persisted readiness state cannot regress")
            if prior["source_checksums"] != self.source_checksums:
                raise ValueError("persisted source checksum identity is immutable")
            if (
                prior["remote_verified"]
                and not self.remote_verified
                and self._verified_manifest_sha256 == self._manifest_sha256
            ):
                raise ValueError("persisted remote verification cannot regress")
            if (
                prior["remote_commit_sha"] is not None
                and prior["remote_commit_sha"] != self.remote_commit_sha
                and self.remote_verified
            ):
                raise ValueError("persisted remote commit identity is immutable")
            old = {item["path"]: item["sha256"] for item in prior["verified_shards"]}
            new = {item["path"]: item["sha256"] for item in self.verified_shards}
            for shard_path, checksum in old.items():
                if shard_path not in new or new[shard_path] != checksum:
                    raise ValueError(f"published shard identity is immutable: {shard_path}")
        document = self.to_document()
        if self.remote_verified:
            self.save_verification_evidence(
                destination,
                readiness_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
            )
        else:
            self._evidence_path = None
            self._evidence_durable = False
        _atomic_write_json(destination, document)


def build_readiness(
    shards: ShardResult | Iterable[ShardInfo],
    source_checksums: Mapping[str, str],
    *,
    state: str = "building",
    path: str | os.PathLike[str] | None = None,
) -> Readiness:
    """Build a readiness document whose shard identities cannot be rewritten."""
    if state != "building":
        raise ValueError("new readiness state must begin at building")
    if not isinstance(source_checksums, Mapping):
        raise TypeError("source_checksums must be a mapping")
    if not source_checksums:
        raise ValueError("at least one source checksum is required")
    checked_sources: dict[str, str] = {}
    for name, checksum in source_checksums.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise ValueError(
                "source checksums require nonempty names and lowercase SHA-256 values"
            )
        checked_sources[name] = checksum

    if isinstance(shards, ShardResult):
        infos = shards.shards
        manifest_sha256 = shards.manifest_sha256
        manifest_document = validate_manifest_bytes(
            shards.manifest_path.read_bytes(), label="local"
        )
    else:
        infos = tuple(shards)
        manifest_sha256 = None
        manifest_document = {}
    verified = [
        {"path": item.path, "sha256": item.sha256, "examples": item.rows}
        for item in infos
    ]
    readiness = Readiness(
        state="building",
        available_examples=sum(item.rows for item in infos),
        verified_shards=verified,
        source_checksums=checked_sources,
        supersedes_commit_sha=manifest_document.get("supersedes_commit_sha"),  # type: ignore[arg-type]
        active_release_root=manifest_document.get("active_release_root"),  # type: ignore[arg-type]
        _manifest_sha256=manifest_sha256,
        _local_paths=frozenset(item.path for item in infos) | METADATA_PATHS,
        _manifest_path=shards.manifest_path if isinstance(shards, ShardResult) else None,
        _artifact_root=shards.output_root if isinstance(shards, ShardResult) else None,
    )
    readiness.to_document()
    if path is not None:
        readiness.save(path)
    return readiness


def load_readiness(
    path: str | os.PathLike[str], manifest_path: str | os.PathLike[str]
) -> Readiness:
    """Restore readiness while re-binding it to the exact local manifest."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_document("dataset-readiness-v1", document)
    local_manifest_path = Path(manifest_path)
    manifest_bytes = local_manifest_path.read_bytes()
    manifest = validate_manifest_bytes(manifest_bytes, label="local")
    validate_readiness_alignment(document, manifest)
    manifest_identities = {
        str(item["path"]): (str(item["sha256"]), int(item["rows"]))
        for item in manifest["shards"]
    }
    readiness_identities = {
        str(item["path"]): (str(item["sha256"]), int(item["examples"]))
        for item in document["verified_shards"]
    }
    if readiness_identities != manifest_identities:
        raise ValueError("readiness shard identities do not match the local manifest")
    documented_remote_verified = bool(document["remote_verified"])
    remote_verified = documented_remote_verified
    remote_commit_sha = document["remote_commit_sha"]
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    local_paths = frozenset(manifest_identities) | METADATA_PATHS
    verified_manifest_sha: str | None = None
    verified_paths: frozenset[str] = frozenset()
    evidence_path = _verification_evidence_path(Path(path))
    if evidence_path.exists():
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise ValueError("persisted readiness lacks remote verification evidence") from error
        if set(evidence) != {
            "commit_sha",
            "manifest_sha256",
            "readiness_sha256",
            "verified_paths",
        }:
            raise ValueError("remote verification evidence has unexpected fields")
        evidence_commit = evidence["commit_sha"]
        if (
            not isinstance(evidence_commit, str)
            or len(evidence_commit) != 40
            or any(character not in "0123456789abcdef" for character in evidence_commit)
        ):
            raise ValueError("remote verification commit SHA is invalid")
        if documented_remote_verified and evidence["commit_sha"] != remote_commit_sha:
            raise ValueError("remote verification commit does not match readiness")
        verified_manifest_sha = evidence["manifest_sha256"]
        if (
            not isinstance(verified_manifest_sha, str)
            or len(verified_manifest_sha) != 64
            or any(
                character not in "0123456789abcdef"
                for character in verified_manifest_sha
            )
        ):
            raise ValueError("remote verification manifest SHA is invalid")
        if not isinstance(evidence["verified_paths"], list) or not all(
            isinstance(item, str) for item in evidence["verified_paths"]
        ):
            raise ValueError("remote verification path evidence is invalid")
        evidence_paths = frozenset(evidence["verified_paths"])
        readiness_sha = _sha256(Path(path))
        if (
            verified_manifest_sha == manifest_sha
            and evidence["readiness_sha256"] == readiness_sha
            and evidence_paths == local_paths
        ):
            verified_paths = evidence_paths
            remote_verified = True
            remote_commit_sha = evidence["commit_sha"]
        else:
            remote_verified = documented_remote_verified
            remote_commit_sha = document["remote_commit_sha"]
            verified_paths = evidence_paths if documented_remote_verified else frozenset()
    elif documented_remote_verified:
        raise ValueError("persisted readiness lacks remote verification evidence")
    return Readiness(
        state=str(document["state"]),
        available_examples=int(document["available_examples"]),
        verified_shards=[dict(item) for item in document["verified_shards"]],
        source_checksums=dict(document["source_checksums"]),
        remote_verified=remote_verified,
        remote_commit_sha=remote_commit_sha,
        supersedes_commit_sha=document.get("supersedes_commit_sha"),
        active_release_root=document.get("active_release_root"),
        _manifest_sha256=manifest_sha,
        _verified_manifest_sha256=verified_manifest_sha,
        _local_paths=local_paths,
        _verified_paths=verified_paths,
        _manifest_path=local_manifest_path,
        _artifact_root=local_manifest_path.parent.parent,
        _evidence_path=(
            evidence_path if remote_verified else None
        ),
        _evidence_durable=remote_verified,
    )


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _verification_evidence_path(readiness_path: Path) -> Path:
    return readiness_path.with_name(readiness_path.name + ".remote-verification.json")


def _atomic_write_json(destination: Path, value: object) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(_canonical_json(value))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_staging_directories(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    directories.sort(key=lambda path: len(path.relative_to(root).parts), reverse=True)
    for directory in directories:
        _fsync_path(directory)
    _fsync_path(root)


def validate_distillation_row(value: object) -> dict[str, object]:
    """Return a normalized row after all schema and semantic checks."""
    if isinstance(value, Mapping):
        document = dict(value)
    else:
        converter = getattr(value, "to_document", None)
        if converter is None or not callable(converter):
            raise TypeError("rows must be mappings or provide to_document()")
        converted = converter()
        if not isinstance(converted, Mapping):
            raise TypeError("to_document() must return a mapping")
        document = dict(converted)
    validate_document("distillation-example-v1", document)
    if isinstance(document["page_id"], bool):
        raise ValueError("page_id must be an integer, not bool")
    revision = document["source_revision_id"]
    if isinstance(revision, bool):
        raise ValueError("source_revision_id must be an integer or null, not bool")
    key = str(document["article_key"])
    expected_key = f"enwiki:{document['snapshot_date']}:{document['page_id']}"
    if key != expected_key:
        raise ValueError(
            f"article_key/page identity mismatch: expected {expected_key!r}"
        )
    if document["split"] != split_for(key):
        raise ValueError(f"split mismatch for article_key {key!r}")
    # Canonicalize tuples/NumPy arrays without asking Arrow to infer types.
    vector = [float(item) for item in document["teacher_vector"]]  # type: ignore[arg-type]
    if any(abs(item) > 3.4028234663852886e38 for item in vector):
        raise ValueError("teacher_vector contains a value outside finite float32 range")
    norm = float(document["teacher_norm"])
    vector_norm = math.sqrt(math.fsum(item * item for item in vector))
    if not math.isclose(norm, vector_norm, rel_tol=1e-6, abs_tol=1e-7):
        raise ValueError("teacher_norm does not match teacher_vector L2 norm")
    document["teacher_vector"] = vector
    document["teacher_norm"] = norm
    return document


def _cooperative_rename_noreplace(source: Path, destination: Path) -> None:
    """Publish on filesystems that reject ``RENAME_NOREPLACE`` flags.

    The parent-directory lock serializes babel writers on filesystems such as
    eCryptfs.  The destination check preserves no-clobber behavior for those
    cooperating writers before the ordinary atomic rename.
    """
    if source.parent != destination.parent:
        raise OSError(errno.EXDEV, "fallback rename requires one parent directory")
    parent = os.open(source.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(parent, fcntl.LOCK_EX)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), destination)
        os.rename(
            source.name,
            destination.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
    finally:
        try:
            fcntl.flock(parent, fcntl.LOCK_UN)
        finally:
            os.close(parent)


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing another entry."""
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    used_fallback = renameat2 is None
    if renameat2 is not None:
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(
                    error_number, os.strerror(error_number), destination
                )
            if error_number not in {
                errno.EINVAL,
                errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
            }:
                raise OSError(error_number, os.strerror(error_number), destination)
            used_fallback = True
    if used_fallback:
        _cooperative_rename_noreplace(source, destination)
    try:
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException as publication_error:
        # The name became visible before its durability check failed. Move it
        # back to the private staging name with the same no-clobber primitive,
        # so the caller's cleanup can fail closed without deleting a racer.
        if used_fallback:
            try:
                _cooperative_rename_noreplace(destination, source)
            except OSError as rollback_error:
                raise OSError(
                    rollback_error.errno,
                    "directory fsync failed and atomic publication rollback failed",
                    destination,
                ) from publication_error
        else:
            assert renameat2 is not None
            rollback = renameat2(
                -100,
                os.fsencode(destination),
                -100,
                os.fsencode(source),
                1,
            )
            if rollback != 0:
                rollback_error = ctypes.get_errno()
                raise OSError(
                    rollback_error,
                    "directory fsync failed and atomic publication rollback failed",
                    destination,
                ) from publication_error
        try:
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
        raise


def _chunks(
    rows: list[dict[str, object]], target_bytes: int
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_bytes = 0
    for row in rows:
        estimated = len(_canonical_json(row))
        if current and current_bytes + estimated > target_bytes:
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(row)
        current_bytes += estimated
    if current:
        chunks.append(current)
    return chunks


def _write_table(rows: list[dict[str, object]], path: Path) -> None:
    table = pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)
    pq.write_table(
        table,
        path,
        version="2.6",
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
        row_group_size=1024,
        use_compliant_nested_type=True,
    )


def write_shards(
    rows: Iterable[object],
    output_root: str | os.PathLike[str],
    pilot_size: int = DEFAULT_PILOT_SIZE,
    target_shard_bytes: int = DEFAULT_TARGET_SHARD_BYTES,
    *,
    provenance: Mapping[str, object] | None = None,
) -> ShardResult:
    """Validate rows and atomically publish deterministic pilot Parquet shards."""
    if (
        isinstance(pilot_size, bool)
        or not isinstance(pilot_size, int)
        or pilot_size <= 0
    ):
        raise ValueError("pilot_size must be a positive integer")
    if (
        isinstance(target_shard_bytes, bool)
        or not isinstance(target_shard_bytes, int)
        or target_shard_bytes <= 0
    ):
        raise ValueError("target_shard_bytes must be a positive integer")
    if provenance is None:
        raise ValueError("provenance-v1 evidence is required")
    validate_document("provenance-v1", provenance)
    checked_provenance = deepcopy(dict(provenance))

    pilot_heap: list[_PilotCandidate] = []
    accepted_rows = 0
    with tempfile.TemporaryDirectory(prefix="babel-identities-") as identity_directory:
        database_path = Path(identity_directory) / "identities.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                "CREATE TABLE identities ("
                "article_key TEXT PRIMARY KEY, page_id INTEGER UNIQUE NOT NULL)"
            )
            for value in rows:
                document = validate_distillation_row(value)
                article_key = str(document["article_key"])
                page_id = int(document["page_id"])
                try:
                    connection.execute(
                        "INSERT INTO identities(article_key, page_id) VALUES (?, ?)",
                        (article_key, page_id),
                    )
                except sqlite3.IntegrityError as error:
                    if connection.execute(
                        "SELECT 1 FROM identities WHERE article_key = ?", (article_key,)
                    ).fetchone():
                        raise ValueError(f"duplicate article_key: {article_key}") from error
                    raise ValueError(f"duplicate page identity: {page_id}") from error
                accepted_rows += 1
                rank = hashlib.sha256(article_key.encode("utf-8")).hexdigest()
                heapq.heappush(
                    pilot_heap, _PilotCandidate(rank, article_key, document)
                )
                if len(pilot_heap) > pilot_size:
                    heapq.heappop(pilot_heap)
        finally:
            connection.close()

    if pilot_size > accepted_rows:
        raise ValueError("pilot_size cannot exceed accepted unique rows")
    selected = sorted(
        ((item.rank, item.article_key, item.document) for item in pilot_heap),
        key=lambda item: (item[0], item[1]),
    )
    pilot_keys = tuple(item[1] for item in selected)

    destination = Path(output_root)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-", dir=destination.parent
        )
    )
    shard_infos: list[ShardInfo] = []
    try:
        for split in ("train", "validation", "test"):
            split_rows = [item for item in selected if item[2]["split"] == split]
            split_rows.sort(key=lambda item: (item[0], item[1]))
            chunks = _chunks([item[2] for item in split_rows], target_shard_bytes)
            for index, chunk_items in enumerate(chunks):
                relative = Path(DATASET_CONFIG) / split / f"part-{index:05d}.parquet"
                shard_path = staging / relative
                shard_path.parent.mkdir(parents=True, exist_ok=True)
                _write_table(chunk_items, shard_path)
                _fsync_path(shard_path)
                chunk_keys = [str(row["article_key"]) for row in chunk_items]
                chunk_ranks = [
                    hashlib.sha256(key.encode("utf-8")).hexdigest()
                    for key in chunk_keys
                ]
                shard_infos.append(
                    ShardInfo(
                        path=relative.as_posix(),
                        split=split,
                        rows=len(chunk_items),
                        bytes=shard_path.stat().st_size,
                        sha256=_sha256(shard_path),
                        rows_sha256=identity_rows_sha256(chunk_items),
                        schema="distillation-example-v1",
                        version=1,
                        min_article_key=min(chunk_keys),
                        max_article_key=max(chunk_keys),
                        min_rank=min(chunk_ranks),
                        max_rank=max(chunk_ranks),
                    )
                )

        shard_documents = [item.to_document() for item in shard_infos]
        counts = {
            "total": len(selected),
            **{
                split: sum(item.rows for item in shard_infos if item.split == split)
                for split in ("train", "validation", "test")
            },
        }
        aggregate_sha256 = hashlib.sha256(
            _canonical_json(shard_documents)
        ).hexdigest()
        rows_sha256 = identity_rows_sha256([item[2] for item in selected])
        reports = checked_provenance["reports"]
        assert isinstance(reports, dict)
        reports.update(
            {
                "dataset_aggregate_sha256": aggregate_sha256,
                "dataset_rows_sha256": rows_sha256,
                "dataset_counts": counts,
            }
        )
        manifest: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "schema_version": 1,
            "state": "prepared",
            "schema": "distillation-example-v1",
            "dataset_config": DATASET_CONFIG,
            "pilot_article_keys": list(pilot_keys),
            "counts": counts,
            "shards": shard_documents,
            "aggregate_sha256": aggregate_sha256,
            "rows_sha256": rows_sha256,
            "provenance": {
                "schema": "provenance-v1",
                "identifiers": {
                    "dataset_config": DATASET_CONFIG,
                    "example_schema": "distillation-example-v1",
                    "snapshot_date": "2016-10-01",
                    "teacher_dimension": 100,
                },
                "document": checked_provenance,
            },
        }
        validate_manifest_document(manifest, label="generated")
        manifest_path = staging / DATASET_CONFIG / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_bytes = _canonical_json(manifest)
        manifest_path.write_bytes(manifest_bytes)
        _fsync_path(manifest_path)
        artifact = checked_provenance.get("artifacts", {}).get("accepted_jsonl")
        if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str):
            raise ValueError("provenance accepted_jsonl artifact is required")
        readiness_document: dict[str, object] = {
            "state": "building",
            "schema_version": 1,
            "teacher_dimension": 100,
            "available_examples": len(selected),
            "verified_shards": [
                {"path": item.path, "sha256": item.sha256, "examples": item.rows}
                for item in shard_infos
            ],
            "source_checksums": {"accepted_jsonl": artifact["sha256"]},
            "remote_verified": False,
            "remote_commit_sha": None,
        }
        validate_readiness_alignment(readiness_document, manifest)
        readiness_path = staging / READINESS_PATH
        readiness_path.write_bytes(_canonical_json(readiness_document))
        _fsync_path(readiness_path)
        readme_path = staging / README_PATH
        readme_path.write_bytes(render_dataset_card())
        _fsync_path(readme_path)
        empty_test_path = staging / EMPTY_TEST_PATH
        empty_test_path.parent.mkdir(parents=True, exist_ok=True)
        _write_table([], empty_test_path)
        _fsync_path(empty_test_path)
        _fsync_staging_directories(staging)
        _rename_noreplace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    final_manifest = destination / DATASET_CONFIG / "manifest.json"
    return ShardResult(
        output_root=destination,
        manifest_path=final_manifest,
        readiness_path=destination / READINESS_PATH,
        readme_path=destination / README_PATH,
        shards=tuple(shard_infos),
        pilot_article_keys=pilot_keys,
        row_count=len(selected),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def write_complete_shards(
    rows: Iterable[object],
    output_root: str | os.PathLike[str],
    *,
    spool_database: str | os.PathLike[str],
    provenance: Mapping[str, object],
    pilot_size: int = DEFAULT_PILOT_SIZE,
    target_shard_bytes: int = DEFAULT_TARGET_SHARD_BYTES,
    release_id: str | None = None,
    supersedes_commit_sha: str | None = None,
) -> ShardResult:
    """Spill, hash-sort, and materialize every valid row with bounded memory.

    The durable SQLite spool is deliberately retained so an interrupted normal
    run can replay inserts idempotently and resume publication without holding
    the complete corpus in RAM.
    """
    if (
        isinstance(pilot_size, bool)
        or not isinstance(pilot_size, int)
        or pilot_size <= 0
    ):
        raise ValueError("pilot_size must be a positive integer")
    if (
        isinstance(target_shard_bytes, bool)
        or not isinstance(target_shard_bytes, int)
        or target_shard_bytes <= 0
    ):
        raise ValueError("target_shard_bytes must be a positive integer")
    validate_document("provenance-v1", provenance)
    if release_id is None or not re.fullmatch(r"[a-f0-9]{64}", release_id):
        raise ValueError("complete release_id must be a lowercase SHA-256")
    if supersedes_commit_sha is None or not re.fullmatch(
        r"[a-f0-9]{40}", supersedes_commit_sha
    ):
        raise ValueError("complete release must pin the superseded commit SHA")
    active_release_root = f"{DATASET_CONFIG}/releases/{release_id}"
    checked_provenance = deepcopy(dict(provenance))
    database_path = Path(spool_database)
    if not database_path.is_absolute():
        raise ValueError("spool_database must be absolute")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    manifest_bytes = b""
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS complete_rows ("
            "article_key TEXT PRIMARY KEY, page_id INTEGER UNIQUE NOT NULL, "
            "split TEXT NOT NULL, rank TEXT NOT NULL UNIQUE, document BLOB NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS complete_metadata "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        ingested = connection.execute(
            "SELECT value FROM complete_metadata WHERE key='ingested'"
        ).fetchone()
        if ingested is None:
            inserted = 0
            for value in rows:
                document = validate_distillation_row(value)
                article_key = str(document["article_key"])
                page_id = int(document["page_id"])
                rank = hashlib.sha256(article_key.encode("utf-8")).hexdigest()
                payload = zlib.compress(_canonical_json(document), level=1)
                try:
                    connection.execute(
                        "INSERT INTO complete_rows VALUES (?,?,?,?,?)",
                        (article_key, page_id, document["split"], rank, payload),
                    )
                except sqlite3.IntegrityError as error:
                    existing = connection.execute(
                        "SELECT page_id,document FROM complete_rows WHERE article_key=?",
                        (article_key,),
                    ).fetchone()
                    if existing == (page_id, payload):
                        continue
                    raise ValueError(
                        f"duplicate complete-row identity: {article_key}/{page_id}"
                    ) from error
                inserted += 1
                if inserted % 10_000 == 0:
                    connection.commit()
            connection.execute(
                "INSERT OR REPLACE INTO complete_metadata VALUES ('ingested','true')"
            )
            connection.commit()

        accepted_rows = int(
            connection.execute("SELECT COUNT(*) FROM complete_rows").fetchone()[0]
        )
        if accepted_rows <= 0:
            raise ValueError("complete shard release must contain at least one row")
        selected_pilot_size = min(pilot_size, accepted_rows)
        pilot_keys = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT article_key FROM complete_rows ORDER BY rank,article_key LIMIT ?",
                (selected_pilot_size,),
            )
        )

        destination = Path(output_root)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-", dir=destination.parent
            )
        )
        shard_infos: list[ShardInfo] = []
        try:
            for split in ("train", "validation", "test"):
                chunk: list[dict[str, object]] = []
                chunk_bytes = 0
                shard_index = 0

                def flush() -> None:
                    nonlocal chunk, chunk_bytes, shard_index
                    if not chunk:
                        return
                    relative = (
                        Path(active_release_root)
                        / split
                        / f"part-{shard_index:05d}.parquet"
                    )
                    shard_path = staging / relative
                    shard_path.parent.mkdir(parents=True, exist_ok=True)
                    _write_table(chunk, shard_path)
                    _fsync_path(shard_path)
                    keys = [str(item["article_key"]) for item in chunk]
                    ranks = [hashlib.sha256(key.encode("utf-8")).hexdigest() for key in keys]
                    shard_infos.append(
                        ShardInfo(
                            path=relative.as_posix(),
                            split=split,
                            rows=len(chunk),
                            bytes=shard_path.stat().st_size,
                            sha256=_sha256(shard_path),
                            rows_sha256=identity_rows_sha256(chunk),
                            schema="distillation-example-v1",
                            version=1,
                            min_article_key=min(keys),
                            max_article_key=max(keys),
                            min_rank=min(ranks),
                            max_rank=max(ranks),
                        )
                    )
                    shard_index += 1
                    chunk = []
                    chunk_bytes = 0

                cursor = connection.execute(
                    "SELECT document FROM complete_rows WHERE split=? "
                    "ORDER BY rank,article_key",
                    (split,),
                )
                for (payload,) in cursor:
                    try:
                        raw_document = zlib.decompress(bytes(payload))
                    except zlib.error as error:
                        raise ValueError("complete row spool payload is malformed") from error
                    document = json.loads(raw_document)
                    estimated = len(raw_document)
                    if chunk and chunk_bytes + estimated > target_shard_bytes:
                        flush()
                    chunk.append(document)
                    chunk_bytes += estimated
                flush()

            shard_documents = [item.to_document() for item in shard_infos]
            counts = {
                "total": accepted_rows,
                **{
                    split: sum(item.rows for item in shard_infos if item.split == split)
                    for split in ("train", "validation", "test")
                },
            }
            if counts["total"] != sum(counts[split] for split in ("train", "validation", "test")):
                raise ValueError("complete spool count does not match emitted shards")
            aggregate_sha256 = hashlib.sha256(_canonical_json(shard_documents)).hexdigest()
            rows_sha256 = identity_rows_sha256(
                json.loads(zlib.decompress(bytes(payload)))
                for (payload,) in connection.execute(
                    "SELECT document FROM complete_rows ORDER BY rank,article_key"
                )
            )
            reports = checked_provenance["reports"]
            assert isinstance(reports, dict)
            reports.update(
                {
                    "dataset_aggregate_sha256": aggregate_sha256,
                    "dataset_rows_sha256": rows_sha256,
                    "dataset_counts": counts,
                }
            )
            manifest: dict[str, Any] = {
                "manifest_version": MANIFEST_VERSION,
                "schema_version": 1,
                "state": "prepared",
                "schema": "distillation-example-v1",
                "dataset_config": DATASET_CONFIG,
                "pilot_article_keys": list(pilot_keys),
                "counts": counts,
                "shards": shard_documents,
                "aggregate_sha256": aggregate_sha256,
                "rows_sha256": rows_sha256,
                "supersedes_commit_sha": supersedes_commit_sha,
                "active_release_root": active_release_root,
                "provenance": {
                    "schema": "provenance-v1",
                    "identifiers": {
                        "dataset_config": DATASET_CONFIG,
                        "example_schema": "distillation-example-v1",
                        "snapshot_date": "2016-10-01",
                        "teacher_dimension": 100,
                    },
                    "document": checked_provenance,
                },
            }
            validate_manifest_document(manifest, label="generated")
            manifest_path = staging / DATASET_CONFIG / "manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_bytes = _canonical_json(manifest)
            manifest_path.write_bytes(manifest_bytes)
            _fsync_path(manifest_path)
            artifact = checked_provenance.get("artifacts", {}).get("accepted_jsonl")
            if not isinstance(artifact, dict) or not isinstance(artifact.get("sha256"), str):
                raise ValueError("provenance accepted_jsonl artifact is required")
            readiness_document: dict[str, object] = {
                "state": "building",
                "schema_version": 1,
                "teacher_dimension": 100,
                "available_examples": accepted_rows,
                "verified_shards": [
                    {"path": item.path, "sha256": item.sha256, "examples": item.rows}
                    for item in shard_infos
                ],
                "source_checksums": {"accepted_jsonl": artifact["sha256"]},
                "remote_verified": False,
                "remote_commit_sha": None,
                "supersedes_commit_sha": supersedes_commit_sha,
                "active_release_root": active_release_root,
            }
            validate_readiness_alignment(readiness_document, manifest)
            readiness_path = staging / READINESS_PATH
            readiness_path.write_bytes(_canonical_json(readiness_document))
            _fsync_path(readiness_path)
            readme_path = staging / README_PATH
            readme_path.write_bytes(render_dataset_card(active_release_root))
            _fsync_path(readme_path)
            empty_test_path = staging / EMPTY_TEST_PATH
            empty_test_path.parent.mkdir(parents=True, exist_ok=True)
            _write_table([], empty_test_path)
            _fsync_path(empty_test_path)
            _fsync_staging_directories(staging)
            _rename_noreplace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    finally:
        connection.close()

    final_manifest = destination / DATASET_CONFIG / "manifest.json"
    return ShardResult(
        output_root=destination,
        manifest_path=final_manifest,
        readiness_path=destination / READINESS_PATH,
        readme_path=destination / README_PATH,
        shards=tuple(shard_infos),
        pilot_article_keys=pilot_keys,
        row_count=accepted_rows,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
