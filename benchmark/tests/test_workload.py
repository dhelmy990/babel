from __future__ import annotations

import json
from uuid import uuid4

import pytest

from babel_benchmark.workload import (
    WorkloadTraceCollector,
    freeze_workload,
    load_frozen_workload,
    load_workload_documents,
    materialize_condition_workload,
    normalize_workload_document,
)
from babel_online.contracts import (
    CandidateActionV1,
    FeedbackEventV2,
    RecommendationRequestV2,
)
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule
from babel_online.simulation.walk import WalkRollEvidence


def _captured_workload():
    run_id, creator_id = uuid4(), uuid4()
    schedule = deterministic_schedule(run_id, [ScheduledWork(
        creator_id=creator_id,
        creator_event_number=0,
        period="2026-06",
        source_article_key="enwiki:1",
        root_babel_id=uuid4(),
    )])
    session = schedule[0]
    request = RecommendationRequestV2(
        schemaVersion=2,
        requestId=uuid4(),
        runId=run_id,
        creatorId=creator_id,
        sourceBabelId=session.root_babel_id,
        sourceArticleKey="enwiki:1",
        traversalSessionId=session.traversal_session_id,
        parentRequestId=None,
        traversalDepth=0,
        title="Root",
        text="Root lead",
        historyBabelIds=[],
        candidateCount=10,
    )
    action = CandidateActionV1(
        babelId=uuid4(), sourceArticleKey="enwiki:2", rank=1,
        modelScore=0.9, action="include",
    )
    feedback = FeedbackEventV2(
        schemaVersion=2,
        eventId=uuid4(),
        requestId=request.requestId,
        runId=run_id,
        creatorId=creator_id,
        sourceBabelId=request.sourceBabelId,
        sourceArticleKey=request.sourceArticleKey,
        traversalSessionId=request.traversalSessionId,
        parentRequestId=None,
        traversalDepth=0,
        modelId=uuid4(),
        modelVersion=0,
        embeddingSpaceId=uuid4(),
        retrievalBackend="pgvector",
        sourceVectorOrigin="qwen_encode",
        candidateActions=[action],
        occurredAtNs=123456,
    )
    rolls = (
        WalkRollEvidence(0, "start", request.sourceBabelId, None, None, 0,
                         0.2, 0.4, True, "started"),
        WalkRollEvidence(1, "continuation", request.sourceBabelId,
                         action.babelId, 1, 0, 0.6, 0.4, False,
                         "continuation_skipped"),
    )
    collector = WorkloadTraceCollector(schedule=schedule, target_rps=5.0)
    collector.record_request(request)
    collector.record_feedback(feedback)
    collector.record_rolls(session.traversal_session_id, rolls)
    return collector, run_id, request, feedback


def _changed_paths(left, right, path=()):
    if isinstance(left, dict) and isinstance(right, dict):
        assert left.keys() == right.keys()
        return {
            changed
            for key in left
            for changed in _changed_paths(left[key], right[key], (*path, key))
        }
    if isinstance(left, list) and isinstance(right, list):
        assert len(left) == len(right)
        return {
            changed
            for index, (left_item, right_item) in enumerate(zip(left, right))
            for changed in _changed_paths(left_item, right_item, (*path, index))
        }
    return {path} if left != right else set()


def test_freeze_records_actual_topology_independent_semantics(tmp_path) -> None:
    collector, _, request, feedback = _captured_workload()

    frozen = freeze_workload(collector, tmp_path / "frozen")
    loaded = load_frozen_workload(frozen.path)

    assert loaded.identity == frozen.identity
    assert frozen.receipt.request_count == 1
    assert frozen.receipt.creator_schedule_scope == "creator_local"
    assert frozen.receipt.start_probability == 0.4
    assert frozen.receipt.continuation_probability == 0.4
    assert frozen.receipt.independent_draw_streams is True
    assert frozen.receipt.start_draws_sha256 != frozen.receipt.continuation_draws_sha256
    request_row = json.loads((frozen.path / "requests.template.jsonl").read_text())
    feedback_row = json.loads((frozen.path / "feedback.template.jsonl").read_text())
    assert request_row["request"] == request.model_dump(mode="json")
    assert request_row["scheduleOffsetNs"] == 0
    assert feedback_row == feedback.model_dump(mode="json")


def test_condition_materialization_changes_only_run_id_and_preserves_identity(tmp_path) -> None:
    collector, source_run_id, request, _ = _captured_workload()
    frozen = freeze_workload(collector, tmp_path / "frozen")
    condition_run_id = uuid4()

    condition = materialize_condition_workload(
        frozen, run_id=condition_run_id, output_path=tmp_path / "condition"
    )

    assert condition.identity == frozen.identity
    assert condition.run_id == condition_run_id
    replay = load_workload_documents(condition.path)
    assert replay["requests.template.jsonl"][0]["request"]["runId"] == str(
        condition_run_id
    )
    assert replay["feedback.template.jsonl"][0]["requestId"] == str(
        request.requestId
    )
    for filename in frozen.payload_files:
        originals = [
            json.loads(line)
            for line in (frozen.path / filename).read_text().splitlines()
        ]
        rebound = [
            json.loads(line)
            for line in (condition.path / filename).read_text().splitlines()
        ]
        assert [normalize_workload_document(row) for row in originals] == [
            normalize_workload_document(row) for row in rebound
        ]
        changed = {
            path
            for original, condition_row in zip(originals, rebound)
            for path in _changed_paths(original, condition_row)
        }
        assert changed
        assert all(path[-1] == "runId" for path in changed)
        assert all(str(source_run_id) not in json.dumps(row) for row in rebound)
    rebound_request = json.loads(
        (condition.path / "requests.template.jsonl").read_text()
    )["request"]
    assert rebound_request["runId"] == str(condition_run_id)
    assert rebound_request["requestId"] == str(request.requestId)


def test_load_rejects_payload_tampering(tmp_path) -> None:
    collector, *_ = _captured_workload()
    frozen = freeze_workload(collector, tmp_path / "frozen")
    path = frozen.path / "event-mix.jsonl"
    path.write_text(path.read_text() + "{}\n")

    with pytest.raises(ValueError, match="checksum"):
        load_frozen_workload(frozen.path)
