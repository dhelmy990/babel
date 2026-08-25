from __future__ import annotations

import hashlib
import json

import pyarrow.parquet as pq

from babel_online.feedback.bus import InMemoryFeedbackBus, OffsetRange, TopicPartition
from babel_online.feedback.export import export_offset_ranges

from .test_bus import feedback_event


def test_export_is_exactly_start_inclusive_end_exclusive(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    for number in range(1, 5):
        event = feedback_event(event_number=number)
        bus.publish(key=str(event.creatorId), event=event)
    bounds = OffsetRange(TopicPartition("babel.feedback.v1", 0), 1, 3)
    stale = tmp_path / "feedback-export.partial"
    stale.mkdir()
    (stale / "interrupted").write_text("not complete")

    result = export_offset_ranges(bus, [bounds], tmp_path)

    assert [record.offset for record in result.records] == [1, 2]
    json_rows = [json.loads(line) for line in result.jsonl_path.read_text().splitlines()]
    assert [row["offset"] for row in json_rows] == [1, 2]
    assert pq.read_table(result.parquet_path).num_rows == 2
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["records"] == 2
    assert manifest["jsonlSha256"] == hashlib.sha256(
        result.jsonl_path.read_bytes()
    ).hexdigest()
    assert manifest["ranges"] == [
        {
            "topic": "babel.feedback.v1",
            "partition": 0,
            "start": 1,
            "endExclusive": 3,
        }
    ]
