"""Bounded graceful shutdown for the continuously running online worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shlex
import time
from threading import Event, Lock, Thread
from uuid import UUID
from typing import Any

from babel_online.feedback.bus import OffsetRange, TopicPartition, capture_high_watermarks
from babel_online.feedback.export import FeedbackExport, export_offset_ranges
from .topology import (
    PlacementManifestV1,
    ResourceRequest,
    RunningTopology,
    ServiceCommand,
    Topology,
    TopologySupervisor,
)


def build_service_commands(
    *,
    serving_command: str,
    trainer_command: str,
    serving_version: str,
    trainer_version: str,
) -> dict[str, ServiceCommand]:
    """Close shell strings into explicit, independently runnable argv values."""
    serving = tuple(shlex.split(serving_command))
    trainer = tuple(shlex.split(trainer_command))
    if not serving or not trainer:
        raise ValueError("serving and trainer commands are required")
    if serving == trainer:
        raise ValueError("split topology requires independent role commands")
    return {
        "serving": ServiceCommand(
            role="serving", argv=serving, version=serving_version
        ),
        "trainer": ServiceCommand(
            role="trainer", argv=trainer, version=trainer_version
        ),
    }


class PerRunTopologyManager:
    """Dashboard control target that launches one pair of run-scoped roles."""

    def __init__(
        self,
        *,
        topology: Topology,
        commands: dict[str, ServiceCommand],
        resources: dict[str, ResourceRequest],
        state_root: str | Path,
        serving_probe: Callable[[], int],
        coordinator_factory: Callable[[UUID, Event], Any],
        starting_reporter: Callable[[UUID], None] | None = None,
        running_reporter: Callable[[UUID], None] | None = None,
        stopped_reporter: Callable[[UUID], None] | None = None,
        failure_reporter: Callable[[UUID, BaseException], None] | None = None,
        serving_start_timeout_seconds: float = 300.0,
    ) -> None:
        self.topology = Topology.parse(topology)
        if self.topology is Topology.SAME_PROCESS:
            raise ValueError("same_process uses the in-process FridayDemoRuntime adapter")
        self.commands = dict(commands)
        self.resources = dict(resources)
        self.state_root = Path(state_root)
        self.serving_probe = serving_probe
        self.coordinator_factory = coordinator_factory
        self.starting_reporter = starting_reporter or (lambda _run_id: None)
        self.running_reporter = running_reporter or (lambda _run_id: None)
        self.stopped_reporter = stopped_reporter or (lambda _run_id: None)
        self.failure_reporter = failure_reporter or (
            lambda _run_id, _error: None
        )
        if serving_start_timeout_seconds <= 0:
            raise ValueError("serving start timeout must be positive")
        self.serving_start_timeout_seconds = serving_start_timeout_seconds
        self._running: RunningTopology | None = None
        self._run_id: UUID | None = None
        self._lock = Lock()
        self._coordinator_stop: Event | None = None
        self._lifecycle_thread: Thread | None = None
        self._coordinator_error: BaseException | None = None
        self._phase = "idle"

    @property
    def active_run_id(self) -> UUID | None:
        return self._run_id

    @property
    def placement(self) -> PlacementManifestV1:
        if self._running is None:
            raise RuntimeError("no run topology has been launched")
        return self._running.manifest

    @property
    def status(self) -> dict[str, str | None]:
        with self._lock:
            return {
                "runId": None if self._run_id is None else str(self._run_id),
                "phase": self._phase,
                "failure": (
                    None
                    if self._coordinator_error is None
                    else str(self._coordinator_error)
                ),
            }

    def _commands_for(self, run_id: UUID) -> dict[str, ServiceCommand]:
        return {
            role: ServiceCommand(
                role=command.role,
                argv=tuple(value.format(run_id=run_id) for value in command.argv),
                version=command.version,
                environment={
                    **command.environment,
                    "BABEL_ONLINE_RUN_ID": str(run_id),
                },
            )
            for role, command in self.commands.items()
        }

    def _orchestrate(self, run_id: UUID, stop_event: Event) -> None:
        running: RunningTopology | None = None
        try:
            self.starting_reporter(run_id)
            running = TopologySupervisor(
                state_root=self.state_root / str(run_id)
            ).launch(
                topology=self.topology,
                commands=self._commands_for(run_id),
                resources=self.resources,
                serving_probe=self.serving_probe,
            )
            with self._lock:
                self._running = running
            deadline = time.monotonic() + self.serving_start_timeout_seconds
            while not stop_event.is_set():
                if not running.process_alive("trainer"):
                    raise RuntimeError("trainer process exited during startup")
                try:
                    if running.serving_status() == 200:
                        break
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("recommendation service did not become healthy")
                time.sleep(0.1)
            if stop_event.is_set():
                return
            if not running.process_alive("trainer"):
                raise RuntimeError("trainer process exited during startup")
            coordinator = self.coordinator_factory(run_id, stop_event)
            self.running_reporter(run_id)
            with self._lock:
                self._phase = "running"
            coordinator.run()
        except BaseException as error:
            with self._lock:
                self._coordinator_error = error
                self._phase = "failed"
            try:
                self.failure_reporter(run_id, error)
            finally:
                if running is not None:
                    running.stop()

    def start(self, run_id: UUID) -> None:
        """Reserve the run and return while role startup continues in background."""
        with self._lock:
            if self._run_id is not None:
                raise RuntimeError("another run topology is active")
            coordinator_stop = Event()
            self._run_id = run_id
            self._coordinator_stop = coordinator_stop
            self._coordinator_error = None
            self._phase = "starting"
            lifecycle_thread = Thread(
                target=self._orchestrate,
                args=(run_id, coordinator_stop),
                daemon=True,
                name=f"babel-topology-{run_id}",
            )
            self._lifecycle_thread = lifecycle_thread
            lifecycle_thread.start()

    def request_stop(self, run_id: UUID) -> None:
        with self._lock:
            if self._run_id != run_id:
                raise KeyError(run_id)
            self._phase = "stopping"
            if self._coordinator_stop is not None:
                self._coordinator_stop.set()
            lifecycle_thread = self._lifecycle_thread
        if lifecycle_thread is not None:
            lifecycle_thread.join(timeout=30)
            if lifecycle_thread.is_alive():
                raise RuntimeError("simulator coordinator did not stop cleanly")
        with self._lock:
            running = self._running
        if running is not None:
            # Trainer first: drain Kafka, checkpoint, publish immutable child.
            # Serving remains available while it observes/activates that file.
            running.graceful_stop_trainer()
            activation_dir = self.state_root / str(run_id) / "activations"
            deadline = time.monotonic() + 5.0
            while any(activation_dir.glob("request-v*.json")):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            running.stop_serving()
        with self._lock:
            completed_cleanly = self._coordinator_error is None
            self._running = None
            self._run_id = None
            self._coordinator_stop = None
            self._lifecycle_thread = None
            self._phase = "idle"
        if completed_cleanly:
            self.stopped_reporter(run_id)

    def kill_trainer(self) -> None:
        with self._lock:
            if self._running is None:
                raise RuntimeError("no run topology is active")
            self._running.kill_trainer()


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    checkpoint_path: Path
    next_offsets: dict[TopicPartition, int]
    feedback_export: FeedbackExport
    sync_artifact: Any
    child_artifact: Any


class OnlineDemoSupervisor:
    """Own shutdown ordering; the simulator must stop calling publish first."""

    def __init__(
        self,
        *,
        producer: Any,
        trainer: Any,
        feedback_source: Any,
        export_root: str | Path,
        publish_sync: Callable[[], Any],
        export_child: Callable[[], Any],
    ) -> None:
        self.producer = producer
        self.trainer = trainer
        self.feedback_source = feedback_source
        self.export_root = Path(export_root)
        self.publish_sync = publish_sync
        self.export_child = export_child
        self._start_offsets = trainer.consumer.committed()
        self._stopped = False

    def graceful_stop(self) -> ShutdownResult:
        if self._stopped:
            raise RuntimeError("online demo worker is already stopped")
        self.producer.flush()
        end_offsets = capture_high_watermarks(self.trainer.consumer)
        self.trainer.drain_to(end_offsets)
        checkpoint = self.trainer.checkpoint_and_commit()
        ranges = [
            OffsetRange(
                partition,
                self._start_offsets.get(partition, 0),
                end_offset,
            )
            for partition, end_offset in sorted(end_offsets.items())
        ]
        feedback_export = export_offset_ranges(
            self.feedback_source, ranges, self.export_root
        )
        sync_artifact = self.publish_sync()
        child_artifact = self.export_child()
        self.trainer.consumer.close()
        self.producer.close()
        self._stopped = True
        return ShutdownResult(
            checkpoint,
            dict(self.trainer.next_offsets),
            feedback_export,
            sync_artifact,
            child_artifact,
        )


__all__ = [
    "OnlineDemoSupervisor",
    "PerRunTopologyManager",
    "ShutdownResult",
    "build_service_commands",
]
