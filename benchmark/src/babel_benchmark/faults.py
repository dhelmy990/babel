"""Bounded fault injection over the Task 9 runtime lifecycle hooks."""

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


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
        if fault == "trainer_kill_restart":
            self._hooks.kill_trainer()
        elif fault == "kafka_pause_resume":
            self._hooks.pause_kafka()
        elif fault == "invalid_model_state":
            invalid_state_rejected = self._hooks.inject_invalid_state()
        else:
            self._hooks.stop_serving()
        during = self._hooks.probe()
        detected_at = self._clock()
        if fault == "trainer_kill_restart":
            self._hooks.restart_trainer()
        elif fault == "kafka_pause_resume":
            self._hooks.resume_kafka()
        elif fault == "serving_restart":
            self._hooks.start_serving()
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


__all__ = [
    "CallbackKafkaControl",
    "FaultController",
    "FaultEvidence",
    "FaultLifecycleHooks",
    "FaultSnapshot",
    "HttpTask9TopologyControl",
    "Task9FaultHooksAdapter",
]
