"""Deterministic Parquet materialization for the 2016 distillation dataset.

Production callers should keep ``target_shard_bytes`` in the 256--512 MiB
range.  The boundary is based on canonical row bytes, making it stable across
machines; fixture tests intentionally use much smaller values.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import validate_document
from .reconcile import split_for


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
        pa.field("teacher_vector", pa.list_(pa.float32(), 100), nullable=False),
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
    _manifest_sha256: str | None = None
    _verified_manifest_sha256: str | None = None
    _local_paths: frozenset[str] = frozenset()
    _verified_paths: frozenset[str] = frozenset()
    _manifest_path: Path | None = None
    _artifact_root: Path | None = None

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
        ):
            return False
        try:
            if _sha256(self._manifest_path) != self._verified_manifest_sha256:
                return False
            for shard in self.verified_shards:
                if _sha256(self._artifact_root / str(shard["path"])) != shard["sha256"]:
                    return False
        except (FileNotFoundError, OSError):
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
        if self.remote_commit_sha is not None and self.remote_commit_sha != commit_sha:
            raise ValueError("remote commit identity is immutable once verified")
        self.remote_verified = True
        self.remote_commit_sha = commit_sha
        self._verified_manifest_sha256 = verified_manifest
        self._verified_paths = paths

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
            old = {item["path"]: item["sha256"] for item in prior["verified_shards"]}
            new = {item["path"]: item["sha256"] for item in self.verified_shards}
            for shard_path, checksum in old.items():
                if shard_path not in new or new[shard_path] != checksum:
                    raise ValueError(f"published shard identity is immutable: {shard_path}")
        if self.remote_verified:
            if (
                self.remote_commit_sha is None
                or self._verified_manifest_sha256 is None
                or self._verified_paths != self._local_paths
            ):
                raise ValueError("remote readiness requires durable exact verification evidence")
            _atomic_write_json(
                _verification_evidence_path(destination),
                {
                    "commit_sha": self.remote_commit_sha,
                    "manifest_sha256": self._verified_manifest_sha256,
                    "verified_paths": sorted(self._verified_paths),
                },
            )
        _atomic_write_json(destination, self.to_document())


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
    else:
        infos = tuple(shards)
        manifest_sha256 = None
    verified = [
        {"path": item.path, "sha256": item.sha256, "examples": item.rows}
        for item in infos
    ]
    readiness = Readiness(
        state="building",
        available_examples=sum(item.rows for item in infos),
        verified_shards=verified,
        source_checksums=checked_sources,
        _manifest_sha256=manifest_sha256,
        _local_paths=frozenset(item.path for item in infos),
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
    manifest = json.loads(manifest_bytes)
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
    remote_verified = bool(document["remote_verified"])
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    local_paths = frozenset(manifest_identities)
    verified_manifest_sha: str | None = None
    verified_paths: frozenset[str] = frozenset()
    if remote_verified:
        evidence_path = _verification_evidence_path(Path(path))
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise ValueError("persisted readiness lacks remote verification evidence") from error
        if set(evidence) != {"commit_sha", "manifest_sha256", "verified_paths"}:
            raise ValueError("remote verification evidence has unexpected fields")
        if evidence["commit_sha"] != document["remote_commit_sha"]:
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
        verified_paths = frozenset(evidence["verified_paths"])
        if verified_paths != local_paths:
            raise ValueError("remote verification does not cover every manifest shard")
    return Readiness(
        state=str(document["state"]),
        available_examples=int(document["available_examples"]),
        verified_shards=[dict(item) for item in document["verified_shards"]],
        source_checksums=dict(document["source_checksums"]),
        remote_verified=remote_verified,
        remote_commit_sha=document["remote_commit_sha"],
        _manifest_sha256=manifest_sha,
        _verified_manifest_sha256=verified_manifest_sha,
        _local_paths=local_paths,
        _verified_paths=verified_paths,
        _manifest_path=local_manifest_path,
        _artifact_root=local_manifest_path.parent.parent,
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


def _document(value: object) -> dict[str, object]:
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
    source_provenance: Mapping[str, object] | None = None,
    dataset_provenance: Mapping[str, object] | None = None,
    model_provenance: Mapping[str, object] | None = None,
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

    pilot_heap: list[_PilotCandidate] = []
    article_keys: set[str] = set()
    page_ids: set[int] = set()
    for value in rows:
        document = _document(value)
        article_key = str(document["article_key"])
        page_id = int(document["page_id"])
        if article_key in article_keys:
            raise ValueError(f"duplicate article_key: {article_key}")
        if page_id in page_ids:
            raise ValueError(f"duplicate page identity: {page_id}")
        article_keys.add(article_key)
        page_ids.add(page_id)
        rank = hashlib.sha256(article_key.encode("utf-8")).hexdigest()
        heapq.heappush(pilot_heap, _PilotCandidate(rank, article_key, document))
        if len(pilot_heap) > pilot_size:
            heapq.heappop(pilot_heap)

    if pilot_size > len(article_keys):
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
                        schema="distillation-example-v1",
                        version=1,
                        min_article_key=min(chunk_keys),
                        max_article_key=max(chunk_keys),
                        min_rank=min(chunk_ranks),
                        max_rank=max(chunk_ranks),
                    )
                )

        shard_documents = [item.to_document() for item in shard_infos]
        manifest: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "schema": "distillation-example-v1",
            "dataset_config": DATASET_CONFIG,
            "pilot_article_keys": list(pilot_keys),
            "counts": {
                "total": len(selected),
                **{
                    split: sum(item.rows for item in shard_infos if item.split == split)
                    for split in ("train", "validation", "test")
                },
            },
            "shards": shard_documents,
            "aggregate_sha256": hashlib.sha256(
                _canonical_json(shard_documents)
            ).hexdigest(),
            "rows_sha256": hashlib.sha256(
                b"".join(_canonical_json(item[2]) for item in selected)
            ).hexdigest(),
            "provenance": {
                "source": dict(source_provenance or {}),
                "dataset": dict(dataset_provenance or {}),
                "model": dict(model_provenance or {}),
            },
        }
        manifest_path = staging / DATASET_CONFIG / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_bytes = _canonical_json(manifest)
        manifest_path.write_bytes(manifest_bytes)
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    final_manifest = destination / DATASET_CONFIG / "manifest.json"
    return ShardResult(
        output_root=destination,
        manifest_path=final_manifest,
        shards=tuple(shard_infos),
        pilot_article_keys=pilot_keys,
        row_count=len(selected),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
