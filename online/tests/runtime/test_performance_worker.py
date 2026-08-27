from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient

from babel_online.model.frozen_population import FrozenPopulationManifestV1
from babel_online.runtime.performance_worker import (
    LiveConditionEvidence,
    PerformanceCondition,
    PerformanceExperiment,
    PerformanceJobManager,
    PerformanceConditionCommandRunner,
    RealPopulationBuilder,
    create_performance_control_app,
)


EXPERIMENT_ID = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
MODEL_ID = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
POPULATION_RUN_ID = uuid5(EXPERIMENT_ID, "population")
SHA = "1" * 64
COMMIT = "2" * 40


def _conditions() -> tuple[PerformanceCondition, ...]:
    values = []
    index = 0
    for topology in ("same_process", "same_host_split", "same_host_isolated"):
        for training, activation in ((False, False), (True, False), (True, True)):
            index += 1
            values.append(
                PerformanceCondition(
                    id=uuid5(EXPERIMENT_ID, f"condition:{index}"),
                    condition_index=index,
                    topology=topology,
                    training_enabled=training,
                    activation_enabled=activation,
                    run_id=None,
                    status="pending",
                )
            )
    return tuple(values)


def _trial(**changes) -> PerformanceExperiment:
    value = PerformanceExperiment(
        id=EXPERIMENT_ID,
        status="population_pending",
        starting_model_id=MODEL_ID,
        model_repository="dhelmy990/babel-qwen-navigation-2016-interview",
        model_revision=COMMIT,
        dataset_repository="dhelmy990/babel-wikipedia-experiment",
        dataset_config="crosswalk_2026_06_07",
        dataset_revision="3" * 40,
        creator_count=50,
        target_created_babels=10_000,
        concurrent_users=50,
        recommendation_start_probability=0.4,
        continuation_probability=0.4,
        maximum_traversal_depth=2,
        maximum_requests_per_traversal=10,
        interleave_creation_and_recommendations=True,
        warmup_seconds=30,
        duration_seconds=1,
        target_rps=5.0,
        training_micro_batch_size=8,
        sync_every_steps=10,
        operator_approved=False,
        population_ready=False,
        population_run_id=None,
        population_bundle_path=None,
        population_manifest_sha256=None,
        conditions=_conditions(),
    )
    return replace(value, **changes)


def _population_manifest() -> FrozenPopulationManifestV1:
    return FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=str(EXPERIMENT_ID),
        sourcePopulationRunId=POPULATION_RUN_ID,
        babelCount=10_000,
        scheduleCount=10_000,
        juneCount=5_000,
        julyCount=5_000,
        creatorCount=50,
        modelId=MODEL_ID,
        modelVersion=0,
        modelManifestSha256=SHA,
        artifactManifestSha256="4" * 64,
        artifactRepo="dhelmy990/babel-qwen-navigation-2016-interview",
        artifactRevision=COMMIT,
        artifactId="5" * 64,
        trainingDatasetRevision="6" * 40,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetConfig="crosswalk_2026_06_07",
        datasetRevision="3" * 40,
        datasetManifestSha256="7" * 64,
        embeddingSpaceId=UUID("cccccccc-cccc-5ccc-8ccc-cccccccccccc"),
        embeddingSpaceVersion="babel-qwen-100d-v1",
        embeddingDimension=100,
        babelsSha256="8" * 64,
        vectorsSha256="9" * 64,
        pgvectorSnapshotSha256="a" * 64,
        scheduleSha256="b" * 64,
        babelsBytes=1,
        vectorBytes=4_000_000,
        scheduleBytes=1,
    )


class FakeDatabase:
    def __init__(self) -> None:
        self.trial = _trial()
        self.progress = []
        self.bound_conditions: list[tuple[UUID, UUID]] = []
        self.clones: list[UUID] = []
        self.results = []
        self.run_transitions: list[tuple[UUID, str]] = []

    def load_performance_experiment(self, experiment_id: UUID):
        if experiment_id != self.trial.id:
            raise KeyError(experiment_id)
        return self.trial

    def append_performance_progress(self, experiment_id, **progress):
        self.progress.append(progress)

    def bind_performance_population(self, experiment_id, run_id, sha, path):
        self.trial = replace(
            self.trial,
            population_run_id=run_id,
            population_manifest_sha256=sha,
            population_bundle_path=path,
        )

    def mark_performance_population_ready(self, experiment_id, manifest):
        self.trial = replace(
            self.trial, status="population_ready", population_ready=True
        )

    def transition_performance(self, experiment_id, status, failure=None):
        self.trial = replace(self.trial, status=status)

    def create_condition_run(self, trial, condition, run_id):
        return run_id

    def clone_performance_population(self, trial, condition, run_id):
        self.clones.append(run_id)

    def bind_performance_condition(self, experiment_id, condition_id, run_id):
        self.bound_conditions.append((condition_id, run_id))

    def transition_performance_condition(self, experiment_id, condition_id, status):
        return None

    def save_performance_condition_result(self, experiment_id, evidence, **trio):
        self.results.append((evidence, trio))

    def transition(self, run_id, status, failure=None):
        self.run_transitions.append((run_id, status))


def _wait(manager: PerformanceJobManager) -> None:
    manager.wait(timeout=5)


def test_start_builds_exact_population_then_waits_for_durable_approval(tmp_path: Path):
    database = FakeDatabase()
    calls = []

    def build(trial, run_id, progress, stop):
        calls.append((trial, run_id))
        progress(created_babels=10_000, indexed_babels=10_000, recent_rate=3.0)
        return _population_manifest(), tmp_path / "population"

    manager = PerformanceJobManager(
        database=database,
        output_root=tmp_path,
        population_builder=build,
        workload_freezer=lambda *_args: pytest.fail("approval is required"),
        condition_runner=lambda *_args: pytest.fail("approval is required"),
    )
    manager.start(EXPERIMENT_ID)
    manager.start(EXPERIMENT_ID)  # retry-safe while active
    _wait(manager)

    assert len(calls) == 1
    assert calls[0][1] == POPULATION_RUN_ID
    assert database.trial.status == "population_ready"
    assert database.trial.population_ready is True
    assert database.progress[-1]["phase"] == "population_ready"
    assert database.run_transitions == [(POPULATION_RUN_ID, "completed")]
    assert manager.status["phase"] == "waiting_for_approval"


def test_population_stop_at_committed_boundary_is_interrupted_not_failed(tmp_path: Path):
    database = FakeDatabase()
    entered = threading.Event()
    release = threading.Event()

    def build(_trial, _run_id, _progress, stop):
        entered.set()
        release.wait(2)
        assert stop()
        raise InterruptedError("stopped at committed boundary")

    manager = PerformanceJobManager(
        database=database,
        output_root=tmp_path,
        population_builder=build,
        workload_freezer=lambda *_args: None,
        condition_runner=lambda *_args: None,
    )
    manager.start(EXPERIMENT_ID)
    assert entered.wait(1)
    manager.request_stop(EXPERIMENT_ID)
    release.set()
    _wait(manager)

    assert database.trial.status == "interrupted"
    assert manager.status["phase"] == "interrupted"
    assert manager.status["failure"] is None


def test_stop_while_waiting_for_approval_transitions_immediately(tmp_path: Path):
    database = FakeDatabase()
    database.trial = _trial(status="population_ready", population_ready=True)
    manager = PerformanceJobManager(
        database=database,
        output_root=tmp_path,
        population_builder=lambda *_args: pytest.fail("population is ready"),
        workload_freezer=lambda *_args: pytest.fail("approval was not given"),
        condition_runner=lambda *_args: pytest.fail("approval was not given"),
    )

    manager.request_stop(EXPERIMENT_ID)

    assert database.trial.status == "interrupted"
    assert manager.status["phase"] == "interrupted"


def test_approval_clones_once_and_replays_one_frozen_workload_across_3x3(tmp_path: Path):
    database = FakeDatabase()
    population = _population_manifest()
    population_dir = tmp_path / "population"
    population_dir.mkdir()
    (population_dir / "manifest.json").write_text(
        population.model_dump_json() + "\n", encoding="utf-8"
    )
    database.trial = _trial(
        status="approved",
        operator_approved=True,
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(population_dir),
        population_manifest_sha256=hashlib.sha256(
            (population_dir / "manifest.json").read_bytes()
        ).hexdigest(),
    )
    frozen = tmp_path / "workload"
    frozen.mkdir()
    (frozen / "manifest.json").write_text(
        json.dumps({"identity": [SHA] * 6}) + "\n"
    )
    (frozen / "requests.template.jsonl").write_text("{}\n" * 7)
    seen = []

    def run(trial, condition, run_id, workload, stop):
        seen.append((condition, run_id, workload))
        return LiveConditionEvidence(
            condition_id=condition.id,
            run_id=run_id,
            request_count=7,
            p95_ms=(
                15.0
                if condition.activation_enabled
                else 12.0 if condition.training_enabled else 10.0
            ),
            raw_evidence={"workloadIdentity": list(workload.identity)},
        )

    manager = PerformanceJobManager(
        database=database,
        output_root=tmp_path,
        population_builder=lambda *_args: pytest.fail("population is already ready"),
        workload_freezer=lambda *_args: type(
            "Frozen", (), {"path": frozen, "identity": (SHA,) * 6}
        )(),
        condition_runner=run,
        population_loader=lambda _path: population,
    )
    manager.approve_next_scale(EXPERIMENT_ID)
    manager.approve_next_scale(EXPERIMENT_ID)  # retry-safe
    _wait(manager)

    assert len(seen) == 9
    assert len({row[2].identity for row in seen}) == 1
    assert len(database.clones) == 9
    assert len(set(database.clones)) == 9
    assert len(database.bound_conditions) == 9
    assert len(database.results) == 9
    assert [row["phase"] for row in database.progress[:2]] == [
        "reference_workload",
        "reference_workload_ready",
    ]
    assert database.progress[1]["completed"] == 7
    assert all(
        trio
        == {
            "serving_p95_ms": 10.0,
            "training_p95_ms": 12.0,
            "full_p95_ms": 15.0,
        }
        for _evidence, trio in database.results
    )
    assert [status for _run_id, status in database.run_transitions] == ["completed"] * 9
    assert database.trial.status == "completed"
    assert manager.status["phase"] == "completed"


def test_approval_refuses_non_durable_gate(tmp_path: Path):
    manager = PerformanceJobManager(
        database=FakeDatabase(),
        output_root=tmp_path,
        population_builder=lambda *_args: None,
        workload_freezer=lambda *_args: None,
        condition_runner=lambda *_args: None,
    )
    with pytest.raises(RuntimeError, match="durable operator approval"):
        manager.approve_next_scale(EXPERIMENT_ID)


def test_control_app_authenticates_exact_cpp_routes(tmp_path: Path):
    database = FakeDatabase()
    manager = PerformanceJobManager(
        database=database,
        output_root=tmp_path,
        population_builder=lambda *_args: (_population_manifest(), tmp_path),
        workload_freezer=lambda *_args: None,
        condition_runner=lambda *_args: None,
    )
    token = "d" * 64
    client = TestClient(create_performance_control_app(manager, token=token))

    assert client.post(f"/v1/performance/{EXPERIMENT_ID}/start").status_code == 403
    assert (
        client.post(
            f"/v1/performance/{EXPERIMENT_ID}/start",
            headers={"X-Babel-Worker-Token": token},
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"/v1/performance/{EXPERIMENT_ID}/graceful-stop",
            headers={"X-Babel-Worker-Token": token},
        ).status_code
        == 202
    )


def test_real_population_builder_closes_trial_into_canonical_qwen_run(
    tmp_path: Path, real_model_manifest, accepted_qwen_factory
):
    database = object()
    bundle = type(
        "Bundle",
        (),
        {
            "dataset_repository": "dhelmy990/babel-wikipedia-experiment",
            "dataset_config": "crosswalk_2026_06_07",
            "dataset_revision": "3" * 40,
        },
    )()
    observed = []

    def build(**values):
        observed.append(values)
        return _population_manifest()

    builder = RealPopulationBuilder(
        database=database,
        bundle=bundle,
        model=real_model_manifest,
        encoder=accepted_qwen_factory(),
        output_root=tmp_path,
        build=build,
    )
    manifest, directory = builder(
        _trial(
            starting_model_id=real_model_manifest.modelId,
            model_repository=real_model_manifest.encoderRepo,
            model_revision=real_model_manifest.encoderRevision,
        ),
        POPULATION_RUN_ID,
        lambda **_values: None,
        lambda: False,
    )

    config = observed[0]["config"]
    assert config.runId == POPULATION_RUN_ID
    assert config.environmentSequence == ["2026-06", "2026-07"]
    assert config.perMonthEventBudget == {"2026-06": 5_000, "2026-07": 5_000}
    assert config.creatorCount == config.concurrentUsers == 50
    assert config.recommendationStartProbability == 0.4
    assert config.continuationProbability == 0.4
    assert config.maximumTraversalDepth == 2
    assert config.maximumRequestsPerTraversal == 10
    assert config.runSeed == int.from_bytes(
        hashlib.sha256(f"{EXPERIMENT_ID}:population".encode()).digest()[:8], "big"
    ) & ((1 << 63) - 1)
    assert observed[0]["identity"].model_id == real_model_manifest.modelId
    assert manifest == _population_manifest()
    assert directory == tmp_path / str(EXPERIMENT_ID) / "population"


def test_condition_runner_invokes_concrete_live_command_and_loads_evidence(
    tmp_path: Path,
):
    condition = _conditions()[0]
    run_id = uuid5(EXPERIMENT_ID, "condition:1")
    workload = type("Frozen", (), {"path": tmp_path / "workload.json"})()
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        evidence_path = Path(argv[argv.index("--evidence") + 1])
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "conditionId": str(condition.id),
                    "runId": str(run_id),
                    "requestCount": 30,
                    "p95Ms": 17.5,
                    "rawEvidence": {"driver": "real_live_condition"},
                }
            )
            + "\n"
        )
        return type("Completed", (), {"returncode": 0})()

    runner = PerformanceConditionCommandRunner(
        output_root=tmp_path, executable="babel-online", execute=execute
    )
    evidence = runner(
        _trial(), condition, run_id, workload, lambda: False
    )
    retried = runner(_trial(), condition, run_id, workload, lambda: False)

    argv = calls[0][0]
    assert argv[:2] == ["babel-online", "performance-condition"]
    assert "--workload" in argv
    assert "--topology" in argv and "same_process" in argv
    assert "--training-enabled" in argv and "false" in argv
    assert "--activation-enabled" in argv and "false" in argv
    assert evidence.p95_ms == 17.5
    assert retried == evidence
    assert len(calls) == 1
    assert evidence.raw_evidence["driver"] == "real_live_condition"
