from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pyarrow.parquet as pq
import pytest

from babel_online.feedback.bus import InMemoryFeedbackBus, OffsetRange, TopicPartition
from babel_online.feedback.export import (
    CanonicalExperimentEdge,
    EdgeProvenanceMismatch,
    export_offset_ranges,
    reconstruct_canonical_edges,
)

from .test_bus import feedback_event, feedback_event_v2


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
    assert result.publication_files() == {
        "feedback.parquet": result.parquet_path,
        "edges.parquet": result.edge_parquet_path,
    }


def test_v2_edge_reconstruction_uses_earliest_event_time_then_id_not_arrival() -> None:
    later = feedback_event_v2(event_number=9)
    earlier = later.model_copy(
        update={
            "eventId": "00000000-0000-5000-8000-000000000008",
            "requestId": "10000000-0000-5000-8000-000000000008",
            "occurredAtNs": 8,
        }
    )
    excluded = later.model_copy(
        update={
            "eventId": "00000000-0000-5000-8000-000000000007",
            "occurredAtNs": 7,
            "candidateActions": [
                later.candidateActions[0].model_copy(update={"action": "exclude"})
            ],
        }
    )

    first = reconstruct_canonical_edges([later, excluded, earlier])
    second = reconstruct_canonical_edges([earlier, later, excluded])

    assert first == second
    assert len(first) == 1
    assert first[0].feedback_event_id == earlier.eventId
    assert first[0].feedback_occurred_at_ns == 8
    assert first[0].source_babel_id == earlier.sourceBabelId
    assert first[0].target_babel_id == earlier.candidateActions[0].babelId
    assert first[0].traversal_depth == 1


def test_export_rejects_edge_or_provenance_mismatch_before_parquet_success(tmp_path) -> None:
    bus = InMemoryFeedbackBus()
    event = feedback_event_v2(event_number=2)
    bus.publish(key=str(event.creatorId), event=event)
    bounds = OffsetRange(TopicPartition("babel.feedback.v1", 0), 0, 1)
    actual = reconstruct_canonical_edges([event])[0]
    wrong = replace(
        actual, feedback_occurred_at_ns=actual.feedback_occurred_at_ns + 1
    )

    with pytest.raises(EdgeProvenanceMismatch):
        export_offset_ranges(bus, [bounds], tmp_path, expected_edges=[wrong])

    assert not (tmp_path / "feedback-export").exists()
    assert not (tmp_path / "feedback-export.partial").exists()
