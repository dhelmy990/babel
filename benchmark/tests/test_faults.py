from __future__ import annotations

import pytest

from babel_benchmark.faults import (
    CallbackKafkaControl,
    FaultController,
    FaultSnapshot,
    HttpTask9TopologyControl,
    Task9FaultHooksAdapter,
)


class LifecycleHarness:
    def __init__(self) -> None:
        self.serving_available = True
        self.kafka_lag = 0
        self.trainer_version = 4
        self.serving_version = 3
        self.duplicates = 0
        self.loss = 0

    def probe(self) -> FaultSnapshot:
        return FaultSnapshot(
            serving_available=self.serving_available,
            kafka_lag=self.kafka_lag,
            duplicate_events=self.duplicates,
            lost_events=self.loss,
            trainer_version=self.trainer_version,
            serving_version=self.serving_version,
        )

    def kill_trainer(self) -> None:
        self.kafka_lag = 7

    def restart_trainer(self) -> None:
        self.kafka_lag = 0
        self.trainer_version = 5

    def pause_kafka(self) -> None:
        self.kafka_lag = 9

    def resume_kafka(self) -> None:
        self.kafka_lag = 0

    def inject_invalid_state(self) -> None:
        self.serving_available = True
        return True

    def stop_serving(self) -> None:
        self.serving_available = False

    def start_serving(self) -> None:
        self.serving_available = True


def test_fault_controller_records_all_task9_lifecycle_scenarios(tmp_path) -> None:
    harness = LifecycleHarness()
    ticks = iter(range(0, 10_000, 100))
    path = tmp_path / "faults.json"

    evidence = FaultController(
        harness,
        clock_ns=lambda: next(ticks),
    ).run_all(receipt_path=path)

    assert [row.fault for row in evidence] == [
        "trainer_kill_restart",
        "kafka_pause_resume",
        "invalid_model_state",
        "serving_restart",
    ]
    trainer = evidence[0]
    assert trainer.serving_available_during_fault
    assert trainer.maximum_kafka_lag == 7
    assert trainer.trainer_version_before == 4
    assert trainer.trainer_version_after == 5
    invalid = evidence[2]
    assert invalid.invalid_state_rejected
    assert invalid.last_valid_serving_version_retained
    restart = evidence[3]
    assert not restart.serving_available_during_fault
    assert restart.serving_available_after_recovery
    assert restart.detection_ns > 0
    assert restart.recovery_ns > 0
    assert all(row.duplicate_events == 0 and row.lost_events == 0 for row in evidence)
    assert path.is_file()


def test_task9_http_and_kafka_adapters_map_to_real_lifecycle_seams() -> None:
    calls = []

    class Response:
        status_code = 202

    class Transport:
        def post(self, url, *, headers, timeout):
            calls.append(("http", url, headers["X-Babel-Worker-Token"], timeout))
            return Response()

    topology = HttpTask9TopologyControl(
        base_url="http://127.0.0.1:8790",
        worker_token="a" * 64,
        start_trainer=lambda: calls.append(("trainer", "start")),
        stop_serving=lambda: calls.append(("serving", "stop")),
        start_serving=lambda: calls.append(("serving", "start")),
        transport=Transport(),
    )
    kafka = CallbackKafkaControl(
        pause=lambda: calls.append(("kafka", "pause")),
        resume=lambda: calls.append(("kafka", "resume")),
    )
    hooks = Task9FaultHooksAdapter(
        topology=topology,
        kafka=kafka,
        probe=LifecycleHarness().probe,
        invalid_state_injector=lambda: True,
    )

    hooks.kill_trainer()
    hooks.restart_trainer()
    hooks.pause_kafka()
    hooks.resume_kafka()
    hooks.stop_serving()
    hooks.start_serving()
    assert hooks.inject_invalid_state()

    assert calls == [
        ("http", "http://127.0.0.1:8790/v1/topology/trainer/stop", "a" * 64, 5.0),
        ("trainer", "start"),
        ("kafka", "pause"),
        ("kafka", "resume"),
        ("serving", "stop"),
        ("serving", "start"),
    ]


@pytest.mark.parametrize(
    "fault,recovery_event",
    [
        ("trainer_kill_restart", "trainer-restarted"),
        ("kafka_pause_resume", "kafka-resumed"),
        ("serving_restart", "serving-started"),
    ],
)
def test_fault_controller_attempts_recovery_when_during_probe_fails(
    fault, recovery_event
) -> None:
    events = []

    class ProbeFailureHarness(LifecycleHarness):
        def __init__(self):
            super().__init__()
            self.probes = 0

        def probe(self):
            self.probes += 1
            if self.probes == 2:
                raise RuntimeError("probe failed")
            return super().probe()

        def restart_trainer(self):
            events.append("trainer-restarted")
            super().restart_trainer()

        def resume_kafka(self):
            events.append("kafka-resumed")
            super().resume_kafka()

        def start_serving(self):
            events.append("serving-started")
            super().start_serving()

    with pytest.raises(RuntimeError, match="probe failed"):
        FaultController(ProbeFailureHarness())._run(fault)

    assert events == [recovery_event]
