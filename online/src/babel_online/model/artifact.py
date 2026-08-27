"""Checksum-verifying immutable model artifact loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ..contracts import EmbeddingSpaceV1, ModelManifestV1, ModelManifestV2
from .distilled_artifact import DistilledArtifactV1


class ArtifactIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedArtifact:
    manifest: ModelManifestV1
    checkpoint_path: Path


@dataclass(frozen=True)
class LoadedRealArtifact:
    """Accepted V2 original kept in memory beside its verified Hub payload."""

    manifest: ModelManifestV2
    distilled_artifact: DistilledArtifactV1
    online_state_path: Path | None = None


def load_artifact(root: Path) -> LoadedArtifact:
    """Load one immutable manifest and verify its checkpoint bytes."""
    artifact_root = Path(root).resolve()
    manifest_path = artifact_root / "manifest.json"
    manifest = ModelManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    checkpoint = (artifact_root / manifest.checkpointPath).resolve()
    try:
        checkpoint.relative_to(artifact_root)
    except ValueError as error:
        raise ArtifactIntegrityError("checkpoint path escapes artifact root") from error
    if not checkpoint.is_file():
        raise ArtifactIntegrityError("checkpoint file is missing")
    actual = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if actual != manifest.checkpointSha256:
        raise ArtifactIntegrityError("checkpoint SHA-256 does not match manifest")
    return LoadedArtifact(manifest=manifest, checkpoint_path=checkpoint)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_real_original_manifest(
    artifact: DistilledArtifactV1,
    *,
    model_id: UUID,
    embedding_space_id: UUID,
    label: str = "2016 interview Qwen original",
) -> ModelManifestV2:
    """Create the immutable online original from one accepted Hub artifact."""
    artifact.assert_real_acceptance()
    contract = artifact.serving_contract
    manifest = artifact.manifest
    artifact_manifest_sha256 = _sha256(artifact.path_for("artifact_manifest.json"))
    return ModelManifestV2(
        schemaVersion=2,
        modelId=model_id,
        label=label,
        parentModelId=None,
        producingRunId=None,
        encoderRepo=contract.artifactRepo,
        encoderRevision=contract.artifactRevision,
        artifactPath=contract.artifactPath,
        artifactId=contract.artifactId,
        artifactManifestSha256=artifact_manifest_sha256,
        checkpointTreeSha256=manifest.final_checkpoint.tree_sha256,
        baseModelId=contract.baseModelId,
        baseModelRevision=contract.baseModelRevision,
        tokenizerRevision=contract.tokenizerRevision,
        datasetRepo=contract.datasetRepo,
        datasetConfig=contract.datasetConfig,
        datasetRevision=contract.datasetRevision,
        datasetManifestSha256=manifest.dataset.manifest_sha256,
        trainingSourceRevision=contract.trainingSourceRevision,
        adapterSha256=contract.adapterSha256,
        projectionSha256=contract.projectionSha256,
        validationSha256=contract.validationSha256,
        trainingExamples=50_000,
        embeddingSpace=EmbeddingSpaceV1(
            schemaVersion=1,
            embeddingSpaceId=embedding_space_id,
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


def model_manifest_sha256(manifest: ModelManifestV1 | ModelManifestV2) -> str:
    """Hash canonical immutable model metadata for restart/reload probes."""
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ArtifactIntegrityError",
    "LoadedArtifact",
    "LoadedRealArtifact",
    "build_real_original_manifest",
    "load_artifact",
    "model_manifest_sha256",
]
