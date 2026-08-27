"""Same-host runtime placement and process-isolation evidence.

The topology layer deliberately treats a "server" as an independently
running executable.  It does not claim cross-host behavior, and it records
what the operating system actually applied instead of echoing requested
limits as facts.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import resource
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping


class Topology(str, Enum):
    SAME_PROCESS = "same_process"
    SAME_HOST_SPLIT = "same_host_split"
    SAME_HOST_ISOLATED = "same_host_isolated"

    @classmethod
    def default(cls) -> "Topology":
        return cls.SAME_HOST_SPLIT

    @classmethod
    def parse(cls, value: str | "Topology" | None) -> "Topology":
        if value is None:
            return cls.default()
        try:
            return cls(value)
        except ValueError as error:
            if value == "cross_host":
                raise ValueError(
                    "cross_host is not implemented; use a measured same-host topology"
                ) from error
            raise ValueError(f"unsupported topology: {value}") from error


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    cpuAffinity: tuple[int, ...] = ()
    memoryLimitBytes: int | None = None
    gpuDevices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(cpu < 0 for cpu in self.cpuAffinity):
            raise ValueError("CPU affinity values must be nonnegative")
        if len(set(self.cpuAffinity)) != len(self.cpuAffinity):
            raise ValueError("CPU affinity values must be unique")
        if self.memoryLimitBytes is not None and self.memoryLimitBytes <= 0:
            raise ValueError("memory limit must be positive")
        if len(set(self.gpuDevices)) != len(self.gpuDevices):
            raise ValueError("GPU device assignments must be unique")

    def gpu_isolation_evidence(self, *, peer: "ResourceRequest") -> str:
        if not self.gpuDevices and not peer.gpuDevices:
            return "not_requested"
        if set(self.gpuDevices) & set(peer.gpuDevices):
            return "shared_not_isolated"
        # CUDA visibility is an assignment, not proof of separate physical
        # hardware or MIG.  Preserve that distinction in the evidence.
        return "disjoint_assignment_not_hardware_verified"


@dataclass(frozen=True, slots=True)
class VerifiedResources:
    cpuAffinity: tuple[int, ...] = ()
    memoryLimitBytes: int | None = None
    gpuDevices: tuple[str, ...] = ()
    cpuAffinityVerified: bool = False
    memoryLimitVerified: bool = False
    gpuAssignmentVerified: bool = False


@dataclass(frozen=True, slots=True)
class ServiceCommand:
    role: str
    argv: tuple[str, ...]
    version: str
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in {"serving", "trainer"}:
            raise ValueError("service role must be serving or trainer")
        if not self.argv or not self.version:
            raise ValueError("service command and version are required")


@dataclass(frozen=True, slots=True)
class ProcessPlacementV1:
    role: str
    pid: int
    containerId: str | None
    version: str
    startedAtNs: int
    requestedResources: ResourceRequest
    verifiedResources: VerifiedResources


@dataclass(frozen=True, slots=True)
class PlacementManifestV1:
    schemaVersion: int
    requestedTopology: str
    actualTopology: str
    hostId: str
    processes: tuple[ProcessPlacementV1, ...]
    gpuIsolation: str
    publishedAtNs: int | None
    activatedAtNs: int | None
    stalenessNs: int | None
    createdAtNs: int
    path: Path = field(compare=False, repr=False)

    def process(self, role: str) -> ProcessPlacementV1:
        for process in self.processes:
            if process.role == role:
                return process
        raise KeyError(role)

    def as_document(self) -> dict[str, object]:
        document = asdict(self)
        document.pop("path", None)
        return document


@dataclass(frozen=True, slots=True)
class ReplayExecution:
    semantic_sha256: str
    worker_pid: int


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def semantic_replay_checksum(value: object) -> str:
    """Hash topology-independent experiment semantics only."""
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _replay_child(connection, replay: Callable[[], object]) -> None:
    try:
        connection.send((semantic_replay_checksum(replay()), os.getpid(), None))
    except BaseException as error:  # pragma: no cover - child error transport
        connection.send((None, os.getpid(), repr(error)))
    finally:
        connection.close()


def execute_replay(topology: Topology, replay: Callable[[], object]) -> ReplayExecution:
    topology = Topology.parse(topology)
    if topology is Topology.SAME_PROCESS:
        return ReplayExecution(semantic_replay_checksum(replay()), os.getpid())
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_replay_child, args=(child, replay))
    process.start()
    child.close()
    digest, pid, error = parent.recv()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join()
        raise RuntimeError("split replay process did not terminate")
    if error is not None:
        raise RuntimeError(f"split replay failed: {error}")
    return ReplayExecution(str(digest), int(pid))


def _preexec(request: ResourceRequest) -> Callable[[], None]:
    def apply_limits() -> None:
        if request.cpuAffinity:
            os.sched_setaffinity(0, set(request.cpuAffinity))
        if request.memoryLimitBytes is not None:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (request.memoryLimitBytes, request.memoryLimitBytes),
            )

    return apply_limits


def _expand_cpu_list(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for part in value.split(","):
        if "-" in part:
            start, end = (int(item) for item in part.split("-", 1))
            result.extend(range(start, end + 1))
        elif part:
            result.append(int(part))
    return tuple(result)


def _verified_resources(pid: int, requested: ResourceRequest) -> VerifiedResources:
    affinity: tuple[int, ...] = ()
    affinity_verified = False
    memory: int | None = None
    memory_verified = False
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("Cpus_allowed_list:"):
                affinity = _expand_cpu_list(line.split(":", 1)[1].strip())
                affinity_verified = (
                    not requested.cpuAffinity
                    or affinity == tuple(sorted(requested.cpuAffinity))
                )
                break
    except OSError:
        pass
    try:
        limits = Path(f"/proc/{pid}/limits").read_text(encoding="utf-8")
        for line in limits.splitlines():
            if line.startswith("Max address space"):
                fields = line.split()
                soft = fields[3]
                memory = None if soft == "unlimited" else int(soft)
                memory_verified = (
                    requested.memoryLimitBytes is None
                    or memory == requested.memoryLimitBytes
                )
                break
    except (OSError, ValueError, IndexError):
        pass
    return VerifiedResources(
        cpuAffinity=affinity,
        memoryLimitBytes=memory,
        gpuDevices=requested.gpuDevices,
        cpuAffinityVerified=affinity_verified,
        memoryLimitVerified=memory_verified,
        # CUDA_VISIBLE_DEVICES can be read back from the launch environment,
        # but it is not proof of device/MIG separation.
        gpuAssignmentVerified=False,
    )


class RunningTopology:
    def __init__(
        self,
        manifest: PlacementManifestV1,
        processes: Mapping[str, subprocess.Popen[bytes]],
        serving_probe: Callable[[], int],
    ) -> None:
        self.manifest = manifest
        self._processes = dict(processes)
        self._serving_probe = serving_probe

    def process_alive(self, role: str) -> bool:
        process = self._processes.get(role)
        return True if process is None else process.poll() is None

    def kill_trainer(self) -> None:
        process = self._processes.get("trainer")
        if process is None:
            raise RuntimeError("trainer is not an independent process")
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    def graceful_stop_trainer(self, *, timeout_seconds: float = 30.0) -> None:
        process = self._processes.get("trainer")
        if process is None:
            raise RuntimeError("trainer is not an independent process")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def stop_serving(self) -> None:
        process = self._processes.get("serving")
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def serving_status(self) -> int:
        process = self._processes.get("serving")
        if process is not None and process.poll() is not None:
            raise RuntimeError("serving process is not alive")
        return int(self._serving_probe())

    def stop(self) -> None:
        for process in self._processes.values():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for process in self._processes.values():
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


class TopologySupervisor:
    def __init__(self, *, state_root: str | Path) -> None:
        self.state_root = Path(state_root)

    def launch(
        self,
        *,
        topology: Topology | str | None = None,
        commands: Mapping[str, ServiceCommand],
        resources: Mapping[str, ResourceRequest] | None = None,
        serving_probe: Callable[[], int],
        published_at_ns: int | None = None,
        activated_at_ns: int | None = None,
    ) -> RunningTopology:
        selected = Topology.parse(topology)
        if set(commands) != {"serving", "trainer"}:
            raise ValueError("serving and trainer commands are both required")
        requested = dict(resources or {})
        created = time.time_ns()
        processes: dict[str, subprocess.Popen[bytes]] = {}
        placements: list[ProcessPlacementV1] = []
        if selected is Topology.SAME_PROCESS:
            for role in ("serving", "trainer"):
                request = requested.get(role, ResourceRequest())
                placements.append(
                    ProcessPlacementV1(
                        role=role,
                        pid=os.getpid(),
                        containerId=os.environ.get("HOSTNAME"),
                        version=commands[role].version,
                        startedAtNs=created,
                        requestedResources=request,
                        verifiedResources=_verified_resources(os.getpid(), request),
                    )
                )
        else:
            try:
                for role in ("serving", "trainer"):
                    command = commands[role]
                    request = requested.get(role, ResourceRequest())
                    environment = os.environ.copy()
                    environment.update(command.environment)
                    if request.gpuDevices:
                        environment["CUDA_VISIBLE_DEVICES"] = ",".join(request.gpuDevices)
                    process = subprocess.Popen(
                        command.argv,
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=(
                            _preexec(request)
                            if selected is Topology.SAME_HOST_ISOLATED
                            else None
                        ),
                    )
                    processes[role] = process
                    time.sleep(0.02)
                    if process.poll() is not None:
                        raise RuntimeError(f"{role} process exited during launch")
                    placements.append(
                        ProcessPlacementV1(
                            role=role,
                            pid=process.pid,
                            containerId=None,
                            version=command.version,
                            startedAtNs=created,
                            requestedResources=request,
                            verifiedResources=_verified_resources(process.pid, request),
                        )
                    )
            except BaseException:
                for process in processes.values():
                    if process.poll() is None:
                        process.terminate()
                raise
        serving_resources = requested.get("serving", ResourceRequest())
        trainer_resources = requested.get("trainer", ResourceRequest())
        gpu_evidence = serving_resources.gpu_isolation_evidence(peer=trainer_resources)
        manifest_path = self.state_root / "placement-manifest-v1.json"
        manifest = PlacementManifestV1(
            schemaVersion=1,
            requestedTopology=selected.value,
            actualTopology=selected.value,
            hostId=os.uname().nodename,
            processes=tuple(placements),
            gpuIsolation=gpu_evidence,
            publishedAtNs=published_at_ns,
            activatedAtNs=activated_at_ns,
            stalenessNs=(
                None
                if published_at_ns is None or activated_at_ns is None
                else max(0, activated_at_ns - published_at_ns)
            ),
            createdAtNs=created,
            path=manifest_path,
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(_canonical_json(manifest.as_document()) + b"\n")
        return RunningTopology(manifest, processes, serving_probe)


__all__ = [
    "PlacementManifestV1",
    "ReplayExecution",
    "ResourceRequest",
    "RunningTopology",
    "ServiceCommand",
    "Topology",
    "TopologySupervisor",
    "VerifiedResources",
    "execute_replay",
    "semantic_replay_checksum",
]
