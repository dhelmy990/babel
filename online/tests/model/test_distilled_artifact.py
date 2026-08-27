from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from babel_online.model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
    ArtifactAcceptanceError,
    ArtifactIntegrityError,
    DistilledArtifactV1,
)


PAYLOAD_NAMES = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "final_checkpoint_identity.json",
    "projection.safetensors",
    "training_config.json",
    "validation_report.json",
)


def _manifest(payloads: dict[str, bytes]) -> dict[str, object]:
    dataset_sha = "b440e98b04ab77afed7caf0455eca3189235fc3b"
    base_sha = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    return {
        "artifact_hashes": {
            name: hashlib.sha256(value).hexdigest()
            for name, value in payloads.items()
        },
        "artifact_id": REAL_ARTIFACT_ID,
        "artifact_schema": "babel-distillation-2016-interview-v1",
        "dataset": {
            "commit_sha": dataset_sha,
            "config": "distillation_2016_interview",
            "counts": {"test": 5000, "total": 60000, "train": 50000, "validation": 5000},
            "manifest_sha256": "3" * 64,
            "ordered_identity_sha256": {"test": "4" * 64, "train": "5" * 64, "validation": "6" * 64},
            "parquet_sha256": {"test": "7" * 64, "train": "8" * 64, "validation": "9" * 64},
            "readiness_sha256": "a" * 64,
            "repo_id": "dhelmy990/babel-wikipedia-experiment",
            "test_usage": "identity metadata only; examples unopened",
        },
        "final_checkpoint": {
            "epoch": 1,
            "global_step": 3125,
            "next_ordered_row": 50000,
            "schema_version": 1,
            "tree_sha256": "b" * 64,
        },
        "immutable": True,
        "lora": {
            "bias": "none",
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "r": 16,
            "target_modules": ["q_proj", "v_proj"],
        },
        "model": {"id": "Qwen/Qwen3-Embedding-0.6B", "revision": base_sha, "tokenizer_revision": base_sha},
        "projection": {"input_dimension": 1024, "output_dimension": 100},
        "protocol": {"epochs": 1, "max_length": 384, "smoke_rows": 1000, "train_rows": 50000, "validation_rows": 5000},
        "publication": {"artifact_payload_commit_sha": "c" * 40, "private": True, "repo_id": REAL_MODEL_REPO},
        "source": {"commit_sha": "92f3ac697d78eb827d75b033df92dcbed887def7"},
        "training_config": json.loads(payloads["training_config.json"]),
        "validation": json.loads(payloads["validation_report.json"]),
    }


def _remote_fixture(tmp_path: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    adapter = {"bias": "none", "lora_alpha": 32, "lora_dropout": 0.05, "r": 16, "target_modules": ["q_proj", "v_proj"]}
    checkpoint = {"epoch": 1, "global_step": 3125, "next_ordered_row": 50000, "schema_version": 1, "tree_sha256": "b" * 64}
    training = {
        "dataset_commit_sha": "b440e98b04ab77afed7caf0455eca3189235fc3b",
        "dataset_config": "distillation_2016_interview",
        "dataset_repo_id": "dhelmy990/babel-wikipedia-experiment",
        "lora_alpha": 32,
        "lora_bias": "none",
        "lora_dropout": 0.05,
        "lora_rank": 16,
        "lora_targets": ["q_proj", "v_proj"],
        "max_length": 384,
        "model_id": "Qwen/Qwen3-Embedding-0.6B",
        "model_revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "projection_input_dimension": 1024,
        "projection_output_dimension": 100,
        "tokenizer_revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "train_rows": 50000,
        "validation_rows": 5000,
    }
    validation = {
        "dataset": {"commit_sha": training["dataset_commit_sha"], "example_count": 5000},
        "invalid_student_vector_count": 0,
        "invalid_teacher_vector_count": 0,
        "invalid_vector_count": 0,
        "metrics": {"mean_paired_cosine": 0.8},
        "model": {"id": training["model_id"], "revision": training["model_revision"], "tokenizer_revision": training["tokenizer_revision"]},
        "norm_statistics": {"student_min": 0.999, "student_max": 1.001},
        "pool_size": 5000,
        "report_version": 1,
        "examples": [],
    }
    payloads = {
        "adapter_config.json": (json.dumps(adapter, sort_keys=True) + "\n").encode(),
        "adapter_model.safetensors": b"adapter-weights",
        "final_checkpoint_identity.json": (json.dumps(checkpoint, sort_keys=True) + "\n").encode(),
        "projection.safetensors": b"projection-weights",
        "training_config.json": (json.dumps(training, sort_keys=True) + "\n").encode(),
        "validation_report.json": (json.dumps(validation, sort_keys=True) + "\n").encode(),
    }
    manifest = _manifest(payloads)
    return payloads, manifest


class _Api:
    def __init__(self, files: list[str], *, private: bool = True, sha: str = REAL_ARTIFACT_REVISION) -> None:
        self.files = files
        self.private = private
        self.sha = sha

    def model_info(self, repo_id: str, *, revision: str, token: str, files_metadata: bool = True):
        assert repo_id == REAL_MODEL_REPO
        assert revision == REAL_ARTIFACT_REVISION
        assert token == "private-token"
        assert files_metadata is True
        return SimpleNamespace(
            private=self.private,
            sha=self.sha,
            siblings=[SimpleNamespace(rfilename=name) for name in self.files],
        )


def _load(tmp_path: Path, *, mutate_manifest=None) -> DistilledArtifactV1:
    payloads, manifest = _remote_fixture(tmp_path)
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    base = f"artifacts/{REAL_ARTIFACT_ID}"
    remote = {f"{base}/{name}": value for name, value in payloads.items()}
    remote[f"{base}/artifact_manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()

    def download(repo_id: str, filename: str, revision: str, token: str, cache_dir: Path | None) -> str:
        assert (repo_id, revision, token) == (REAL_MODEL_REPO, REAL_ARTIFACT_REVISION, "private-token")
        path = tmp_path / filename.replace("/", "-")
        path.write_bytes(remote[filename])
        return str(path)

    return DistilledArtifactV1.load(
        repo_id=REAL_MODEL_REPO,
        revision=REAL_ARTIFACT_REVISION,
        artifact_id=REAL_ARTIFACT_ID,
        token="private-token",
        api=_Api(sorted(remote)),
        downloader=download,
        cache_dir=tmp_path / "cache",
    )


def test_load_verifies_real_identity_hashes_and_closed_serving_contract(tmp_path: Path) -> None:
    artifact = _load(tmp_path)

    artifact.assert_real_acceptance()
    contract = artifact.serving_contract
    assert contract.artifactRepo == REAL_MODEL_REPO
    assert contract.artifactRevision == REAL_ARTIFACT_REVISION
    assert contract.baseModelRevision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert contract.datasetRevision == "b440e98b04ab77afed7caf0455eca3189235fc3b"
    assert contract.trainingSourceRevision == "92f3ac697d78eb827d75b033df92dcbed887def7"
    assert contract.semanticsAuthority == "pinned_training_source"
    assert contract.inputFormat == "canonical_title\\n\\nlead_text"
    assert contract.maxLength == 384
    assert contract.paddingSide == "left"
    assert contract.pooling == "last_non_padding_token"
    assert (contract.projectionInputDimension, contract.embeddingDimension) == (1024, 100)
    assert contract.normalization == "l2"
    assert contract.adapterSha256 == artifact.manifest.artifact_hashes["adapter_model.safetensors"]
    assert contract.validationSha256 == artifact.manifest.artifact_hashes["validation_report.json"]
    assert "inputFormat" not in artifact.manifest.model_dump(mode="json")


def test_load_rejects_payload_checksum_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIntegrityError, match="checksum"):
        _load(tmp_path, mutate_manifest=lambda value: value["artifact_hashes"].update({"projection.safetensors": "0" * 64}))


def test_load_rejects_unknown_manifest_field(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIntegrityError, match="manifest"):
        _load(tmp_path, mutate_manifest=lambda value: value.update({"fixture": True}))


def test_local_fixture_cannot_satisfy_real_acceptance(tmp_path: Path) -> None:
    payloads, manifest = _remote_fixture(tmp_path)
    root = tmp_path / REAL_ARTIFACT_ID
    root.mkdir()
    for name, value in payloads.items():
        (root / name).write_bytes(value)
    (root / "artifact_manifest.json").write_text(json.dumps(manifest))

    fixture = DistilledArtifactV1.load_fixture(root)

    with pytest.raises(ArtifactAcceptanceError, match="private.*commit"):
        fixture.assert_real_acceptance()


@pytest.mark.real_model
def test_exact_private_artifact_passes_real_acceptance_when_token_is_available(tmp_path: Path) -> None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        pytest.skip("HF_TOKEN is required for private artifact acceptance")

    artifact = DistilledArtifactV1.load(
        repo_id=REAL_MODEL_REPO,
        revision=REAL_ARTIFACT_REVISION,
        artifact_id=REAL_ARTIFACT_ID,
        token=token,
        cache_dir=tmp_path,
    )

    artifact.assert_real_acceptance()
    assert artifact.manifest.validation["pool_size"] == 5000
