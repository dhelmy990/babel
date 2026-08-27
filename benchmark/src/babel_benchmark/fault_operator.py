"""Concrete bounded same-host operator for the real Qwen/Kafka roles."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .faults import FaultCampaignTarget, FaultSnapshot


class DockerKafkaControl:
    """Pause exactly one explicitly named local Kafka container."""

    def __init__(self, container: str, *, run: Callable[..., Any] = subprocess.run):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
            raise ValueError("Kafka container name is invalid")
        self.container = container
        self._run = run

    def _command(self, action: str) -> str:
        result = self._run(
            ["docker", action, self.container],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return str(result.stdout).strip()

    @property
    def available(self) -> bool:
        result = self._run(
            [
                "docker", "inspect", "--format",
                "{{.State.Running}} {{.State.Paused}}", self.container,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return str(result.stdout).strip() == "true false"

    def pause(self) -> None:
        self._command("pause")

    def resume(self) -> None:
        self._command("unpause")


class BoundedLiveTraffic:
    """Issue real recommendation POSTs and publish their feedback, with a hard cap."""

    def __init__(
        self,
        *,
        workload: str | Path,
        endpoint: str,
        kafka_bootstrap_servers: str,
        run_id: UUID,
        limit: int = 1000,
        interval_seconds: float = 0.05,
    ) -> None:
        if limit <= 0 or interval_seconds <= 0:
            raise ValueError("probe limit and interval must be positive")
        root = Path(workload)
        self._requests = self._load(root / "requests.template.jsonl")
        self._feedback = self._load(root / "feedback.template.jsonl")
        if not self._requests or len(self._requests) != len(self._feedback):
            raise ValueError("fault probe workload must pair requests and feedback")
        if any(row.get("runId") != str(run_id) for row in (*self._requests, *self._feedback)):
            raise ValueError("fault probe workload belongs to another condition run")
        self.endpoint = endpoint.rstrip("/")
        self.bootstrap = kafka_bootstrap_servers
        self.limit = limit
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._attempted = 0
        self._published_ids: set[UUID] = set()
        self._pending: list[Any] = []
        self._duplicates = 0
        self._lost = 0

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"fault probe workload is invalid: {path.name}") from error

    def _clients(self):
        from babel_online.feedback.kafka import KafkaFeedbackProducer, _load_clients
        from babel_online.simulation.client import RecommendationClient

        producer_type, _, _ = _load_clients()
        raw = producer_type(
            {
                "bootstrap.servers": self.bootstrap,
                "enable.idempotence": True,
                "acks": "all",
                "message.timeout.ms": 1000,
            }
        )
        return RecommendationClient(self.endpoint, timeout_seconds=10), KafkaFeedbackProducer(
            self.bootstrap, client=raw
        )

    def _request(self, index: int, client: Any):
        from babel_online.contracts import RecommendationRequestV2

        request = RecommendationRequestV2.model_validate(self._requests[index])
        return client.recommend(request.model_copy(update={"requestId": uuid4()}))

    def _event(self, index: int, response: Any):
        from babel_online.contracts import FeedbackEventV2

        event = FeedbackEventV2.model_validate(self._feedback[index])
        return event.model_copy(
            update={
                "eventId": uuid4(),
                "requestId": response.requestId,
                "modelId": response.modelId,
                "modelVersion": response.modelVersion,
                "embeddingSpaceId": response.embeddingSpaceId,
                "retrievalBackend": response.retrievalBackend,
                "sourceVectorOrigin": response.sourceVectorOrigin,
                "occurredAtNs": time.time_ns(),
            }
        )

    def _run(self) -> None:
        client = producer = None
        try:
            client, producer = self._clients()
            while not self._stop.is_set() and self._attempted < self.limit:
                index = self._attempted % len(self._requests)
                try:
                    response = self._request(index, client)
                    event = self._event(index, response)
                    with self._lock:
                        self._pending.append(event)
                    while self._pending and not self._stop.is_set():
                        event = self._pending[0]
                        try:
                            producer.publish(key=str(event.creatorId), event=event)
                        except (RuntimeError, TimeoutError):
                            break
                        with self._lock:
                            if event.eventId in self._published_ids:
                                self._duplicates += 1
                            self._published_ids.add(event.eventId)
                            self._pending.pop(0)
                except Exception:  # noqa: BLE001, S110 - reflected by live availability
                    pass
                self._attempted += 1
                self._stop.wait(self.interval)
        finally:
            if client is not None:
                client.close()
            if producer is not None:
                try:
                    producer.close()
                except (RuntimeError, TimeoutError):
                    pass

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("fault probe traffic is already started")
        self._thread = threading.Thread(target=self._run, daemon=True, name="babel-fault-probe")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise RuntimeError("bounded fault probe did not stop")
        with self._lock:
            self._lost += len(self._pending)
            self._pending.clear()

    def probe_serving(self) -> tuple[bool, int]:
        client = None
        try:
            from babel_online.simulation.client import RecommendationClient

            client = RecommendationClient(self.endpoint, timeout_seconds=5)
            response = self._request(0, client)
            return True, int(response.modelVersion)
        except Exception:  # noqa: BLE001 - any request failure means unavailable
            return False, 0
        finally:
            if client is not None:
                client.close()

    @property
    def duplicate_events(self) -> int:
        with self._lock:
            return self._duplicates

    @property
    def lost_events(self) -> int:
        with self._lock:
            return self._lost


class SameHostFaultOperator:
    """Own and fault real independent serving/trainer processes on one host."""

    def __init__(
        self,
        *,
        run_id: UUID,
        state_root: str | Path,
        serving_port: int,
        kafka: Any,
        traffic: Any,
        runtime_health: Callable[[], dict[str, Any]],
        backend_probe: Callable[[], bool],
        launcher: Callable[[str, list[str], dict[str, str]], Any] | None = None,
        wait_until_ready: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        kafka_bootstrap_servers: str = "127.0.0.1:29092",
    ) -> None:
        self.run_id = run_id
        self.state_root = Path(state_root)
        self.serving_port = serving_port
        self.kafka = kafka
        self.traffic = traffic
        self.runtime_health = runtime_health
        self.backend_probe = backend_probe
        self._launcher = launcher or self._launch
        self._wait = wait_until_ready or self._wait_ready
        self._sleep = sleep
        self._kafka_bootstrap_servers = kafka_bootstrap_servers
        self._processes: dict[str, Any] = {}
        self.owned_processes: list[Any] = []
        self._invalid_path: Path | None = None

    def _launch(self, role: str, argv: list[str], extra_env: dict[str, str]):
        environment = os.environ.copy()
        environment.update(extra_env)
        return subprocess.Popen(
            argv, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _argv(self, role: str) -> list[str]:
        scripts = Path(sys.executable).parent
        if role == "serving":
            return [str(scripts / "babel-recommendation-server"), "--run-id", str(self.run_id), "--port", str(self.serving_port)]
        return [str(scripts / "babel-online-trainer"), "--run-id", str(self.run_id), "--activation-enabled", "true"]

    def _start_role(self, role: str) -> None:
        extra = {"BABEL_ONLINE_STATE_ROOT": str(self.state_root)}
        if role == "trainer":
            ready_path = self.state_root / str(self.run_id) / "fault-trainer-ready.json"
            ready_path.unlink(missing_ok=True)
            extra["BABEL_TRAINER_READY_PATH"] = str(ready_path)
            extra["BABEL_KAFKA_BOOTSTRAP_SERVERS"] = self._kafka_bootstrap_servers
        process = self._launcher(role, self._argv(role), extra)
        self._processes[role] = process
        self.owned_processes.append(process)
        self._wait(role)

    def _wait_ready(self, role: str) -> None:
        deadline = time.monotonic() + 300
        ready = self.state_root / str(self.run_id) / "fault-trainer-ready.json"
        while time.monotonic() < deadline:
            process = self._processes[role]
            if process.poll() is not None:
                raise RuntimeError(f"{role} exited during fault campaign startup")
            if role == "trainer" and ready.is_file():
                return
            if role == "serving" and self.traffic.probe_serving()[0]:
                return
            time.sleep(0.1)
        raise TimeoutError(f"{role} did not become ready")

    def start(self) -> None:
        if not self.backend_probe():
            raise RuntimeError("backend health probe failed")
        try:
            self._start_role("serving")
            self._start_role("trainer")
            self.traffic.start()
        except BaseException:
            self._stop_owned()
            raise

    def probe(self) -> FaultSnapshot:
        serving, serving_version = self.traffic.probe_serving()
        backend = self.backend_probe()
        health = self.runtime_health()
        trainer = self._processes.get("trainer")
        return FaultSnapshot(
            serving_available=bool(serving and backend),
            kafka_lag=max(0, int(health.get("kafka_lag", 0))),
            duplicate_events=self.traffic.duplicate_events,
            lost_events=self.traffic.lost_events,
            trainer_version=max(0, int(health.get("trainer_version", 0))),
            serving_version=max(serving_version, int(health.get("serving_version", 0))),
            trainer_running=trainer is not None and trainer.poll() is None,
            kafka_available=bool(self.kafka.available),
        )

    def kill_trainer(self) -> None:
        process = self._processes["trainer"]
        process.kill()
        process.wait(timeout=10)

    def restart_trainer(self) -> None:
        self._start_role("trainer")

    def pause_kafka(self) -> None:
        self.kafka.pause()

    def resume_kafka(self) -> None:
        self.kafka.resume()

    def inject_invalid_state(self) -> bool:
        before = self.probe()
        path = self.state_root / str(self.run_id) / "activations" / "request-v99999999.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps({
            "schemaVersion": 1, "runId": str(self.run_id), "modelId": str(uuid4()),
            "modelVersion": 99999999, "descriptorPath": str(path.parent / "missing.json"),
            "descriptorSha256": "0" * 64, "publishedAtNs": time.time_ns(),
        }, sort_keys=True) + "\n")
        os.replace(partial, path)
        self._invalid_path = path
        self._sleep(0.6)
        after = self.probe()
        return path.is_file() and after.serving_available and after.serving_version == before.serving_version

    def stop_serving(self) -> None:
        process = self._processes["serving"]
        process.terminate()
        process.wait(timeout=10)

    def start_serving(self) -> None:
        self._start_role("serving")

    def _stop_owned(self) -> None:
        for process in reversed(self.owned_processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    def cleanup(self) -> FaultSnapshot:
        if not self.kafka.available:
            self.kafka.resume()
        if self._processes.get("serving") is None or self._processes["serving"].poll() is not None:
            self._start_role("serving")
        if self._processes.get("trainer") is None or self._processes["trainer"].poll() is not None:
            self._start_role("trainer")
        try:
            self.traffic.stop()
            restored = self.probe()
        finally:
            self._stop_owned()
            if self._invalid_path is not None:
                self._invalid_path.unlink(missing_ok=True)
        return restored


def _http_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return int(response.status) == 200
    except Exception:  # noqa: BLE001 - any HTTP failure means unavailable
        return False


def build_same_host_fault_operator(
    target: FaultCampaignTarget,
    *,
    workload: str | Path,
    serving_port: int,
    backend_base_url: str,
    kafka_bootstrap_servers: str,
    kafka_container: str,
    probe_limit: int,
    probe_interval_seconds: float,
) -> SameHostFaultOperator:
    """Construct and start the real current-service adapter for the accepted run."""
    from babel_online.runtime.database import RuntimeDatabase

    dsn = os.environ.get("BABEL_DATABASE_URL")
    if not dsn:
        raise ValueError("BABEL_DATABASE_URL is required")
    database = RuntimeDatabase(dsn)
    persisted = database.load_run(target.run_id)
    state_root = Path(persisted.config.stateRoot)
    traffic = BoundedLiveTraffic(
        workload=workload,
        endpoint=f"http://127.0.0.1:{serving_port}",
        kafka_bootstrap_servers=kafka_bootstrap_servers,
        run_id=target.run_id,
        limit=probe_limit,
        interval_seconds=probe_interval_seconds,
    )
    operator = SameHostFaultOperator(
        run_id=target.run_id,
        state_root=state_root,
        serving_port=serving_port,
        kafka=DockerKafkaControl(kafka_container),
        traffic=traffic,
        runtime_health=lambda: database.performance_runtime_health(target.run_id),
        backend_probe=lambda: _http_health(backend_base_url.rstrip("/") + "/health"),
        kafka_bootstrap_servers=kafka_bootstrap_servers,
    )
    operator.start()
    return operator


__all__ = [
    "BoundedLiveTraffic", "DockerKafkaControl", "SameHostFaultOperator",
    "build_same_host_fault_operator",
]
