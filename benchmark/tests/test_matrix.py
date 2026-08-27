from __future__ import annotations

import json
import time

import pytest

from babel_benchmark.matrix import (
    CallbackSmokeRuntime,
    DashboardPerformanceHttpClient,
    SmokeConditionResult,
    run_lifecycle_tiny_smoke,
    run_tiny_smoke,
    tiny_smoke_plan,
)


def test_tiny_smoke_plan_is_exactly_bounded_three_by_three() -> None:
    plan = tiny_smoke_plan(timeout_seconds=30.0)

    assert len(plan.conditions) == 9
    assert {row.topology for row in plan.conditions} == {
        "same_process",
        "same_host_split",
        "same_host_isolated",
    }
    assert {row.load_mode for row in plan.conditions} == {
        "serving_only",
        "training_no_activation",
        "training_and_activation",
    }
    assert all(row.request_limit == 20 for row in plan.conditions)
    assert plan.total_request_limit == 180
    assert plan.fixture == "current_fixture"
    assert plan.timeout_seconds == 30.0
    assert plan.formal_performance_claim is False


def test_tiny_smoke_persists_complete_nonformal_receipt(tmp_path) -> None:
    plan = tiny_smoke_plan(timeout_seconds=30.0)
    receipt_path = tmp_path / "smoke-receipt.json"

    def execute(condition, request_limit, timeout_seconds, cancel):
        assert request_limit == 20
        assert 0 < timeout_seconds <= 30.0
        assert not cancel.is_set()
        return SmokeConditionResult(
            condition_id=condition.condition_id,
            request_count=request_limit,
            startup_verified=True,
            cleanup_verified=True,
            edges_observed=2,
            progress_observed=True,
            raw_results_path=f"raw/{condition.condition_id}.jsonl",
            ratios_observed=True,
            trainer_failure_serving_available=True,
        )

    receipt = run_tiny_smoke(plan, execute, receipt_path=receipt_path)

    assert len(receipt.conditions) == 9
    assert receipt.total_requests == 180
    assert receipt.formal_performance_claim is False
    assert receipt.startup_cleanup_verified
    assert receipt.edges_observed
    assert receipt.progress_observed
    assert receipt.raw_persistence_verified
    assert receipt.ratios_observed
    assert receipt.trainer_failure_availability_verified
    assert json.loads(receipt_path.read_text())["formal_performance_claim"] is False


def test_tiny_smoke_fails_closed_when_strict_suite_timeout_expires(tmp_path) -> None:
    ticks = iter((0.0, 0.0, 31.0))
    plan = tiny_smoke_plan(timeout_seconds=30.0)
    receipt_path = tmp_path / "must-not-exist.json"

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            lambda condition, _limit, _timeout, _cancel: SmokeConditionResult(
                condition_id=condition.condition_id,
                request_count=1,
                startup_verified=True,
                cleanup_verified=True,
                edges_observed=1,
                progress_observed=True,
                raw_results_path="raw.jsonl",
                ratios_observed=True,
                trainer_failure_serving_available=True,
            ),
            receipt_path=receipt_path,
            monotonic=lambda: next(ticks),
        )

    assert not receipt_path.exists()


def test_tiny_smoke_returns_at_deadline_when_condition_callback_blocks(tmp_path) -> None:
    plan = tiny_smoke_plan(timeout_seconds=0.02)
    started = time.monotonic()
    cancelled = []
    forbidden_post_timeout_side_effect = []

    def block(_condition, _limit, _timeout, cancel):
        if cancel.wait(1):
            cancelled.append(True)
            return None
        forbidden_post_timeout_side_effect.append(True)
        return None

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            block,
            receipt_path=tmp_path / "receipt.json",
        )

    assert time.monotonic() - started < 0.2
    assert cancelled == [True]
    time.sleep(0.03)
    assert forbidden_post_timeout_side_effect == []


def test_lifecycle_smoke_starts_once_runs_nine_conditions_and_stops(tmp_path) -> None:
    events = []
    plan = tiny_smoke_plan(timeout_seconds=30)
    runtime = CallbackSmokeRuntime(
        start_suite=lambda _plan: events.append("start") or "trial-1",
        run_condition=lambda handle, condition, limit, _timeout, _cancel: (
            events.append(("run", handle, condition.condition_id)),
            SmokeConditionResult(
                condition_id=condition.condition_id,
                request_count=limit,
                startup_verified=True,
                cleanup_verified=True,
                edges_observed=1,
                progress_observed=True,
                raw_results_path=f"raw/{condition.condition_id}.jsonl",
                ratios_observed=True,
                trainer_failure_serving_available=True,
            ),
        )[1],
        stop_suite=lambda handle: events.append(("stop", handle)),
    )

    receipt = run_lifecycle_tiny_smoke(
        plan, runtime, receipt_path=tmp_path / "receipt.json"
    )

    assert receipt.total_requests == 180
    assert events[0] == "start"
    assert len([row for row in events if isinstance(row, tuple) and row[0] == "run"]) == 9
    assert events[-1] == ("stop", "trial-1")


def test_dashboard_http_adapter_uses_saved_trial_endpoints() -> None:
    calls = []

    class Response:
        status_code = 201

        def json(self):
            return {"trial": {"experimentId": "trial-1"}}

    class Transport:
        def request(self, method, url, *, headers, json, timeout):
            calls.append((method, url, headers, json, timeout))
            response = Response()
            if method == "POST" and url.endswith("graceful-stop"):
                response.status_code = 202
            return response

    client = DashboardPerformanceHttpClient(
        base_url="http://127.0.0.1:8787",
        admin_nonce="nonce",
        transport=Transport(),
    )

    assert client.create_trial({"creatorCount": 3}) == "trial-1"
    client.graceful_stop("trial-1")
    assert [row[0] for row in calls] == ["POST", "POST"]
    assert calls[0][1].endswith("/admin/api/v1/performance")
    assert calls[1][1].endswith("/admin/api/v1/performance/trial-1/graceful-stop")
