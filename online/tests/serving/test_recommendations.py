from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from babel_online.contracts import ModelManifestV1, RecommendationResponseV1
from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.item_tower import ItemTower
from babel_online.model.registry import ModelRegistry
from babel_online.observable import CreatedBabel, VectorRecord
from babel_online.serving.app import create_app
from babel_online.serving.state import ServingState


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPOSITORY_ROOT / "fixtures/online/tiny"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def serving_state() -> tuple[ServingState, ModelRegistry, set[str], set[str]]:
    model = ModelManifestV1.model_validate_json((FIXTURE / "original-model.json").read_text())
    registry = ModelRegistry()
    registry.register_original(model)
    tower = ItemTower(model.embeddingSpace)
    created = [
        CreatedBabel.model_validate(row)
        for row in read_jsonl(FIXTURE / "observable/created-babels.jsonl")
    ]
    records = [
        VectorRecord(
            babel=row,
            catalogContentHash="a" * 64,
            embeddingSpaceId=model.embeddingSpace.embeddingSpaceId,
            servingModelId=model.modelId,
            materializedModelVersion=0,
            vector=tuple(float(value) for value in tower.encode(row.text)),
        )
        for row in created
    ]
    materialized = MaterializedServingState(
        run_id=created[0].runId,
        model_id=model.modelId,
        model_version=0,
        embedding_space_id=model.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    index = InMemoryCreatedBabelIndex(records)
    state = ServingState(
        registry=registry,
        selected_model_id=model.modelId,
        materialized_state=materialized,
        candidate_index=index,
        vector_records=records,
    )
    return (
        state,
        registry,
        {str(row.babelId) for row in created},
        {row.sourceArticleKey for row in created},
    )


def test_post_recommends_only_current_run_created_babels_with_timings() -> None:
    state, _registry, created_ids, created_sources = serving_state()
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    client = TestClient(create_app(state))

    http = client.post("/api/v1/recommendations", json=request)

    assert http.status_code == 200
    response = RecommendationResponseV1.model_validate(http.json())
    assert response.modelId == UUID("00000000-0000-5000-8000-000000000002")
    assert response.modelVersion == 0
    assert response.retrievalBackend == "pgvector"
    assert {str(row.babelId) for row in response.candidates} <= created_ids
    assert all(row.creatorId != UUID(request["creatorId"]) for row in response.candidates)
    assert {row.sourceArticleKey for row in response.candidates} <= created_sources
    assert "enwiki:5739" not in {row.sourceArticleKey for row in response.candidates}
    assert response.timingsNs["serverTotal"] >= sum(
        duration
        for stage, duration in response.timingsNs.items()
        if stage != "serverTotal"
    )
    assert "Server-Timing" in http.headers


def test_post_rejects_duplicate_source_for_same_creator() -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    request["newSourceArticleKey"] = "enwiki:593"

    http = TestClient(create_app(state)).post("/api/v1/recommendations", json=request)

    assert http.status_code == 409


def test_apply_sync_selects_child_without_replacing_original() -> None:
    state, registry, _created_ids, _created_sources = serving_state()
    original = registry.original
    child_data = original.model_dump(mode="json")
    child_data.update(
        {
            "modelId": "00000000-0000-5000-8000-000000000099",
            "label": "Selected child",
            "parentModelId": str(original.modelId),
            "producingRunId": "00000000-0000-5000-8000-000000000001",
            "trainingExamples": 12,
            "checkpointPath": "models/child/model.safetensors",
            "checkpointSha256": "e" * 64,
        }
    )
    child = ModelManifestV1.model_validate(child_data)
    registry.register_child(child)
    created = [
        CreatedBabel.model_validate(row)
        for row in read_jsonl(FIXTURE / "observable/created-babels.jsonl")
    ]
    tower = ItemTower(child.embeddingSpace)
    records = [
        VectorRecord(
            babel=row,
            catalogContentHash="a" * 64,
            embeddingSpaceId=child.embeddingSpace.embeddingSpaceId,
            servingModelId=child.modelId,
            materializedModelVersion=1,
            vector=tuple(float(value) for value in tower.encode(row.text)),
        )
        for row in created
    ]
    materialized = MaterializedServingState(
        run_id=created[0].runId,
        model_id=child.modelId,
        model_version=1,
        embedding_space_id=child.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="c" * 64,
        backend_snapshot_sha256="c" * 64,
    )
    state.apply_sync(
        selected_model_id=child.modelId,
        materialized_state=materialized,
        candidate_index=InMemoryCreatedBabelIndex(records),
        vector_records=records,
    )

    assert state.snapshot().model.modelId == child.modelId
    assert state.snapshot().materialized_state.model_version == 1
    assert registry.original.modelId == original.modelId
