from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import hashlib
from uuid import UUID

import pytest
import babel_online.contracts as contracts
from pydantic import ValidationError

from babel_online.contracts import (
    ActivityLogV1,
    CandidateActionV1,
    EmbeddingSpaceV1,
    FeedbackEventV1,
    FeedbackEventV2,
    HnswSnapshotV1,
    ModelManifestV1,
    RecommendationActivityV1,
    RecommendationCandidateV1,
    RecommendationRequestV1,
    RecommendationRequestV2,
    RecommendationResponseV1,
    RecommendationResponseV2,
    RunConfigV1,
    RunConfigV2,
    validate_contract,
    contract_schema_documents,
    canonical_pgvector_snapshot_sha256,
    canonical_vector_sha256,
)


def test_real_model_manifest_v2_closes_the_trained_qwen_identity() -> None:
    manifest_type = getattr(contracts, "ModelManifestV2", None)
    assert manifest_type is not None, "Task 6 must define the real-model manifest"
    document = {
        "schemaVersion": 2,
        "modelId": "2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67",
        "label": "2016 interview Qwen original",
        "parentModelId": None,
        "producingRunId": None,
        "encoderRepo": "dhelmy990/babel-qwen-navigation-2016-interview",
        "encoderRevision": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "artifactPath": "artifacts/" + "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
        "artifactId": "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
        "artifactManifestSha256": "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f",
        "checkpointTreeSha256": "ddf8721cc38abc9f61b8738d6092e4f6c9542c3c533fc6a81677b307533edcff",
        "baseModelId": "Qwen/Qwen3-Embedding-0.6B",
        "baseModelRevision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "tokenizerRevision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "datasetRepo": "dhelmy990/babel-wikipedia-experiment",
        "datasetConfig": "distillation_2016_interview",
        "datasetRevision": "b440e98b04ab77afed7caf0455eca3189235fc3b",
        "datasetManifestSha256": "33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09",
        "trainingSourceRevision": "92f3ac697d78eb827d75b033df92dcbed887def7",
        "adapterSha256": "4792009bfdaa9df25e3cd79f634ddfa081dc3c620828bda478be5db2fd7b8921",
        "projectionSha256": "e156701da777fbb37e999c7d897f09cdd1993cd5c9d740aaafcfdeb6395d3ddb",
        "validationSha256": "e4b76f00f65f4de0165e4eb47c652531295b4718d4d7bcc5008c5945a86f9e13",
        "trainingExamples": 50_000,
        "embeddingSpace": {
            "schemaVersion": 1,
            "embeddingSpaceId": "f3665769-b470-5228-8df4-08004e252aa4",
            "dimension": 100,
            "distance": "cosine",
            "distilledEncoderArtifact": "hf://dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e/artifacts/3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
            "datasetRevision": "b440e98b04ab77afed7caf0455eca3189235fc3b",
            "compatibilityVersion": "babel-qwen-100d-v1",
        },
        "acceptance": "real_50k_qwen",
        "immutable": True,
    }

    manifest = manifest_type.model_validate(document)

    assert manifest.parentModelId is None
    assert manifest.adapterSha256 == document["adapterSha256"]
    with pytest.raises(ValidationError):
        manifest_type.model_validate({**document, "artifactId": "0" * 64})
from babel_online.config import default_run_config


RUN_ID = UUID("00000000-0000-5000-8000-000000000001")
MODEL_ID = UUID("00000000-0000-5000-8000-000000000002")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_default_experiment_config_pins_the_connected_crosswalk_bundle() -> None:
    run = default_run_config(
        run_id=RUN_ID,
        dataset_revision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        starting_model_id=MODEL_ID,
    )

    assert run.datasetConfig == "demo_crosswalk"


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
        datasetConfig="demo_catalog_2026_06",
        datasetRevision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        startingModelId=MODEL_ID,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 6, "2026-07": 6},
        runSeed=7,
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
        datasetRevision="0d1ab2c7f0e2295682288fcf10077d2d776bf559",
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
    with pytest.raises(ValidationError):
        RecommendationResponseV1.model_validate(
            {
                **response.model_dump(mode="json"),
                "candidates": [
                    candidate.model_dump(mode="json"),
                    {**candidate.model_dump(mode="json"), "rank": 2},
                ],
            }
        )

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
        details=RecommendationActivityV1(
            kind="recommendation",
            creatorId="00000000-0000-5000-8000-000000000020",
            newBabelId="00000000-0000-5000-8000-000000000030",
            newBabelTitle="Compiler notes",
            candidateBabelIds=["00000000-0000-5000-8000-000000000040"],
            includeBabelIds=["00000000-0000-5000-8000-000000000040"],
            excludeBabelIds=[],
            ignoreBabelIds=[],
            acceptedEdgeCount=1,
            modelId=MODEL_ID,
            modelVersion=0,
        ),
    )
    payload = log.model_dump(mode="json")
    assert payload["details"]["acceptedEdgeCount"] == 1
    with pytest.raises(ValidationError):
        ActivityLogV1.model_validate(
            {**payload, "details": {**payload["details"], "hiddenPpr": {}}}
        )


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
        "experiment-run-v2",
        "recommendation-request-v2",
        "recommendation-response-v2",
        "feedback-event-v2",
    }
    for name, schema in schemas.items():
        checked_in = json.loads(
            (REPOSITORY_ROOT / "schemas" / "online" / f"{name}.json").read_text()
        )
        assert checked_in == schema
        assert checked_in["additionalProperties"] is False


def valid_request_v2(*, depth: int = 0) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "requestId": "00000000-0000-5000-8000-000000000011",
        "runId": str(RUN_ID),
        "creatorId": "00000000-0000-5000-8000-000000000020",
        "sourceBabelId": "00000000-0000-5000-8000-000000000030",
        "sourceArticleKey": "enwiki:593",
        "traversalSessionId": "00000000-0000-5000-8000-000000000031",
        "parentRequestId": None if depth == 0 else "00000000-0000-5000-8000-000000000010",
        "traversalDepth": depth,
        "title": "Animation notes" if depth == 0 else None,
        "text": "A short observable note about animation." if depth == 0 else None,
        "historyBabelIds": [],
        "candidateCount": 3,
    }


def test_scaled_v2_contracts_close_walk_identity_and_preserve_v1() -> None:
    run = RunConfigV2(
        schemaVersion=2,
        runId=RUN_ID,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetConfig="crosswalk_2026_06_07",
        datasetRevision="0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        startingModelId=MODEL_ID,
        creatorCount=50,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
        runSeed=7,
        sourceArticlesPerMonth=5_000,
        targetCreatedBabels=10_000,
        concurrentUsers=50,
    )
    assert run.recommendationStartProbability == 0.4
    assert run.continuationProbability == 0.4
    assert run.maximumTraversalDepth == 2
    assert run.maximumRequestsPerTraversal == 10
    assert run.interleaveCreationAndRecommendations is True
    disabled_interleave = RunConfigV2.model_validate(
        {**run.model_dump(mode="json"), "interleaveCreationAndRecommendations": False}
    )
    assert disabled_interleave.interleaveCreationAndRecommendations is False
    with pytest.raises(ValidationError):
        RunConfigV2.model_validate(
            {**run.model_dump(mode="json"), "targetCreatedBabels": 9_999}
        )

    root = RecommendationRequestV2.model_validate(valid_request_v2(depth=0))
    existing = RecommendationRequestV2.model_validate(valid_request_v2(depth=1))
    assert root.sourceBabelId == existing.sourceBabelId
    with pytest.raises(ValidationError):
        RecommendationRequestV2.model_validate(
            {**valid_request_v2(depth=1), "parentRequestId": None}
        )
    with pytest.raises(ValidationError):
        RecommendationRequestV2.model_validate(
            {**valid_request_v2(depth=0), "hiddenGraphScore": 1.0}
        )

    candidate = RecommendationCandidateV1(
        babelId="00000000-0000-5000-8000-000000000040",
        creatorId="00000000-0000-5000-8000-000000000041",
        sourceArticleKey="enwiki:594",
        rank=1,
        modelScore=0.75,
    )
    response = RecommendationResponseV2(
        schemaVersion=2,
        requestId=root.requestId,
        runId=RUN_ID,
        modelId=MODEL_ID,
        modelVersion=0,
        retrievalBackend="pgvector",
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        pgvectorSnapshotSha256="a" * 64,
        backendSnapshotSha256="a" * 64,
        queryVectorSha256="b" * 64,
        sourceVectorOrigin="qwen_encode",
        candidates=[candidate],
        timingsNs={
            "queue": 1, "encode": 2, "context": 3, "ann": 4,
            "filtering": 5, "serialization": 6, "serverTotal": 21,
        },
    )
    feedback = FeedbackEventV2(
        schemaVersion=2,
        eventId="00000000-0000-5000-8000-000000000050",
        requestId=root.requestId,
        runId=RUN_ID,
        creatorId=root.creatorId,
        sourceBabelId=root.sourceBabelId,
        sourceArticleKey=root.sourceArticleKey,
        traversalSessionId=root.traversalSessionId,
        parentRequestId=root.parentRequestId,
        traversalDepth=root.traversalDepth,
        modelId=response.modelId,
        modelVersion=response.modelVersion,
        embeddingSpaceId=response.embeddingSpaceId,
        retrievalBackend=response.retrievalBackend,
        candidateActions=[CandidateActionV1(
            babelId=candidate.babelId,
            sourceArticleKey=candidate.sourceArticleKey,
            rank=1,
            modelScore=0.75,
            action="include",
        )],
        occurredAtNs=1_725_000_000_000_000_123,
    )
    assert response.sourceVectorOrigin == "qwen_encode"
    assert feedback.occurredAtNs == 1_725_000_000_000_000_123
    assert RecommendationRequestV1.model_validate(valid_request()).model_dump(mode="json") == valid_request()


def test_vector_and_pgvector_snapshot_checksums_are_canonical() -> None:
    first = "00000000-0000-5000-8000-000000000001"
    second = "00000000-0000-5000-8000-000000000002"
    x = [1.0] + [0.0] * 99
    y = [0.0, 2.0] + [0.0] * 98
    digest = canonical_vector_sha256({second: y, first: x})
    expected = hashlib.sha256(
        bytes.fromhex("0000803f" + "00000000" * 99)
        + bytes.fromhex("00000000" + "0000803f" + "00000000" * 98)
    ).hexdigest()
    assert digest == expected

    rows = [
        {
            "babelId": first,
            "creatorId": "00000000-0000-5000-8000-000000000101",
            "sourceArticleKey": "enwiki:593",
            "catalogContentHash": "a" * 64,
            "embeddingSpaceId": "00000000-0000-5000-8000-000000000003",
            "servingModelId": "00000000-0000-5000-8000-000000000002",
            "materializedModelVersion": 0,
            "vectorSha256": hashlib.sha256(bytes.fromhex("0000803f" + "00000000" * 99)).hexdigest(),
        }
    ]
    assert canonical_pgvector_snapshot_sha256(rows) == canonical_pgvector_snapshot_sha256(
        list(reversed(rows))
    )
