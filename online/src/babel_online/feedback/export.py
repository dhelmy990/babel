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

from .bus import FeedbackRecord, OffsetRange


class BoundedFeedbackSource(Protocol):
    def records(self, offset_range: OffsetRange) -> tuple[FeedbackRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class FeedbackExport:
    records: tuple[FeedbackRecord, ...]
    jsonl_path: Path
    parquet_path: Path
    manifest_path: Path


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
    manifest = partial / "manifest.json"
    manifest.write_bytes(
        _canonical_json(
            {
                "schemaVersion": 1,
                "records": len(records),
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
            }
        )
    )
    for path in (jsonl, parquet, manifest):
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
        final / manifest.name,
    )


__all__ = ["FeedbackExport", "export_offset_ranges"]
