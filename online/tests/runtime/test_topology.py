from __future__ import annotations

import os
import json
import signal
import sys
import threading
import time
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
    derive_container_id,
)
from babel_online.runtime.supervisor import PerRunTopologyManager, build_service_commands
from babel_online.runtime.control import create_control_app
from babel_online.runtime import cli as runtime_cli
from babel_online.runtime import PlacementManifestV1 as PublicPlacementManifestV1
from babel_online.runtime import Topology as PublicTopology


def test_split_is_default_and_cross_host_is_rejected() -> None:
    assert Topology.default() is Topology.SAME_HOST_SPLIT
    with pytest.raises(ValueError, match="cross_host"):
        Topology.parse("cross_host")


def test_placement_versions_are_concrete_and_container_id_is_derived() -> None:
    with pytest.raises(ValueError, match="concrete"):
        ServiceCommand(role="serving", argv=("serve",), version="unknown")
    with pytest.raises(ValueError, match="required"):
        ServiceCommand(role="serving", argv=("serve",), version="   ")
    container_id = "a" * 64
    assert derive_container_id(
        f"0::/system.slice/docker-{container_id}.scope\n", hostname="host"
    ) == container_id
    assert derive_container_id("0::/\n", hostname="b" * 12) == "b" * 12


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


def test_isolated_topology_rejects_roles_without_cpu_or_memory_limits(tmp_path) -> None:
    commands = {
        role: ServiceCommand(
            role=role,
            argv=(sys.executable, "-c", "import time; time.sleep(1)"),
            version=f"{role}-test-v1",
        )
        for role in ("serving", "trainer")
    }
    with pytest.raises(ValueError, match="both roles"):
        TopologySupervisor(state_root=tmp_path).launch(
            topology=Topology.SAME_HOST_ISOLATED,
            commands=commands,
            resources={
                "serving": ResourceRequest(cpuAffinity=(0,)),
                "trainer": ResourceRequest(),
            },
            serving_probe=lambda: 200,
        )


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


def test_python_cli_dispatches_performance_worker_and_condition(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        runtime_cli, "_performance_worker", lambda: calls.append(("worker", ()))
    )
    monkeypatch.setattr(
        runtime_cli,
        "_performance_condition",
        lambda argv: calls.append(("condition", tuple(argv))),
    )
    monkeypatch.setattr(
        runtime_cli,
        "_performance_command",
        lambda argv: calls.append(("operator", tuple(argv))),
    )

    assert runtime_cli.main(["performance-worker"]) == 0
    assert runtime_cli.main(["performance-condition", "--run-id", "value"]) == 0
    assert runtime_cli.main(["performance-command", "--action", "start"]) == 0
    assert calls == [
        ("worker", ()),
        ("condition", ("--run-id", "value")),
        ("operator", ("--action", "start")),
    ]


def test_console_entrypoint_reads_sys_argv(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runtime_cli, "_performance_worker", lambda: calls.append("worker"))
    monkeypatch.setattr(sys, "argv", ["babel-online", "performance-worker"])

    assert runtime_cli.main() == 0
    assert calls == ["worker"]


def test_condition_sigterm_interrupts_execution_and_restores_handlers(
    monkeypatch, tmp_path
) -> None:
    from babel_online.runtime import performance_condition as condition_module
    from babel_online.runtime.performance_worker import PerformanceCondition

    experiment_id, condition_id, run_id = uuid4(), uuid4(), uuid4()
    condition = PerformanceCondition(
        id=condition_id,
        condition_index=1,
        topology="same_process",
        training_enabled=False,
        activation_enabled=False,
        run_id=run_id,
        status="running",
    )
    trial = SimpleNamespace(
        conditions=(condition,), duration_seconds=120, target_rps=5.0
    )
    monkeypatch.setenv("BABEL_DATABASE_URL", "unused")
    monkeypatch.setattr(
        runtime_cli,
        "RuntimeDatabase",
        lambda _dsn: SimpleNamespace(
            load_performance_experiment=lambda _experiment_id: trial
        ),
    )
    installed = {}
    old = object()
    monkeypatch.setattr(signal, "getsignal", lambda _selected: old)
    monkeypatch.setattr(
        signal, "signal", lambda selected, handler: installed.__setitem__(selected, handler)
    )
    cleaned = []

    def execute(**_values):
        assert callable(_values["progress_sink"])
        try:
            installed[signal.SIGTERM](signal.SIGTERM, None)
        finally:
            cleaned.append("condition-cleanup")

    monkeypatch.setattr(condition_module, "execute_live_condition", execute)
    with pytest.raises(InterruptedError, match="graceful-stop"):
        runtime_cli._performance_condition(
            [
                "--experiment-id",
                str(experiment_id),
                "--condition-id",
                str(condition_id),
                "--run-id",
                str(run_id),
                "--topology",
                "same_process",
                "--training-enabled",
                "false",
                "--activation-enabled",
                "false",
                "--workload",
                str(tmp_path / "workload"),
                "--duration-seconds",
                "120",
                "--target-rps",
                "5",
                "--evidence",
                str(tmp_path / "evidence.json"),
            ]
        )

    assert cleaned == ["condition-cleanup"]
    assert installed[signal.SIGTERM] is old
    assert installed[signal.SIGINT] is old


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
    assert placement.modelVersion == 0
    assert placement.publishedAtNs is not None
    assert placement.activatedAtNs == placement.publishedAtNs
    assert placement.stalenessNs == 0
    assert placement.path.is_file()


def test_split_placement_refreshes_from_shared_activation_receipt(tmp_path) -> None:
    commands = {
        role: ServiceCommand(
            role=role, argv=(sys.executable, "-c", "import time; time.sleep(60)"),
            version=f"{role}-v1",
        )
        for role in ("serving", "trainer")
    }
    running = TopologySupervisor(state_root=tmp_path).launch(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        serving_probe=lambda: 200,
    )
    activation_dir = tmp_path / "activations"
    activation_dir.mkdir()
    (activation_dir / "receipt-v00000004.json").write_text(json.dumps({
        "schemaVersion": 1, "runId": str(uuid4()), "modelId": str(uuid4()),
        "modelVersion": 4, "publishedAtNs": 100, "activatedAtNs": 130,
        "requestedAtNs": 110, "preparedAtNs": 120,
        "stageDurationNs": 10, "switchDurationNs": 10,
        "stalenessNs": 30,
    }))
    try:
        running.refresh_activation_evidence(activation_dir)
        assert running.manifest.modelVersion == 4
        assert running.manifest.publishedAtNs == 100
        assert running.manifest.activatedAtNs == 130
        assert running.manifest.stalenessNs == 30
        assert json.loads(running.manifest.path.read_text())["modelVersion"] == 4
    finally:
        running.stop()


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
        assert manager.placement.modelVersion == 0
        assert manager.placement.publishedAtNs is not None
        assert manager.placement.activatedAtNs >= manager.placement.publishedAtNs
        assert manager.placement.stalenessNs == (
            manager.placement.activatedAtNs - manager.placement.publishedAtNs
        )
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


def test_graceful_stop_retains_serving_when_final_activation_times_out(
    tmp_path: Path,
) -> None:
    run_id = uuid4()
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="serving-v1",
        trainer_version="trainer-v1",
    )
    started = threading.Event()

    class Coordinator:
        def run(self):
            started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200,
        coordinator_factory=lambda *_args: Coordinator(),
        activation_timeout_seconds=0.05,
    )
    manager.start(run_id)
    assert started.wait(1)
    activation_dir = tmp_path / str(run_id) / "activations"
    activation_dir.mkdir(parents=True)
    pending = activation_dir / "request-v00000003.json"
    pending.write_text(json.dumps({
        "schemaVersion": 1, "runId": str(run_id), "modelId": str(uuid4()),
        "modelVersion": 3, "descriptorPath": "unused",
        "descriptorSha256": "a" * 64, "publishedAtNs": 1,
    }))

    with pytest.raises(RuntimeError, match="final model activation"):
        manager.request_stop(run_id)

    assert manager.status["phase"] == "failed"
    assert manager.placement.actualTopology == "same_host_split"
    assert manager._running.serving_status() == 200
    pending.unlink()
    manager.request_stop(run_id)


def test_graceful_stop_reports_and_clears_irrecoverable_missing_receipt(
    tmp_path: Path,
) -> None:
    run_id = uuid4()
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="serving-v1",
        trainer_version="trainer-v1",
    )
    started = threading.Event()
    failures = []

    class Coordinator:
        def run(self):
            started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200,
        coordinator_factory=lambda *_args: Coordinator(),
        failure_reporter=lambda run, error: failures.append((run, str(error))),
        activation_timeout_seconds=1,
    )
    manager.start(run_id)
    assert started.wait(1)
    activation_dir = tmp_path / str(run_id) / "activations"
    activation_dir.mkdir(parents=True)
    pending = activation_dir / "request-v00000003.json"
    pending.write_text(json.dumps({
        "schemaVersion": 1, "runId": str(run_id), "modelId": str(uuid4()),
        "modelVersion": 3, "descriptorPath": "unused",
        "descriptorSha256": "a" * 64, "publishedAtNs": 1,
    }))
    release = threading.Thread(
        target=lambda: (time.sleep(0.05), pending.unlink()), daemon=True
    )
    release.start()

    with pytest.raises(RuntimeError, match="receipt is missing"):
        manager.request_stop(run_id)

    release.join(timeout=1)
    assert failures == [(run_id, "final model activation receipt is missing")]
    assert manager.active_run_id is None
    assert manager.status["phase"] == "failed"


def test_graceful_stop_persists_already_consumed_final_activation_receipt(
    tmp_path: Path,
) -> None:
    run_id = uuid4()
    model_id = uuid4()
    commands = build_service_commands(
        serving_command=f"{sys.executable} -c 'import time; time.sleep(60)' --run-id {{run_id}}",
        trainer_command=f"{sys.executable} -c 'import time; time.sleep(60)' --trainer-run {{run_id}}",
        serving_version="serving-v1",
        trainer_version="trainer-v1",
    )
    started = threading.Event()

    class Coordinator:
        def run(self):
            started.set()

    manager = PerRunTopologyManager(
        topology=Topology.SAME_HOST_SPLIT,
        commands=commands,
        resources={},
        state_root=tmp_path,
        serving_probe=lambda: 200,
        coordinator_factory=lambda *_args: Coordinator(),
    )
    manager.start(run_id)
    assert started.wait(1)
    placement_path = manager.placement.path
    activation_dir = tmp_path / str(run_id) / "activations"
    activation_dir.mkdir(parents=True)
    (activation_dir / "receipt-v00000003.json").write_text(json.dumps({
        "schemaVersion": 1, "runId": str(run_id), "modelId": str(model_id),
        "modelVersion": 3, "publishedAtNs": 10, "activatedAtNs": 25,
        "stalenessNs": 15,
    }))

    manager.request_stop(run_id)

    persisted = json.loads(placement_path.read_text())
    assert persisted["modelVersion"] == 3
    assert persisted["publishedAtNs"] == 10
    assert persisted["activatedAtNs"] == 25
