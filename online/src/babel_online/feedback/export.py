"""Bounded observable feedback export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .bus import FeedbackRecord, OffsetRange


class BoundedFeedbackSource(Protocol):
    def records(self, offset_range: OffsetRange) -> tuple[FeedbackRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class FeedbackExport:
    records: tuple[FeedbackRecord, ...]
    jsonl_path: Path
    parquet_path: Path
    edge_jsonl_path: Path
    edge_parquet_path: Path
    manifest_path: Path

    def publication_files(self) -> dict[str, Path]:
        """Return the canonical observable Parquet inputs for a run bundle."""
        return {
            "feedback.parquet": self.parquet_path,
            "edges.parquet": self.edge_parquet_path,
        }


@dataclass(frozen=True, order=True, slots=True)
class CanonicalExperimentEdge:
    run_id: UUID
    source_babel_id: UUID
    target_babel_id: UUID
    acting_creator_id: UUID
    request_id: UUID
    feedback_event_id: UUID
    feedback_occurred_at_ns: int
    traversal_session_id: UUID
    traversal_depth: int

    def as_row(self) -> dict[str, object]:
        return {
            "runId": str(self.run_id),
            "sourceBabelId": str(self.source_babel_id),
            "targetBabelId": str(self.target_babel_id),
            "actingCreatorId": str(self.acting_creator_id),
            "requestId": str(self.request_id),
            "feedbackEventId": str(self.feedback_event_id),
            "feedbackOccurredAtNs": self.feedback_occurred_at_ns,
            "traversalSessionId": str(self.traversal_session_id),
            "traversalDepth": self.traversal_depth,
        }


class EdgeProvenanceMismatch(ValueError):
    pass


def reconstruct_canonical_edges(
    events: Iterable[object],
) -> tuple[CanonicalExperimentEdge, ...]:
    """Select earliest V2 include provenance independent of arrival order."""
    from babel_online.contracts import FeedbackEventV2

    selected: dict[tuple[UUID, UUID, UUID], CanonicalExperimentEdge] = {}
    for value in events:
        event = getattr(value, "event", value)
        if not isinstance(event, FeedbackEventV2):
            continue
        for action in event.candidateActions:
            if action.action != "include" or action.babelId == event.sourceBabelId:
                continue
            edge = CanonicalExperimentEdge(
                run_id=event.runId,
                source_babel_id=event.sourceBabelId,
                target_babel_id=action.babelId,
                acting_creator_id=event.creatorId,
                request_id=event.requestId,
                feedback_event_id=event.eventId,
                feedback_occurred_at_ns=event.occurredAtNs,
                traversal_session_id=event.traversalSessionId,
                traversal_depth=event.traversalDepth + 1,
            )
            key = (edge.run_id, edge.source_babel_id, edge.target_babel_id)
            previous = selected.get(key)
            if previous is None or (
                edge.feedback_occurred_at_ns,
                edge.feedback_event_id,
            ) < (
                previous.feedback_occurred_at_ns,
                previous.feedback_event_id,
            ):
                selected[key] = edge
    return tuple(selected[key] for key in sorted(selected, key=lambda row: tuple(map(str, row))))


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(record: FeedbackRecord) -> dict[str, object]:
    event = record.event
    if hasattr(event, "model_dump"):
        payload = event.model_dump(mode="json")
    else:
        payload = dict(event)
    return {
        "topic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
        "key": record.key,
        **payload,
    }


def _fsync(path: Path) -> None:
    with path.open("rb") as source:
        os.fsync(source.fileno())


def export_offset_ranges(
    source: BoundedFeedbackSource,
    ranges: Iterable[OffsetRange],
    output_root: str | Path,
    *,
    expected_edges: Iterable[CanonicalExperimentEdge] | None = None,
) -> FeedbackExport:
    """Write exact start-inclusive/end-exclusive ranges as JSONL and Parquet."""
    ordered_ranges = sorted(
        ranges,
        key=lambda item: (
            item.topic_partition.topic,
            item.topic_partition.partition,
            item.start,
        ),
    )
    records = tuple(
        record
        for offset_range in ordered_ranges
        for record in source.records(offset_range)
    )
    rows = [_row(record) for record in records]
    edges = reconstruct_canonical_edges(records)
    if expected_edges is not None:
        expected = tuple(sorted(expected_edges))
        if edges != expected:
            raise EdgeProvenanceMismatch(
                "Kafka reconstruction differs from PostgreSQL edge identity or provenance"
            )
    root = Path(output_root)
    partial = root / "feedback-export.partial"
    final = root / "feedback-export"
    if final.exists():
        raise FileExistsError("feedback export destination already exists")
    if partial.is_dir():
        shutil.rmtree(partial)
    elif partial.exists():
        partial.unlink()
    partial.mkdir(parents=True)
    jsonl = partial / "feedback.jsonl"
    jsonl.write_bytes(b"".join(_canonical_json(row) for row in rows))

    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = partial / "feedback.parquet"
    pq.write_table(pa.Table.from_pylist(rows), parquet, compression="zstd")
    edge_rows = [edge.as_row() for edge in edges]
    edge_jsonl = partial / "edges.jsonl"
    edge_jsonl.write_bytes(b"".join(_canonical_json(row) for row in edge_rows))
    edge_parquet = partial / "edges.parquet"
    edge_schema = pa.schema(
        [
            ("runId", pa.string()),
            ("sourceBabelId", pa.string()),
            ("targetBabelId", pa.string()),
            ("actingCreatorId", pa.string()),
            ("requestId", pa.string()),
            ("feedbackEventId", pa.string()),
            ("feedbackOccurredAtNs", pa.int64()),
            ("traversalSessionId", pa.string()),
            ("traversalDepth", pa.int64()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(edge_rows, schema=edge_schema),
        edge_parquet,
        compression="zstd",
    )
    manifest = partial / "manifest.json"
    manifest.write_bytes(
        _canonical_json(
            {
                "schemaVersion": 1,
                "records": len(records),
                "canonicalEdges": len(edges),
                "ranges": [
                    {
                        "topic": item.topic_partition.topic,
                        "partition": item.topic_partition.partition,
                        "start": item.start,
                        "endExclusive": item.end_exclusive,
                    }
                    for item in ordered_ranges
                ],
                "jsonlSha256": _sha256(jsonl),
                "parquetSha256": _sha256(parquet),
                "edgeJsonlSha256": _sha256(edge_jsonl),
                "edgeParquetSha256": _sha256(edge_parquet),
            }
        )
    )
    for path in (jsonl, parquet, edge_jsonl, edge_parquet, manifest):
        _fsync(path)
    os.replace(partial, final)
    directory_fd = os.open(final.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return FeedbackExport(
        records,
        final / jsonl.name,
        final / parquet.name,
        final / edge_jsonl.name,
        final / edge_parquet.name,
        final / manifest.name,
    )


__all__ = [
    "CanonicalExperimentEdge",
    "EdgeProvenanceMismatch",
    "FeedbackExport",
    "export_offset_ranges",
    "reconstruct_canonical_edges",
]
