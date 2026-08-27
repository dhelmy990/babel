from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from babel_online.runtime.topology import (
    ResourceRequest,
    ServiceCommand,
    Topology,
    TopologySupervisor,
    execute_replay,
    semantic_replay_checksum,
)
from babel_online.runtime.supervisor import PerRunTopologyManager, build_service_commands
from babel_online.runtime.control import create_control_app
from babel_online.runtime import cli as runtime_cli
from babel_online.runtime import PlacementManifestV1 as PublicPlacementManifestV1
from babel_online.runtime import Topology as PublicTopology


def _replay() -> dict[str, object]:
    return {
        "edges": [("babel-1", "babel-2")],
        "feedback": [{"action": "include", "candidate": "babel-2"}],
        "servingModelVersion": 3,
    }


def test_same_process_and_split_replay_have_identical_semantic_checksum() -> None:
    same_process = execute_replay(Topology.SAME_PROCESS, _replay)
    split = execute_replay(Topology.SAME_HOST_SPLIT, _replay)

    assert same_process.worker_pid == os.getpid()
    assert split.worker_pid != os.getpid()
    assert same_process.semantic_sha256 == split.semantic_sha256
    assert same_process.semantic_sha256 == semantic_replay_checksum(_replay())


def test_split_is_default_and_cross_host_is_rejected() -> None:
    assert Topology.default() is Topology.SAME_HOST_SPLIT
    with pytest.raises(ValueError, match="cross_host"):
        Topology.parse("cross_host")


def test_killing_split_trainer_keeps_independent_serving_process_healthy(tmp_path: Path) -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    commands = {
        "serving": ServiceCommand(
            role="serving",
            argv=(
                sys.executable,
                "-c",
                (
                    "from http.server import HTTPServer,BaseHTTPRequestHandler;"
                    "H=type('H',(BaseHTTPRequestHandler,),{"
                    "'do_GET':lambda s:(s.send_response(200),s.end_headers(),s.wfile.write(b'ok'))"
                    ","
                    "'log_message':lambda *a:None});"
                    f"HTTPServer(('127.0.0.1',{port}),H).serve_forever()"
                ),
            ),
            version="serving-test-v1",
        ),
        "trainer": ServiceCommand(
            role="trainer",
            argv=(sys.executable, "-c", "import time; time.sleep(60)"),
            version="trainer-test-v1",
        ),
    }
    def http_status() -> int:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status

    supervisor = TopologySupervisor(state_root=tmp_path)
    running = supervisor.launch(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        serving_probe=http_status,
    )
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                assert http_status() == 200
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        serving_pid = running.manifest.process("serving").pid
        trainer_pid = running.manifest.process("trainer").pid
        assert serving_pid != trainer_pid

        running.kill_trainer()

        assert running.serving_status() == 200
        assert running.process_alive("serving")
        assert not running.process_alive("trainer")
        assert running.manifest.actualTopology == "same_host_split"
    finally:
        running.stop()


def test_isolated_manifest_records_requested_and_verified_limits(tmp_path: Path) -> None:
    cpu = min(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else 0
    request = ResourceRequest(
        cpuAffinity=(cpu,),
        memoryLimitBytes=512 * 1024 * 1024,
        gpuDevices=(),
    )
    command = ServiceCommand(
        role="serving",
        argv=(sys.executable, "-c", "import time; time.sleep(60)"),
        version="serving-test-v1",
    )
    trainer = ServiceCommand(
        role="trainer",
        argv=(sys.executable, "-c", "import time; time.sleep(60)"),
        version="trainer-test-v1",
    )
    running = TopologySupervisor(state_root=tmp_path).launch(
        topology=Topology.SAME_HOST_ISOLATED,
        commands={"serving": command, "trainer": trainer},
        resources={"serving": request, "trainer": request},
        serving_probe=lambda: 200,
    )
    try:
        placement = running.manifest.process("serving")
        assert placement.requestedResources == request
        assert placement.verifiedResources.cpuAffinity == (cpu,)
        assert placement.verifiedResources.memoryLimitBytes == request.memoryLimitBytes
        assert running.manifest.gpuIsolation == "not_requested"
        assert running.manifest.path.is_file()
    finally:
        running.stop()


def test_shared_gpu_is_reported_as_not_isolated() -> None:
    request = ResourceRequest(gpuDevices=("0",))
    assert request.gpu_isolation_evidence(peer=request) == "shared_not_isolated"


def test_runtime_supervisor_builds_distinct_real_role_commands() -> None:
    commands = build_service_commands(
        serving_command="/opt/babel/bin/recommendation-server --port 8791",
        trainer_command="/opt/babel/bin/online-trainer --topic babel.feedback.v1",
        serving_version="git:abc123",
        trainer_version="git:abc123",
    )

    assert commands["serving"].argv[0].endswith("recommendation-server")
    assert commands["trainer"].argv[0].endswith("online-trainer")
    assert commands["serving"].argv != commands["trainer"].argv
    assert commands["serving"].version == "git:abc123"


def test_runtime_supervisor_rejects_same_command_for_both_roles() -> None:
    with pytest.raises(ValueError, match="independent role commands"):
        build_service_commands(
            serving_command="babel-online serve",
            trainer_command="babel-online serve",
            serving_version="v1",
            trainer_version="v1",
        )


def test_control_plane_exposes_placement_and_trainer_fault_without_touching_serving(
    tmp_path: Path,
) -> None:
    commands = {
        role: ServiceCommand(
            role=role,
            argv=(sys.executable, "-c", "import time; time.sleep(60)"),
            version=f"{role}-v1",
        )
        for role in ("serving", "trainer")
    }
    running = TopologySupervisor(state_root=tmp_path).launch(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        serving_probe=lambda: 200,
    )

    class Manager:
        placement = running.manifest
        status = {
            "runId": str(uuid4()),
            "phase": "running",
            "failure": None,
        }

        def kill_trainer(self):
            running.kill_trainer()

    token = "a" * 64
    client = TestClient(create_control_app(Manager(), token=token))
    headers = {"X-Babel-Worker-Token": token}
    try:
        placement = client.get("/v1/topology", headers=headers)
        assert placement.status_code == 200
        assert placement.json()["actualTopology"] == "same_host_split"

        runtime_status = client.get("/v1/topology/status", headers=headers)
        assert runtime_status.status_code == 200
        assert runtime_status.json()["phase"] == "running"

        stopped = client.post("/v1/topology/trainer/stop", headers=headers)
        assert stopped.status_code == 202
        assert running.serving_status() == 200
        assert not running.process_alive("trainer")
    finally:
        running.stop()


def test_python_cli_defaults_to_split_supervisor(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runtime_cli, "_supervise", lambda: calls.append("split"))

    assert runtime_cli.main([]) == 0
    assert calls == ["split"]
    assert PublicTopology is Topology
    assert PublicPlacementManifestV1.__name__ == "PlacementManifestV1"


def test_same_process_entrypoint_records_current_process_placement(tmp_path) -> None:
    placement = runtime_cli.record_same_process_placement(
        state_root=tmp_path,
        serving_version="serving-git-1",
        trainer_version="trainer-git-1",
    )

    assert placement.actualTopology == "same_process"
    assert placement.process("serving").pid == os.getpid()
    assert placement.process("trainer").pid == os.getpid()
    assert placement.process("serving").version == "serving-git-1"
    assert placement.process("trainer").version == "trainer-git-1"
    assert placement.path.is_file()


def test_dashboard_start_launches_run_scoped_role_executables(tmp_path: Path) -> None:
    run_id = uuid4()
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="v1",
        trainer_version="v1",
    )
    work_started = __import__("threading").Event()
    work = {"recommendations": 0, "feedback": 0}

    class Coordinator:
        def run(self):
            work["recommendations"] += 1
            work["feedback"] += 1
            work_started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200,
        coordinator_factory=lambda _run_id, _stop: Coordinator(),
    )
    manager.start(run_id)
    try:
        assert work_started.wait(2)
        assert manager.placement.actualTopology == "same_host_split"
        assert manager.placement.path.parent.name == str(run_id)
        assert manager.active_run_id == run_id
        assert work == {"recommendations": 1, "feedback": 1}
    finally:
        manager.request_stop(run_id)


def test_dashboard_start_returns_before_delayed_serving_health(tmp_path: Path) -> None:
    run_id = uuid4()
    health_ready = threading.Event()
    coordinator_started = threading.Event()
    lifecycle = []
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="v1",
        trainer_version="v1",
    )

    class Coordinator:
        def run(self):
            coordinator_started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200 if health_ready.is_set() else 503,
        coordinator_factory=lambda _run_id, _stop: Coordinator(),
        starting_reporter=lambda reported: lifecycle.append(("starting", reported)),
        running_reporter=lambda reported: lifecycle.append(("running", reported)),
        stopped_reporter=lambda reported: lifecycle.append(("completed", reported)),
        serving_start_timeout_seconds=2,
    )

    started = time.monotonic()
    manager.start(run_id)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 0.2
        assert manager.status["phase"] == "starting"
        assert not coordinator_started.is_set()
        health_ready.set()
        assert coordinator_started.wait(2)
        assert manager.status["phase"] == "running"
    finally:
        manager.request_stop(run_id)
    assert lifecycle == [
        ("starting", run_id),
        ("running", run_id),
        ("completed", run_id),
    ]


def test_coordinator_exception_is_reported_as_run_failure(tmp_path: Path) -> None:
    run_id = uuid4()
    failed = threading.Event()
    reports = []
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="v1",
        trainer_version="v1",
    )

    class Coordinator:
        def run(self):
            raise RuntimeError("simulator exploded")

    def report_failure(reported_run_id, error):
        reports.append((reported_run_id, str(error)))
        failed.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200,
        coordinator_factory=lambda _run_id, _stop: Coordinator(),
        failure_reporter=report_failure,
    )

    manager.start(run_id)

    assert failed.wait(2)
    assert reports == [(run_id, "simulator exploded")]
    assert manager.status == {
        "runId": str(run_id),
        "phase": "failed",
        "failure": "simulator exploded",
    }


def test_trainer_exit_during_serving_start_fails_without_starting_coordinator(
    tmp_path: Path,
) -> None:
    run_id = uuid4()
    failed = threading.Event()
    coordinator_started = threading.Event()
    reports = []
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(0.1)' --trainer-run {{run_id}}",
        serving_version="v1",
        trainer_version="v1",
    )

    class Coordinator:
        def run(self):
            coordinator_started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 503,
        coordinator_factory=lambda _run_id, _stop: Coordinator(),
        failure_reporter=lambda reported, error: (
            reports.append((reported, str(error))), failed.set()
        ),
        serving_start_timeout_seconds=2,
    )

    manager.start(run_id)

    assert failed.wait(2)
    assert reports == [(run_id, "trainer process exited during startup")]
    assert not coordinator_started.is_set()
