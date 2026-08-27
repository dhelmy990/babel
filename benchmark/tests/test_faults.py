from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from babel_benchmark.faults import (
    BoundedFaultCampaign,
    CallbackKafkaControl,
    FaultController,
    FaultSnapshot,
    HttpTask9TopologyControl,
    Task9FaultHooksAdapter,
    load_accepted_fault_target,
    load_fault_hooks,
)


class LifecycleHarness:
    def __init__(self) -> None:
        self.serving_available = True
        self.kafka_lag = 0
        self.trainer_version = 4
        self.serving_version = 3
        self.duplicates = 0
        self.loss = 0
        self.trainer_running = True
        self.kafka_available = True
        self.cleanup_calls = 0

    def probe(self) -> FaultSnapshot:
        return FaultSnapshot(
            serving_available=self.serving_available,
            kafka_lag=self.kafka_lag,
            duplicate_events=self.duplicates,
            lost_events=self.loss,
            trainer_version=self.trainer_version,
            serving_version=self.serving_version,
            trainer_running=self.trainer_running,
            kafka_available=self.kafka_available,
        )

    def kill_trainer(self) -> None:
        self.trainer_running = False
        self.kafka_lag = 7

    def restart_trainer(self) -> None:
        self.trainer_running = True
        self.kafka_lag = 0
        self.trainer_version = 5

    def pause_kafka(self) -> None:
        self.kafka_available = False
        self.kafka_lag = 9

    def resume_kafka(self) -> None:
        self.kafka_available = True
        self.kafka_lag = 0

    def inject_invalid_state(self) -> None:
        self.serving_available = True
        return True

    def stop_serving(self) -> None:
        self.serving_available = False

    def start_serving(self) -> None:
        self.serving_available = True

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        self.trainer_running = True
        self.kafka_available = True
        self.serving_available = True
        self.kafka_lag = 0


def _accepted_target_files(tmp_path: Path) -> tuple[Path, Path, UUID]:
    trial_id = UUID("00000000-0000-5000-8000-000000000130")
    population = {
        "schemaVersion": 1,
        "experimentId": str(trial_id),
        "babelCount": 10000,
        "scheduleCount": 10000,
        "juneCount": 5000,
        "julyCount": 5000,
        "creatorCount": 50,
        "embeddingDimension": 100,
        "vectorsSha256": "a" * 64,
    }
    population_path = tmp_path / "population.json"
    population_path.write_text(json.dumps(population) + "\n")
    trial_path = tmp_path / "trial.json"
    trial_path.write_text(
        json.dumps(
            {
                "trial": {
                    "experimentId": str(trial_id),
                    "status": "completed",
                    "operatorApproved": True,
                    "populationReady": True,
                    "creatorCount": 50,
                    "targetCreatedBabels": 10000,
                    "requiredVectorCount": 10000,
                    "progress": {"conditionCount": 9},
                    "results": [{"conditionIndex": index} for index in range(1, 10)],
                }
            }
        )
        + "\n"
    )
    return trial_path, population_path, trial_id


def test_fault_target_requires_completed_approved_formal_population(tmp_path: Path) -> None:
    trial_path, population_path, trial_id = _accepted_target_files(tmp_path)

    target = load_accepted_fault_target(trial_path, population_path)

    assert target.trial_id == trial_id
    assert target.condition_count == 9
    assert target.trial_sha256 == hashlib.sha256(trial_path.read_bytes()).hexdigest()
    assert target.population_manifest_sha256 == hashlib.sha256(
        population_path.read_bytes()
    ).hexdigest()

    rejected = json.loads(trial_path.read_text())
    rejected["trial"]["operatorApproved"] = False
    trial_path.write_text(json.dumps(rejected))
    with pytest.raises(ValueError, match="completed and operator-approved"):
        load_accepted_fault_target(trial_path, population_path)


def test_bounded_fault_campaign_writes_truthful_fault_only_receipt(tmp_path: Path) -> None:
    trial_path, population_path, trial_id = _accepted_target_files(tmp_path)
    target = load_accepted_fault_target(trial_path, population_path)
    harness = LifecycleHarness()
    ticks = iter(range(1_000, 100_000, 100))
    receipt_path = tmp_path / "fault-campaign.json"

    receipt = BoundedFaultCampaign(
        target,
        harness,
        clock_ns=lambda: next(ticks),
        sleep=lambda _seconds: None,
        detection_timeout_seconds=1,
        recovery_timeout_seconds=1,
        fault_hold_seconds=0,
        poll_interval_seconds=0.01,
    ).run(receipt_path)

    assert receipt["schemaVersion"] == 1
    assert receipt["experimentId"] == str(trial_id)
    assert receipt["deploymentScope"] == "same_host"
    assert receipt["evidenceUse"] == "fault_only_not_topology_performance"
    assert receipt["status"] == "completed"
    assert [row["fault"] for row in receipt["faults"]] == [
        "trainer_kill_restart",
        "kafka_pause_resume",
        "invalid_model_state",
        "serving_restart",
    ]
    trainer, kafka, invalid, serving = receipt["faults"]
    assert trainer["availability"]["availableDuringFault"]
    assert trainer["versions"]["trainerAfter"] == 5
    assert kafka["kafkaLag"]["maximum"] == 9
    assert kafka["kafkaLag"]["recoveredToBaseline"]
    assert invalid["invalidStateRejected"]
    assert invalid["lastValidServingVersionRetained"]
    assert not serving["availability"]["availableDuringFault"]
    assert serving["availability"]["availableAfterRecovery"]
    assert all(row["duplicates"] == 0 and row["lost"] == 0 for row in receipt["faults"])
    assert receipt["cleanup"]["verified"]
    assert harness.cleanup_calls == 1
    assert json.loads(receipt_path.read_text()) == receipt


def test_bounded_fault_campaign_persists_failure_and_cleans_up(tmp_path: Path) -> None:
    trial_path, population_path, _trial_id = _accepted_target_files(tmp_path)
    target = load_accepted_fault_target(trial_path, population_path)

    class BrokenHarness(LifecycleHarness):
        def kill_trainer(self) -> None:
            raise RuntimeError("trainer control unavailable")

    harness = BrokenHarness()
    receipt_path = tmp_path / "failed.json"

    receipt = BoundedFaultCampaign(
        target,
        harness,
        fault_hold_seconds=0,
    ).run(receipt_path)

    assert receipt["status"] == "failed"
    assert receipt["failure"] == "trainer control unavailable"
    assert receipt["cleanup"]["verified"]
    assert harness.cleanup_calls == 1
    assert receipt_path.is_file()


def test_bounded_fault_campaign_fails_if_invalid_state_is_accepted(
    tmp_path: Path,
) -> None:
    trial_path, population_path, _trial_id = _accepted_target_files(tmp_path)

    class UnsafeHarness(LifecycleHarness):
        def inject_invalid_state(self) -> bool:
            return False

    receipt = BoundedFaultCampaign(
        load_accepted_fault_target(trial_path, population_path),
        UnsafeHarness(),
        fault_hold_seconds=0,
    ).run(tmp_path / "unsafe.json")

    assert receipt["status"] == "failed"
    assert receipt["failedFault"] == "invalid_model_state"
    assert receipt["faults"][-1]["status"] == "failed"
    assert "invalid child/checkpoint was accepted" in receipt["failure"]


def test_fault_hook_factory_is_explicit_and_requires_complete_lifecycle(
    monkeypatch,
) -> None:
    module = ModuleType("operator_faults")
    harness = LifecycleHarness()
    module.build_hooks = lambda: harness
    monkeypatch.setitem(sys.modules, "operator_faults", module)

    assert load_fault_hooks("operator_faults:build_hooks") is harness

    module.incomplete = lambda: object()
    with pytest.raises(ValueError, match="incomplete lifecycle adapter"):
        load_fault_hooks("operator_faults:incomplete")
    with pytest.raises(ValueError, match="module.path:factory_name"):
        load_fault_hooks("operator_faults")


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
