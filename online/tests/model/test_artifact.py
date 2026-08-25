from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from babel_online.contracts import ModelManifestV1
from babel_online.model.artifact import ArtifactIntegrityError, load_artifact


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
