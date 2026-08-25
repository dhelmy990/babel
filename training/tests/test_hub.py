from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from safetensors import safe_open


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training" / "src"))

from babel_training.hub import (  # noqa: E402
    ArtifactExportError,
    ArtifactPublicationError,
    DEFAULT_MODEL_REPO,
    export_distilled_artifact,
    publish_model_artifact,
)


MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DATASET_REVISION = "a" * 40
PARENT = "b" * 40
PUBLISHED = "c" * 40


def export(tmp_path: Path, **overrides: object):
    arguments: dict[str, object] = {
        "projection_tensors": {
            "weight": np.arange(102400, dtype=np.float32).reshape(100, 1024),
            "bias": np.zeros(100, dtype=np.float32),
        },
        "adapter_tensors": {
            "base_model.model.layers.0.self_attn.q_proj.lora_A.default.weight": np.ones((16, 1024), dtype=np.float32),
            "base_model.model.layers.0.self_attn.q_proj.lora_B.default.weight": np.ones((1024, 16), dtype=np.float32),
            "base_model.model.layers.0.self_attn.v_proj.lora_A.default.weight": np.ones((16, 1024), dtype=np.float32),
            "base_model.model.layers.0.self_attn.v_proj.lora_B.default.weight": np.ones((1024, 16), dtype=np.float32),
        },
        "adapter_config": {
            "r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
            "bias": "none", "target_modules": ["q_proj", "v_proj"],
        },
        "model_id": "Qwen/Qwen3-Embedding-0.6B",
        "model_revision": MODEL_REVISION,
        "tokenizer_revision": MODEL_REVISION,
        "dataset_commit_sha": DATASET_REVISION,
        "dataset_manifest_sha256": "d" * 64,
        "dataset_readiness_sha256": "e" * 64,
        "training_config": {"seed": 7, "max_length": 512},
        "validation_report": {"mean_cosine": 0.75, "examples": 10},
    }
    arguments.update(overrides)
    return export_distilled_artifact(tmp_path, **arguments)


def test_export_is_atomic_deterministic_and_physical_safetensors(tmp_path: Path) -> None:
    first = export(tmp_path / "one")
    second = export(tmp_path / "two")
    assert first.artifact_id == second.artifact_id
    assert first.model_revision == MODEL_REVISION
    assert first.dataset_commit_sha == DATASET_REVISION
    assert first.path.name == first.artifact_id
    assert (first.path / "artifact_manifest.json").read_bytes() == (
        second.path / "artifact_manifest.json"
    ).read_bytes()
    for filename in ("projection.safetensors", "adapter_model.safetensors"):
        with safe_open(first.path / filename, framework="np") as stored:
            assert stored.keys()
    document = json.loads((first.path / "artifact_manifest.json").read_bytes())
    assert set(document["files"]) == {
        "adapter_config.json", "adapter_model.safetensors", "projection.safetensors",
        "training_config.json", "validation_report.json",
    }
    for name, identity in document["files"].items():
        value = (first.path / name).read_bytes()
        assert identity == {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}


def test_export_never_overwrites_and_rejects_symlink_destination(tmp_path: Path) -> None:
    artifact = export(tmp_path)
    with pytest.raises(FileExistsError):
        export(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    link = tmp_path / "linked"; link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactExportError, match="symlink"):
        export(link)
    assert artifact.path.exists()


def test_export_failure_leaves_no_partial_and_rejects_base_weights(tmp_path: Path) -> None:
    bad = np.ones((2, 2), dtype=np.float32); bad[0, 0] = np.nan
    with pytest.raises(ArtifactExportError, match="finite"):
        export(tmp_path / "nan", projection_tensors={"weight": bad})
    assert not (tmp_path / "nan").exists()

    with pytest.raises(ArtifactExportError, match="base model"):
        export(tmp_path / "base", adapter_tensors={
            "base_model.model.layers.0.self_attn.q_proj.base_layer.weight": np.ones((2, 2), dtype=np.float32)
        })
    with pytest.raises(ArtifactExportError, match="base model"):
        export(tmp_path / "disguised", adapter_tensors={
            "base_model.model.layers.0.weight.lora_marker": np.ones((2, 2), dtype=np.float32)
        })


def test_export_rejects_invalid_projection_and_adapter_layouts(tmp_path: Path) -> None:
    invalid = [
        {"projection_tensors": {"bias": np.zeros(100, dtype=np.float32)}},
        {"projection_tensors": {"weight": np.zeros((99, 1024), dtype=np.float32)}},
        {"projection_tensors": {
            "weight": np.zeros((100, 1024), dtype=np.float32),
            "projection.weight": np.zeros((100, 1024), dtype=np.float32),
        }},
        {"adapter_tensors": {
            "base_model.model.layers.0.self_attn.q_proj.lora_A.default.weight": np.ones((3, 4), dtype=np.float32),
            "base_model.model.layers.0.self_attn.q_proj.lora_B.default.weight": np.ones((4, 3), dtype=np.float32),
        }},
        {"adapter_tensors": {
            "base_model.model.layers.0.self_attn.k_proj.lora_A.default.weight": np.ones((2, 4), dtype=np.float32),
            "base_model.model.layers.0.self_attn.k_proj.lora_B.default.weight": np.ones((4, 2), dtype=np.float32),
        }},
        {"adapter_config": {
            "r": 8, "lora_alpha": 32, "lora_dropout": 0.05,
            "bias": "none", "target_modules": ["q_proj", "v_proj"],
        }},
    ]
    for index, overrides in enumerate(invalid):
        with pytest.raises(ArtifactExportError):
            export(tmp_path / f"invalid-{index}", **overrides)


class Missing(Exception):
    pass


class ParentConflict(Exception):
    def __init__(self) -> None:
        super().__init__("parent conflict")
        self.response = SimpleNamespace(status_code=409)


class FakeApi:
    def __init__(self, *, private: bool | None = True) -> None:
        self.private = private
        self.current_sha = PARENT
        self.remote: dict[str, bytes] = {}
        self.create_calls: list[dict[str, object]] = []
        self.commit_calls: list[dict[str, object]] = []

    def create_repo(self, **kwargs: object) -> None:
        self.create_calls.append(kwargs)

    def model_info(self, repo_id: str, **kwargs: object) -> object:
        revision = kwargs.get("revision")
        sha = self.current_sha if revision in (None, "main") else revision
        return SimpleNamespace(sha=sha, private=self.private)

    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        if path_in_repo not in self.remote:
            raise Missing(path_in_repo)
        return self.remote[path_in_repo]

    def iter_file_bytes(self, *, path_in_repo: str, **kwargs: object):
        value = self.get_file_bytes(path_in_repo=path_in_repo)
        yield from (value[index:index + 7] for index in range(0, len(value), 7))

    def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
        self.commit_calls.append(kwargs)
        for operation in operations:
            self.remote[str(operation.path_in_repo)] = Path(operation.path_or_fileobj).read_bytes()
        self.current_sha = PUBLISHED
        return SimpleNamespace(oid=PUBLISHED)


def test_publish_proves_private_uses_expected_parent_and_verifies_every_remote_file(tmp_path: Path) -> None:
    artifact = export(tmp_path)
    api = FakeApi()
    evidence = publish_model_artifact(api, artifact.path, "secret", sleep=lambda _: None)
    assert evidence.commit_sha == PUBLISHED
    assert api.create_calls == [{
        "repo_id": DEFAULT_MODEL_REPO, "repo_type": "model", "private": True,
        "exist_ok": True, "token": "secret",
    }]
    assert api.commit_calls[0]["parent_commit"] == PARENT
    assert set(api.remote) == {
        f"artifacts/{artifact.artifact_id}/{path.name}" for path in artifact.path.iterdir()
    }
    persisted = artifact.path.parent / f"{artifact.artifact_id}.publication-verification.json"
    assert json.loads(persisted.read_bytes())["commit_sha"] == PUBLISHED


def test_publish_is_idempotent_for_same_bytes_and_rejects_conflict(tmp_path: Path) -> None:
    artifact = export(tmp_path)
    api = FakeApi()
    first = publish_model_artifact(api, artifact.path, "secret", sleep=lambda _: None)
    second = publish_model_artifact(api, artifact.path, "secret", sleep=lambda _: None)
    assert second == first
    assert len(api.commit_calls) == 1
    conflict = FakeApi()
    path = f"artifacts/{artifact.artifact_id}/artifact_manifest.json"
    conflict.remote[path] = b"conflict"
    with pytest.raises(ArtifactPublicationError, match="conflict"):
        publish_model_artifact(conflict, artifact.path, "secret", sleep=lambda _: None)


def test_publish_retries_parent_race(tmp_path: Path) -> None:
    artifact = export(tmp_path)

    class RacingApi(FakeApi):
        def __init__(self) -> None:
            super().__init__(); self.attempts = 0

        def create_commit(self, **kwargs: object) -> object:
            self.attempts += 1
            if self.attempts == 1:
                self.current_sha = "f" * 40
                raise ParentConflict()
            return super().create_commit(**kwargs)  # type: ignore[arg-type]

    api = RacingApi()
    assert publish_model_artifact(api, artifact.path, "secret", retries=2, sleep=lambda _: None).commit_sha == PUBLISHED
    assert api.attempts == 2


def test_publish_retries_http_412_parent_race(tmp_path: Path) -> None:
    artifact = export(tmp_path)

    class PreconditionFailed(Exception):
        response = SimpleNamespace(status_code=412)

    class RacingApi(FakeApi):
        def __init__(self) -> None:
            super().__init__(); self.attempts = 0

        def create_commit(self, **kwargs: object) -> object:
            self.attempts += 1
            if self.attempts == 1:
                self.current_sha = "f" * 40
                raise PreconditionFailed()
            return super().create_commit(**kwargs)  # type: ignore[arg-type]

    api = RacingApi()
    publish_model_artifact(api, artifact.path, "secret", retries=2, sleep=lambda _: None)
    assert api.attempts == 2


def test_publish_snapshots_validated_artifact_before_mutable_hub_calls(tmp_path: Path) -> None:
    artifact = export(tmp_path)
    original = (artifact.path / "projection.safetensors").read_bytes()

    class MutatingApi(FakeApi):
        def create_repo(self, **kwargs: object) -> None:
            super().create_repo(**kwargs)
            (artifact.path / "projection.safetensors").write_bytes(b"replaced-after-validation")

    api = MutatingApi()
    publish_model_artifact(api, artifact.path, "secret", sleep=lambda _: None)
    remote_path = f"artifacts/{artifact.artifact_id}/projection.safetensors"
    assert api.remote[remote_path] == original


@pytest.mark.parametrize("mode", ["missing", "tamper"])
def test_publish_rejects_remote_missing_or_tampered_bytes(tmp_path: Path, mode: str) -> None:
    artifact = export(tmp_path)

    class BadRemote(FakeApi):
        def create_commit(self, **kwargs: object) -> object:
            result = super().create_commit(**kwargs)  # type: ignore[arg-type]
            target = sorted(self.remote)[0]
            if mode == "missing":
                del self.remote[target]
            else:
                self.remote[target] += b"tampered"
            return result

    with pytest.raises(ArtifactPublicationError, match="remote"):
        publish_model_artifact(BadRemote(), artifact.path, "secret", sleep=lambda _: None)


def test_publish_rejects_unproved_privacy_and_redacts_token(tmp_path: Path) -> None:
    artifact = export(tmp_path)
    token = "never-show-this"
    with pytest.raises(ArtifactPublicationError) as captured:
        publish_model_artifact(FakeApi(private=None), artifact.path, token, sleep=lambda _: None)
    assert token not in str(captured.value)
