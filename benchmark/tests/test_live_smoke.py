from __future__ import annotations

import json
from pathlib import Path

import pytest

from babel_benchmark.live_smoke import (
    FixtureLiveSmoke,
    LiveSmokeSettings,
    build_live_smoke_plan,
    main,
    _record_activation,
)


def test_activation_evidence_uses_verified_receipt_version_once(tmp_path) -> None:
    path = tmp_path / "evidence.jsonl"
    _record_activation(
        path,
        health={"modelVersion": 1},
        activation={
            "schemaVersion": 2,
            "modelVersion": 1,
            "changedVectorCount": 2,
            "beforeVectorStateSha256": "a" * 64,
            "afterVectorStateSha256": "b" * 64,
        },
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["modelVersion"] == 1
    assert row["changedVectorCount"] == 2


ROOT = Path(__file__).resolve().parents[2]


def test_live_smoke_plan_is_small_nonformal_and_uses_isolated_namespaces(tmp_path) -> None:
    settings = LiveSmokeSettings(
        fixture_root=ROOT / "fixtures/online/tiny",
        state_root=tmp_path / "live-smoke",
        kafka_bootstrap_servers="127.0.0.1:29092",
        request_count=2,
        timeout_seconds=180,
    )

    plan = build_live_smoke_plan(settings)
    scopes = [settings.scope(row) for row in plan.conditions]

    assert len(plan.conditions) == 9
    assert plan.total_request_limit == 18
    assert plan.formal_performance_claim is False
    assert all(row.request_limit == 2 for row in plan.conditions)
    assert len({scope.port for scope in scopes}) == 9
    assert all(scope.port >= 18_000 for scope in scopes)
    assert len({scope.consumer_group for scope in scopes}) == 9
    assert all(scope.state_root.is_relative_to(settings.state_root) for scope in scopes)
    assert not hasattr(settings, "database_url")


def test_same_process_fixture_condition_runs_real_http_feedback_and_training(tmp_path) -> None:
    settings = LiveSmokeSettings(
        fixture_root=ROOT / "fixtures/online/tiny",
        state_root=tmp_path / "live-smoke",
        kafka_bootstrap_servers="memory://",
        request_count=2,
        timeout_seconds=30,
    )
    condition = next(
        row
        for row in build_live_smoke_plan(settings).conditions
        if row.topology == "same_process"
        and row.load_mode == "training_and_activation"
    )

    result = FixtureLiveSmoke(settings).execute(
        condition,
        condition.request_limit,
        20,
        _NeverCancelled(),
    )

    evidence = [
        json.loads(line)
        for line in Path(result.raw_results_path).read_text(encoding="utf-8").splitlines()
    ]
    assert result.request_count == 2
    assert result.edges_observed >= 2
    assert result.startup_verified and result.cleanup_verified
    assert result.progress_observed
    assert result.client_p95_ms > 0
    assert result.trainer_failure_status == "not_applicable"
    assert {row["kind"] for row in evidence} >= {
        "recommendation",
        "feedback",
        "training",
        "activation",
        "topology",
    }
    assert all(row["formalPerformanceClaim"] is False for row in evidence)
    activation = next(row for row in evidence if row["kind"] == "activation")
    assert activation["changedVectorCount"] > 0
    assert activation["beforeVectorStateSha256"] != activation["afterVectorStateSha256"]


class _NeverCancelled:
    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float | None = None) -> bool:
        return False


def test_split_fixture_condition_refuses_to_fake_kafka_with_memory_transport(tmp_path) -> None:
    settings = LiveSmokeSettings(
        fixture_root=ROOT / "fixtures/online/tiny",
        state_root=tmp_path / "live-smoke",
        kafka_bootstrap_servers="memory://",
        request_count=1,
        timeout_seconds=30,
    )
    condition = next(
        row
        for row in build_live_smoke_plan(settings).conditions
        if row.topology == "same_host_split" and row.load_mode == "serving_only"
    )

    with pytest.raises(RuntimeError, match="real Kafka"):
        FixtureLiveSmoke(settings).execute(condition, 1, 20, _NeverCancelled())


def test_failed_cli_rerun_removes_stale_success_receipt(tmp_path, monkeypatch) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"stale":true}\n', encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise RuntimeError("condition failed")

    monkeypatch.setattr("babel_benchmark.matrix.run_tiny_smoke", fail)
    with pytest.raises(RuntimeError, match="condition failed"):
        main(
            [
                "--fixture-root",
                str(ROOT / "fixtures/online/tiny"),
                "--state-root",
                str(tmp_path / "state"),
                "--receipt",
                str(receipt),
            ]
        )

    assert not receipt.exists()
    assert not receipt.with_suffix(".json.running").exists()
