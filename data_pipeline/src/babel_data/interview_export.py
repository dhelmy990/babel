"""Bounded, read-only export of a frozen incomplete 2016 extraction frontier."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .reconcile import SNAPSHOT_DATE, split_for
from .release import canonical_json, identity_rows_sha256
from .shard import PARQUET_SCHEMA, validate_distillation_row


INTERVIEW_CONFIG = "distillation_2016_interview"
INTERVIEW_SEED = "babel-interview-2016-v1"
INTERVIEW_COUNTS = {"train": 50_000, "validation": 5_000, "test": 5_000}
INTERVIEW_SMOKE_ROWS = 1_000
PROVENANCE_CORRECTION_REASON = (
    "Correct nonexistent producer commit SHA recorded during export"
)
MAX_SQLITE_BATCH_ROWS = 5_000
DEFAULT_SOURCE_SHA256 = {
    "teacher": "5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56",
    "wikipedia": "dbe52efb14e85049fcb0b88970b413f6e85972a76fd19b224514368b9b0e3df6",
}
DEFAULT_SOURCE_REVISIONS = {
    "teacher": "ee01785fc4cf3d7f25c90917f41e3962f93e9370",
    "wikipedia": "d949e81abe9fd4e1daf930bfe5990f9914c74b2e",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_COMMITISH = re.compile(r"^[0-9a-f]{7,40}$")
_SPLITS = ("train", "validation", "test")
_T = TypeVar("_T")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _resolve_git_commit(value: str, repository_root: Path) -> str | None:
    if not isinstance(value, str) or _COMMITISH.fullmatch(value) is None:
        return None
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "rev-parse",
            "--verify",
            f"{value}^{{commit}}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    canonical = completed.stdout.strip()
    if completed.returncode != 0 or _COMMIT.fullmatch(canonical) is None:
        return None
    return canonical


def canonical_git_commit(
    value: str, *, repository_root: str | os.PathLike[str] = _REPOSITORY_ROOT
) -> str:
    """Resolve a hexadecimal commitish to an existing full commit in this repository."""
    if not isinstance(value, str) or _COMMITISH.fullmatch(value) is None:
        raise ValueError("code commit must be 7-40 lowercase hexadecimal characters")
    canonical = _resolve_git_commit(value, Path(repository_root).resolve(strict=True))
    if canonical is None:
        raise ValueError("code commit does not resolve to an actual repository commit")
    return canonical


@dataclass(frozen=True, slots=True)
class FrozenFrontier:
    database_path: str
    database_device: int
    database_inode: int
    database_identity_sha256: str
    selected_count: int
    max_page_id: int
    max_selected_text_journal_row: int
    frozen_at_utc: str

    def to_document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "complete_corpus": False,
            "disclosure": (
                "Deterministic sample of a frozen incomplete selected-text frontier; "
                "not a sample selected after complete-corpus extraction."
            ),
        }


@dataclass(frozen=True, slots=True)
class SelectedIdentity:
    rank_sha256: str
    article_key: str
    page_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.page_id, int) or isinstance(self.page_id, bool) or self.page_id <= 0:
            raise ValueError("selected page_id must be a positive integer")
        if self.article_key != f"enwiki:{SNAPSHOT_DATE}:{self.page_id}":
            raise ValueError("selected article_key does not match page_id")
        if self.rank_sha256 != _rank_identity(self.article_key):
            raise ValueError("selected identity rank does not match the interview seed")

    def to_document(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InterviewSelectionV1:
    seed: str
    train: tuple[SelectedIdentity, ...]
    validation: tuple[SelectedIdentity, ...]
    test: tuple[SelectedIdentity, ...]
    smoke: tuple[SelectedIdentity, ...]
    ordered_sha256: Mapping[str, str]

    @property
    def counts(self) -> dict[str, int]:
        return {split: len(getattr(self, split)) for split in _SPLITS}

    @property
    def all_identities(self) -> tuple[SelectedIdentity, ...]:
        return self.train + self.validation + self.test


@dataclass(frozen=True, slots=True)
class InterviewShard:
    path: str
    split: str
    rows: int
    bytes: int
    sha256: str
    rows_sha256: str
    min_rank: str
    max_rank: str
    min_article_key: str
    max_article_key: str

    def to_document(self) -> dict[str, object]:
        return {**asdict(self), "schema": "distillation-example-v1", "version": 1}


@dataclass(frozen=True, slots=True)
class InterviewReleaseResult:
    output_root: Path
    manifest_path: Path
    readiness_path: Path
    shards: tuple[InterviewShard, ...]
    counts: Mapping[str, int]
    ordered_sha256: Mapping[str, str]
    frontier: FrozenFrontier

    def to_document(self) -> dict[str, object]:
        return {
            "output_root": str(self.output_root),
            "manifest_path": str(self.manifest_path),
            "readiness_path": str(self.readiness_path),
            "counts": dict(self.counts),
            "ordered_sha256": dict(self.ordered_sha256),
            "frontier": self.frontier.to_document(),
            "shards": [item.to_document() for item in self.shards],
        }


@dataclass(frozen=True, slots=True)
class _WorstCandidate:
    identity: SelectedIdentity

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _WorstCandidate):
            return NotImplemented
        left = (self.identity.rank_sha256, self.identity.article_key)
        right = (other.identity.rank_sha256, other.identity.article_key)
        return left > right


def _rank_identity(article_key: str) -> str:
    return hashlib.sha256(
        INTERVIEW_SEED.encode("utf-8") + b"\0" + article_key.encode("utf-8")
    ).hexdigest()


def _database_identity(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    payload = canonical_json(
        {"path": str(path.resolve()), "device": stat.st_dev, "inode": stat.st_ino}
    )
    return stat.st_dev, stat.st_ino, hashlib.sha256(payload).hexdigest()


def _open_query_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=1.0,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def _short_read(
    path: Path,
    query: str,
    parameters: Sequence[object] = (),
    *,
    retries: int = 8,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[object, ...]]:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        connection: sqlite3.Connection | None = None
        try:
            connection = _open_query_only(path)
            return list(connection.execute(query, tuple(parameters)).fetchall())
        except sqlite3.OperationalError as error:
            if "busy" not in str(error).lower() and "locked" not in str(error).lower():
                raise
            last_error = error
        finally:
            if connection is not None:
                connection.close()
        if attempt + 1 < retries:
            sleep(min(0.05 * (2**attempt), 1.0))
    raise RuntimeError("SQLite short read remained busy") from last_error


def freeze_frontier(database_path: str | os.PathLike[str]) -> FrozenFrontier:
    """Capture one durable committed selected-text boundary without retaining WAL."""
    path = Path(database_path).resolve(strict=True)
    before = _database_identity(path)
    rows = _short_read(
        path,
        """
        SELECT
          (SELECT COUNT(*) FROM selected_text),
          (SELECT COALESCE(MAX(page_id), 0) FROM selected_text),
          (SELECT COALESCE(MAX(end_row), 0) FROM journal WHERE phase='selected-text')
        """,
    )
    after = _database_identity(path)
    if before != after:
        raise ValueError("reconciliation database identity changed during frontier freeze")
    selected_count, max_page_id, journal_row = (int(value) for value in rows[0])
    if selected_count <= 0 or max_page_id <= 0:
        raise ValueError("selected-text frontier is empty")
    if journal_row != selected_count:
        raise ValueError("selected-text frontier is not at a durable journal boundary")
    return FrozenFrontier(
        database_path=str(path),
        database_device=before[0],
        database_inode=before[1],
        database_identity_sha256=before[2],
        selected_count=selected_count,
        max_page_id=max_page_id,
        max_selected_text_journal_row=journal_row,
        frozen_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _validate_frontier_identity(path: Path, frontier: FrozenFrontier) -> None:
    device, inode, digest = _database_identity(path)
    if (
        str(path) != frontier.database_path
        or device != frontier.database_device
        or inode != frontier.database_inode
        or digest != frontier.database_identity_sha256
    ):
        raise ValueError("reconciliation database identity differs from frozen frontier")


def _identity_digest(identities: Sequence[SelectedIdentity]) -> str:
    return identity_rows_sha256(
        {"article_key": item.article_key, "page_id": item.page_id}
        for item in identities
    )


def _push_candidate(
    heap: list[_WorstCandidate], identity: SelectedIdentity, required: int
) -> None:
    heapq.heappush(heap, _WorstCandidate(identity))
    if len(heap) > required:
        heapq.heappop(heap)


def _recount_frontier(path: Path, frontier: FrozenFrontier) -> None:
    count = int(
        _short_read(
            path,
            "SELECT COUNT(*) FROM selected_text WHERE page_id <= ?",
            (frontier.max_page_id,),
        )[0][0]
    )
    if count != frontier.selected_count:
        raise ValueError(
            "frozen frontier changed: selected-text count at or below max_page_id drifted"
        )


def select_interview_ids(
    database_path: str | os.PathLike[str],
    frontier: FrozenFrontier,
    batch_size: int = MAX_SQLITE_BATCH_ROWS,
    *,
    required_counts: Mapping[str, int] = INTERVIEW_COUNTS,
    smoke_size: int = INTERVIEW_SMOKE_ROWS,
) -> InterviewSelectionV1:
    """Select the lowest seeded hashes per existing split using bounded heaps."""
    if not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_SQLITE_BATCH_ROWS:
        raise ValueError("batch_size must be in 1..5000")
    counts = {split: int(required_counts.get(split, 0)) for split in _SPLITS}
    if any(value <= 0 for value in counts.values()):
        raise ValueError("required split counts must be positive")
    if not isinstance(smoke_size, int) or not 1 <= smoke_size <= counts["train"]:
        raise ValueError("smoke_size must be a positive train prefix")
    path = Path(database_path).resolve(strict=True)
    _validate_frontier_identity(path, frontier)
    heaps: dict[str, list[_WorstCandidate]] = {split: [] for split in _SPLITS}
    last_page_id = 0
    scanned = 0
    while True:
        batch = _short_read(
            path,
            "SELECT page_id FROM selected_text "
            "WHERE page_id > ? AND page_id <= ? ORDER BY page_id LIMIT ?",
            (last_page_id, frontier.max_page_id, batch_size),
        )
        if not batch:
            break
        for raw_page_id, in batch:
            page_id = int(raw_page_id)
            article_key = f"enwiki:{SNAPSHOT_DATE}:{page_id}"
            identity = SelectedIdentity(_rank_identity(article_key), article_key, page_id)
            split = split_for(article_key)
            _push_candidate(heaps[split], identity, counts[split])
        scanned += len(batch)
        last_page_id = int(batch[-1][0])
    if scanned != frontier.selected_count:
        raise ValueError("frozen frontier changed during identity scan")
    _recount_frontier(path, frontier)
    selected = {
        split: tuple(
            sorted(
                (item.identity for item in heaps[split]),
                key=lambda item: (item.rank_sha256, item.article_key),
            )
        )
        for split in _SPLITS
    }
    for split in _SPLITS:
        if len(selected[split]) != counts[split]:
            raise ValueError(f"insufficient {split} rows at frozen frontier")
    ordered_sha256 = {
        split: _identity_digest(selected[split]) for split in _SPLITS
    }
    return InterviewSelectionV1(
        seed=INTERVIEW_SEED,
        train=selected["train"],
        validation=selected["validation"],
        test=selected["test"],
        smoke=selected["train"][:smoke_size],
        ordered_sha256=ordered_sha256,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _atomic_replace(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def correct_interview_provenance(
    output_root: str | os.PathLike[str],
    *,
    superseded_code_commit: str,
    corrected_code_commit: str,
    prior_hf_commit: str,
    reason: str = PROVENANCE_CORRECTION_REASON,
    repository_root: str | os.PathLike[str] = _REPOSITORY_ROOT,
) -> dict[str, object]:
    """Correct only a nonexistent producer SHA and bind the prior Hub commit."""
    root = Path(output_root).resolve(strict=True)
    manifest_path = root / INTERVIEW_CONFIG / "manifest.json"
    readiness_path = root / INTERVIEW_CONFIG / "readiness.json"
    old_manifest_bytes = manifest_path.read_bytes()
    old_readiness_bytes = readiness_path.read_bytes()
    try:
        old_manifest = json.loads(old_manifest_bytes)
        old_readiness = json.loads(old_readiness_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("interview correction metadata is malformed") from error
    if not isinstance(old_manifest, dict) or not isinstance(old_readiness, dict):
        raise ValueError("interview correction metadata must be JSON objects")
    repository = Path(repository_root).resolve(strict=True)
    if _COMMIT.fullmatch(superseded_code_commit) is None:
        raise ValueError("superseded code commit must be an exact SHA")
    if _resolve_git_commit(superseded_code_commit, repository) is not None:
        raise ValueError("superseded code commit resolves and cannot be provenance-corrected")
    corrected = canonical_git_commit(
        corrected_code_commit, repository_root=repository
    )
    if _COMMIT.fullmatch(prior_hf_commit) is None:
        raise ValueError("prior Hub commit must be an exact SHA")
    if reason != PROVENANCE_CORRECTION_REASON:
        raise ValueError("provenance correction reason is fixed")
    correction: dict[str, object] = {
        "schema_version": 1,
        "prior_hf_commit": prior_hf_commit,
        "reason": reason,
        "superseded_code_commit": superseded_code_commit,
        "corrected_code_commit": corrected,
    }
    if old_manifest.get("code_commit") == corrected:
        if old_manifest.get("provenance_correction") != correction:
            raise ValueError("existing provenance correction is nonidentical")
        return correction
    if old_manifest.get("code_commit") != superseded_code_commit:
        raise ValueError("local manifest does not contain the superseded code commit")
    if "provenance_correction" in old_manifest or "provenance_correction" in old_readiness:
        raise ValueError("interview provenance was already corrected")
    if old_readiness.get("manifest_sha256") != hashlib.sha256(old_manifest_bytes).hexdigest():
        raise ValueError("interview readiness does not bind the pre-correction manifest")
    shards = old_manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != 3:
        raise ValueError("interview manifest shards are malformed")
    for item in shards:
        if not isinstance(item, dict):
            raise ValueError("interview manifest shard is malformed")
        path = root / str(item.get("path"))
        if (
            not path.is_file()
            or path.stat().st_size != int(item.get("bytes", -1))
            or _file_sha256(path) != item.get("sha256")
        ):
            raise ValueError("interview shard changed before provenance correction")
    new_manifest = deepcopy(old_manifest)
    new_manifest["code_commit"] = corrected
    new_manifest["provenance_correction"] = correction
    new_manifest_bytes = canonical_json(new_manifest)
    new_readiness = deepcopy(old_readiness)
    new_readiness["manifest_sha256"] = hashlib.sha256(new_manifest_bytes).hexdigest()
    new_readiness["provenance_correction"] = correction
    new_readiness_bytes = canonical_json(new_readiness)
    try:
        _atomic_replace(manifest_path, new_manifest_bytes)
        _atomic_replace(readiness_path, new_readiness_bytes)
    except BaseException:
        _atomic_replace(manifest_path, old_manifest_bytes)
        _atomic_replace(readiness_path, old_readiness_bytes)
        raise
    return correction


def _selected_rows(
    path: Path,
    identities: Sequence[SelectedIdentity],
    *,
    batch_size: int = 900,
) -> list[dict[str, object]]:
    by_page: dict[int, dict[str, object]] = {}
    ordered_pages = sorted(item.page_id for item in identities)
    for offset in range(0, len(ordered_pages), batch_size):
        page_ids = ordered_pages[offset : offset + batch_size]
        placeholders = ",".join("?" for _ in page_ids)
        fetched = _short_read(
            path,
            "SELECT t.page_id,t.vector,t.norm,s.canonical_title,s.revision_id,"
            "s.lead_text,s.article_text FROM teacher t JOIN selected_text s "
            "ON s.page_id=t.page_id WHERE t.status='matched' AND t.page_id IN ("
            + placeholders
            + ") ORDER BY t.page_id",
            page_ids,
        )
        for raw in fetched:
            page_id = int(raw[0])
            if page_id in by_page:
                raise ValueError(f"duplicate article_key for selected page {page_id}")
            vector = np.frombuffer(bytes(raw[1]), dtype=np.float32)
            norm = float(raw[2])
            lead_text = str(raw[5])
            article_text = str(raw[6])
            if vector.shape != (100,):
                raise ValueError("selected teacher vector must contain exactly 100 values")
            if not np.isfinite(vector).all() or not math.isfinite(norm):
                raise ValueError("selected teacher vector and norm must be finite")
            if not lead_text or not article_text:
                raise ValueError("selected row text must be nonempty")
            article_key = f"enwiki:{SNAPSHOT_DATE}:{page_id}"
            document = {
                "article_key": article_key,
                "page_id": page_id,
                "canonical_title": str(raw[3]),
                "wikidata_id": None,
                "lead_text": lead_text,
                "article_text": article_text,
                "teacher_vector": vector.tolist(),
                "teacher_norm": norm,
                "source_revision_id": int(raw[4]) if raw[4] is not None else None,
                "snapshot_date": SNAPSHOT_DATE,
                "split": split_for(article_key),
                "reconciliation_status": "matched",
            }
            by_page[page_id] = validate_distillation_row(document)
    expected_pages = {item.page_id for item in identities}
    if set(by_page) != expected_pages:
        missing = len(expected_pages - set(by_page))
        raise ValueError(f"selected row fetch is missing {missing} committed rows")
    return [by_page[item.page_id] for item in identities]


def _validate_selection(selection: InterviewSelectionV1) -> None:
    if selection.seed != INTERVIEW_SEED:
        raise ValueError("interview selection seed is fixed")
    seen: set[str] = set()
    for split in _SPLITS:
        identities = getattr(selection, split)
        for identity in identities:
            if identity.article_key in seen:
                raise ValueError(f"duplicate article_key: {identity.article_key}")
            seen.add(identity.article_key)
            if split_for(identity.article_key) != split:
                raise ValueError(f"{split} selection contains split drift")
        if tuple(sorted(identities, key=lambda item: (item.rank_sha256, item.article_key))) != identities:
            raise ValueError(f"{split} selection is not in deterministic rank order")
        if selection.ordered_sha256.get(split) != _identity_digest(identities):
            raise ValueError(f"{split} ordered identity checksum drifted")
    if selection.smoke != selection.train[: len(selection.smoke)]:
        raise ValueError("smoke selection must be the train prefix")


def write_interview_release(
    database_path: str | os.PathLike[str],
    frontier: FrozenFrontier,
    selection: InterviewSelectionV1,
    output_root: str | os.PathLike[str],
    *,
    source_sha256: Mapping[str, str] = DEFAULT_SOURCE_SHA256,
    source_revisions: Mapping[str, str] = DEFAULT_SOURCE_REVISIONS,
    code_commit: str,
) -> InterviewReleaseResult:
    """Fetch only selected rows and atomically write config-local Parquet metadata."""
    _validate_selection(selection)
    if set(source_sha256) != {"teacher", "wikipedia"} or any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in source_sha256.values()
    ):
        raise ValueError("teacher and wikipedia source SHA-256 values are required")
    if set(source_revisions) != {"teacher", "wikipedia"} or any(
        not isinstance(value, str) or _COMMIT.fullmatch(value) is None
        for value in source_revisions.values()
    ):
        raise ValueError("teacher and wikipedia source revision SHAs are required")
    code_commit = canonical_git_commit(code_commit)
    path = Path(database_path).resolve(strict=True)
    _validate_frontier_identity(path, frontier)
    _recount_frontier(path, frontier)
    destination = Path(output_root).resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    shards: list[InterviewShard] = []
    try:
        for split in _SPLITS:
            identities = getattr(selection, split)
            rows = _selected_rows(path, identities)
            relative = Path(INTERVIEW_CONFIG) / split / "part-00000.parquet"
            shard_path = staging / relative
            shard_path.parent.mkdir(parents=True, exist_ok=True)
            _write_table(rows, shard_path)
            shard = InterviewShard(
                path=relative.as_posix(),
                split=split,
                rows=len(rows),
                bytes=shard_path.stat().st_size,
                sha256=_file_sha256(shard_path),
                rows_sha256=_identity_digest(identities),
                min_rank=min(item.rank_sha256 for item in identities),
                max_rank=max(item.rank_sha256 for item in identities),
                min_article_key=min(item.article_key for item in identities),
                max_article_key=max(item.article_key for item in identities),
            )
            shards.append(shard)
        shard_documents = [item.to_document() for item in shards]
        counts = {"total": sum(selection.counts.values()), **selection.counts}
        manifest: dict[str, object] = {
            "manifest_version": 1,
            "schema_version": 1,
            "state": "interview_ready",
            "schema": "distillation-example-v1",
            "dataset_config": INTERVIEW_CONFIG,
            "counts": counts,
            "selection": {
                "policy_version": "interview-selection-v1",
                "seed": selection.seed,
                "smoke_article_keys": [item.article_key for item in selection.smoke],
                "ordered_identity_sha256": dict(selection.ordered_sha256),
                "ordered_identities": {
                    split: [item.to_document() for item in getattr(selection, split)]
                    for split in _SPLITS
                },
            },
            "frontier": frontier.to_document(),
            "source_sha256": dict(source_sha256),
            "source_revisions": dict(source_revisions),
            "code_commit": code_commit,
            "shards": shard_documents,
            "aggregate_sha256": hashlib.sha256(canonical_json(shard_documents)).hexdigest(),
        }
        config_root = staging / INTERVIEW_CONFIG
        manifest_path = config_root / "manifest.json"
        manifest_bytes = canonical_json(manifest)
        manifest_path.write_bytes(manifest_bytes)
        readiness = {
            "schema_version": 1,
            "state": "interview_ready",
            "dataset_config": INTERVIEW_CONFIG,
            "available_examples": counts["total"],
            "counts": counts,
            "teacher_dimension": 100,
            "verified_shards": [
                {"path": item.path, "sha256": item.sha256, "examples": item.rows}
                for item in shards
            ],
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "remote_verified": False,
            "remote_commit_sha": None,
        }
        readiness_path = config_root / "readiness.json"
        readiness_path.write_bytes(canonical_json(readiness))
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"output already exists: {destination}")
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return InterviewReleaseResult(
        output_root=destination,
        manifest_path=destination / INTERVIEW_CONFIG / "manifest.json",
        readiness_path=destination / INTERVIEW_CONFIG / "readiness.json",
        shards=tuple(shards),
        counts=counts,
        ordered_sha256=dict(selection.ordered_sha256),
        frontier=frontier,
    )


__all__ = [
    "DEFAULT_SOURCE_SHA256",
    "DEFAULT_SOURCE_REVISIONS",
    "FrozenFrontier",
    "INTERVIEW_CONFIG",
    "INTERVIEW_COUNTS",
    "INTERVIEW_SEED",
    "INTERVIEW_SMOKE_ROWS",
    "PROVENANCE_CORRECTION_REASON",
    "InterviewReleaseResult",
    "InterviewSelectionV1",
    "InterviewShard",
    "SelectedIdentity",
    "canonical_git_commit",
    "correct_interview_provenance",
    "freeze_frontier",
    "select_interview_ids",
    "write_interview_release",
]
