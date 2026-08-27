from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "gcp"


def _load_release_module():
    path = DEPLOY / "release.py"
    spec = importlib.util.spec_from_file_location("babel_gcp_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_predeploy_module():
    path = DEPLOY / "predeploy.py"
    spec = importlib.util.spec_from_file_location("babel_gcp_predeploy_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_release() -> dict[str, str]:
    trial_id = UUID("4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70")
    registry = "us-central1-docker.pkg.dev/demo-project/babel-demo"
    return {
        "BABEL_SOURCE_COMMIT": "a" * 40,
        "BABEL_BACKEND_IMAGE": f"{registry}/babel-backend@sha256:{'1' * 64}",
        "BABEL_SERVING_IMAGE": f"{registry}/babel-serving@sha256:{'2' * 64}",
        "BABEL_TRAINER_IMAGE": f"{registry}/babel-trainer@sha256:{'3' * 64}",
        "BABEL_PERFORMANCE_WORKER_IMAGE": f"{registry}/babel-performance-worker@sha256:{'6' * 64}",
        "BABEL_MODEL_REVISION": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "BABEL_DATASET_REVISION": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "BABEL_GCP_TRIAL_ID": str(trial_id),
        "BABEL_GCP_RUN_ID": str(uuid5(trial_id, "population")),
        "BABEL_POPULATION_VECTOR_SHA256": "4" * 64,
        "BABEL_POPULATION_SNAPSHOT_SHA256": "5" * 64,
        "BABEL_DEPLOYMENT_RUN_ID": "123456789",
        "BABEL_DEPLOYMENT_RUN_ATTEMPT": "2",
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
        ("BABEL_POPULATION_VECTOR_SHA256", "not-a-sha"),
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


def test_runtime_worker_token_is_parsed_without_evaluation_or_release_override(
    tmp_path: Path,
) -> None:
    release = _load_release_module()
    marker = tmp_path / "must-not-exist"
    token = "a" * 64
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text(
        f"BABEL_PERFORMANCE_WORKER_TOKEN={token}\n"
        "BABEL_GCP_TRIAL_ID=runtime-must-not-override-release\n"
        f"MALICIOUS=$(touch {marker})\n",
        encoding="utf-8",
    )

    assert release.read_runtime_worker_token(runtime_env) == token
    assert not marker.exists()

    runtime_env.write_text(
        f"BABEL_PERFORMANCE_WORKER_TOKEN={token}\n"
        f"BABEL_PERFORMANCE_WORKER_TOKEN={'b' * 64}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="repeats"):
        release.read_runtime_worker_token(runtime_env)


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
        "performanceWorkerImageDigest": "sha256:" + "6" * 64,
        "modelRevision": "57d949cd634b920cc1a46f27c9b21df094b5240e",
        "datasetRevision": "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
        "trialId": "4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70",
        "runId": str(
            uuid5(UUID("4b8ba3f2-4464-4da8-adf0-7a8cb8aa1a70"), "population")
        ),
        "populationVectorSha256": "4" * 64,
        "populationSnapshotSha256": "5" * 64,
        "deploymentRunId": 123456789,
        "deploymentRunAttempt": 2,
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
    assert "target: performance-worker" in workflow
    assert "babel-performance-worker:${{ github.sha }}" in workflow
    assert "performance-worker --check-runtime" in workflow
    assert "github.ref == 'refs/heads/demo'" in workflow
    assert "GITHUB_RUN_ID" in workflow
    assert "BABEL_GCP_TRIAL_ID" in workflow
    assert "tests/deploy/test_gcp_predeploy.py" in workflow
    assert "tests/deploy/test_rollout_supervisor.py" in workflow
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
    postgres = parsed["jobs"]["test"]["services"]["postgres"]
    assert postgres["image"] == "pgvector/pgvector:0.8.6-pg18-bookworm"
    assert postgres["ports"] == ["54329:5432"]
    assert "job.services.postgres.id" in workflow
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public" in workflow
    native_build = next(
        step["run"]
        for step in parsed["jobs"]["test"]["steps"]
        if step.get("name") == "Build and run the native test image"
    )
    assert "docker build --network host --target backend-test" in native_build
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            assert "${{ vars." not in step.get("run", "")


def test_workflow_provisions_pinned_just_before_javascript_tests() -> None:
    workflow = (ROOT / ".github/workflows/deploy-gcp-demo.yml").read_text()
    steps = yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]["test"]["steps"]
    just_index = next(
        (index for index, step in enumerate(steps) if step.get("name") == "Set up just"),
        None,
    )
    test_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Run JavaScript and deployment tests"
    )

    assert just_index is not None
    assert just_index < test_index
    install = steps[just_index]["run"]
    assert "cargo install --locked --version '=1.57.0'" in install
    assert '--root "$RUNNER_TEMP/just" just' in install
    assert '"$RUNNER_TEMP/just/bin" >>"$GITHUB_PATH"' in install
    assert (
        '[[ "$("$RUNNER_TEMP/just/bin/just" --version)" == "just 1.57.0" ]]'
        in install
    )


def test_workflow_installs_locked_extras_for_selected_publication_tests() -> None:
    workflow = (ROOT / ".github/workflows/deploy-gcp-demo.yml").read_text()
    steps = yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]["test"]["steps"]
    install = next(
        step["run"]
        for step in steps
        if step.get("name") == "Install test dependencies"
    )
    runtime_tests = next(
        step["run"]
        for step in steps
        if step.get("name") == "Run online runtime tests"
    )

    assert "benchmark/tests/test_representative_publication.py" in runtime_tests
    assert "uv sync --directory online --frozen" in install
    assert "--extra benchmark" in install
    assert "--extra parquet" in install


def test_release_rejects_nonfresh_or_unbound_gcp_ids() -> None:
    release = _load_release_module()
    candidate = _valid_release()
    candidate["BABEL_GCP_TRIAL_ID"] = "ce8e54ff-e317-4a89-b7db-90327e02dc43"
    with pytest.raises(ValueError, match="fresh"):
        release.validate_release(candidate)

    candidate = _valid_release()
    candidate["BABEL_GCP_RUN_ID"] = str(uuid4())
    with pytest.raises(ValueError, match="uuid5"):
        release.validate_release(candidate)


def test_deployment_ownership_accepts_only_a_newer_github_attempt() -> None:
    release = _load_release_module()
    assert release.require_newer_deployment(11, 1, previous_run_id=10, previous_attempt=9)
    assert release.require_newer_deployment(11, 2, previous_run_id=11, previous_attempt=1)
    with pytest.raises(ValueError, match="older or duplicate"):
        release.require_newer_deployment(10, 9, previous_run_id=11, previous_attempt=1)
    with pytest.raises(ValueError, match="older or duplicate"):
        release.require_newer_deployment(11, 1, previous_run_id=11, previous_attempt=1)


def test_deployment_predecessor_allows_clean_first_deploy_and_exact_three_image_release() -> None:
    release = _load_release_module()
    candidate = _valid_release()
    assert release.validate_deployment_predecessor(candidate, previous=None) == candidate

    legacy = {
        key: value
        for key, value in candidate.items()
        if key != "BABEL_PERFORMANCE_WORKER_IMAGE"
    }
    legacy["BABEL_DEPLOYMENT_RUN_ATTEMPT"] = "1"
    assert release.validate_deployment_predecessor(candidate, previous=legacy) == candidate

    with pytest.raises(ValueError, match="exact keys"):
        release.validate_deployment_predecessor(
            candidate, previous=legacy | {"UNKNOWN": "value"}
        )


def test_trainer_readiness_is_bound_to_run_and_current_rollout() -> None:
    release = _load_release_module()
    run_id = _valid_release()["BABEL_GCP_RUN_ID"]
    threshold = time.time_ns()
    ready = {
        "schemaVersion": 1,
        "runId": run_id,
        "consumerGroup": f"babel-performance-population-demo.{run_id}",
        "readyAtNs": threshold,
    }
    assert release.validate_trainer_readiness(
        ready, expected_run_id=run_id, not_before_ns=threshold
    ) == ready
    with pytest.raises(ValueError, match="run"):
        release.validate_trainer_readiness(
            ready | {"runId": str(uuid4())},
            expected_run_id=run_id,
            not_before_ns=threshold,
        )
    with pytest.raises(ValueError, match="stale"):
        release.validate_trainer_readiness(
            ready | {"readyAtNs": threshold - 1},
            expected_run_id=run_id,
            not_before_ns=threshold,
        )


def test_trainer_readiness_rejects_sigkill_restart_stale_file() -> None:
    release = _load_release_module()
    run_id = _valid_release()["BABEL_GCP_RUN_ID"]
    ready = {
        "schemaVersion": 1,
        "runId": run_id,
        "consumerGroup": f"babel-performance-population-demo.{run_id}",
        "readyAtNs": 1_000_000_100,
    }
    before = {
        "containerId": "a" * 64,
        "startedAt": "1970-01-01T00:00:01.000000200Z",
        "pid": 222,
        "restartCount": 1,
    }
    with pytest.raises(ValueError, match="stale"):
        release.validate_trainer_instance(
            ready,
            expected_run_id=run_id,
            rollout_not_before_ns=1_000_000_000,
            before=before,
            after=before,
        )

    fresh = ready | {"readyAtNs": 1_000_000_300}
    assert release.validate_trainer_instance(
        fresh,
        expected_run_id=run_id,
        rollout_not_before_ns=1_000_000_000,
        before=before,
        after=before,
    ) == fresh
    with pytest.raises(ValueError, match="changed"):
        release.validate_trainer_instance(
            fresh,
            expected_run_id=run_id,
            rollout_not_before_ns=1_000_000_000,
            before=before,
            after=before | {"pid": 333, "restartCount": 2},
        )


def test_serving_health_and_smoke_are_exactly_attested() -> None:
    release = _load_release_module()
    run_id = _valid_release()["BABEL_GCP_RUN_ID"]
    model_id = "2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67"
    health = {"status": "ok", "modelId": model_id, "modelVersion": 0}
    assert release.validate_serving_health(health) == health
    with pytest.raises(ValueError, match="model"):
        release.validate_serving_health(health | {"modelVersion": 1})

    smoke = {
        "schemaVersion": 2,
        "runId": run_id,
        "modelId": model_id,
        "modelVersion": 0,
        "sourceVectorOrigin": "qwen_encode",
        "candidates": [{"babelId": str(uuid4())}],
    }
    assert release.validate_serving_smoke(smoke, expected_run_id=run_id) == smoke
    with pytest.raises(ValueError, match="CUDA Qwen"):
        release.validate_serving_smoke(
            smoke | {"sourceVectorOrigin": "pgvector_load"}, expected_run_id=run_id
        )


def test_multistage_image_and_compose_preserve_compute_boundary() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert " AS backend" in dockerfile
    assert " AS serving" in dockerfile
    assert " AS trainer" in dockerfile
    assert " AS performance-worker" in dockerfile
    assert "COPY benchmark/pyproject.toml" in dockerfile
    assert "COPY benchmark/src" in dockerfile
    assert 'CMD ["performance-worker"]' in dockerfile
    assert "-DBABEL_MIGRATION_DIRECTORY=/opt/babel/migrations" in dockerfile
    assert "-DBABEL_ADMIN_ASSET_DIRECTORY=/opt/babel/admin" in dockerfile
    assert "--depth 1" not in dockerfile
    assert "bison build-essential" in dockerfile
    assert "flex git ninja-build pkg-config python3" in dockerfile
    assert "ARG BABEL_TEST_DATABASE_URL=postgresql://babel:babel-local-dev@127.0.0.1:54329/babel" in dockerfile
    assert 'BABEL_TEST_DATABASE_URL="$BABEL_TEST_DATABASE_URL" ctest' in dockerfile
    assert "USER 65534:65534" in dockerfile

    compose = (DEPLOY / "compose.yaml").read_text()
    assert "${BABEL_BACKEND_IMAGE:?" in compose
    assert "${BABEL_SERVING_IMAGE:?" in compose
    assert "${BABEL_TRAINER_IMAGE:?" in compose
    assert "${BABEL_PERFORMANCE_WORKER_IMAGE:?" in compose
    assert "BABEL_ONLINE_QWEN_DEVICE: cuda" in compose
    assert "gpus: all" in compose
    trainer = compose.split("  trainer:", 1)[1].split(
        "  performance-worker:", 1
    )[0]
    assert "gpus: all" not in trainer
    worker = compose.split("  performance-worker:", 1)[1]
    assert 'profiles: ["matrix"]' in worker
    assert "BABEL_ONLINE_ALLOW_POPULATION_BUILD: \"false\"" in worker
    assert "BABEL_ONLINE_QWEN_DEVICE: cuda" in worker
    assert "gpus: all" in worker
    assert '127.0.0.1:8792' in compose
    assert "BABEL_ONLINE_WORKER_ENDPOINT: http://127.0.0.1:9" in compose
    assert "BABEL_PERFORMANCE_WORKER_ENDPOINT: http://127.0.0.1:8792" in compose


def test_performance_worker_build_context_includes_only_benchmark_package() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    benchmark_rules = [
        rule
        for rule in dockerignore
        if rule.lstrip("!").startswith("benchmark")
    ]

    assert benchmark_rules == [
        "benchmark/**",
        "!benchmark/pyproject.toml",
        "!benchmark/src/",
        "!benchmark/src/babel_benchmark/",
        "!benchmark/src/babel_benchmark/**",
    ]


def test_rollout_validates_before_migration_and_rolls_back_on_failed_health() -> None:
    script = (DEPLOY / "rollout.sh").read_text()
    supervisor = (DEPLOY / "rollout_supervisor.sh").read_text()
    all_shell = script + supervisor
    validate_at = script.index("release.py validate")
    pull_at = script.index("compose pull")
    migrate_at = script.index("backend migrate")
    gate_at = script.index("/opt/babel-predeploy.py")
    stop_at = script.index('compose_for "$PREVIOUS_RELEASE" stop')
    health_at = script.index('verify_release "$NEW_RELEASE"')
    assert validate_at < pull_at < migrate_at < gate_at < stop_at < health_at
    assert "rollback_current_release" in script
    assert 'elif [[ -e "$CURRENT_LINK" ]]' in script
    assert "flock --wait" in all_shell
    assert "trap 'exit 143' TERM" in all_shell
    assert "trap 'exit 130' INT" in all_shell
    assert "trap 'exit 129' HUP" in all_shell
    assert "trap '' TERM INT HUP" in all_shell
    assert "trainer-ready.json" in script and "rm -f" in script
    assert "predeploy.py" in script
    assert "--population-vector-sha256" in script
    assert "--population-snapshot-sha256" in script
    assert "--ordered-vector-sha256" not in script
    assert "--snapshot-sha256" not in script
    assert "validate-trainer-instance" in script
    assert "StartedAt" in script and "RestartCount" in script
    assert "validate-serving-smoke" in script
    assert "docker image inspect" in script
    assert 'dev.babel.model-revision' in script
    assert 'dev.babel.dataset-revision' in script
    assert "rollback_current_release || true" not in script
    assert "docker compose down -v" not in script
    assert "rm -rf" not in script
    assert 'BABEL_ONLINE_ALLOW_POPULATION_BUILD=false' in script
    assert '--profile matrix' in script
    assert 'performance-worker --check-runtime' in script
    assert 'docker run --rm --gpus all --entrypoint python "$BABEL_SERVING_IMAGE"' in script
    assert '"$BABEL_SERVING_IMAGE" \\\n  -c \'import torch;' in script


def test_condition3_operator_is_bounded_and_never_auto_continues_matrix() -> None:
    script = (DEPLOY / "condition3_gate.sh").read_text()
    worker_source = (
        ROOT / "online/src/babel_online/runtime/performance_worker.py"
    ).read_text()
    assert "condition-3-gate" in script
    assert "source.warmup_seconds != 30" in worker_source
    assert "source.duration_seconds != 120" in worker_source
    assert "source.target_rps != 5.0" in worker_source
    assert "BABEL_ONLINE_ALLOW_POPULATION_BUILD" in script
    assert "approve-next-scale" not in script
    assert "condition-4" not in script
    assert "source \"$RUNTIME_ENV\"" not in script
    assert "runtime-token" in script
    assert "--header \"X-Babel-Worker-Token:" not in script
    assert "--config -" in script
    assert "babel_acquire_rollout_lock /var/lock/babel-gcp-demo-rollout.lock" in script
    assert "trap condition3_finish EXIT" in script
    assert "trap 'exit 129' HUP" in script
    assert 'status_phase "$GATE_TRIAL_ID"' in script
    assert 'document.get("experimentId") != expected' in script
    assert "failed to restore regular services after Condition 3" in script
    assert "elif ! compose up --detach backend serving trainer" not in script
    assert 'rm -f "$READY_PATH"' in script
    assert "validate-trainer-instance" in script
    assert "validate-serving-health" in script
    restore = script.split("restore_regular_services() {", 1)[1].split(
        "condition3_finish() {", 1
    )[0]
    assert "return 1" not in restore.split("compose stop performance-worker", 1)[0]
    result = subprocess.run(
        ["bash", "-n", os.fspath(DEPLOY / "condition3_gate.sh")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_rollout_predeploy_argv_is_accepted_end_to_end(monkeypatch, capsys) -> None:
    script = (DEPLOY / "rollout.sh").read_text()
    assert (
        "--volume /var/lib/babel-gpu/evidence/import-receipt.json:"
        "/var/lib/babel-gpu/evidence/import-receipt.json:ro"
    ) in script
    command = script.split(
        '--entrypoint python "$BABEL_TRAINER_IMAGE" /opt/babel-predeploy.py', 1
    )[1].split(
        '>"$NEW_RELEASE/predeploy-evidence.json"', 1
    )[0]
    flags = re.findall(r"(--[a-z0-9-]+)\s+", command)
    assert flags == [
        "--trial-id",
        "--run-id",
        "--population-vector-sha256",
        "--population-snapshot-sha256",
        "--reuse-import-receipt",
    ]

    predeploy = _load_predeploy_module()
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://not-logged")
    monkeypatch.setattr(predeploy, "read_database_reuse_snapshot", lambda *_a, **_k: {})
    monkeypatch.setattr(
        predeploy,
        "validate_reuse_snapshot",
        lambda *_a, **_k: {"schemaVersion": 1, "verified": True},
    )
    values = {
        "--trial-id": _valid_release()["BABEL_GCP_TRIAL_ID"],
        "--run-id": _valid_release()["BABEL_GCP_RUN_ID"],
        "--population-vector-sha256": "4" * 64,
        "--population-snapshot-sha256": "5" * 64,
        "--reuse-import-receipt": "/var/lib/babel-gpu/evidence/import-receipt.json",
    }
    argv = [part for flag in flags for part in (flag, values[flag])]
    assert predeploy.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"schemaVersion": 1, "verified": True}


def test_shell_scripts_parse() -> None:
    for path in (
        DEPLOY / "rollout.sh",
        DEPLOY / "rollout_supervisor.sh",
        DEPLOY / "condition3_gate.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", os.fspath(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
