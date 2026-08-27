from __future__ import annotations

import hashlib
from uuid import UUID

import numpy as np
import pytest

from babel_online.contracts import (
    DistilledServingArtifactV1,
    EmbeddingSpaceV1,
    ModelManifestV2,
)
from babel_online.model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
)
from babel_online.model.qwen_encoder import Qwen100Encoder


MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")


def accepted_contract() -> DistilledServingArtifactV1:
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


def accepted_manifest() -> ModelManifestV2:
    contract = accepted_contract()
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


class StubAcceptedQwen(Qwen100Encoder):
    def __init__(self) -> None:
        self.contract = accepted_contract()
        self.device = "cpu"
        self.cache_identity = "accepted-qwen-test-double"
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            row = np.frombuffer((digest * 4)[:100], dtype=np.uint8).astype(np.float32)
            row -= 127.5
            rows.append(row / np.linalg.norm(row))
        return np.asarray(rows, dtype=np.float32)


@pytest.fixture
def real_model_manifest() -> ModelManifestV2:
    return accepted_manifest()


@pytest.fixture
def accepted_qwen_factory():
    return StubAcceptedQwen
