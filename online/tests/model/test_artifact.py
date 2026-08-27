from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import babel_online.model.artifact as artifact_module
from babel_online.contracts import ModelManifestV1
from babel_online.model.artifact import ArtifactIntegrityError, load_artifact


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_build_real_original_manifest_binds_the_verified_distilled_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = getattr(artifact_module, "build_real_original_manifest", None)
    assert builder is not None, "Task 6 must build the immutable real original"
    artifact_manifest = tmp_path / "artifact_manifest.json"
    artifact_manifest.write_bytes(b"accepted manifest bytes\n")
    accepted = SimpleNamespace(
        assert_real_acceptance=lambda: None,
        serving_contract=SimpleNamespace(
            artifactRepo="dhelmy990/babel-qwen-navigation-2016-interview",
            artifactRevision="57d949cd634b920cc1a46f27c9b21df094b5240e",
            artifactPath="artifacts/"
            + "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
            artifactId="3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8",
            baseModelId="Qwen/Qwen3-Embedding-0.6B",
            baseModelRevision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            tokenizerRevision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            datasetRepo="dhelmy990/babel-wikipedia-experiment",
            datasetConfig="distillation_2016_interview",
            datasetRevision="b440e98b04ab77afed7caf0455eca3189235fc3b",
            trainingSourceRevision="92f3ac697d78eb827d75b033df92dcbed887def7",
            adapterSha256="4792009bfdaa9df25e3cd79f634ddfa081dc3c620828bda478be5db2fd7b8921",
            projectionSha256="e156701da777fbb37e999c7d897f09cdd1993cd5c9d740aaafcfdeb6395d3ddb",
            validationSha256="e4b76f00f65f4de0165e4eb47c652531295b4718d4d7bcc5008c5945a86f9e13",
        ),
        manifest=SimpleNamespace(
            dataset=SimpleNamespace(
                manifest_sha256="33c65554da38af5888e5aae75350ae8ee7889d6047c9f8339d97781e4326de09"
            ),
            final_checkpoint=SimpleNamespace(
                tree_sha256="ddf8721cc38abc9f61b8738d6092e4f6c9542c3c533fc6a81677b307533edcff"
            ),
        ),
        path_for=lambda name: artifact_manifest
        if name == "artifact_manifest.json"
        else (_ for _ in ()).throw(KeyError(name)),
    )
    monkeypatch.setattr(
        artifact_module,
        "_sha256",
        lambda _path: "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f",
    )

    manifest = builder(
        accepted,
        model_id=UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67"),
        embedding_space_id=UUID("f3665769-b470-5228-8df4-08004e252aa4"),
    )

    assert manifest.schemaVersion == 2
    assert manifest.parentModelId is None
    assert manifest.producingRunId is None
    assert manifest.trainingExamples == 50_000
    assert manifest.artifactManifestSha256 == (
        "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
    )
    checksum = artifact_module.model_manifest_sha256(manifest)
    renamed = manifest.model_copy(update={"label": "renamed immutable identity"})
    assert len(checksum) == 64
    assert artifact_module.model_manifest_sha256(renamed) != checksum


def test_load_artifact_verifies_manifest_and_checkpoint_checksum(tmp_path: Path) -> None:
    source = json.loads(
        (REPOSITORY_ROOT / "fixtures/online/tiny/original-model.json").read_text()
    )
    payload = b"immutable demo checkpoint\n"
    source["checkpointPath"] = "model.safetensors"
    source["checkpointSha256"] = hashlib.sha256(payload).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(source))
    (tmp_path / "model.safetensors").write_bytes(payload)

    loaded = load_artifact(tmp_path)

    assert loaded.manifest == ModelManifestV1.model_validate(source)
    assert loaded.checkpoint_path == tmp_path / "model.safetensors"
    (tmp_path / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        load_artifact(tmp_path)
