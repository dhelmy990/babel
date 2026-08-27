from __future__ import annotations

import hashlib
import struct
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest
from babel_online.contracts import RunConfigV2
from babel_online.model.frozen_population import (
    FrozenPopulationIntegrityError,
    build_frozen_population,
    clone_frozen_population,
    load_frozen_population,
)
from babel_online.model.population import (
    PopulationActivationEvidence,
    PopulationBatchProgress,
    PopulationIdentity,
    PopulationReceipt,
    PopulationSource,
)
from babel_online.runtime.database import FrozenPopulationRow, PersistedRun
from babel_online.runtime.dataset_bundle import (
    DEMO_DATASET_REPOSITORY,
    SCALE_DATASET_CONFIG,
    SCALE_DATASET_REVISION,
    DatasetBundle,
)


def config(tmp_path: Path, model_id: UUID, *, run_id: UUID) -> RunConfigV2:
    return RunConfigV2(
        schemaVersion=2,
        runId=run_id,
        datasetRepo=DEMO_DATASET_REPOSITORY,
        datasetConfig=SCALE_DATASET_CONFIG,
        datasetRevision=SCALE_DATASET_REVISION,
        startingModelId=model_id,
        creatorCount=50,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
        runSeed=11,
        sourceArticlesPerMonth=5_000,
        targetCreatedBabels=10_000,
        concurrentUsers=50,
        stateRoot=str(tmp_path / "state"),
    )


def bundle() -> DatasetBundle:
    def rows(period):
        return tuple(
            {
                "article_key": f"enwiki:{number}",
                "canonical_title": f"{period} {number}",
                "lead_text": f"{period} lead {number}",
                "article_text": f"{period} article {number}",
                "content_hash": hashlib.sha256(
                    f"{period}:{number}".encode()
                ).hexdigest(),
            }
            for number in range(1, 5_001)
        )

    return DatasetBundle(
        root=Path("/dataset"),
        dataset_repository=DEMO_DATASET_REPOSITORY,
        dataset_config=SCALE_DATASET_CONFIG,
        dataset_revision=SCALE_DATASET_REVISION,
        release_scope="timeboxed_engineering_snapshot",
        snapshot_claim="real_timeboxed_engineering_snapshot",
        configs={
            "catalog_2026_06": rows("June"),
            "catalog_2026_07": rows("July"),
            SCALE_DATASET_CONFIG: (),
            "simulator_2026_06_hidden": (),
            "simulator_2026_07_hidden": (),
        },
        manifest_sha256="d" * 64,
    )


class MemoryDatabase:
    def __init__(self):
        self.sources = {}
        self.source_by_id = {}
        self.schedules = {}
        self.vectors = {}
        self.states = {}
        self.created_runs = set()

    def stage_population_plan(self, plan, *, batch_size=500):
        assert batch_size <= 500
        self.sources[plan.run_id] = sorted(
            [
                PopulationSource(row.babel, row.catalog_content_hash)
                for row in plan.babels
            ],
            key=lambda row: str(row.babel.babelId),
        )
        self.source_by_id[plan.run_id] = {
            row.babel.babelId: row for row in self.sources[plan.run_id]
        }
        self.schedules[plan.run_id] = {row.root_babel_id: row for row in plan.schedule}

    def population_sources(self, run_id, *, after_babel_id, limit):
        return [
            row
            for row in self.sources[run_id]
            if after_babel_id is None or str(row.babel.babelId) > str(after_babel_id)
        ][:limit]

    def write_population_batch(self, records, expected):
        target = self.vectors.setdefault(expected.run_id, {})
        for row in records:
            previous = target.get(row.babel.babelId)
            if previous is not None and previous != row:
                raise ValueError("different vector")
            target[row.babel.babelId] = row

    def population_vectors(self, expected, *, after_babel_id, limit):
        return [
            row
            for _key, row in sorted(
                self.vectors.get(expected.run_id, {}).items(),
                key=lambda item: str(item[0]),
            )
            if after_babel_id is None or str(row.babel.babelId) > str(after_babel_id)
        ][:limit]

    def activate_population(self, expected, *, snapshot_sha256):
        self.states[expected.run_id] = (expected, snapshot_sha256)
        return PopulationActivationEvidence(
            table_bytes=4_000_000,
            index_bytes=1,
            explain_plan=[{"Plan": {"Index Name": "babel_embeddings_cosine_hnsw"}}],
        )

    def frozen_population_rows(self, expected, *, after_babel_id, limit):
        records = self.population_vectors(
            expected, after_babel_id=after_babel_id, limit=limit
        )
        result = []
        for record in records:
            f32le = np.asarray(record.vector, dtype="<f4").tobytes()
            wire = (
                struct.pack(">hh", 100, 0)
                + np.asarray(record.vector, dtype=">f4").tobytes()
            )
            schedule = self.schedules[expected.run_id][record.babel.babelId]
            source = self.source_by_id[expected.run_id][record.babel.babelId]
            result.append(
                FrozenPopulationRow(
                    babel=record.babel,
                    catalog_content_hash=source.catalog_content_hash,
                    event_number=schedule.schedule_index,
                    scheduled=schedule,
                    vector_send_bytes=wire,
                    vector_f32le_bytes=f32le,
                )
            )
        return result

    def create_scaled_run(self, destination):
        self.created_runs.add(destination.runId)
        return PersistedRun(destination, "starting", "a" * 64)

    def clone_population_transaction(self, source, destination_run_id):
        from babel_online.model.candidate_index import MaterializedServingState

        self.sources[destination_run_id] = [
            PopulationSource(
                row.babel.model_copy(update={"runId": destination_run_id}),
                row.catalog_content_hash,
            )
            for row in self.sources[source.run_id]
        ]
        self.source_by_id[destination_run_id] = {
            row.babel.babelId: row for row in self.sources[destination_run_id]
        }
        self.schedules[destination_run_id] = {
            babel_id: replace(row, run_id=destination_run_id)
            for babel_id, row in self.schedules[source.run_id].items()
        }
        self.vectors[destination_run_id] = {
            babel_id: row.model_copy(
                update={
                    "babel": row.babel.model_copy(update={"runId": destination_run_id})
                }
            )
            for babel_id, row in self.vectors[source.run_id].items()
        }
        identity, snapshot = self.states[source.run_id]
        self.states[destination_run_id] = (identity, snapshot)
        return MaterializedServingState(
            run_id=destination_run_id,
            model_id=source.model_id,
            model_version=source.model_version,
            embedding_space_id=source.embedding_space_id,
            pgvector_snapshot_sha256=snapshot,
            backend_snapshot_sha256=snapshot,
        )


def test_build_exports_valid_exact_bundle_and_nine_clones_never_encode(
    tmp_path, real_model_manifest, accepted_qwen_factory
) -> None:
    database = MemoryDatabase()
    encoder = accepted_qwen_factory()
    source_config = config(tmp_path, real_model_manifest.modelId, run_id=uuid4())
    identity = PopulationIdentity.from_real_model(
        run_id=source_config.runId,
        dataset_revision=source_config.datasetRevision,
        model=real_model_manifest,
        model_version=0,
    )
    manifest = build_frozen_population(
        database=database,
        config=source_config,
        bundle=bundle(),
        model=real_model_manifest,
        encoder=encoder,
        identity=identity,
        output_root=tmp_path / "artifacts",
        experiment_id="experiment-1",
        batch_size=500,
    )

    population_dir = tmp_path / "artifacts/experiment-1/population"
    assert manifest == load_frozen_population(population_dir)
    assert manifest.babelCount == manifest.scheduleCount == 10_000
    assert manifest.juneCount == manifest.julyCount == 5_000
    assert manifest.creatorCount == 50
    assert manifest.vectorBytes == 4_000_000
    assert (population_dir / "vectors.f32le").stat().st_size == 10_000 * 400
    assert encoder.calls > 0

    calls_after_build = encoder.calls
    for _ in range(9):
        destination = config(tmp_path, real_model_manifest.modelId, run_id=uuid4())
        state = clone_frozen_population(database, manifest, destination)
        assert state.run_id == destination.runId
    assert encoder.calls == calls_after_build


def test_load_rejects_tampered_bundle(
    tmp_path, real_model_manifest, accepted_qwen_factory
):
    database = MemoryDatabase()
    source_config = config(tmp_path, real_model_manifest.modelId, run_id=uuid4())
    manifest = build_frozen_population(
        database=database,
        config=source_config,
        bundle=bundle(),
        model=real_model_manifest,
        encoder=accepted_qwen_factory(),
        identity=PopulationIdentity.from_real_model(
            run_id=source_config.runId,
            dataset_revision=source_config.datasetRevision,
            model=real_model_manifest,
            model_version=0,
        ),
        output_root=tmp_path,
        experiment_id="tamper",
        batch_size=1_000,
    )
    directory = tmp_path / "tamper/population"
    (directory / "vectors.f32le").write_bytes(b"bad")
    with pytest.raises(FrozenPopulationIntegrityError, match="vector"):
        load_frozen_population(directory)


def test_build_reports_committed_progress_and_gracefully_returns_incomplete_receipt(
    tmp_path, real_model_manifest, accepted_qwen_factory
) -> None:
    database = MemoryDatabase()
    source_config = config(tmp_path, real_model_manifest.modelId, run_id=uuid4())
    progress: list[PopulationBatchProgress] = []

    result = build_frozen_population(
        database=database,
        config=source_config,
        bundle=bundle(),
        model=real_model_manifest,
        encoder=accepted_qwen_factory(),
        identity=PopulationIdentity.from_real_model(
            run_id=source_config.runId,
            dataset_revision=source_config.datasetRevision,
            model=real_model_manifest,
            model_version=0,
        ),
        output_root=tmp_path,
        experiment_id="stopped",
        batch_size=128,
        progress_sink=progress.append,
        stop_requested=lambda: bool(progress),
    )

    assert isinstance(result, PopulationReceipt)
    assert result.complete is False
    assert result.indexed_count == 128
    assert [(item.batch_count, item.committed_count) for item in progress] == [
        (128, 128)
    ]
    assert len(database.vectors[source_config.runId]) == 128
    assert not (tmp_path / "stopped/population/manifest.json").exists()
