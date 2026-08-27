"""Bounded fault injection over the Task 9 runtime lifecycle hooks."""

import hashlib
import importlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

FaultName = Literal[
    "trainer_kill_restart",
    "kafka_pause_resume",
    "invalid_model_state",
    "serving_restart",
]


@dataclass(frozen=True, slots=True)
class FaultSnapshot:
    serving_available: bool
    kafka_lag: int
    duplicate_events: int
    lost_events: int
    trainer_version: int
    serving_version: int
    trainer_running: bool = True
    kafka_available: bool = True

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.kafka_lag,
                self.duplicate_events,
                self.lost_events,
                self.trainer_version,
                self.serving_version,
            )
        ):
            raise ValueError("fault observations cannot contain negative counters")


class FaultLifecycleHooks(Protocol):
    def probe(self) -> FaultSnapshot: ...

    def kill_trainer(self) -> None: ...

    def restart_trainer(self) -> None: ...

    def pause_kafka(self) -> None: ...

    def resume_kafka(self) -> None: ...

    def inject_invalid_state(self) -> bool: ...

    def stop_serving(self) -> None: ...

    def start_serving(self) -> None: ...


class FaultCampaignHooks(FaultLifecycleHooks, Protocol):
    def cleanup(self) -> None: ...


@dataclass(frozen=True, slots=True)
class FaultCampaignTarget:
    trial_id: UUID
    creator_count: int
    condition_count: int
    trial_sha256: str
    population_manifest_sha256: str


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be one JSON object")
    return value


def load_accepted_fault_target(
    trial_path: str | Path, population_manifest_path: str | Path
) -> FaultCampaignTarget:
    """Bind fault evidence to one completed, explicitly accepted real population."""
    trial_document = _object(Path(trial_path), "saved performance trial")
    trial = trial_document.get("trial", trial_document)
    if not isinstance(trial, dict):
        raise TypeError("saved performance trial must contain one trial object")
    try:
        trial_id = UUID(str(trial["experimentId"]))
        creator_count = int(trial["creatorCount"])
        target_count = int(trial["targetCreatedBabels"])
        condition_count = int(trial["progress"]["conditionCount"])
        results = trial["results"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("saved performance trial identity is incomplete") from error
    expected_conditions = 9 if creator_count == 50 else 6
    if (
        trial.get("status") != "completed"
        or trial.get("operatorApproved") is not True
        or trial.get("populationReady") is not True
    ):
        raise ValueError("fault campaign requires a completed and operator-approved trial")
    if (
        creator_count not in {50, 100, 500}
        or target_count != 10_000
        or int(trial.get("requiredVectorCount", 0)) != 10_000
        or condition_count != expected_conditions
        or not isinstance(results, list)
        or len(results) != expected_conditions
    ):
        raise ValueError("fault campaign requires one complete formal cohort matrix")

    population_path = Path(population_manifest_path)
    population = _object(population_path, "frozen population manifest")
    if (
        population.get("experimentId") != str(trial_id)
        or population.get("babelCount") != 10_000
        or population.get("scheduleCount") != 10_000
        or population.get("juneCount") != 5_000
        or population.get("julyCount") != 5_000
        or population.get("creatorCount") != creator_count
        or population.get("embeddingDimension") != 100
        or not isinstance(population.get("vectorsSha256"), str)
        or len(population["vectorsSha256"]) != 64
    ):
        raise ValueError("frozen population differs from the accepted formal cohort")
    return FaultCampaignTarget(
        trial_id=trial_id,
        creator_count=creator_count,
        condition_count=condition_count,
        trial_sha256=hashlib.sha256(Path(trial_path).read_bytes()).hexdigest(),
        population_manifest_sha256=hashlib.sha256(
            population_path.read_bytes()
        ).hexdigest(),
    )


def load_fault_hooks(specification: str) -> FaultCampaignHooks:
    """Load an operator-owned lifecycle adapter without serializing its controls."""
    try:
        module_name, factory_name = specification.split(":", 1)
    except ValueError as error:
        raise ValueError("fault hooks factory must use module.path:factory_name") from error
    dotted_name = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
    if not dotted_name.fullmatch(module_name) or not factory_name.isidentifier():
        raise ValueError("fault hooks factory must use module.path:factory_name")
    try:
        factory = getattr(importlib.import_module(module_name), factory_name)
    except (ImportError, AttributeError) as error:
        raise ValueError("fault hooks factory is unavailable") from error
    if not callable(factory):
        raise TypeError("fault hooks factory is not callable")
    hooks = factory()
    required = (
        "probe",
        "kill_trainer",
        "restart_trainer",
        "pause_kafka",
        "resume_kafka",
        "inject_invalid_state",
        "stop_serving",
        "start_serving",
        "cleanup",
    )
    if any(not callable(getattr(hooks, method, None)) for method in required):
        raise ValueError("fault hooks factory returned an incomplete lifecycle adapter")
    return hooks


class HttpTask9TopologyControl:
    """Use Task 9's trainer-stop endpoint plus explicit service restart hooks."""

    def __init__(
        self,
        *,
        base_url: str,
        worker_token: str,
        start_trainer: Callable[[], None],
        stop_serving: Callable[[], None],
        start_serving: Callable[[], None],
        transport: Any | None = None,
    ) -> None:
        if len(worker_token) != 64 or any(
            c not in "0123456789abcdef" for c in worker_token
        ):
            raise ValueError("Task 9 worker token must be 64 lowercase hex digits")
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Task 9 control endpoint must be loopback")
        if transport is None:
            import httpx

            transport = httpx.Client()
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Babel-Worker-Token": worker_token}
        self._start_trainer = start_trainer
        self._stop_serving = stop_serving
        self._start_serving = start_serving

    def kill_trainer(self) -> None:
        response = self._transport.post(
            f"{self._base_url}/v1/topology/trainer/stop",
            headers=self._headers,
            timeout=5.0,
        )
        if int(response.status_code) != 202:
            raise RuntimeError(
                f"Task 9 trainer stop failed with HTTP {response.status_code}"
            )

    def restart_trainer(self) -> None:
        self._start_trainer()

    def stop_serving(self) -> None:
        self._stop_serving()

    def start_serving(self) -> None:
        self._start_serving()

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()


class CallbackKafkaControl:
    def __init__(
        self, *, pause: Callable[[], None], resume: Callable[[], None]
    ) -> None:
        self._pause = pause
        self._resume = resume

    def pause(self) -> None:
        self._pause()

    def resume(self) -> None:
        self._resume()


class Task9FaultHooksAdapter:
    """Compose Task 9 topology, Kafka and activation seams for FaultController."""

    def __init__(
        self,
        *,
        topology: HttpTask9TopologyControl,
        kafka: CallbackKafkaControl,
        probe: Callable[[], FaultSnapshot],
        invalid_state_injector: Callable[[], bool],
    ) -> None:
        self.topology = topology
        self.kafka = kafka
        self._probe = probe
        self._invalid_state = invalid_state_injector

    def probe(self) -> FaultSnapshot:
        return self._probe()

    def kill_trainer(self) -> None:
        self.topology.kill_trainer()

    def restart_trainer(self) -> None:
        self.topology.restart_trainer()

    def pause_kafka(self) -> None:
        self.kafka.pause()

    def resume_kafka(self) -> None:
        self.kafka.resume()

    def inject_invalid_state(self) -> bool:
        return bool(self._invalid_state())

    def stop_serving(self) -> None:
        self.topology.stop_serving()

    def start_serving(self) -> None:
        self.topology.start_serving()


@dataclass(frozen=True, slots=True)
class FaultEvidence:
    fault: FaultName
    serving_available_during_fault: bool
    serving_available_after_recovery: bool
    maximum_kafka_lag: int
    detection_ns: int
    recovery_ns: int
    duplicate_events: int
    lost_events: int
    trainer_version_before: int
    trainer_version_after: int
    serving_version_before: int
    serving_version_after: int
    invalid_state_rejected: bool | None
    last_valid_serving_version_retained: bool | None


class FaultController:
    """Invoke only explicit lifecycle seams; it never reaches into processes."""

    def __init__(
        self,
        hooks: FaultLifecycleHooks,
        *,
        clock_ns=time.monotonic_ns,
    ) -> None:
        self._hooks = hooks
        self._clock = clock_ns

    def _run(self, fault: FaultName) -> FaultEvidence:
        before = self._hooks.probe()
        injected_at = self._clock()
        invalid_state_rejected: bool | None = None
        recovery: Callable[[], None] | None = None
        try:
            if fault == "trainer_kill_restart":
                recovery = self._hooks.restart_trainer
                self._hooks.kill_trainer()
            elif fault == "kafka_pause_resume":
                recovery = self._hooks.resume_kafka
                self._hooks.pause_kafka()
            elif fault == "invalid_model_state":
                invalid_state_rejected = self._hooks.inject_invalid_state()
            else:
                recovery = self._hooks.start_serving
                self._hooks.stop_serving()
            during = self._hooks.probe()
            detected_at = self._clock()
        finally:
            if recovery is not None:
                recovery()
        after = self._hooks.probe()
        recovered_at = (
            detected_at if fault == "invalid_model_state" else self._clock()
        )
        retained = None
        if fault == "invalid_model_state":
            retained = (
                during.serving_version == before.serving_version
                and after.serving_version == before.serving_version
                and during.serving_available
                and after.serving_available
            )
        return FaultEvidence(
            fault=fault,
            serving_available_during_fault=during.serving_available,
            serving_available_after_recovery=after.serving_available,
            maximum_kafka_lag=max(before.kafka_lag, during.kafka_lag, after.kafka_lag),
            detection_ns=max(0, detected_at - injected_at),
            recovery_ns=max(0, recovered_at - detected_at),
            duplicate_events=max(0, after.duplicate_events - before.duplicate_events),
            lost_events=max(0, after.lost_events - before.lost_events),
            trainer_version_before=before.trainer_version,
            trainer_version_after=after.trainer_version,
            serving_version_before=before.serving_version,
            serving_version_after=after.serving_version,
            invalid_state_rejected=invalid_state_rejected,
            last_valid_serving_version_retained=retained,
        )

    def run_all(self, *, receipt_path: str | Path) -> tuple[FaultEvidence, ...]:
        evidence = tuple(
            self._run(fault)
            for fault in (
                "trainer_kill_restart",
                "kafka_pause_resume",
                "invalid_model_state",
                "serving_restart",
            )
        )
        path = Path(receipt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_text(
            json.dumps(
                [asdict(row) for row in evidence],
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
        return evidence


class BoundedFaultCampaign:
    """Run the four same-host fault windows without making topology claims."""

    _FAULTS: tuple[FaultName, ...] = (
        "trainer_kill_restart",
        "kafka_pause_resume",
        "invalid_model_state",
        "serving_restart",
    )

    def __init__(
        self,
        target: FaultCampaignTarget,
        hooks: FaultCampaignHooks,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
        detection_timeout_seconds: float = 10.0,
        recovery_timeout_seconds: float = 30.0,
        fault_hold_seconds: float = 1.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if detection_timeout_seconds <= 0 or recovery_timeout_seconds <= 0:
            raise ValueError("fault detection and recovery timeouts must be positive")
        if fault_hold_seconds < 0 or poll_interval_seconds <= 0:
            raise ValueError("fault hold must be nonnegative and polling must be positive")
        self._target = target
        self._hooks = hooks
        self._clock = clock_ns
        self._sleep = sleep
        self._detection_timeout_ns = int(detection_timeout_seconds * 1_000_000_000)
        self._recovery_timeout_ns = int(recovery_timeout_seconds * 1_000_000_000)
        self._fault_hold_seconds = fault_hold_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def _wait_for(
        self,
        predicate: Callable[[FaultSnapshot], bool],
        *,
        timeout_ns: int,
        samples: list[FaultSnapshot],
        label: str,
    ) -> tuple[FaultSnapshot, int]:
        started = self._clock()
        while True:
            snapshot = self._hooks.probe()
            samples.append(snapshot)
            observed = self._clock()
            if predicate(snapshot):
                return snapshot, observed
            if observed - started >= timeout_ns:
                raise TimeoutError(f"fault campaign timed out waiting for {label}")
            self._sleep(self._poll_interval_seconds)

    @staticmethod
    def _detected(fault: FaultName, before: FaultSnapshot, row: FaultSnapshot) -> bool:
        if fault == "trainer_kill_restart":
            return not row.trainer_running
        if fault == "kafka_pause_resume":
            return not row.kafka_available or row.kafka_lag > before.kafka_lag
        if fault == "serving_restart":
            return not row.serving_available
        return True

    @staticmethod
    def _recovered(fault: FaultName, before: FaultSnapshot, row: FaultSnapshot) -> bool:
        if fault == "trainer_kill_restart":
            return row.trainer_running and row.serving_available
        if fault == "kafka_pause_resume":
            return row.kafka_available and row.kafka_lag <= before.kafka_lag
        if fault == "serving_restart":
            return row.serving_available
        return row.serving_available and row.serving_version == before.serving_version

    def _run_fault(self, fault: FaultName) -> dict[str, Any]:
        before = self._hooks.probe()
        samples: list[FaultSnapshot] = []
        started_ns = self._clock()
        invalid_rejected: bool | None = None
        recovery: Callable[[], None] | None = None
        recovered = before
        detected_ns = started_ns
        recovery_started_ns = started_ns
        try:
            if fault == "trainer_kill_restart":
                recovery = self._hooks.restart_trainer
                self._hooks.kill_trainer()
            elif fault == "kafka_pause_resume":
                recovery = self._hooks.resume_kafka
                self._hooks.pause_kafka()
            elif fault == "invalid_model_state":
                invalid_rejected = bool(self._hooks.inject_invalid_state())
            else:
                recovery = self._hooks.start_serving
                self._hooks.stop_serving()
            _during, detected_ns = self._wait_for(
                lambda row: self._detected(fault, before, row),
                timeout_ns=self._detection_timeout_ns,
                samples=samples,
                label=f"{fault} detection",
            )
            if self._fault_hold_seconds:
                self._sleep(self._fault_hold_seconds)
                samples.append(self._hooks.probe())
        finally:
            recovery_started_ns = self._clock()
            if recovery is not None:
                recovery()
        recovered, recovered_ns = self._wait_for(
            lambda row: self._recovered(fault, before, row),
            timeout_ns=self._recovery_timeout_ns,
            samples=samples,
            label=f"{fault} recovery",
        )
        ended_ns = self._clock()
        during_samples = samples[:-1] if len(samples) > 1 else samples
        available_samples = sum(row.serving_available for row in during_samples)
        maximum_lag = max([before.kafka_lag, *(row.kafka_lag for row in samples)])
        retained = None
        if fault == "invalid_model_state":
            retained = (
                invalid_rejected is True
                and all(row.serving_version == before.serving_version for row in samples)
                and all(row.serving_available for row in samples)
            )
        fault_failure: str | None = None
        if fault == "trainer_kill_restart" and not all(
            row.serving_available for row in during_samples
        ):
            fault_failure = "serving continuity was lost during trainer restart"
        elif fault == "invalid_model_state" and retained is not True:
            fault_failure = (
                "invalid child/checkpoint was accepted or replaced the last valid version"
            )
        elif fault == "kafka_pause_resume" and maximum_lag <= before.kafka_lag:
            fault_failure = "Kafka pause produced no observable lag increase"
        counter_reset = (
            recovered.duplicate_events < before.duplicate_events
            or recovered.lost_events < before.lost_events
        )
        return {
            "fault": fault,
            "invalidStateKind": (
                "child_or_checkpoint" if fault == "invalid_model_state" else None
            ),
            "status": "completed" if fault_failure is None else "failed",
            "failure": fault_failure,
            "faultWindow": {
                "startedNs": started_ns,
                "detectedNs": detected_ns,
                "recoveryStartedNs": recovery_started_ns,
                "recoveredNs": recovered_ns,
                "endedNs": ended_ns,
            },
            "detectionNs": max(0, detected_ns - started_ns),
            "recoveryNs": max(0, recovered_ns - recovery_started_ns),
            "availability": {
                "availableSamples": available_samples,
                "totalSamples": len(during_samples),
                "availableRatio": (
                    available_samples / len(during_samples) if during_samples else 0.0
                ),
                "availableDuringFault": all(
                    row.serving_available for row in during_samples
                ),
                "availableAfterRecovery": recovered.serving_available,
            },
            "kafkaLag": {
                "before": before.kafka_lag,
                "maximum": maximum_lag,
                "after": recovered.kafka_lag,
                "recoveredToBaseline": recovered.kafka_lag <= before.kafka_lag,
            },
            "duplicates": max(0, recovered.duplicate_events - before.duplicate_events),
            "lost": max(0, recovered.lost_events - before.lost_events),
            "eventCounterResetDetected": counter_reset,
            "versions": {
                "trainerBefore": before.trainer_version,
                "trainerAfter": recovered.trainer_version,
                "servingBefore": before.serving_version,
                "servingAfter": recovered.serving_version,
            },
            "invalidStateRejected": invalid_rejected,
            "lastValidServingVersionRetained": retained,
        }

    @staticmethod
    def _write(path: Path, receipt: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".partial")
        partial.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)

    def run(self, receipt_path: str | Path) -> dict[str, Any]:
        path = Path(receipt_path)
        started_ns = self._clock()
        faults: list[dict[str, Any]] = []
        failure: str | None = None
        failed_fault: FaultName | None = None
        cleanup_error: str | None = None
        cleanup_snapshot: FaultSnapshot | None = None
        initial_snapshot: FaultSnapshot | None = None
        try:
            initial_snapshot = self._hooks.probe()
            for fault in self._FAULTS:
                failed_fault = fault
                row = self._run_fault(fault)
                faults.append(row)
                if row["status"] == "failed":
                    failure = str(row["failure"])
                    break
                failed_fault = None
        except Exception as error:  # noqa: BLE001 - persist arbitrary hook failures
            failure = str(error)
        finally:
            try:
                self._hooks.cleanup()
                cleanup_snapshot = self._hooks.probe()
            except Exception as error:  # noqa: BLE001 - cleanup must not hide receipt
                cleanup_error = str(error)
                if failure is None:
                    failure = cleanup_error
        cleanup_verified = bool(
            cleanup_snapshot is not None
            and cleanup_snapshot.serving_available
            and cleanup_snapshot.trainer_running
            and cleanup_snapshot.kafka_available
            and (
                initial_snapshot is None
                or cleanup_snapshot.kafka_lag <= initial_snapshot.kafka_lag
            )
        )
        if not cleanup_verified and failure is None:
            failure = "fault campaign cleanup could not verify healthy services"
        receipt: dict[str, Any] = {
            "schemaVersion": 1,
            "experimentId": str(self._target.trial_id),
            "creatorCount": self._target.creator_count,
            "conditionCount": self._target.condition_count,
            "acceptedTrialSha256": self._target.trial_sha256,
            "populationManifestSha256": self._target.population_manifest_sha256,
            "deploymentScope": "same_host",
            "evidenceUse": "fault_only_not_topology_performance",
            "status": "completed" if failure is None else "failed",
            "campaignWindow": {
                "startedNs": started_ns,
                "endedNs": self._clock(),
                "detectionTimeoutNs": self._detection_timeout_ns,
                "recoveryTimeoutNs": self._recovery_timeout_ns,
                "faultHoldNs": int(self._fault_hold_seconds * 1_000_000_000),
            },
            "faults": faults,
            "cleanup": {
                "verified": cleanup_verified,
                "error": cleanup_error,
            },
            "failure": failure,
            "failedFault": failed_fault,
        }
        self._write(path, receipt)
        return receipt


__all__ = [
    "BoundedFaultCampaign",
    "CallbackKafkaControl",
    "FaultCampaignHooks",
    "FaultCampaignTarget",
    "FaultController",
    "FaultEvidence",
    "FaultLifecycleHooks",
    "FaultSnapshot",
    "HttpTask9TopologyControl",
    "Task9FaultHooksAdapter",
    "load_accepted_fault_target",
    "load_fault_hooks",
]
