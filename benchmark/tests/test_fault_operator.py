from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from babel_benchmark.fault_operator import SameHostFaultOperator


class FakeProcess:
    def __init__(self, role: str) -> None:
        self.role = role
        self.alive = True
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.killed = True
        self.alive = False

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class FakeTraffic:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def probe_serving(self):
        return True, 7

    @property
    def duplicate_events(self):
        return 0

    @property
    def lost_events(self):
        return 0


class FakeKafka:
    def __init__(self) -> None:
        self.available = True
        self.calls = []

    def pause(self):
        self.calls.append("pause")
        self.available = False

    def resume(self):
        self.calls.append("resume")
        self.available = True


def test_same_host_operator_owns_real_roles_faults_and_cleanup(tmp_path: Path) -> None:
    launched = []

    def launch(role, argv, env):
        launched.append((role, tuple(argv), dict(env)))
        return FakeProcess(role)

    traffic = FakeTraffic()
    kafka = FakeKafka()
    health = {"kafka_lag": 0, "trainer_version": 4, "serving_version": 7}
    backend_calls = []
    operator = SameHostFaultOperator(
        run_id=UUID("00000000-0000-5000-8000-000000000006"),
        state_root=tmp_path,
        serving_port=8793,
        kafka=kafka,
        traffic=traffic,
        runtime_health=lambda: dict(health),
        backend_probe=lambda: backend_calls.append(True) or True,
        launcher=launch,
        wait_until_ready=lambda role: None,
    )

    operator.start()
    assert traffic.started
    assert [row[0] for row in launched] == ["serving", "trainer"]
    assert "babel-recommendation-server" in launched[0][1][0]
    assert "babel-online-trainer" in launched[1][1][0]

    operator.kill_trainer()
    assert launched[1] and launched[1][0] == "trainer"
    stale_ready = tmp_path / str(operator.run_id) / "fault-trainer-ready.json"
    stale_ready.parent.mkdir(parents=True, exist_ok=True)
    stale_ready.write_text("stale")
    operator.restart_trainer()
    assert not stale_ready.exists()
    assert [row[0] for row in launched] == ["serving", "trainer", "trainer"]
    operator.pause_kafka()
    assert not operator.probe().kafka_available
    operator.resume_kafka()
    operator.stop_serving()
    operator.start_serving()
    assert [row[0] for row in launched][-1] == "serving"

    activation = tmp_path / str(operator.run_id) / "activations"
    assert operator.inject_invalid_state()
    invalid = activation / "request-v99999999.json"
    assert json.loads(invalid.read_text())["descriptorSha256"] == "0" * 64

    cleanup = operator.cleanup()
    assert cleanup.serving_available
    assert traffic.stopped
    assert not launched[-1][2].get("HF_TOKEN")
    assert all(not process.alive for process in operator.owned_processes)
    assert not invalid.exists()
