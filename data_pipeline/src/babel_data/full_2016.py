"""Restartable complete-2016 reconciliation from pinned private-Hub objects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from .mirror import open_processing_source, validate_data_root
from .reconcile import SNAPSHOT_DATE, ReconciledRow, split_for, validate_teacher_record
from .release import (
    EMPTY_TEST_PATH,
    canonical_json,
    render_dataset_card,
    validate_manifest_document,
    validate_readiness_alignment,
)
from .shard import ShardInfo, ShardResult, write_complete_shards
from .sources import SourceSpec, load_source_manifest
from .teacher import TeacherAudit, TeacherRecord, iter_teacher
from .wikipedia import (
    WikipediaIdentity,
    WikipediaPage,
    is_non_article_title,
    iter_wikipedia_identities,
    iter_wikipedia_pages_by_id,
    normalize_title,
)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BATCH_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class Full2016SourcePin:
    """Exact private-Hub identities required by the complete builder."""

    repository: str
    teacher_revision: str
    teacher_path: str
    teacher_sha256: str
    wikipedia_revision: str
    wikipedia_path: str
    wikipedia_sha256: str
    token: str

    def __post_init__(self) -> None:
        if not self.repository or not self.token:
            raise ValueError("private repository and token are required")
        for name, revision in (
            ("teacher", self.teacher_revision),
            ("wikipedia", self.wikipedia_revision),
        ):
            if _COMMIT.fullmatch(revision) is None:
                raise ValueError(f"{name} revision must be an exact commit SHA")
        for name, checksum in (
            ("teacher", self.teacher_sha256),
            ("wikipedia", self.wikipedia_sha256),
        ):
            if _SHA256.fullmatch(checksum) is None:
                raise ValueError(f"{name} SHA-256 is invalid")
        if not self.teacher_path.startswith("sources/teacher-zip/"):
            raise ValueError("teacher path must be the pinned teacher mirror object")
        if not self.wikipedia_path.startswith("sources/wikipedia-xml/"):
            raise ValueError("Wikipedia path must be the pinned XML mirror object")

    def identity(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "teacher_revision": self.teacher_revision,
            "teacher_path": self.teacher_path,
            "teacher_sha256": self.teacher_sha256,
            "wikipedia_revision": self.wikipedia_revision,
            "wikipedia_path": self.wikipedia_path,
            "wikipedia_sha256": self.wikipedia_sha256,
        }


@dataclass(frozen=True, slots=True)
class Complete2016Result:
    teacher_total: int
    matched: int
    excluded: int
    rows_written: int
    duplicate_article_keys: int
    invalid_vector_count: int
    split_counts: dict[str, int]
    exclusion_counts: dict[str, int]
    output_root: Path
    manifest_path: Path
    readiness_path: Path
    exclusion_ledger: Path
    range_journal: Path
    full_release_proof: Path
    readiness_state: str
    remote_commit_sha: str | None
    publication_commits: tuple[str, ...]
    active_release_root: str
    supersedes_commit_sha: str

    def to_document(self) -> dict[str, object]:
        return {
            "teacher_total": self.teacher_total,
            "matched": self.matched,
            "excluded": self.excluded,
            "rows_written": self.rows_written,
            "duplicate_article_keys": self.duplicate_article_keys,
            "invalid_vector_count": self.invalid_vector_count,
            "split_counts": dict(self.split_counts),
            "exclusion_counts": dict(self.exclusion_counts),
            "output_root": str(self.output_root),
            "manifest_path": str(self.manifest_path),
            "readiness_path": str(self.readiness_path),
            "exclusion_ledger": str(self.exclusion_ledger),
            "range_journal": str(self.range_journal),
            "full_release_proof": str(self.full_release_proof),
            "readiness_state": self.readiness_state,
            "remote_commit_sha": self.remote_commit_sha,
            "publication_commits": list(self.publication_commits),
            "active_release_root": self.active_release_root,
            "supersedes_commit_sha": self.supersedes_commit_sha,
        }

    @classmethod
    def from_document(cls, value: dict[str, object]) -> "Complete2016Result":
        return cls(
            teacher_total=int(value["teacher_total"]),
            matched=int(value["matched"]),
            excluded=int(value["excluded"]),
            rows_written=int(value["rows_written"]),
            duplicate_article_keys=int(value["duplicate_article_keys"]),
            invalid_vector_count=int(value["invalid_vector_count"]),
            split_counts={str(k): int(v) for k, v in dict(value["split_counts"]).items()},  # type: ignore[arg-type]
            exclusion_counts={str(k): int(v) for k, v in dict(value["exclusion_counts"]).items()},  # type: ignore[arg-type]
            output_root=Path(str(value["output_root"])),
            manifest_path=Path(str(value["manifest_path"])),
            readiness_path=Path(str(value["readiness_path"])),
            exclusion_ledger=Path(str(value["exclusion_ledger"])),
            range_journal=Path(str(value["range_journal"])),
            full_release_proof=Path(str(value["full_release_proof"])),
            readiness_state=str(value["readiness_state"]),
            remote_commit_sha=(
                str(value["remote_commit_sha"])
                if value.get("remote_commit_sha") is not None
                else None
            ),
            publication_commits=tuple(str(item) for item in value["publication_commits"]),  # type: ignore[union-attr]
            active_release_root=str(value["active_release_root"]),
            supersedes_commit_sha=str(value["supersedes_commit_sha"]),
        )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _work_key(pin: Full2016SourcePin, output_root: Path) -> str:
    identity = {**pin.identity(), "output_root": str(output_root.resolve())}
    return hashlib.sha256(_canonical(identity)).hexdigest()[:20]


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS teacher (
          position INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          normalized TEXT NOT NULL,
          vector BLOB NOT NULL,
          norm REAL NOT NULL,
          status TEXT,
          detail TEXT,
          page_id INTEGER,
          rank TEXT
        );
        CREATE INDEX IF NOT EXISTS teacher_normalized ON teacher(normalized);
        CREATE INDEX IF NOT EXISTS teacher_page_id ON teacher(page_id);
        CREATE TABLE IF NOT EXISTS page (
          normalized TEXT PRIMARY KEY,
          page_id INTEGER NOT NULL,
          canonical_title TEXT NOT NULL,
          revision_id INTEGER,
          redirect_target TEXT,
          ambiguous INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS page_id_unique ON page(page_id);
        CREATE TABLE IF NOT EXISTS selected_text (
          page_id INTEGER PRIMARY KEY,
          canonical_title TEXT NOT NULL,
          revision_id INTEGER,
          lead_text TEXT NOT NULL,
          article_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_exclusion (
          position INTEGER PRIMARY KEY,
          teacher_title TEXT NOT NULL,
          normalized_title TEXT NOT NULL,
          reason TEXT NOT NULL,
          detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS journal (
          range_id TEXT PRIMARY KEY,
          phase TEXT NOT NULL,
          start_row INTEGER NOT NULL,
          end_row INTEGER NOT NULL,
          row_count INTEGER NOT NULL
        );
        """
    )
    return connection


def _phase(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?", (f"phase:{name}",)
    ).fetchone()
    return row is not None and row[0] == "complete"


def _finish_phase(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, 'complete')",
        (f"phase:{name}",),
    )
    connection.commit()


def _journal_batch(
    connection: sqlite3.Connection,
    phase: str,
    start: int,
    end: int,
    count: int,
) -> None:
    range_id = f"{phase}:{start:012d}-{end:012d}"
    connection.execute(
        "INSERT OR IGNORE INTO journal VALUES (?, ?, ?, ?, ?)",
        (range_id, phase, start, end, count),
    )


def _ingest_teacher(
    connection: sqlite3.Connection, path: Path
) -> tuple[int, list[dict[str, object]]]:
    audit = TeacherAudit()
    start = 1
    emitted = 0
    for position, record in enumerate(iter_teacher(path, audit=audit), 1):
        normalized, norm = validate_teacher_record(record)
        connection.execute(
            "INSERT OR IGNORE INTO teacher(position,title,normalized,vector,norm) "
            "VALUES (?,?,?,?,?)",
            (position, record.title, normalized, record.vector.tobytes(), norm),
        )
        emitted = position
        if position % _BATCH_ROWS == 0:
            _journal_batch(connection, "teacher", start, position, position - start + 1)
            connection.commit()
            start = position + 1
    if emitted >= start:
        _journal_batch(connection, "teacher", start, emitted, emitted - start + 1)
    connection.execute(
        "INSERT OR REPLACE INTO metadata VALUES ('teacher_total', ?)",
        (str(audit.raw_record_count or emitted),),
    )
    connection.commit()
    if audit.exclusions_truncated:
        raise ValueError("teacher exclusion audit was truncated; complete accounting is impossible")
    exclusions = [
        {
            "teacher_title": bytes.fromhex(item.raw_title_hex).decode("utf-8", "replace"),
            "normalized_title": "",
            "reason": item.reason,
            "detail": item.detail,
        }
        for item in audit.exclusions
    ]
    for item in exclusions:
        connection.execute(
            "INSERT OR REPLACE INTO source_exclusion VALUES (?,?,?,?,?)",
            (
                emitted + connection.execute(
                    "SELECT COUNT(*) FROM source_exclusion"
                ).fetchone()[0] + 1,
                item["teacher_title"],
                item["normalized_title"],
                item["reason"],
                item["detail"],
            ),
        )
    connection.commit()
    return audit.raw_record_count or emitted, exclusions


def _ingest_page_identities(connection: sqlite3.Connection, path: Path) -> None:
    start = 1
    count = 0
    for count, page in enumerate(iter_wikipedia_identities(path), 1):
        normalized = normalize_title(page.canonical_title)
        try:
            connection.execute(
                "INSERT INTO page(normalized,page_id,canonical_title,revision_id,redirect_target) "
                "VALUES (?,?,?,?,?)",
                (
                    normalized,
                    page.page_id,
                    page.canonical_title,
                    page.revision_id,
                    page.redirect_target,
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                "SELECT page_id,canonical_title,revision_id,redirect_target "
                "FROM page WHERE normalized=?",
                (normalized,),
            ).fetchone()
            identity = (
                page.page_id,
                page.canonical_title,
                page.revision_id,
                page.redirect_target,
            )
            if existing != identity:
                connection.execute(
                    "UPDATE page SET ambiguous=1 WHERE normalized=? OR page_id=?",
                    (normalized, page.page_id),
                )
        if count % _BATCH_ROWS == 0:
            _journal_batch(connection, "wikipedia", start, count, count - start + 1)
            connection.commit()
            start = count + 1
    if count >= start:
        _journal_batch(connection, "wikipedia", start, count, count - start + 1)
    connection.execute(
        "INSERT OR REPLACE INTO metadata VALUES ('wikipedia_total', ?)",
        (str(count),),
    )
    connection.commit()


def _exclude_duplicates(connection: sqlite3.Connection) -> None:
    groups = connection.execute(
        "SELECT normalized FROM teacher GROUP BY normalized HAVING COUNT(*) > 1"
    )
    for (normalized,) in groups:
        rows = connection.execute(
            "SELECT position,title,vector FROM teacher WHERE normalized=?",
            (normalized,),
        ).fetchall()
        winner = min(rows, key=lambda row: (row[1] != normalized, row[1], row[2]))
        connection.execute(
            "UPDATE teacher SET status='duplicate/ambiguous_title', "
            "detail='duplicate normalized teacher identity' "
            "WHERE normalized=? AND position<>?",
            (normalized, winner[0]),
        )


def _resolve_one(
    connection: sqlite3.Connection, normalized: str, *, max_depth: int = 16
) -> tuple[str, str, int | None]:
    if is_non_article_title(normalized):
        return "non_article_namespace", "teacher title is outside namespace zero", None
    current = normalized
    visited: set[str] = set()
    for depth in range(max_depth + 1):
        row = connection.execute(
            "SELECT page_id,redirect_target,ambiguous FROM page WHERE normalized=?",
            (current,),
        ).fetchone()
        if row is None:
            reason = "title_not_found" if depth == 0 else "redirect_target_missing"
            detail = (
                "no exact normalized-title page in the pinned snapshot"
                if depth == 0
                else "redirect target is absent from the pinned snapshot"
            )
            return reason, detail, None
        if row[2]:
            return "duplicate/ambiguous_title", "Wikipedia title or page identity is ambiguous", None
        if row[1] is None:
            return "matched", "exact normalized-title reconciliation", int(row[0])
        if current in visited:
            return "redirect_cycle", "redirect chain contains a cycle", None
        if depth == max_depth:
            return "redirect_depth_exceeded", "redirect chain exceeds the configured bound", None
        visited.add(current)
        current = str(row[1])
    raise AssertionError("unreachable")


def _resolve_teachers(connection: sqlite3.Connection) -> None:
    _exclude_duplicates(connection)
    cursor = connection.execute(
        "SELECT position,normalized FROM teacher WHERE status IS NULL ORDER BY position"
    )
    start = 1
    processed = 0
    for processed, (position, normalized) in enumerate(cursor, 1):
        status, detail, page_id = _resolve_one(connection, str(normalized))
        rank = (
            hashlib.sha256(f"enwiki:{SNAPSHOT_DATE}:{page_id}".encode()).hexdigest()
            if page_id is not None
            else None
        )
        connection.execute(
            "UPDATE teacher SET status=?,detail=?,page_id=?,rank=? WHERE position=?",
            (status, detail, page_id, rank, position),
        )
        if processed % _BATCH_ROWS == 0:
            _journal_batch(connection, "reconcile", start, processed, processed - start + 1)
            connection.commit()
            start = processed + 1
    if processed >= start:
        _journal_batch(connection, "reconcile", start, processed, processed - start + 1)

    collisions = connection.execute(
        "SELECT page_id FROM teacher WHERE status='matched' "
        "GROUP BY page_id HAVING COUNT(*) > 1"
    )
    for (page_id,) in collisions:
        canonical = connection.execute(
            "SELECT canonical_title FROM page WHERE page_id=?", (page_id,)
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT position,title,normalized,vector FROM teacher "
            "WHERE status='matched' AND page_id=?",
            (page_id,),
        ).fetchall()
        winner = min(
            rows,
            key=lambda row: (
                row[2] != normalize_title(str(canonical)),
                row[2],
                row[1],
                row[3],
            ),
        )
        connection.execute(
            "UPDATE teacher SET status='canonical_identity_collision', "
            "detail='multiple teacher identities resolve to one canonical page', "
            "page_id=NULL,rank=NULL WHERE page_id=? AND position<>?",
            (page_id, winner[0]),
        )
    connection.commit()


def _collect_selected_text(connection: sqlite3.Connection, path: Path) -> None:
    count = 0
    start = 1
    wanted_ids = (
        int(row[0])
        for row in connection.execute(
            "SELECT page_id FROM teacher WHERE status='matched' ORDER BY page_id"
        )
    )
    for page in iter_wikipedia_pages_by_id(path, wanted_ids):
        if not page.article_text:
            connection.execute(
                "UPDATE teacher SET status='empty_text',detail='resolved page has empty article text',"
                "page_id=NULL,rank=NULL WHERE status='matched' AND page_id=?",
                (page.page_id,),
            )
            continue
        if not page.lead_text:
            connection.execute(
                "UPDATE teacher SET status='empty_lead',detail='resolved page has empty lead text',"
                "page_id=NULL,rank=NULL WHERE status='matched' AND page_id=?",
                (page.page_id,),
            )
            continue
        connection.execute(
            "INSERT OR REPLACE INTO selected_text VALUES (?,?,?,?,?)",
            (
                page.page_id,
                page.canonical_title,
                page.revision_id,
                page.lead_text,
                page.article_text,
            ),
        )
        count += 1
        if count % _BATCH_ROWS == 0:
            _journal_batch(connection, "selected-text", start, count, count - start + 1)
            connection.commit()
            start = count + 1
    if count >= start:
        _journal_batch(connection, "selected-text", start, count, count - start + 1)
    missing = connection.execute(
        "SELECT COUNT(*) FROM teacher t LEFT JOIN selected_text p ON p.page_id=t.page_id "
        "WHERE t.status='matched' AND p.page_id IS NULL"
    ).fetchone()[0]
    if missing:
        raise ValueError(f"{missing} reconciled pages were absent from the text pass")
    connection.commit()


def _rows(connection: sqlite3.Connection) -> Iterator[dict[str, object]]:
    cursor = connection.execute(
        "SELECT t.title,t.normalized,t.vector,t.norm,t.page_id,p.canonical_title,"
        "p.revision_id,p.lead_text,p.article_text FROM teacher t "
        "JOIN selected_text p ON p.page_id=t.page_id "
        "WHERE t.status='matched' ORDER BY t.rank,t.position"
    )
    for title, normalized, vector_bytes, norm, page_id, canonical, revision, lead, text in cursor:
        vector = np.frombuffer(vector_bytes, dtype=np.float32)
        if vector.shape != (100,) or not np.isfinite(vector).all() or not math.isfinite(norm):
            raise ValueError("persisted teacher vector violates the finite 100d contract")
        row = ReconciledRow(
            teacher_title=str(title),
            teacher_normalized_title=str(normalized),
            teacher_vector=vector,
            teacher_norm=float(norm),
            page_id=int(page_id),
            source_revision_id=int(revision) if revision is not None else None,
            canonical_title=str(canonical),
            lead_text=str(lead),
            article_text=str(text),
            article_key=f"enwiki:{SNAPSHOT_DATE}:{page_id}",
            snapshot_date=SNAPSHOT_DATE,
            split=split_for(f"enwiki:{SNAPSHOT_DATE}:{page_id}"),
            reconciliation_status="matched",
        )
        yield row.to_document()


def _write_ledger(connection: sqlite3.Connection, path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    with path.open("wb") as destination:
        cursor = connection.execute(
            "SELECT title,normalized,status,detail FROM teacher "
            "WHERE status<>'matched' ORDER BY position"
        )
        for title, normalized, reason, detail in cursor:
            document = {
                "teacher_title": title,
                "normalized_title": normalized,
                "reason": reason,
                "detail": detail,
            }
            counts[str(reason)] += 1
            destination.write(_canonical(document) + b"\n")
        for title, normalized, reason, detail in connection.execute(
            "SELECT teacher_title,normalized_title,reason,detail "
            "FROM source_exclusion ORDER BY position"
        ):
            document = {
                "teacher_title": title,
                "normalized_title": normalized,
                "reason": reason,
                "detail": detail,
            }
            counts[str(reason)] += 1
            destination.write(_canonical(document) + b"\n")
        destination.flush()
        os.fsync(destination.fileno())
    return dict(sorted(counts.items()))


def _write_journal(connection: sqlite3.Connection, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as destination:
        for range_id, phase, start, end, count in connection.execute(
            "SELECT range_id,phase,start_row,end_row,row_count FROM journal ORDER BY range_id"
        ):
            destination.write(
                _canonical(
                    {
                        "range_id": range_id,
                        "phase": phase,
                        "start_row": start,
                        "end_row": end,
                        "row_count": count,
                    }
                )
                + b"\n"
            )
        destination.flush()
        os.fsync(destination.fileno())


def _write_accepted(connection: sqlite3.Connection, path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    rows = 0
    with path.open("wb") as destination:
        for row in _rows(connection):
            payload = _canonical(row) + b"\n"
            destination.write(payload)
            digest.update(payload)
            size += len(payload)
            rows += 1
        destination.flush()
        os.fsync(destination.fileno())
    return {"sha256": digest.hexdigest(), "size": size, "rows": rows}


def _source_document(spec: SourceSpec, role: str) -> dict[str, object]:
    document: dict[str, object] = {
        "role": role,
        "filename": spec.filename,
        "url": spec.url,
        "size": spec.size,
        "md5": spec.md5,
        "downloaded_at": "2026-08-26",
    }
    if spec.sha1 is not None:
        document["sha1"] = spec.sha1
    return document


def _source_documents() -> list[dict[str, object]]:
    manifest_path = Path(__file__).resolve().parents[2] / "manifests" / "2016-sources.json"
    sources = load_source_manifest(manifest_path)
    return [
        _source_document(sources["teacher-zip"], "teacher"),
        _source_document(sources["wikipedia-xml"], "wikipedia"),
    ]


def _write_report(
    path: Path,
    *,
    teacher_total: int,
    matched: int,
    exclusion_counts: dict[str, int],
    wikipedia_total: int,
    source_pin: Full2016SourcePin,
) -> dict[str, object]:
    document = {
        "schema_version": 1,
        "complete": True,
        "teacher_total": teacher_total,
        "matched": matched,
        "excluded": teacher_total - matched,
        "matched_wikipedia_pages": matched,
        "wikipedia_pages_scanned": wikipedia_total,
        "exclusion_counts": exclusion_counts,
        "source_pin": source_pin.identity(),
    }
    payload = _canonical(document) + b"\n"
    path.write_bytes(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("accepted JSONL row is not an object")
                yield value


def _adopt_complete_shards(
    output: Path,
    *,
    provenance: dict[str, object],
    release_id: str,
    predecessor: str,
    matched: int,
) -> ShardResult:
    """Validate and adopt a durable bundle created before receipt persistence."""
    manifest_path = output / "distillation_2016" / "manifest.json"
    readiness_path = output / "readiness.json"
    readme_path = output / "README.md"
    manifest_bytes = manifest_path.read_bytes()
    manifest = validate_manifest_document(
        json.loads(manifest_bytes), label="resumable complete release"
    )
    active_root = f"distillation_2016/releases/{release_id}"
    published_provenance = json.loads(
        _canonical(manifest["provenance"]["document"])  # type: ignore[index]
    )
    published_reports = published_provenance["reports"]
    for field in (
        "dataset_aggregate_sha256",
        "dataset_rows_sha256",
        "dataset_counts",
    ):
        published_reports.pop(field, None)
    if (
        manifest.get("active_release_root") != active_root
        or manifest.get("supersedes_commit_sha") != predecessor
        or manifest["counts"]["total"] != matched  # type: ignore[index]
        or published_provenance != provenance
    ):
        raise ValueError("existing output does not match the resumable release identity")
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    validate_readiness_alignment(readiness, manifest)
    if (
        readiness.get("state") != "building"
        or readiness.get("remote_verified") is not False
        or readiness.get("remote_commit_sha") is not None
    ):
        raise ValueError("existing output is not an unpublished resumable release")
    if readme_path.read_bytes() != render_dataset_card(active_root):
        raise ValueError("existing output dataset card does not select the active release")
    if not (output / EMPTY_TEST_PATH).is_file():
        raise ValueError("existing output lacks the deterministic empty test sentinel")

    shards: list[ShardInfo] = []
    for item in manifest["shards"]:  # type: ignore[union-attr]
        shard = ShardInfo(**item)
        local = (output / shard.path).resolve(strict=True)
        local.relative_to(output.resolve())
        if local.stat().st_size != shard.bytes or _file_sha256(local) != shard.sha256:
            raise ValueError("existing output shard identity does not match its manifest")
        shards.append(shard)
    return ShardResult(
        output_root=output,
        manifest_path=manifest_path,
        readiness_path=readiness_path,
        readme_path=readme_path,
        shards=tuple(shards),
        pilot_article_keys=tuple(str(key) for key in manifest["pilot_article_keys"]),
        row_count=int(manifest["counts"]["total"]),  # type: ignore[index]
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _provenance(
    pin: Full2016SourcePin,
    teacher_total: int,
    matched: int,
    exclusion_counts: dict[str, int],
    accepted: dict[str, object],
    report: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sources": _source_documents(),
        "artifacts": {
            "accepted_jsonl": {
                "sha256": accepted["sha256"],
                "size": accepted["size"],
            },
            "reconciliation_report": dict(report),
        },
        "reports": {
            "row_counts": {
                "raw": teacher_total,
                "accepted": matched,
                "excluded": teacher_total - matched,
                "matched_wikipedia_pages": matched,
                "teacher_input_rows": teacher_total,
            },
            "match_rate": matched / teacher_total if teacher_total else 0.0,
            "exclusion_counts": dict(exclusion_counts),
            "text_statistics": {
                "count": matched, "min_length": 0, "max_length": 0,
                "mean_length": 0.0, "stddev_length": 0.0,
                "p50_length": 0.0, "p95_length": 0.0, "p99_length": 0.0,
                "histogram": [matched],
            },
            "vector_statistics": {
                "dimension": 100, "count": matched, "min_norm": 0.0,
                "max_norm": 0.0, "mean_norm": 0.0, "stddev_norm": 0.0,
                "p50_norm": 0.0, "p95_norm": 0.0, "non_finite_count": 0,
            },
        },
    }


def _write_full_release_proof(
    destination: Path,
    *,
    provenance: dict[str, object],
    accepted: dict[str, object],
    report: dict[str, object],
    teacher_total: int,
    wikipedia_total: int,
) -> None:
    sources = _source_documents()
    inventories = [
        {
            **sources[0],
            "records": teacher_total,
            "emitted_records": teacher_total,
            "upstream_excluded_records": 0,
        },
        {
            **sources[1],
            "records": wikipedia_total,
            "emitted_records": wikipedia_total,
            "upstream_excluded_records": 0,
        },
    ]
    proof = {
        "schema_version": 1,
        "dataset_config": "distillation_2016",
        "provenance_sha256": hashlib.sha256(canonical_json(provenance)).hexdigest(),
        "accepted_jsonl": dict(accepted),
        "reconciliation_report": {
            **report,
            "complete": True,
            "raw_rows": teacher_total,
            "accepted_rows": int(accepted["rows"]),
            "excluded_rows": teacher_total - int(accepted["rows"]),
            "matched_wikipedia_pages": int(accepted["rows"]),
        },
        "source_inventories": inventories,
    }
    destination.write_bytes(_canonical(proof) + b"\n")


def build_complete_2016(
    source_pin: Full2016SourcePin,
    data_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    resume: bool = True,
) -> Complete2016Result:
    """Build the complete release without ever opening authoritative staging bytes."""
    if not isinstance(source_pin, Full2016SourcePin):
        raise TypeError("source_pin must be Full2016SourcePin")
    root = validate_data_root(data_root)
    output = Path(output_root)
    if not output.is_absolute():
        raise ValueError("output_root must be absolute")
    work = root / "full-2016-work" / _work_key(source_pin, output)
    work.mkdir(parents=True, exist_ok=True)
    result_path = work / "result.json"
    if result_path.exists():
        result = Complete2016Result.from_document(json.loads(result_path.read_text()))
        if result.manifest_path.exists() and result.readiness_path.exists():
            return result
    if not resume and (work / "reconcile.sqlite3").exists():
        raise FileExistsError("existing resumable state requires resume=True")

    cache = root / "hf-cache"
    teacher_path = open_processing_source(
        source_pin.repository,
        source_pin.teacher_revision,
        source_pin.teacher_path,
        source_pin.token,
        cache,
    )
    wikipedia_path = open_processing_source(
        source_pin.repository,
        source_pin.wikipedia_revision,
        source_pin.wikipedia_path,
        source_pin.token,
        cache,
    )
    if _file_sha256(teacher_path) != source_pin.teacher_sha256:
        raise ValueError("pinned teacher SHA-256 disagrees with the verified cache object")
    if _file_sha256(wikipedia_path) != source_pin.wikipedia_sha256:
        raise ValueError("pinned Wikipedia SHA-256 disagrees with the verified cache object")
    connection = _connect(work / "reconcile.sqlite3")
    try:
        if not _phase(connection, "teacher"):
            teacher_total, _parser_exclusions = _ingest_teacher(connection, teacher_path)
            _finish_phase(connection, "teacher")
        else:
            teacher_total = int(
                connection.execute(
                    "SELECT value FROM metadata WHERE key='teacher_total'"
                ).fetchone()[0]
            )
        if not _phase(connection, "wikipedia"):
            _ingest_page_identities(connection, wikipedia_path)
            _finish_phase(connection, "wikipedia")
        if not _phase(connection, "reconcile"):
            _resolve_teachers(connection)
            _finish_phase(connection, "reconcile")
        if not _phase(connection, "selected-text"):
            _collect_selected_text(connection, wikipedia_path)
            _finish_phase(connection, "selected-text")

        matched = int(
            connection.execute("SELECT COUNT(*) FROM teacher WHERE status='matched'").fetchone()[0]
        )
        excluded = teacher_total - matched
        if matched <= 0:
            raise ValueError("complete 2016 reconciliation produced no matched rows")
        ledger = work / "exclusions.jsonl"
        exclusion_counts = _write_ledger(connection, ledger)
        journal = work / "range-journal.jsonl"
        _write_journal(connection, journal)
        if sum(exclusion_counts.values()) != excluded:
            raise ValueError("teacher accounting does not agree with the exclusion ledger")

        accepted_path = work / "accepted.jsonl"
        accepted = _write_accepted(connection, accepted_path)
        if int(accepted["rows"]) != matched:
            raise ValueError("accepted JSONL row count does not match reconciliation")
        wikipedia_total = int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='wikipedia_total'"
            ).fetchone()[0]
        )
        report_path = work / "reconciliation-report.json"
        report = _write_report(
            report_path,
            teacher_total=teacher_total,
            matched=matched,
            exclusion_counts=exclusion_counts,
            wikipedia_total=wikipedia_total,
            source_pin=source_pin,
        )
        provenance = _provenance(
            source_pin,
            teacher_total,
            matched,
            exclusion_counts,
            accepted,
            report,
        )
        release_id = hashlib.sha256(
            _canonical(
                {
                    "source_pin": source_pin.identity(),
                    "accepted_sha256": accepted["sha256"],
                    "teacher_total": teacher_total,
                    "matched": matched,
                }
            )
        ).hexdigest()

        if not output.exists():
            shards = write_complete_shards(
                _iter_jsonl(accepted_path),
                output,
                spool_database=work / "complete-rows.sqlite3",
                provenance=provenance,
                release_id=release_id,
                supersedes_commit_sha=source_pin.wikipedia_revision,
            )
        elif resume:
            shards = _adopt_complete_shards(
                output,
                provenance=provenance,
                release_id=release_id,
                predecessor=source_pin.wikipedia_revision,
                matched=matched,
            )
        else:
            raise FileExistsError("existing output requires resume=True")
        split_counts = {"train": 0, "validation": 0, "test": 0}
        for (page_id,) in connection.execute(
            "SELECT page_id FROM teacher WHERE status='matched'"
        ):
            split_counts[split_for(f"enwiki:{SNAPSHOT_DATE}:{page_id}")] += 1
        proof_path = work / "full-release-proof.json"
        published_manifest = json.loads(shards.manifest_path.read_text(encoding="utf-8"))
        published_provenance = published_manifest["provenance"]["document"]
        _write_full_release_proof(
            proof_path,
            provenance=published_provenance,
            accepted=accepted,
            report=report,
            teacher_total=teacher_total,
            wikipedia_total=wikipedia_total,
        )
        result = Complete2016Result(
            teacher_total=teacher_total,
            matched=matched,
            excluded=excluded,
            rows_written=shards.row_count,
            duplicate_article_keys=0,
            invalid_vector_count=0,
            split_counts=split_counts,
            exclusion_counts=exclusion_counts,
            output_root=output,
            manifest_path=shards.manifest_path,
            readiness_path=shards.readiness_path,
            exclusion_ledger=ledger,
            range_journal=journal,
            full_release_proof=proof_path,
            readiness_state="building",
            remote_commit_sha=None,
            publication_commits=(),
            active_release_root=f"distillation_2016/releases/{release_id}",
            supersedes_commit_sha=source_pin.wikipedia_revision,
        )
        result_path.write_bytes(_canonical(result.to_document()) + b"\n")
        return result
    finally:
        connection.close()


def rebind_supersession_predecessor(
    output_root: str | os.PathLike[str], predecessor_commit_sha: str
) -> None:
    """Bind final metadata to the last inactive-shard staging commit."""
    if _COMMIT.fullmatch(predecessor_commit_sha) is None:
        raise ValueError("predecessor commit must be an exact lowercase SHA")
    root = Path(output_root)
    manifest_path = root / "distillation_2016" / "manifest.json"
    readiness_path = root / "readiness.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if manifest.get("active_release_root") is None:
        raise ValueError("only a versioned complete release can be rebound")
    if readiness.get("state") not in {"building", "complete"}:
        raise ValueError("supersession predecessor has an invalid publication state")
    if readiness.get("remote_verified") is not False or readiness.get(
        "remote_commit_sha"
    ) is not None:
        raise ValueError("cannot rebind a remotely verified supersession")
    manifest["supersedes_commit_sha"] = predecessor_commit_sha
    readiness["supersedes_commit_sha"] = predecessor_commit_sha
    validate_manifest_document(manifest, label="rebound")
    validate_readiness_alignment(readiness, manifest)
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    readiness_path.write_bytes(_canonical(readiness) + b"\n")


__all__ = [
    "Complete2016Result",
    "Full2016SourcePin",
    "build_complete_2016",
    "rebind_supersession_predecessor",
]
