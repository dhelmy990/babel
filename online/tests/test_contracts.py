from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from babel_online.contracts import (
    ActivityLogV1,
    CandidateActionV1,
    EmbeddingSpaceV1,
    FeedbackEventV1,
    HnswSnapshotV1,
    ModelManifestV1,
    RecommendationCandidateV1,
    RecommendationRequestV1,
    RecommendationResponseV1,
    RunConfigV1,
    validate_contract,
    contract_schema_documents,
)


RUN_ID = UUID("00000000-0000-5000-8000-000000000001")
MODEL_ID = UUID("00000000-0000-5000-8000-000000000002")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def valid_request() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "requestId": "00000000-0000-5000-8000-000000000010",
        "runId": str(RUN_ID),
        "creatorId": "00000000-0000-5000-8000-000000000020",
        "newBabelId": "00000000-0000-5000-8000-000000000030",
        "newSourceArticleKey": "enwiki:593",
        "title": "Animation notes",
        "text": "A short observable note about animation.",
        "historyBabelIds": [],
        "candidateCount": 3,
    }


def test_run_defaults_and_request_contract_are_closed_and_frozen() -> None:
    run = RunConfigV1(
        schemaVersion=1,
        runId=RUN_ID,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetRevision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        startingModelId=MODEL_ID,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 6, "2026-07": 6},
    )

    assert run.retrievalBackend == "pgvector"
    assert run.creatorCount == 50
    assert run.embeddingDimension == 100
    with pytest.raises((ValidationError, FrozenInstanceError)):
        run.retrievalBackend = "hnswlib"  # type: ignore[misc]

    request = RecommendationRequestV1.model_validate(valid_request())
    assert request.candidateCount == 3
    with pytest.raises(ValidationError):
        RecommendationRequestV1.model_validate(
            {**valid_request(), "hiddenPpr": {"enwiki:593": 1.0}}
        )

    space = EmbeddingSpaceV1(
        schemaVersion=1,
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        dimension=100,
        distance="cosine",
        distilledEncoderArtifact="hf://model/original@abc",
        datasetRevision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        compatibilityVersion="babel-online-v1",
    )
    assert space.dimension == 100


def test_feedback_and_response_expose_only_observable_closed_fields() -> None:
    candidate = RecommendationCandidateV1(
        babelId="00000000-0000-5000-8000-000000000040",
        creatorId="00000000-0000-5000-8000-000000000041",
        sourceArticleKey="enwiki:593",
        rank=1,
        modelScore=0.75,
    )
    response = RecommendationResponseV1(
        schemaVersion=1,
        requestId=valid_request()["requestId"],
        runId=RUN_ID,
        modelId=MODEL_ID,
        modelVersion=0,
        retrievalBackend="pgvector",
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        pgvectorSnapshotSha256="a" * 64,
        backendSnapshotSha256="a" * 64,
        queryVectorSha256="b" * 64,
        candidates=[candidate],
        timingsNs={
            "queue": 1,
            "encode": 2,
            "context": 3,
            "ann": 4,
            "filtering": 5,
            "serialization": 6,
            "serverTotal": 21,
        },
    )
    assert set(response.timingsNs) == {
        "queue", "encode", "context", "ann", "filtering", "serialization",
        "serverTotal",
    }

    feedback = FeedbackEventV1(
        schemaVersion=1,
        eventId="00000000-0000-5000-8000-000000000050",
        requestId=valid_request()["requestId"],
        runId=RUN_ID,
        creatorId=valid_request()["creatorId"],
        newBabelId=valid_request()["newBabelId"],
        newSourceArticleKey="enwiki:593",
        modelId=MODEL_ID,
        modelVersion=0,
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        retrievalBackend="pgvector",
        candidateActions=[
            CandidateActionV1(
                babelId=candidate.babelId,
                sourceArticleKey=candidate.sourceArticleKey,
                rank=1,
                modelScore=0.75,
                action="include",
            )
        ],
        occurredAtNs=123,
    )
    payload = feedback.model_dump(mode="json")
    assert "hiddenPpr" not in payload
    with pytest.raises(ValidationError):
        FeedbackEventV1.model_validate({**payload, "randomDraw": 0.5})


def test_model_snapshot_activity_and_named_validation_are_frozen() -> None:
    space = EmbeddingSpaceV1(
        schemaVersion=1,
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        dimension=100,
        distance="cosine",
        distilledEncoderArtifact="hf://model/original@abc",
        datasetRevision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        compatibilityVersion="babel-online-v1",
    )
    original = ModelManifestV1(
        schemaVersion=1,
        modelId=MODEL_ID,
        label="Friday demo original",
        parentModelId=None,
        producingRunId=None,
        encoderRepo="dhelmy990/babel-distilled-qwen",
        encoderRevision="c" * 40,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetRevision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        environmentSequence=["2026-06", "2026-07"],
        trainingExamples=0,
        checkpointPath="models/original/model.safetensors",
        checkpointSha256="d" * 64,
        embeddingSpace=space,
        immutable=True,
    )
    assert validate_contract("model-manifest-v1", original.model_dump(mode="json")) == original

    snapshot = HnswSnapshotV1(
        schemaVersion=1,
        runId=RUN_ID,
        servingModelId=MODEL_ID,
        servingModelVersion=0,
        embeddingSpaceId=space.embeddingSpaceId,
        pgvectorSnapshotSha256="a" * 64,
        orderedBabelIds=["00000000-0000-5000-8000-000000000040"],
        rowCount=1,
        vectorSha256="b" * 64,
    )
    assert snapshot.efSearch == 100

    log = ActivityLogV1(
        schemaVersion=1,
        runId=RUN_ID,
        sequence=1,
        occurredAtNs=1,
        level="info",
        component="serving",
        event="recommendation_completed",
        message="Recommendation completed.",
        metrics={"serverTotalNs": 21},
    )
    assert "hiddenProfile" not in log.model_dump()


def test_checked_in_json_schemas_match_typed_contracts() -> None:
    schemas = contract_schema_documents()

    assert set(schemas) == {
        "experiment-run-v1",
        "recommendation-request-v1",
        "recommendation-response-v1",
        "feedback-event-v1",
        "activity-log-v1",
        "model-manifest-v1",
        "embedding-space-v1",
        "hnsw-snapshot-v1",
    }
    for name, schema in schemas.items():
        checked_in = json.loads(
            (REPOSITORY_ROOT / "schemas" / "online" / f"{name}.json").read_text()
        )
        assert checked_in == schema
        assert checked_in["additionalProperties"] is False
