from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import UUID

import numpy as np
from fastapi.testclient import TestClient

from babel_online.contracts import (
    ModelManifestV1,
    RecommendationResponseV1,
    RecommendationResponseV2,
)
from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.item_tower import ItemTower
from babel_online.model.registry import ModelRegistry
from babel_online.model.source_vector_cache import SourceVectorResolver
from babel_online.observable import CreatedBabel, VectorRecord, reject_hidden_fields
from babel_online.serving.app import create_app
from babel_online.serving.state import ServingState


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPOSITORY_ROOT / "fixtures/online/tiny"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def serving_state(
    *, context_tower=None
) -> tuple[ServingState, ModelRegistry, set[str], set[str]]:
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
        context_tower=context_tower,
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
    reject_hidden_fields(http.json())
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
    assert http.headers["X-Babel-Encoder-Mode"] == "fixture"
    assert http.headers["X-Babel-Encoder-Device"] == "cpu"
    assert http.headers["X-Babel-Encoder-Batch-Size"] == "1"
    assert len(http.headers["X-Babel-Model-Manifest-Sha256"]) == 64


def test_serving_health_reports_last_valid_model_without_trainer_dependency() -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    client = TestClient(create_app(state))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "modelId": "00000000-0000-5000-8000-000000000002",
        "modelVersion": 0,
    }


def test_serving_snapshot_uses_an_activated_online_context_head() -> None:
    class ActivatedContext:
        def __call__(self, *, new, history):
            vector = np.zeros(100, dtype=np.float32)
            vector[17] = 1.0
            return vector

    state, _registry, _created_ids, _created_sources = serving_state(
        context_tower=ActivatedContext()
    )

    query = state.snapshot().context_tower(
        new=np.eye(100, dtype=np.float32)[0],
        history=np.empty((0, 100), dtype=np.float32),
    )

    assert query[17] == 1.0


def test_fixture_smoke_also_keeps_one_long_lived_item_tower() -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    tower = state.snapshot().item_tower
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    client = TestClient(create_app(state))

    assert client.post("/api/v1/recommendations", json=request).status_code == 200
    request["requestId"] = "00000000-0000-5000-8000-000000000402"
    assert client.post("/api/v1/recommendations", json=request).status_code == 200
    assert state.snapshot().item_tower is tower


def test_post_serializes_the_wire_payload_once(monkeypatch) -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    calls = 0
    original = RecommendationResponseV1.model_dump_json

    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RecommendationResponseV1, "model_dump_json", counted)

    http = TestClient(create_app(state)).post("/api/v1/recommendations", json=request)

    assert http.status_code == 200
    assert calls == 1


def test_post_includes_wire_json_encoding_in_serialization_timing(monkeypatch) -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    original = RecommendationResponseV1.model_dump_json

    def deliberately_slow_json_encoding(self, *args, **kwargs):
        time.sleep(0.02)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        RecommendationResponseV1,
        "model_dump_json",
        deliberately_slow_json_encoding,
    )

    http = TestClient(create_app(state)).post(
        "/api/v1/recommendations", json=request
    )

    assert http.status_code == 200
    timings = RecommendationResponseV1.model_validate(http.json()).timingsNs
    assert timings["serialization"] >= 20_000_000
    assert timings["serverTotal"] >= sum(
        value for name, value in timings.items() if name != "serverTotal"
    )
    assert http.headers["X-Babel-Serialization-Measurement"] == (
        "wire-json-template-with-timing-token-patch"
    )


def test_post_rejects_duplicate_source_for_same_creator() -> None:
    state, _registry, _created_ids, _created_sources = serving_state()
    request = json.loads((FIXTURE / "observable/request.json").read_text())
    request["newSourceArticleKey"] = "enwiki:593"

    http = TestClient(create_app(state)).post("/api/v1/recommendations", json=request)

    assert http.status_code == 409


def test_apply_sync_selects_child_without_replacing_original() -> None:
    state, registry, _created_ids, _created_sources = serving_state()
    held_snapshot = state.snapshot()
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
    assert held_snapshot.model.modelId == original.modelId
    assert held_snapshot.materialized_state.model_version == 0
    assert registry.original.modelId == original.modelId


def test_v2_resolves_root_and_existing_sources_and_filters_source_itself(
    real_model_manifest, accepted_qwen_factory
) -> None:
    run_id = UUID("00000000-0000-5000-8000-000000000001")
    acting_creator = UUID("00000000-0000-5000-8000-000000000090")
    owner = UUID("00000000-0000-5000-8000-000000000091")
    source = CreatedBabel(
        babelId=UUID("00000000-0000-5000-8000-000000000092"),
        runId=run_id,
        creatorId=owner,
        sourceArticleKey="enwiki:92",
        title="Existing source",
        text="Existing source lead",
        createdAtNs=1,
    )
    other = CreatedBabel(
        babelId=UUID("00000000-0000-5000-8000-000000000093"),
        runId=run_id,
        creatorId=owner,
        sourceArticleKey="enwiki:93",
        title="Other source",
        text="Other source lead",
        createdAtNs=2,
    )
    encoder = accepted_qwen_factory()
    vectors = encoder.encode([source.title + "\n\n" + source.text, other.title + "\n\n" + other.text])
    records = [
        VectorRecord(
            babel=babel,
            catalogContentHash="a" * 64,
            embeddingSpaceId=real_model_manifest.embeddingSpace.embeddingSpaceId,
            servingModelId=real_model_manifest.modelId,
            materializedModelVersion=0,
            vector=tuple(float(value) for value in vector),
        )
        for babel, vector in zip([source, other], vectors, strict=True)
    ]
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    materialized = MaterializedServingState(
        run_id=run_id,
        model_id=real_model_manifest.modelId,
        model_version=0,
        embedding_space_id=real_model_manifest.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    state = ServingState(
        registry=registry,
        selected_model_id=real_model_manifest.modelId,
        materialized_state=materialized,
        candidate_index=InMemoryCreatedBabelIndex(records),
        vector_records=records,
        qwen_encoder=encoder,
        scale_run=True,
    )
    stored = {source.babelId: vectors[0], other.babelId: vectors[1]}
    resolver = SourceVectorResolver(
        encoder, load_active=lambda key: stored[key.babel_id], capacity=8
    )
    client = TestClient(create_app(state, source_vector_resolver=resolver))
    base = {
        "schemaVersion": 2,
        "requestId": "00000000-0000-5000-8000-000000000094",
        "runId": str(run_id),
        "creatorId": str(acting_creator),
        "sourceBabelId": str(source.babelId),
        "sourceArticleKey": source.sourceArticleKey,
        "traversalSessionId": "00000000-0000-5000-8000-000000000095",
        "parentRequestId": "00000000-0000-5000-8000-000000000096",
        "traversalDepth": 1,
        "title": None,
        "text": None,
        "historyBabelIds": [],
        "candidateCount": 2,
    }

    first = client.post("/api/v2/recommendations", json=base)
    second = client.post(
        "/api/v2/recommendations",
        json={**base, "requestId": "00000000-0000-5000-8000-000000000097"},
    )
    root = client.post(
        "/api/v2/recommendations",
        json={
            **base,
            "requestId": "00000000-0000-5000-8000-000000000098",
            "sourceBabelId": "00000000-0000-5000-8000-000000000099",
            "sourceArticleKey": "enwiki:99",
            "parentRequestId": None,
            "traversalDepth": 0,
            "title": "New root",
            "text": "New root lead",
        },
    )
    spoofed_existing = client.post(
        "/api/v2/recommendations",
        json={**base, "sourceArticleKey": "enwiki:999"},
    )
    duplicate_root = client.post(
        "/api/v2/recommendations",
        json={
            **base,
            "requestId": "00000000-0000-5000-8000-000000000100",
            "creatorId": str(owner),
            "sourceBabelId": "00000000-0000-5000-8000-000000000100",
            "parentRequestId": None,
            "traversalDepth": 0,
            "title": "Duplicate",
            "text": "Duplicate source",
        },
    )

    assert first.status_code == second.status_code == root.status_code == 200
    first_response = RecommendationResponseV2.model_validate(first.json())
    second_response = RecommendationResponseV2.model_validate(second.json())
    root_response = RecommendationResponseV2.model_validate(root.json())
    assert first_response.sourceVectorOrigin == "pgvector_load"
    assert second_response.sourceVectorOrigin == "cache_hit"
    assert root_response.sourceVectorOrigin == "qwen_encode"
    assert source.babelId not in {row.babelId for row in first_response.candidates}
    assert spoofed_existing.status_code == 422
    assert duplicate_root.status_code == 409
    assert resolver.telemetry().as_dict() == {
        "qwen_encode": 1,
        "cache_hit": 1,
        "pgvector_load": 1,
        "evictions": 0,
    }
