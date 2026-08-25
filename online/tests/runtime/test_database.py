from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from babel_online.contracts import ActivityLogV1, ModelManifestV1, RunConfigV1
from babel_online.runtime.database import (
    ArtifactConfigurationError,
    canonical_json_sha256,
    load_configured_model_artifact,
)


ROOT = Path(__file__).resolve().parents[3]


def test_launch_config_digest_is_stable_and_validates_the_pinned_run() -> None:
    document = json.loads((ROOT / "fixtures/online/tiny/run.json").read_text())
    digest = canonical_json_sha256(document)
    assert digest == hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run = RunConfigV1.model_validate(document)
    assert run.datasetRevision == "e1acc648fcace8820dd5ee70bae9216ea4334555"


def test_configured_model_artifact_requires_real_checksum_verified_bytes(tmp_path) -> None:
    state = b'{"fixture":"checksum-verified Friday demo model"}\n'
    (tmp_path / "working-state.json").write_bytes(state)
    manifest = json.loads((ROOT / "fixtures/online/demo-model/manifest.json").read_text())
    manifest["checkpointPath"] = "working-state.json"
    manifest["checkpointSha256"] = hashlib.sha256(state).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    loaded = load_configured_model_artifact(tmp_path)

    assert loaded.manifest == ModelManifestV1.model_validate(manifest)
    assert "demo" in loaded.manifest.label.casefold()
    (tmp_path / "working-state.json").write_text("tampered")
    with pytest.raises(ArtifactConfigurationError):
        load_configured_model_artifact(tmp_path)


def test_activity_boundary_rejects_hidden_simulator_fields() -> None:
    with pytest.raises(ValueError):
        ActivityLogV1.model_validate(
            {
                "schemaVersion": 1,
                "runId": str(UUID(int=1)),
                "sequence": 1,
                "occurredAtNs": 1,
                "level": "info",
                "component": "supervisor",
                "event": "hidden",
                "message": "must fail",
                "metrics": {"pprScore": 0.9},
                "details": {"kind": "lifecycle"},
            }
        )
