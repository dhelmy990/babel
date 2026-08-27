from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "gcp"


def _load_release_module():
    path = DEPLOY / "release.py"
    spec = importlib.util.spec_from_file_location("babel_gcp_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_release() -> dict[str, str]:
    registry = "us-central1-docker.pkg.dev/demo-project/babel-demo"
    return {
        "BABEL_SOURCE_COMMIT": "a" * 40,
        "BABEL_BACKEND_IMAGE": f"{registry}/babel-backend@sha256:{'1' * 64}",
        "BABEL_SERVING_IMAGE": f"{registry}/babel-serving@sha256:{'2' * 64}",
        "BABEL_TRAINER_IMAGE": f"{registry}/babel-trainer@sha256:{'3' * 64}",
        "BABEL_MODEL_REVISION": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "BABEL_DATASET_REVISION": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "BABEL_GCP_RUN_ID": "4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70",
    }


def test_release_contract_accepts_only_digest_images_and_pinned_provenance() -> None:
    release = _load_release_module()
    assert release.validate_release(_valid_release()) == _valid_release()

    for key, invalid in (
        ("BABEL_BACKEND_IMAGE", "us-central1-docker.pkg.dev/p/r/i:latest"),
        ("BABEL_SOURCE_COMMIT", "demo"),
        ("BABEL_MODEL_REVISION", "f" * 40),
        ("BABEL_DATASET_REVISION", "e" * 40),
        ("BABEL_GCP_RUN_ID", "not-a-uuid"),
    ):
        candidate = _valid_release()
        candidate[key] = invalid
        with pytest.raises(ValueError):
            release.validate_release(candidate)


def test_release_contract_rejects_missing_unknown_and_multiline_values() -> None:
    release = _load_release_module()
    missing = _valid_release()
    missing.pop("BABEL_TRAINER_IMAGE")
    with pytest.raises(ValueError, match="exact keys"):
        release.validate_release(missing)

    unknown = _valid_release() | {"HF_TOKEN": "must-not-enter-release-metadata"}
    with pytest.raises(ValueError, match="exact keys"):
        release.validate_release(unknown)

    multiline = _valid_release()
    multiline["BABEL_SOURCE_COMMIT"] += "\nINJECTED=value"
    with pytest.raises(ValueError):
        release.validate_release(multiline)


def test_receipt_is_canonical_and_records_exact_image_digests() -> None:
    release = _load_release_module()
    receipt = release.deployment_receipt(
        _valid_release(), deployed_at="2026-08-27T14:00:00Z"
    )
    assert receipt == {
        "schemaVersion": 1,
        "sourceCommit": "a" * 40,
        "backendImageDigest": "sha256:" + "1" * 64,
        "servingImageDigest": "sha256:" + "2" * 64,
        "trainerImageDigest": "sha256:" + "3" * 64,
        "modelRevision": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "datasetRevision": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "runId": "4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70",
        "deployedAt": "2026-08-27T14:00:00Z",
    }
    encoded = release.canonical_json(receipt)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == receipt


def test_workflow_is_wif_only_digest_deployment_from_demo_branch() -> None:
    workflow = (ROOT / ".github/workflows/deploy-gcp-demo.yml").read_text()
    assert "branches: [demo]" in workflow
    assert "group: babel-gcp-demo" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "id-token: write" in workflow
    assert "workload_identity_provider:" in workflow
    assert "service_account:" in workflow
    assert "credentials_json" not in workflow
    assert "service_account_key" not in workflow
    assert "@sha256:" in workflow
    assert "git pull" not in workflow
    assert "deploy/gcp/rollout.sh" in workflow
    assert "performance-worker" not in workflow


def test_multistage_image_and_compose_preserve_compute_boundary() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert " AS backend" in dockerfile
    assert " AS serving" in dockerfile
    assert " AS trainer" in dockerfile
    assert "-DBABEL_MIGRATION_DIRECTORY=/opt/babel/migrations" in dockerfile
    assert "-DBABEL_ADMIN_ASSET_DIRECTORY=/opt/babel/admin" in dockerfile
    assert "--depth 1" not in dockerfile
    assert "bison build-essential" in dockerfile
    assert "flex git ninja-build pkg-config python3" in dockerfile

    compose = (DEPLOY / "compose.yaml").read_text()
    assert "${BABEL_BACKEND_IMAGE:?" in compose
    assert "${BABEL_SERVING_IMAGE:?" in compose
    assert "${BABEL_TRAINER_IMAGE:?" in compose
    assert "BABEL_ONLINE_QWEN_DEVICE: cuda" in compose
    assert "gpus: all" in compose
    trainer = compose.split("  trainer:", 1)[1]
    assert "gpus: all" not in trainer
    assert "performance-worker" not in compose


def test_rollout_validates_before_migration_and_rolls_back_on_failed_health() -> None:
    script = (DEPLOY / "rollout.sh").read_text()
    validate_at = script.index("release.py validate")
    pull_at = script.index("compose pull")
    migrate_at = script.index("backend migrate")
    stop_at = script.index("stop_current_release")
    health_at = script.index("verify_health")
    assert validate_at < pull_at < migrate_at < stop_at < health_at
    assert "rollback_current_release" in script
    assert "trap rollback_on_error ERR" in script
    assert "docker compose down -v" not in script
    assert "rm -rf" not in script


def test_shell_scripts_parse() -> None:
    for path in (DEPLOY / "rollout.sh",):
        result = subprocess.run(
            ["bash", "-n", os.fspath(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
