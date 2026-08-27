from __future__ import annotations

import json
import multiprocessing
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import babel_benchmark.matrix as matrix_module
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
        raw_path = tmp_path / f"{condition.condition_id}.jsonl"
        raw_path.write_text("{}\n", encoding="utf-8")
        return SmokeConditionResult(
            condition_id=condition.condition_id,
            request_count=request_limit,
            startup_verified=True,
            cleanup_verified=True,
            edges_observed=2,
            progress_observed=True,
            raw_results_path=str(raw_path),
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
    plan = tiny_smoke_plan(timeout_seconds=0.1)
    started = time.monotonic()
    cancelled_path = tmp_path / "cancelled"
    forbidden_post_timeout_side_effect = tmp_path / "forbidden"

    def block(_condition, _limit, _timeout, cancel):
        if cancel.wait(1):
            cancelled_path.write_text("yes", encoding="utf-8")
            return None
        forbidden_post_timeout_side_effect.write_text("bad", encoding="utf-8")
        return None

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            block,
            receipt_path=tmp_path / "receipt.json",
        )

    assert time.monotonic() - started < 0.25
    assert cancelled_path.read_text(encoding="utf-8") == "yes"
    time.sleep(0.03)
    assert not forbidden_post_timeout_side_effect.exists()


def test_timeout_does_not_return_while_cancel_ignoring_callback_is_alive(
    tmp_path,
) -> None:
    plan = tiny_smoke_plan(timeout_seconds=0.01)
    delayed_side_effect = tmp_path / "delayed-side-effect"
    started = time.monotonic()

    def ignores_cancel(_condition, _limit, _timeout, _cancel):
        time.sleep(0.12)
        delayed_side_effect.write_text("bad", encoding="utf-8")
        return None

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            ignores_cancel,
            receipt_path=tmp_path / "receipt.json",
        )

    assert time.monotonic() - started < 0.1
    time.sleep(0.14)
    assert not delayed_side_effect.exists()


def test_timeout_terminates_never_returning_executor(tmp_path) -> None:
    plan = tiny_smoke_plan(timeout_seconds=0.03)
    delayed_side_effect = tmp_path / "never-side-effect"
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()

    def never_returns(_condition, _limit, _timeout, _cancel):
        while True:
            time.sleep(0.2)
            delayed_side_effect.write_text("bad", encoding="utf-8")

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            never_returns,
            receipt_path=tmp_path / "receipt.json",
        )

    assert time.monotonic() - started < 0.2
    assert {child.pid for child in multiprocessing.active_children()} == before
    time.sleep(0.22)
    assert not delayed_side_effect.exists()


def test_timeout_terminates_executor_process_group_descendants(tmp_path) -> None:
    plan = tiny_smoke_plan(timeout_seconds=0.08)
    descendant_pid_path = tmp_path / "descendant.pid"
    delayed_side_effect = tmp_path / "descendant-side-effect"
    started = time.monotonic()

    def launches_descendant(_condition, _limit, _timeout, _cancel):
        script = (
            "import os,time,pathlib; "
            f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(os.getpid())); "
            "time.sleep(0.25); "
            f"pathlib.Path({str(delayed_side_effect)!r}).write_text('bad'); "
            "time.sleep(10)"
        )
        subprocess.Popen([sys.executable, "-c", script])
        while True:
            time.sleep(1)

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            launches_descendant,
            receipt_path=tmp_path / "receipt.json",
        )

    assert time.monotonic() - started < 0.25
    assert descendant_pid_path.is_file()
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    descendant_proc = Path(f"/proc/{descendant_pid}")
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and descendant_proc.exists():
        time.sleep(0.01)
    assert not descendant_proc.exists()
    time.sleep(0.3)
    assert not delayed_side_effect.exists()


def test_timeout_recovers_late_process_group_handshake(
    tmp_path, monkeypatch
) -> None:
    plan = tiny_smoke_plan(timeout_seconds=0.14)
    descendant_pid_path = tmp_path / "late-descendant.pid"
    delayed_side_effect = tmp_path / "late-descendant-side-effect"
    real_setsid = matrix_module.os.setsid

    def delayed_setsid():
        time.sleep(0.07)
        return real_setsid()

    monkeypatch.setattr(matrix_module.os, "setsid", delayed_setsid)

    def launches_descendant(_condition, _limit, _timeout, _cancel):
        script = (
            "import os,time,pathlib; "
            f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(os.getpid())); "
            "time.sleep(0.25); "
            f"pathlib.Path({str(delayed_side_effect)!r}).write_text('bad')"
        )
        subprocess.Popen([sys.executable, "-c", script])
        while True:
            time.sleep(1)

    with pytest.raises(TimeoutError, match="strict suite timeout"):
        run_tiny_smoke(
            plan,
            launches_descendant,
            receipt_path=tmp_path / "receipt.json",
        )

    assert not descendant_pid_path.exists()
    time.sleep(0.3)
    assert not delayed_side_effect.exists()


@pytest.mark.parametrize(
    "invalid_update,raw_state",
    [
        ({"request_count": 0}, "valid"),
        ({"edges_observed": 0}, "valid"),
        ({"startup_verified": False}, "valid"),
        ({"cleanup_verified": False}, "valid"),
        ({"progress_observed": False}, "valid"),
        ({"ratios_observed": False}, "valid"),
        ({"trainer_failure_serving_available": False}, "valid"),
        ({}, "missing"),
        ({}, "empty"),
    ],
)
def test_tiny_smoke_rejects_incomplete_success_evidence(
    tmp_path, invalid_update, raw_state
) -> None:
    plan = tiny_smoke_plan(timeout_seconds=2)
    raw_path = tmp_path / "raw.jsonl"
    if raw_state == "valid":
        raw_path.write_text("{}\n", encoding="utf-8")
    elif raw_state == "empty":
        raw_path.touch()
    valid = SmokeConditionResult(
        condition_id=plan.conditions[0].condition_id,
        request_count=1,
        startup_verified=True,
        cleanup_verified=True,
        edges_observed=1,
        progress_observed=True,
        raw_results_path=str(raw_path),
        ratios_observed=True,
        trainer_failure_serving_available=True,
    )

    with pytest.raises(ValueError, match="successful smoke condition"):
        run_tiny_smoke(
            plan,
            lambda condition, _limit, _timeout, _cancel: replace(
                valid,
                condition_id=condition.condition_id,
                **invalid_update,
            ),
            receipt_path=tmp_path / "receipt.json",
        )

    assert not (tmp_path / "receipt.json").exists()


def test_lifecycle_smoke_starts_once_runs_nine_conditions_and_stops(tmp_path) -> None:
    events = []
    plan = tiny_smoke_plan(timeout_seconds=30)
    runtime = CallbackSmokeRuntime(
        start_suite=lambda _plan: events.append("start") or "trial-1",
        run_condition=lambda handle, condition, limit, _timeout, _cancel: (
            events.append(("run", handle, condition.condition_id)),
            (tmp_path / f"{condition.condition_id}.jsonl").write_text(
                "{}\n", encoding="utf-8"
            ),
            SmokeConditionResult(
                condition_id=condition.condition_id,
                request_count=limit,
                startup_verified=True,
                cleanup_verified=True,
                edges_observed=1,
                progress_observed=True,
                raw_results_path=str(tmp_path / f"{condition.condition_id}.jsonl"),
                ratios_observed=True,
                trainer_failure_serving_available=True,
            ),
        )[2],
        stop_suite=lambda handle: events.append(("stop", handle)),
    )

    receipt = run_lifecycle_tiny_smoke(
        plan, runtime, receipt_path=tmp_path / "receipt.json"
    )

    assert receipt.total_requests == 180
    assert events[0] == "start"
    assert events[-1] == ("stop", "trial-1")
    assert len(list(tmp_path.glob("*.jsonl"))) == 9


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
