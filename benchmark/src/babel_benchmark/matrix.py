"""Bounded smoke matrix orchestration for recommendation experiments."""

import json
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


Topology = Literal["same_process", "same_host_split", "same_host_isolated"]
LoadMode = Literal[
    "serving_only", "training_no_activation", "training_and_activation"
]


@dataclass(frozen=True, slots=True)
class SmokeCondition:
    topology: Topology
    load_mode: LoadMode
    request_limit: int = 20

    @property
    def condition_id(self) -> str:
        return f"{self.topology}.{self.load_mode}.pgvector"


@dataclass(frozen=True, slots=True)
class TinySmokePlan:
    conditions: tuple[SmokeCondition, ...]
    fixture: Literal["current_fixture"]
    timeout_seconds: float
    formal_performance_claim: Literal[False]

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("smoke timeout must be positive")
        if len(self.conditions) != 9 or len(
            {(row.topology, row.load_mode) for row in self.conditions}
        ) != 9:
            raise ValueError("tiny smoke requires exactly one 3x3 condition matrix")
        if any(row.request_limit <= 0 or row.request_limit > 20 for row in self.conditions):
            raise ValueError("tiny smoke conditions are capped at 20 requests")
        if self.total_request_limit > 180:
            raise ValueError("tiny smoke is capped at 180 requests")

    @property
    def total_request_limit(self) -> int:
        return sum(row.request_limit for row in self.conditions)


@dataclass(frozen=True, slots=True)
class SmokeConditionResult:
    condition_id: str
    request_count: int
    startup_verified: bool
    cleanup_verified: bool
    edges_observed: int
    progress_observed: bool
    raw_results_path: str
    ratios_observed: bool
    trainer_failure_serving_available: bool

    def __post_init__(self) -> None:
        if not self.condition_id or not self.raw_results_path:
            raise ValueError("smoke condition evidence paths and identity are required")
        if not 0 <= self.request_count <= 20:
            raise ValueError("smoke result exceeds its 20-request bound")
        if self.edges_observed < 0:
            raise ValueError("observed edge count cannot be negative")


@dataclass(frozen=True, slots=True)
class TinySmokeReceipt:
    schema_version: Literal[1]
    fixture: Literal["current_fixture"]
    timeout_seconds: float
    started_at_monotonic_ns: int
    completed_at_monotonic_ns: int
    conditions: tuple[SmokeConditionResult, ...]
    formal_performance_claim: Literal[False]

    @property
    def total_requests(self) -> int:
        return sum(row.request_count for row in self.conditions)

    @property
    def startup_cleanup_verified(self) -> bool:
        return all(row.startup_verified and row.cleanup_verified for row in self.conditions)

    @property
    def edges_observed(self) -> bool:
        return all(row.edges_observed > 0 for row in self.conditions)

    @property
    def progress_observed(self) -> bool:
        return all(row.progress_observed for row in self.conditions)

    @property
    def raw_persistence_verified(self) -> bool:
        return all(bool(row.raw_results_path) for row in self.conditions)

    @property
    def ratios_observed(self) -> bool:
        return all(row.ratios_observed for row in self.conditions)

    @property
    def trainer_failure_availability_verified(self) -> bool:
        return all(row.trainer_failure_serving_available for row in self.conditions)

    def as_document(self) -> dict[str, object]:
        document = asdict(self)
        document.update(
            total_requests=self.total_requests,
            startup_cleanup_verified=self.startup_cleanup_verified,
            edges_observed=self.edges_observed,
            progress_observed=self.progress_observed,
            raw_persistence_verified=self.raw_persistence_verified,
            ratios_observed=self.ratios_observed,
            trainer_failure_availability_verified=(
                self.trainer_failure_availability_verified
            ),
        )
        return document


def tiny_smoke_plan(*, timeout_seconds: float = 60.0) -> TinySmokePlan:
    topologies: tuple[Topology, ...] = (
        "same_process",
        "same_host_split",
        "same_host_isolated",
    )
    load_modes: tuple[LoadMode, ...] = (
        "serving_only",
        "training_no_activation",
        "training_and_activation",
    )
    return TinySmokePlan(
        conditions=tuple(
            SmokeCondition(topology, load_mode)
            for topology in topologies
            for load_mode in load_modes
        ),
        fixture="current_fixture",
        timeout_seconds=timeout_seconds,
        formal_performance_claim=False,
    )


ConditionExecutor = Callable[
    [SmokeCondition, int, float, threading.Event], SmokeConditionResult
]


def _execute_with_timeout(
    execute_condition: ConditionExecutor,
    condition: SmokeCondition,
    timeout_seconds: float,
) -> SmokeConditionResult:
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
    cancel = threading.Event()

    def invoke() -> None:
        try:
            outcome.put(
                (
                    True,
                    execute_condition(
                        condition,
                        condition.request_limit,
                        timeout_seconds,
                        cancel,
                    ),
                )
            )
        except BaseException as error:
            outcome.put((False, error))

    worker = threading.Thread(
        target=invoke,
        daemon=True,
        name=f"tiny-smoke-{condition.condition_id}",
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        cancel.set()
        worker.join(0.05)
        if worker.is_alive():
            raise TimeoutError(
                "tiny smoke exceeded its strict suite timeout; "
                "condition callback did not honor cancellation"
            )
        raise TimeoutError("tiny smoke exceeded its strict suite timeout")
    succeeded, value = outcome.get_nowait()
    if not succeeded:
        if not isinstance(value, BaseException):  # pragma: no cover - defensive
            raise RuntimeError("smoke condition callback failed without an error")
        raise value
    if not isinstance(value, SmokeConditionResult):
        raise TypeError("smoke condition callback returned an invalid result")
    return value


def _persist_receipt(path: Path, receipt: TinySmokeReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(receipt.as_document(), sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.replace(partial, path)


def run_tiny_smoke(
    plan: TinySmokePlan,
    execute_condition: ConditionExecutor,
    *,
    receipt_path: str | Path,
    monotonic: Callable[[], float] = time.monotonic,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> TinySmokeReceipt:
    """Run only the bounded fixture matrix and persist its non-formal receipt."""
    started = monotonic()
    started_ns = monotonic_ns()
    results: list[SmokeConditionResult] = []
    for condition in plan.conditions:
        remaining = plan.timeout_seconds - (monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("tiny smoke exceeded its strict suite timeout")
        result = _execute_with_timeout(execute_condition, condition, remaining)
        if result.condition_id != condition.condition_id:
            raise ValueError("condition result identity differs from smoke plan")
        if result.request_count > condition.request_limit:
            raise ValueError("condition result exceeds planned request bound")
        if monotonic() - started > plan.timeout_seconds:
            raise TimeoutError("tiny smoke exceeded its strict suite timeout")
        results.append(result)
    receipt = TinySmokeReceipt(
        schema_version=1,
        fixture=plan.fixture,
        timeout_seconds=plan.timeout_seconds,
        started_at_monotonic_ns=started_ns,
        completed_at_monotonic_ns=monotonic_ns(),
        conditions=tuple(results),
        formal_performance_claim=False,
    )
    if len(receipt.conditions) != 9 or receipt.total_requests > 180:
        raise RuntimeError("tiny smoke receipt escaped its fixed execution bound")
    _persist_receipt(Path(receipt_path), receipt)
    return receipt


class CallbackSmokeRuntime:
    """Concrete orchestration seam for dashboard/Task 9 condition actions."""

    def __init__(
        self,
        *,
        start_suite: Callable[[TinySmokePlan], Any],
        run_condition: Callable[
            [Any, SmokeCondition, int, float, threading.Event],
            SmokeConditionResult,
        ],
        stop_suite: Callable[[Any], None],
    ) -> None:
        self._start_suite = start_suite
        self._run_condition = run_condition
        self._stop_suite = stop_suite

    def start_suite(self, plan: TinySmokePlan) -> Any:
        return self._start_suite(plan)

    def run_condition(
        self,
        handle: Any,
        condition: SmokeCondition,
        request_limit: int,
        timeout_seconds: float,
        cancel: threading.Event,
    ) -> SmokeConditionResult:
        return self._run_condition(
            handle, condition, request_limit, timeout_seconds, cancel
        )

    def stop_suite(self, handle: Any) -> None:
        self._stop_suite(handle)


def run_lifecycle_tiny_smoke(
    plan: TinySmokePlan,
    runtime: CallbackSmokeRuntime,
    *,
    receipt_path: str | Path,
) -> TinySmokeReceipt:
    """Start one saved trial, execute its nine conditions, then stop it once."""
    destination = Path(receipt_path)
    provisional = destination.with_suffix(destination.suffix + ".running")
    handle = runtime.start_suite(plan)
    completed = False
    try:
        receipt = run_tiny_smoke(
            plan,
            lambda condition, limit, timeout, cancel: runtime.run_condition(
                handle, condition, limit, timeout, cancel
            ),
            receipt_path=provisional,
        )
        completed = True
    finally:
        try:
            runtime.stop_suite(handle)
        except Exception:
            provisional.unlink(missing_ok=True)
            raise
    if not completed:  # pragma: no cover - defensive typing path
        raise RuntimeError("tiny smoke did not complete")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(provisional, destination)
    return receipt


class DashboardPerformanceHttpClient:
    """Loopback adapter for Task 10 saved-trial create and graceful stop."""

    def __init__(
        self,
        *,
        base_url: str,
        admin_nonce: str,
        transport: Any | None = None,
    ) -> None:
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("dashboard endpoint must be loopback")
        if not admin_nonce:
            raise ValueError("dashboard admin nonce is required")
        if transport is None:
            import httpx

            transport = httpx.Client()
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Babel-Admin-Nonce": admin_nonce}

    def create_trial(self, launch: dict[str, object]) -> str:
        response = self._transport.request(
            "POST",
            f"{self._base_url}/admin/api/v1/performance",
            headers=self._headers,
            json=launch,
            timeout=10.0,
        )
        if int(response.status_code) != 201:
            raise RuntimeError(
                f"dashboard trial create failed: HTTP {response.status_code}"
            )
        experiment_id = response.json().get("trial", {}).get("experimentId")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise RuntimeError("dashboard trial create response has no experiment ID")
        return experiment_id

    def graceful_stop(self, experiment_id: str) -> None:
        response = self._transport.request(
            "POST",
            f"{self._base_url}/admin/api/v1/performance/{experiment_id}/graceful-stop",
            headers=self._headers,
            json=None,
            timeout=10.0,
        )
        if int(response.status_code) != 202:
            raise RuntimeError(
                f"dashboard graceful stop failed: HTTP {response.status_code}"
            )

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()


__all__ = [
    "CallbackSmokeRuntime",
    "DashboardPerformanceHttpClient",
    "SmokeCondition",
    "SmokeConditionResult",
    "TinySmokePlan",
    "TinySmokeReceipt",
    "run_lifecycle_tiny_smoke",
    "run_tiny_smoke",
    "tiny_smoke_plan",
]
