"""Export and zero-Qwen clone of one canonical database-backed population."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from ..contracts import (
    FrozenContract,
    ModelManifestV2,
    RunConfigV2,
    canonical_pgvector_snapshot_sha256,
)
from ..runtime.dataset_bundle import DatasetBundle
from ..simulation.population_plan import plan_population
from .artifact import model_manifest_sha256
from .candidate_index import MaterializedServingState
from .population import (
    PopulationBatchProgress,
    PopulationIdentity,
    PopulationIntegrityError,
    PopulationReceipt,
    populate_created_babel_vectors,
)
from .qwen_encoder import Qwen100Encoder


class FrozenPopulationIntegrityError(PopulationIntegrityError):
    """A frozen bundle or clone differs from its authoritative source bytes."""


class FrozenPopulationManifestV1(FrozenContract):
    schemaVersion: Literal[1]
    experimentId: str = Field(min_length=1)
    sourcePopulationRunId: UUID
    babelCount: Literal[10_000]
    scheduleCount: Literal[10_000]
    juneCount: Literal[5_000]
    julyCount: Literal[5_000]
    creatorCount: Literal[50]
    modelId: UUID
    modelVersion: int = Field(ge=0)
    modelManifestSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifactManifestSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifactRepo: str = Field(min_length=1)
    artifactRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    artifactId: str = Field(pattern=r"^[a-f0-9]{64}$")
    trainingDatasetRevision: str = Field(pattern=r"^[a-f0-9]{40}$")
    datasetRepo: str = Field(min_length=1)
    datasetConfig: str = Field(min_length=1)
    datasetRevision: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    datasetManifestSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    embeddingSpaceId: UUID
    embeddingSpaceVersion: Literal["babel-qwen-100d-v1"]
    embeddingDimension: Literal[100]
    babelsSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    vectorsSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pgvectorSnapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scheduleSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    babelsBytes: int = Field(gt=0)
    vectorBytes: Literal[4_000_000]
    scheduleBytes: int = Field(gt=0)
    babelsFile: Literal["babels.jsonl"] = "babels.jsonl"
    vectorsFile: Literal["vectors.f32le"] = "vectors.f32le"
    scheduleFile: Literal["schedule.jsonl"] = "schedule.jsonl"

    @model_validator(mode="after")
    def exact_counts(self) -> "FrozenPopulationManifestV1":
        if self.babelCount != self.scheduleCount:
            raise ValueError("frozen Babel and schedule counts differ")
        return self

    def population_identity(self) -> PopulationIdentity:
        return PopulationIdentity(
            run_id=self.sourcePopulationRunId,
            dataset_revision=self.datasetRevision,
            model_id=self.modelId,
            model_version=self.modelVersion,
            model_manifest_sha256=self.modelManifestSha256,
            artifact_manifest_sha256=self.artifactManifestSha256,
            artifact_repo=self.artifactRepo,
            artifact_revision=self.artifactRevision,
            artifact_id=self.artifactId,
            training_dataset_revision=self.trainingDatasetRevision,
            embedding_space_id=self.embeddingSpaceId,
            embedding_space_version=self.embeddingSpaceVersion,
        )


class FrozenPopulationDatabase(Protocol):
    def stage_population_plan(self, plan, *, batch_size: int = 500) -> None: ...

    def frozen_population_rows(
        self,
        expected: PopulationIdentity,
        *,
        after_babel_id: UUID | None,
        limit: int,
    ) -> Sequence[object]: ...

    def create_scaled_run(self, destination: RunConfigV2): ...

    def clone_population_transaction(
        self, source: PopulationIdentity, destination_run_id: UUID
    ) -> MaterializedServingState: ...


def _json_line(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _documents(
    rows: Sequence[object], identity: PopulationIdentity
) -> tuple[bytes, bytes, bytes, str]:
    babel_bytes = bytearray()
    vector_bytes = bytearray()
    schedule_bytes = bytearray()
    snapshots: list[dict[str, object]] = []
    for ordinal, row in enumerate(rows):
        babel = row.babel
        vector = bytes(row.vector_f32le_bytes)
        if len(vector) != 400:
            raise FrozenPopulationIntegrityError("frozen vector row is not 400 bytes")
        vector_sha = hashlib.sha256(vector).hexdigest()
        babel_bytes.extend(
            _json_line(
                {
                    "babelId": str(babel.babelId),
                    "catalogContentHash": row.catalog_content_hash,
                    "createdAtNs": babel.createdAtNs,
                    "creatorId": str(babel.creatorId),
                    "eventNumber": row.event_number,
                    "ordinal": ordinal,
                    "sourceArticleKey": babel.sourceArticleKey,
                    "text": babel.text,
                    "title": babel.title,
                }
            )
        )
        vector_bytes.extend(vector)
        scheduled = row.scheduled
        schedule_bytes.extend(
            _json_line(
                {
                    "creatorEventNumber": scheduled.creator_event_number,
                    "creatorId": str(scheduled.creator_id),
                    "ordinal": ordinal,
                    "period": scheduled.period,
                    "rootBabelId": str(scheduled.root_babel_id),
                    "scheduleIndex": scheduled.schedule_index,
                    "sourceArticleKey": scheduled.source_article_key,
                    "traversalSessionId": str(scheduled.traversal_session_id),
                    "workId": str(scheduled.work_id),
                    "workloadSha256": scheduled.workload_sha256,
                }
            )
        )
        snapshots.append(
            {
                "babelId": babel.babelId,
                "creatorId": babel.creatorId,
                "sourceArticleKey": babel.sourceArticleKey,
                "catalogContentHash": row.catalog_content_hash,
                "embeddingSpaceId": identity.embedding_space_id,
                "servingModelId": identity.model_id,
                "materializedModelVersion": identity.model_version,
                "vectorSha256": vector_sha,
            }
        )
    snapshot = canonical_pgvector_snapshot_sha256(snapshots)
    return bytes(babel_bytes), bytes(vector_bytes), bytes(schedule_bytes), snapshot


def _read_rows(database, identity: PopulationIdentity, batch_size: int) -> list[object]:
    rows: list[object] = []
    after: UUID | None = None
    while True:
        batch = list(
            database.frozen_population_rows(
                identity, after_babel_id=after, limit=batch_size
            )
        )
        if not batch:
            break
        identifiers = [str(row.babel.babelId) for row in batch]
        if identifiers != sorted(identifiers) or (
            after is not None and identifiers[0] <= str(after)
        ):
            raise FrozenPopulationIntegrityError(
                "frozen database rows are not strictly Babel-ID ordered"
            )
        rows.extend(batch)
        after = batch[-1].babel.babelId
    if len(rows) != 10_000 or len({row.babel.babelId for row in rows}) != 10_000:
        raise FrozenPopulationIntegrityError(
            "frozen population is not exactly 10,000 IDs"
        )
    return rows


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_frozen_population(
    *,
    database,
    config: RunConfigV2,
    bundle: DatasetBundle,
    model: ModelManifestV2,
    encoder: Qwen100Encoder,
    identity: PopulationIdentity,
    output_root: str | Path,
    experiment_id: str,
    batch_size: int = 32,
    progress_sink: Callable[[PopulationBatchProgress], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> FrozenPopulationManifestV1 | PopulationReceipt:
    """Materialize once with accepted Qwen and export the trusted frozen bundle."""
    if identity != PopulationIdentity.from_real_model(
        run_id=config.runId,
        dataset_revision=config.datasetRevision,
        model=model,
        model_version=identity.model_version,
    ):
        raise FrozenPopulationIntegrityError(
            "population identity differs from build inputs"
        )
    database.create_scaled_run(config)
    plan = plan_population(config, bundle)
    database.stage_population_plan(plan, batch_size=min(500, max(1, batch_size)))
    receipt = populate_created_babel_vectors(
        database=database,
        encoder=encoder,
        identity=identity,
        state_root=config.stateRoot,
        batch_size=batch_size,
        progress_sink=progress_sink,
        stop_requested=stop_requested,
    )
    if not receipt.complete:
        return receipt
    if not receipt.formal_ready or receipt.indexed_count != 10_000:
        raise FrozenPopulationIntegrityError(
            "formal frozen population did not complete exactly 10,000 vectors"
        )
    rows = _read_rows(database, identity, batch_size)
    babels, vectors, schedule, snapshot = _documents(rows, identity)
    if receipt.snapshot_sha256 != snapshot:
        raise FrozenPopulationIntegrityError(
            "database vector snapshot differs from population activation"
        )
    periods = {"2026-06": 0, "2026-07": 0}
    for row in rows:
        periods[row.scheduled.period] += 1
    manifest = FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=experiment_id,
        sourcePopulationRunId=config.runId,
        babelCount=10_000,
        scheduleCount=10_000,
        juneCount=periods["2026-06"],
        julyCount=periods["2026-07"],
        creatorCount=len({row.babel.creatorId for row in rows}),
        modelId=identity.model_id,
        modelVersion=identity.model_version,
        modelManifestSha256=model_manifest_sha256(model),
        artifactManifestSha256=identity.artifact_manifest_sha256,
        artifactRepo=identity.artifact_repo,
        artifactRevision=identity.artifact_revision,
        artifactId=identity.artifact_id,
        trainingDatasetRevision=identity.training_dataset_revision,
        datasetRepo=config.datasetRepo,
        datasetConfig=config.datasetConfig,
        datasetRevision=config.datasetRevision,
        datasetManifestSha256=bundle.manifest_sha256,
        embeddingSpaceId=identity.embedding_space_id,
        embeddingSpaceVersion=identity.embedding_space_version,
        embeddingDimension=100,
        babelsSha256=_sha(babels),
        vectorsSha256=_sha(vectors),
        pgvectorSnapshotSha256=snapshot,
        scheduleSha256=_sha(schedule),
        babelsBytes=len(babels),
        vectorBytes=len(vectors),
        scheduleBytes=len(schedule),
    )
    directory = Path(output_root) / experiment_id / "population"
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_bytes(directory / manifest.babelsFile, babels)
    _atomic_bytes(directory / manifest.vectorsFile, vectors)
    _atomic_bytes(directory / manifest.scheduleFile, schedule)
    _atomic_bytes(
        directory / "manifest.json",
        _json_line(manifest.model_dump(mode="json")),
    )
    return load_frozen_population(directory)


def _validate_files(directory: Path, manifest: FrozenPopulationManifestV1) -> None:
    checks = (
        (manifest.babelsFile, manifest.babelsBytes, manifest.babelsSha256, "Babel"),
        (manifest.vectorsFile, manifest.vectorBytes, manifest.vectorsSha256, "vector"),
        (
            manifest.scheduleFile,
            manifest.scheduleBytes,
            manifest.scheduleSha256,
            "schedule",
        ),
    )
    for name, expected_size, expected_sha, label in checks:
        path = directory / name
        try:
            value = path.read_bytes()
        except OSError as error:
            raise FrozenPopulationIntegrityError(
                f"frozen {label} file is unavailable"
            ) from error
        if len(value) != expected_size or _sha(value) != expected_sha:
            raise FrozenPopulationIntegrityError(
                f"frozen {label} file checksum differs"
            )
    babel_lines = (directory / manifest.babelsFile).read_bytes().splitlines()
    schedule_lines = (directory / manifest.scheduleFile).read_bytes().splitlines()
    if len(babel_lines) != 10_000 or len(schedule_lines) != 10_000:
        raise FrozenPopulationIntegrityError("frozen JSONL counts differ")
    babels = [json.loads(line) for line in babel_lines]
    schedules = [json.loads(line) for line in schedule_lines]
    if [row.get("ordinal") for row in babels] != list(range(10_000)):
        raise FrozenPopulationIntegrityError("frozen Babel order differs")
    if [row.get("ordinal") for row in schedules] != list(range(10_000)):
        raise FrozenPopulationIntegrityError("frozen schedule order differs")
    if any(
        babel.get("babelId") != schedule.get("rootBabelId")
        or babel.get("creatorId") != schedule.get("creatorId")
        or babel.get("sourceArticleKey") != schedule.get("sourceArticleKey")
        for babel, schedule in zip(babels, schedules, strict=True)
    ):
        raise FrozenPopulationIntegrityError(
            "frozen Babel and schedule identities differ"
        )
    if {row.get("period") for row in schedules} != {"2026-06", "2026-07"}:
        raise FrozenPopulationIntegrityError("frozen schedule periods differ")
    if sum(row.get("period") == "2026-06" for row in schedules) != 5_000:
        raise FrozenPopulationIntegrityError("frozen June count differs")
    if len({row.get("creatorId") for row in babels}) != 50:
        raise FrozenPopulationIntegrityError("frozen creator count differs")
    vector_data = (directory / manifest.vectorsFile).read_bytes()
    snapshot = canonical_pgvector_snapshot_sha256(
        {
            "babelId": babel["babelId"],
            "creatorId": babel["creatorId"],
            "sourceArticleKey": babel["sourceArticleKey"],
            "catalogContentHash": babel["catalogContentHash"],
            "embeddingSpaceId": manifest.embeddingSpaceId,
            "servingModelId": manifest.modelId,
            "materializedModelVersion": manifest.modelVersion,
            "vectorSha256": hashlib.sha256(
                vector_data[index * 400 : (index + 1) * 400]
            ).hexdigest(),
        }
        for index, babel in enumerate(babels)
    )
    if snapshot != manifest.pgvectorSnapshotSha256:
        raise FrozenPopulationIntegrityError("frozen pgvector snapshot hash differs")


def load_frozen_population(path: str | Path) -> FrozenPopulationManifestV1:
    directory = Path(path)
    try:
        manifest = FrozenPopulationManifestV1.model_validate_json(
            (directory / "manifest.json").read_text(encoding="utf-8")
        )
    except Exception as error:
        raise FrozenPopulationIntegrityError(
            "frozen population manifest is invalid"
        ) from error
    _validate_files(directory, manifest)
    return manifest


def clone_frozen_population(
    database: FrozenPopulationDatabase,
    manifest: FrozenPopulationManifestV1,
    destination: RunConfigV2,
) -> MaterializedServingState:
    """Create one condition run and clone exact DB rows without invoking an encoder."""
    if not isinstance(destination, RunConfigV2):
        raise TypeError("frozen population destination requires RunConfigV2")
    if (
        destination.runId == manifest.sourcePopulationRunId
        or destination.datasetRepo != manifest.datasetRepo
        or destination.datasetConfig != manifest.datasetConfig
        or destination.datasetRevision != manifest.datasetRevision
        or destination.startingModelId != manifest.modelId
        or destination.creatorCount != 50
        or destination.perMonthEventBudget != {"2026-06": 5_000, "2026-07": 5_000}
        or destination.targetCreatedBabels != 10_000
    ):
        raise FrozenPopulationIntegrityError(
            "clone destination differs from frozen population"
        )
    database.create_scaled_run(destination)
    source = manifest.population_identity()
    state = database.clone_population_transaction(source, destination.runId)
    destination_identity = replace(source, run_id=destination.runId)
    rows = _read_rows(database, destination_identity, 500)
    babels, vectors, schedule, snapshot = _documents(rows, destination_identity)
    if (
        _sha(babels) != manifest.babelsSha256
        or _sha(vectors) != manifest.vectorsSha256
        or _sha(schedule) != manifest.scheduleSha256
        or snapshot != manifest.pgvectorSnapshotSha256
        or state.pgvector_snapshot_sha256 != manifest.pgvectorSnapshotSha256
        or state.backend_snapshot_sha256 != manifest.pgvectorSnapshotSha256
    ):
        raise FrozenPopulationIntegrityError(
            "destination normalized population checksums differ from source"
        )
    return state


def freeze_cloned_population(
    *,
    database: FrozenPopulationDatabase,
    config: RunConfigV2,
    bundle: DatasetBundle,
    model: ModelManifestV2,
    model_version: int,
    source_identity: PopulationIdentity,
    expected_snapshot_sha256: str,
    output_root: str | Path,
    experiment_id: str,
    batch_size: int = 500,
) -> FrozenPopulationManifestV1:
    """Clone a selected real child population and bind a fresh trial manifest."""
    destination_identity = PopulationIdentity.from_real_model(
        run_id=config.runId,
        dataset_revision=config.datasetRevision,
        model=model,
        model_version=model_version,
    )
    if (
        source_identity.run_id == config.runId
        or replace(source_identity, run_id=config.runId) != destination_identity
        or config.startingModelId != model.modelId
    ):
        raise FrozenPopulationIntegrityError(
            "selected child clone identity differs from destination"
        )
    database.create_scaled_run(config)
    state = database.clone_population_transaction(source_identity, config.runId)
    rows = _read_rows(database, destination_identity, batch_size)
    babels, vectors, schedule, snapshot = _documents(rows, destination_identity)
    if (
        snapshot != expected_snapshot_sha256
        or state.model_id != model.modelId
        or state.model_version != model_version
        or state.embedding_space_id != model.embeddingSpace.embeddingSpaceId
        or state.pgvector_snapshot_sha256 != expected_snapshot_sha256
        or state.backend_snapshot_sha256 != expected_snapshot_sha256
    ):
        raise FrozenPopulationIntegrityError(
            "selected child clone snapshot differs from immutable source"
        )
    periods = {"2026-06": 0, "2026-07": 0}
    for row in rows:
        periods[row.scheduled.period] += 1
    manifest = FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=experiment_id,
        sourcePopulationRunId=config.runId,
        babelCount=10_000,
        scheduleCount=10_000,
        juneCount=periods["2026-06"],
        julyCount=periods["2026-07"],
        creatorCount=len({row.babel.creatorId for row in rows}),
        modelId=model.modelId,
        modelVersion=model_version,
        modelManifestSha256=model_manifest_sha256(model),
        artifactManifestSha256=destination_identity.artifact_manifest_sha256,
        artifactRepo=destination_identity.artifact_repo,
        artifactRevision=destination_identity.artifact_revision,
        artifactId=destination_identity.artifact_id,
        trainingDatasetRevision=destination_identity.training_dataset_revision,
        datasetRepo=config.datasetRepo,
        datasetConfig=config.datasetConfig,
        datasetRevision=config.datasetRevision,
        datasetManifestSha256=bundle.manifest_sha256,
        embeddingSpaceId=destination_identity.embedding_space_id,
        embeddingSpaceVersion=destination_identity.embedding_space_version,
        embeddingDimension=100,
        babelsSha256=_sha(babels),
        vectorsSha256=_sha(vectors),
        pgvectorSnapshotSha256=snapshot,
        scheduleSha256=_sha(schedule),
        babelsBytes=len(babels),
        vectorBytes=len(vectors),
        scheduleBytes=len(schedule),
    )
    directory = Path(output_root) / experiment_id / "population"
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_bytes(directory / manifest.babelsFile, babels)
    _atomic_bytes(directory / manifest.vectorsFile, vectors)
    _atomic_bytes(directory / manifest.scheduleFile, schedule)
    _atomic_bytes(
        directory / "manifest.json", _json_line(manifest.model_dump(mode="json"))
    )
    return load_frozen_population(directory)


__all__ = [
    "FrozenPopulationIntegrityError",
    "FrozenPopulationManifestV1",
    "build_frozen_population",
    "clone_frozen_population",
    "freeze_cloned_population",
    "load_frozen_population",
]
