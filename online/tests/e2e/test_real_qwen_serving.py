from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from fastapi.testclient import TestClient

from babel_online.contracts import (
    DistilledServingArtifactV1,
    EmbeddingSpaceV1,
    ModelManifestV2,
    RecommendationResponseV1,
)
from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.artifact import (
    build_real_original_manifest,
    model_manifest_sha256,
)
from babel_online.model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
    DistilledArtifactV1,
)
from babel_online.model.qwen_encoder import Qwen100Encoder
from babel_online.model.registry import ModelRegistry
from babel_online.observable import CreatedBabel, VectorRecord
from babel_online.serving.app import create_app
from babel_online.serving.state import ServingState


MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")
RUN_ID = UUID("00000000-0000-5000-8000-000000000001")


def _contract() -> DistilledServingArtifactV1:
    return DistilledServingArtifactV1(
        schemaVersion=1,
        artifactRepo=REAL_MODEL_REPO,
        artifactRevision=REAL_ARTIFACT_REVISION,
        artifactPath=f"artifacts/{REAL_ARTIFACT_ID}",
        artifactId=REAL_ARTIFACT_ID,
        artifactSchema="babel-distillation-2016-interview-v1",
        baseModelId="Qwen/Qwen3-Embedding-0.6B",
        baseModelRevision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        tokenizerRevision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetConfig="distillation_2016_interview",
        datasetRevision="b440e98b04ab77afed7caf0455eca3189235fc3b",
        trainingSourceRevision="92f3ac697d78eb827d75b033df92dcbed887def7",
        semanticsAuthority="pinned_training_source",
        inputFormat="canonical_title\\n\\nlead_text",
        maxLength=384,
        paddingSide="left",
        pooling="last_non_padding_token",
        projectionInputDimension=1024,
        embeddingDimension=100,
        normalization="l2",
        adapterSha256="4792009bfdaa9df25e3cd79f634ddfa081dc3c620828bda478be5db2fd7b8921",
        projectionSha256="e156701da777fbb37e999c7d897f09cdd1993cd5c9d740aaafcfdeb6395d3ddb",
        validationSha256="e4b76f00f65f4de0165e4eb47c652531295b4718d4d7bcc5008c5945a86f9e13",
        immutable=True,
    )


def _manifest() -> ModelManifestV2:
    contract = _contract()
    return ModelManifestV2(
        schemaVersion=2,
        modelId=MODEL_ID,
        label="2016 interview Qwen original",
        parentModelId=None,
        producingRunId=None,
        encoderRepo=contract.artifactRepo,
        encoderRevision=contract.artifactRevision,
        artifactPath=contract.artifactPath,
        artifactId=contract.artifactId,
        artifactManifestSha256="5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f",
        checkpointTreeSha256="ddf8721cc38abc9f61b8738d6092e4f6c9542c3c533fc6a81677b307533edcff",
        baseModelId=contract.baseModelId,
        baseModelRevision=contract.baseModelRevision,
        tokenizerRevision=contract.tokenizerRevision,
        datasetRepo=contract.datasetRepo,
        datasetConfig=contract.datasetConfig,
        datasetRevision=contract.datasetRevision,
        datasetManifestSha256="33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09",
        trainingSourceRevision=contract.trainingSourceRevision,
        adapterSha256=contract.adapterSha256,
        projectionSha256=contract.projectionSha256,
        validationSha256=contract.validationSha256,
        trainingExamples=50_000,
        embeddingSpace=EmbeddingSpaceV1(
            schemaVersion=1,
            embeddingSpaceId=SPACE_ID,
            dimension=100,
            distance="cosine",
            distilledEncoderArtifact=(
                f"hf://{contract.artifactRepo}@{contract.artifactRevision}/"
                f"{contract.artifactPath}"
            ),
            datasetRevision=contract.datasetRevision,
            compatibilityVersion="babel-qwen-100d-v1",
        ),
        acceptance="real_50k_qwen",
        immutable=True,
    )


class _StubAcceptedQwen(Qwen100Encoder):
    """No-Torch contract double; only the token-gated test may claim real inference."""

    def __init__(self) -> None:
        self.contract = _contract()
        self.device = "cpu"
        self.cache_identity = (
            f"hf://{self.contract.baseModelId}@{self.contract.baseModelRevision}"
        )
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            row = np.frombuffer((digest * 4)[:100], dtype=np.uint8).astype(np.float32)
            row -= 127.5
            rows.append(row / np.linalg.norm(row))
        return np.asarray(rows, dtype=np.float32)


def _serving_state(encoder: Qwen100Encoder) -> ServingState:
    model = _manifest()
    registry = ModelRegistry()
    registry.register_real_original(model)
    babel = CreatedBabel(
        babelId=UUID("00000000-0000-5000-8000-000000000202"),
        runId=RUN_ID,
        creatorId=UUID("00000000-0000-5000-8000-000000000102"),
        sourceArticleKey="enwiki:2032",
        title="Candidate",
        text="A candidate Babel created by another synthetic creator.",
        createdAtNs=1,
    )
    candidate_vector = tuple([1.0] + [0.0] * 99)
    record = VectorRecord(
        babel=babel,
        catalogContentHash="a" * 64,
        embeddingSpaceId=SPACE_ID,
        servingModelId=MODEL_ID,
        materializedModelVersion=0,
        vector=candidate_vector,
    )
    materialized = MaterializedServingState(
        run_id=RUN_ID,
        model_id=MODEL_ID,
        model_version=0,
        embedding_space_id=SPACE_ID,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    return ServingState(
        registry=registry,
        selected_model_id=MODEL_ID,
        materialized_state=materialized,
        candidate_index=InMemoryCreatedBabelIndex([record]),
        vector_records=[record],
        qwen_encoder=encoder,
        scale_run=True,
    )


def _request() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "requestId": "00000000-0000-5000-8000-000000000401",
        "runId": str(RUN_ID),
        "creatorId": "00000000-0000-5000-8000-000000000101",
        "newBabelId": "00000000-0000-5000-8000-000000000301",
        "newSourceArticleKey": "enwiki:5739",
        "title": "Compiler notes",
        "text": "An observable note about compiler design.",
        "historyBabelIds": [],
        "candidateCount": 1,
    }


def test_real_manifest_uses_one_injected_encoder_and_survives_restart() -> None:
    first_encoder = _StubAcceptedQwen()
    second_encoder = _StubAcceptedQwen()

    first = TestClient(create_app(_serving_state(first_encoder))).post(
        "/api/v1/recommendations", json=_request()
    )
    restarted = TestClient(create_app(_serving_state(second_encoder))).post(
        "/api/v1/recommendations", json=_request()
    )

    assert first.status_code == restarted.status_code == 200
    first_response = RecommendationResponseV1.model_validate(first.json())
    restarted_response = RecommendationResponseV1.model_validate(restarted.json())
    assert first_encoder.calls == second_encoder.calls == 1
    assert first_response.queryVectorSha256 == restarted_response.queryVectorSha256
    assert first.headers["X-Babel-Model-Manifest-Sha256"] == restarted.headers[
        "X-Babel-Model-Manifest-Sha256"
    ]
    assert first.headers["X-Babel-Encoder-Device"] == "cpu"
    assert first.headers["X-Babel-Encoder-Batch-Size"] == "1"
    assert first.headers["X-Babel-Encoder-Cache-Identity"].startswith("hf://Qwen/")
    assert set(first_response.timingsNs) == {
        "queue",
        "encode",
        "context",
        "ann",
        "filtering",
        "serialization",
        "serverTotal",
    }


def test_formal_scale_state_rejects_fixture_manifest() -> None:
    from babel_online.contracts import ModelManifestV1

    root = Path(__file__).resolve().parents[3]
    fixture = ModelManifestV1.model_validate_json(
        (root / "fixtures/online/tiny/original-model.json").read_text()
    )
    registry = ModelRegistry()
    registry.register_original(fixture)
    materialized = MaterializedServingState(
        run_id=RUN_ID,
        model_id=fixture.modelId,
        model_version=0,
        embedding_space_id=fixture.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="b" * 64,
    )

    with pytest.raises(ValueError, match="real.*Qwen|scale"):
        ServingState(
            registry=registry,
            selected_model_id=fixture.modelId,
            materialized_state=materialized,
            candidate_index=InMemoryCreatedBabelIndex([]),
            vector_records=[],
            qwen_encoder=_StubAcceptedQwen(),
            scale_run=True,
        )


@pytest.mark.real_model
def test_exact_real_commit_returns_one_finite_normalized_cpu_vector(tmp_path: Path) -> None:
    token = os.environ.get("HF_TOKEN")
    required = ("torch", "transformers", "peft", "safetensors")
    if not token or any(importlib.util.find_spec(name) is None for name in required):
        pytest.skip("HF_TOKEN and Qwen dependencies are required")
    base_cache = Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B"
    if not base_cache.is_dir():
        pytest.skip("the pinned Qwen base is not locally cached")

    artifact = DistilledArtifactV1.load(
        repo_id=REAL_MODEL_REPO,
        revision=REAL_ARTIFACT_REVISION,
        artifact_id=REAL_ARTIFACT_ID,
        token=token,
        cache_dir=tmp_path,
    )
    manifest = build_real_original_manifest(
        artifact,
        model_id=MODEL_ID,
        embedding_space_id=SPACE_ID,
    )
    encoder = Qwen100Encoder.from_artifact(
        artifact,
        token=token,
        device="cpu",
        local_files_only=True,
    )

    vector = encoder.encode(["Virtual memory\n\nA memory-management technique."])
    http = TestClient(create_app(_serving_state(encoder))).post(
        "/api/v1/recommendations", json=_request()
    )

    assert vector.shape == (1, 100)
    assert vector.dtype == np.float32
    assert np.isfinite(vector).all()
    np.testing.assert_allclose(np.linalg.norm(vector, axis=1), np.ones(1), atol=1e-5)
    assert http.status_code == 200
    response = RecommendationResponseV1.model_validate(http.json())
    assert response.modelId == MODEL_ID
    assert response.timingsNs["encode"] > 0
    assert http.headers["X-Babel-Encoder-Mode"] == "real_qwen"
    assert http.headers["X-Babel-Model-Manifest-Sha256"] == model_manifest_sha256(
        manifest
    )
