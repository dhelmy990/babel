from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from babel_online.runtime.performance_rerun import (
    ISOLATED_SMOKE_SCOPE,
    REPRESENTATIVE_SCOPE,
    SPLIT_SMOKE_SCOPE,
    create_representative_rerun,
    validate_representative_reuse,
)
from babel_online.runtime.performance_worker import FrozenWorkload
from babel_online.model.frozen_population import FrozenPopulationManifestV1
from babel_online.runtime.performance_worker import (
    PerformanceCondition,
    PerformanceExperiment,
    _validate_population_cohort,
)


SOURCE_ID = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
MODEL_ID = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
POPULATION_RUN_ID = uuid5(SOURCE_ID, "population")
RERUN_ID = UUID("dddddddd-dddd-5ddd-8ddd-dddddddddddd")
SHA = "1" * 64
COMMIT = "2" * 40


def _conditions():
    return tuple(
        PerformanceCondition(
            id=uuid5(SOURCE_ID, f"condition:{index}"),
            condition_index=index,
            topology=topology,
            training_enabled=training,
            activation_enabled=activation,
            run_id=None,
            status="pending",
        )
        for index, (topology, training, activation) in enumerate(
            (
                (topology, training, activation)
                for topology in ("same_process", "same_host_split", "same_host_isolated")
                for training, activation in ((False, False), (True, False), (True, True))
            ),
            start=1,
        )
    )


def _trial(**changes):
    value = PerformanceExperiment(
        id=SOURCE_ID,
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


def _population_manifest():
    return FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=str(SOURCE_ID),
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


class FakeRerunDatabase:
    def __init__(self, source):
        self.source = source
        self.persisted = []

    def load_performance_experiment(self, experiment_id):
        if experiment_id != SOURCE_ID:
            raise KeyError(experiment_id)
        return self.source

    def create_representative_performance_rerun(self, binding):
        self.persisted.append(binding)
        return binding


def _ready_source(population_dir: Path):
    manifest = _population_manifest()
    encoded = (manifest.model_dump_json() + "\n").encode()
    (population_dir / "manifest.json").write_bytes(encoded)
    return _trial(
        status="failed",
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(population_dir),
        population_manifest_sha256=hashlib.sha256(encoded).hexdigest(),
        population_vector_count=10_000,
        population_vector_sha256=manifest.vectorsSha256,
        population_model_repository=manifest.artifactRepo,
        population_model_revision=manifest.artifactRevision,
        population_model_sha256=manifest.modelManifestSha256,
        population_dataset_repository=manifest.datasetRepo,
        population_dataset_revision=manifest.datasetRevision,
        population_dataset_sha256=manifest.datasetManifestSha256,
    )


def test_reuse_validation_binds_exact_population_and_workload_identity(tmp_path: Path):
    population_dir = tmp_path / "population"
    population_dir.mkdir()
    source = _ready_source(population_dir)
    manifest = _population_manifest()
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    workload = FrozenWorkload(workload_dir, tuple(str(i) * 64 for i in range(6)))

    binding = validate_representative_reuse(
        source=source,
        manifest=manifest,
        workload=workload,
        rerun_id=RERUN_ID,
    )

    assert binding.source_trial_id == SOURCE_ID
    assert binding.rerun_id == RERUN_ID
    assert binding.evidence_scope == REPRESENTATIVE_SCOPE
    assert binding.population_run_id == POPULATION_RUN_ID
    assert binding.population_manifest_sha256 == source.population_manifest_sha256
    assert binding.workload_identity == workload.identity
    assert (binding.warmup_seconds, binding.duration_seconds, binding.target_rps) == (
        5,
        25,
        5.0,
    )
    assert binding.request_limit == 150


def test_isolated_smoke_reuse_preserves_scope_population_and_request_limit(
    tmp_path: Path,
):
    population_dir = tmp_path / "population"
    population_dir.mkdir()
    source = _ready_source(population_dir)
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    workload = FrozenWorkload(workload_dir, ("1" * 64,) * 6)

    binding = validate_representative_reuse(
        source=source,
        manifest=_population_manifest(),
        workload=workload,
        rerun_id=RERUN_ID,
        evidence_scope=ISOLATED_SMOKE_SCOPE,
    )

    assert binding.evidence_scope == ISOLATED_SMOKE_SCOPE
    assert binding.population_run_id == POPULATION_RUN_ID
    assert binding.request_limit == 150


@pytest.mark.parametrize(
    ("source_change", "manifest_change", "message"),
    [
        ({"population_vector_sha256": "f" * 64}, {}, "vector checksum"),
        ({"population_model_sha256": "f" * 64}, {}, "model checksum"),
        ({"population_dataset_sha256": "f" * 64}, {}, "dataset checksum"),
        ({}, {"datasetRevision": "f" * 40}, "dataset identity"),
        ({}, {"modelId": UUID("eeeeeeee-eeee-5eee-8eee-eeeeeeeeeeee")}, "model identity"),
    ],
)
def test_reuse_validation_rejects_any_identity_or_checksum_drift(
    tmp_path: Path, source_change, manifest_change, message
):
    population_dir = tmp_path / "population"
    population_dir.mkdir()
    source = replace(_ready_source(population_dir), **source_change)
    manifest = _population_manifest().model_copy(update=manifest_change)
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "requests.template.jsonl").write_text("{}\n" * 200)
    workload = FrozenWorkload(workload_dir, ("1" * 64,) * 6)

    with pytest.raises(ValueError, match=message):
        validate_representative_reuse(
            source=source,
            manifest=manifest,
            workload=workload,
            rerun_id=RERUN_ID,
        )


def test_create_representative_rerun_uses_verified_files_without_population_builder(
    tmp_path: Path,
):
    population_dir = tmp_path / str(SOURCE_ID) / "population"
    population_dir.mkdir(parents=True)
    source = _ready_source(population_dir)
    workload_dir = tmp_path / str(SOURCE_ID) / "workload"
    workload_dir.mkdir()
    (workload_dir / "requests.template.jsonl").write_text("{}\n" * 200)
    workload = FrozenWorkload(workload_dir, ("1" * 64,) * 6)
    database = FakeRerunDatabase(source)

    receipt = create_representative_rerun(
        database=database,
        source_trial_id=SOURCE_ID,
        rerun_id=RERUN_ID,
        state_root=tmp_path,
        population_loader=lambda _path: _population_manifest(),
        workload_loader=lambda _path: type(
            "Loaded", (), {"path": workload.path, "identity": workload.identity}
        )(),
    )

    assert receipt.rerun_id == RERUN_ID
    assert database.persisted == [receipt]
    assert receipt.population_path == population_dir.resolve()
    assert receipt.workload_path == workload_dir.resolve()


def test_representative_rerun_identity_is_new_and_deterministic(tmp_path: Path):
    population_dir = tmp_path / str(SOURCE_ID) / "population"
    population_dir.mkdir(parents=True)
    source = _ready_source(population_dir)
    workload_dir = tmp_path / str(SOURCE_ID) / "workload"
    workload_dir.mkdir()
    (workload_dir / "requests.template.jsonl").write_text("{}\n" * 200)
    database = FakeRerunDatabase(source)

    receipt = create_representative_rerun(
        database=database,
        source_trial_id=SOURCE_ID,
        rerun_id=None,
        state_root=tmp_path,
        population_loader=lambda _path: _population_manifest(),
        workload_loader=lambda _path: type(
            "Loaded", (), {"path": workload_dir, "identity": ("1" * 64,) * 6}
        )(),
        nonce="operator-selected-rerun-1",
    )

    assert receipt.rerun_id == uuid5(SOURCE_ID, "representative-rerun:operator-selected-rerun-1")
    assert receipt.rerun_id != SOURCE_ID


def test_representative_trial_runs_explicit_monolith_vs_split_2x3(tmp_path: Path):
    conditions = tuple(
        PerformanceCondition(
            id=uuid5(RERUN_ID, f"condition:{index}"),
            condition_index=index,
            topology=topology,
            training_enabled=mode >= 1,
            activation_enabled=mode == 2,
            run_id=None,
            status="pending",
        )
        for index, (topology, mode) in enumerate(
            (
                (topology, mode)
                for topology in ("same_process", "same_host_split")
                for mode in range(3)
            ),
            start=1,
        )
    )
    trial = replace(
        _trial(),
        id=RERUN_ID,
        evidence_scope=REPRESENTATIVE_SCOPE,
        source_trial_id=SOURCE_ID,
        source_workload_path=str(tmp_path / "workload"),
        source_workload_identity=("1" * 64,) * 6,
        replay_request_limit=150,
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(tmp_path / "population"),
        population_manifest_sha256="2" * 64,
        conditions=conditions,
    )

    trial.validate_runnable_contract()
    with pytest.raises(ValueError, match="formal"):
        trial.validate_formal_defaults()


def test_split_only_smoke_is_distinctly_labelled_and_runnable(tmp_path: Path):
    conditions = tuple(
        PerformanceCondition(
            id=uuid5(RERUN_ID, f"condition:{index}"),
            condition_index=index,
            topology="same_host_split",
            training_enabled=mode >= 1,
            activation_enabled=mode == 2,
            run_id=None,
            status="pending",
        )
        for index, mode in enumerate(range(3), start=1)
    )
    trial = replace(
        _trial(),
        id=RERUN_ID,
        evidence_scope=SPLIT_SMOKE_SCOPE,
        source_trial_id=SOURCE_ID,
        source_workload_path=str(tmp_path / "workload"),
        source_workload_identity=("1" * 64,) * 6,
        replay_request_limit=150,
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(tmp_path / "population"),
        population_manifest_sha256="2" * 64,
        conditions=conditions,
    )

    trial.validate_runnable_contract()


def test_isolated_smoke_runs_exact_same_host_isolated_trio(tmp_path: Path):
    conditions = tuple(
        PerformanceCondition(
            id=uuid5(RERUN_ID, f"condition:{index}"),
            condition_index=index,
            topology="same_host_isolated",
            training_enabled=training,
            activation_enabled=activation,
            run_id=None,
            status="pending",
        )
        for index, (training, activation) in enumerate(
            ((False, False), (True, False), (True, True)), start=1
        )
    )
    trial = replace(
        _trial(),
        id=RERUN_ID,
        evidence_scope=ISOLATED_SMOKE_SCOPE,
        source_trial_id=SOURCE_ID,
        source_workload_path=str(tmp_path / "workload"),
        source_workload_identity=("1" * 64,) * 6,
        replay_request_limit=150,
        population_ready=True,
        population_run_id=POPULATION_RUN_ID,
        population_bundle_path=str(tmp_path / "population"),
        population_manifest_sha256="2" * 64,
        conditions=conditions,
    )

    trial.validate_runnable_contract()
    with pytest.raises(ValueError, match="formal"):
        trial.validate_formal_defaults()


def test_representative_population_may_only_name_declared_source_trial(tmp_path: Path):
    trial = replace(
        _trial(),
        id=RERUN_ID,
        evidence_scope=REPRESENTATIVE_SCOPE,
        source_trial_id=SOURCE_ID,
    )
    manifest = _population_manifest()

    _validate_population_cohort(trial, manifest)

    with pytest.raises(ValueError, match="another trial"):
        _validate_population_cohort(
            replace(trial, source_trial_id=UUID("ffffffff-ffff-5fff-8fff-ffffffffffff")),
            manifest,
        )
