"""Read-only export of the single accepted local performance population."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..model.frozen_population import (
    FrozenPopulationManifestV1,
    load_frozen_population,
)
from ..model.population import PopulationIntegrityError
from ..contracts import RunConfigV2
from ..runtime.database import RuntimeDatabase
from .contracts import (
    BASE_MODEL_REPOSITORY,
    BASE_MODEL_REVISION,
    DATASET_CONFIGURATION,
    DATASET_REPOSITORY,
    DATASET_REVISION,
    EMBEDDING_SPACE_ID,
    MODEL_ARTIFACT_ID,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    ORIGIN_RUN_ID,
    ORIGIN_TRIAL_ID,
    SERVING_MODEL_ID,
    OriginToFreshRebindingV1,
    PopulationTransferMetadataV1,
)
from .parquet_bundle import (
    BundleFiles,
    PopulationTransferBundleInput,
    PopulationTransferIntegrityError,
    PopulationTransferRow,
    vector_f32le,
    verify_bundle,
    write_bundle_payloads,
)


_TRAINING_DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
_TRAINING_DATASET_REVISION = "b440e98b04ab77afed7caf0455eca3189235fc3b"
_ARTIFACT_MANIFEST_SHA256 = (
    "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
)
_MODEL_MANIFEST_SHA256 = (
    "174e5109b5f34808b2d3814b12a6b2a452da1f1828f43561d392aa58844a8f09"
)
_SOURCE_DATASET_MANIFEST_SHA256 = (
    "069c84e32195d7e175968aa0c569fe5bebc3a148247dbf9e7e34918ef3a22c0f"
)
_SHA256 = frozenset("0123456789abcdef")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connect_psycopg(database_url: str):
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - deployment setup
        raise RuntimeError("population export requires babel-online[pgvector]") from error
    return psycopg.connect(database_url)


class ExportReceiptV1(BaseModel):
    """Protected local receipt for one independently verified bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schemaVersion: Literal[1]
    originTrialId: UUID
    originRunId: UUID
    bundlePath: str = Field(min_length=1)
    bundleDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    rowCount: Literal[10_000]
    sourceFrozenManifestSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exportedAt: datetime

    @field_validator("originTrialId")
    @classmethod
    def exact_origin_trial(cls, value: UUID) -> UUID:
        if value != ORIGIN_TRIAL_ID:
            raise ValueError("export receipt trial differs from frozen origin")
        return value

    @field_validator("originRunId")
    @classmethod
    def exact_origin_run(cls, value: UUID) -> UUID:
        if value != ORIGIN_RUN_ID:
            raise ValueError("export receipt run differs from frozen origin")
        return value

    @field_validator("exportedAt")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("exportedAt must be an aware UTC timestamp")
        return value


class ImportReceiptV1(BaseModel):
    """Protected state machine receipt for one fresh-ID population import."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schemaVersion: Literal[1]
    state: Literal["planned", "quarantined", "ready"]
    bundleDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    originTrialId: UUID
    originRunId: UUID
    freshTrialId: UUID
    freshPopulationRunId: UUID
    rowCount: Literal[10_000]
    orderedVectorSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frozenManifestSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sampleCount: Literal[100]
    hnswIndex: Literal["babel_embeddings_cosine_hnsw"]
    modelArtifactManifestPath: str = Field(min_length=1)
    modelCheckpointRoot: str = Field(min_length=1)

    @field_validator("freshTrialId")
    @classmethod
    def fresh_trial_is_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("freshTrialId must be UUIDv4")
        return value

    @field_validator("freshPopulationRunId")
    @classmethod
    def fresh_run_is_uuid5(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("freshPopulationRunId must be UUIDv5")
        return value

    @field_validator("originTrialId")
    @classmethod
    def origin_trial_is_exact(cls, value: UUID) -> UUID:
        if value != ORIGIN_TRIAL_ID:
            raise ValueError("originTrialId differs from accepted origin")
        return value

    @field_validator("originRunId")
    @classmethod
    def origin_run_is_exact(cls, value: UUID) -> UUID:
        if value != ORIGIN_RUN_ID:
            raise ValueError("originRunId differs from accepted origin")
        return value


@dataclass(frozen=True, slots=True)
class _SourceEvidence:
    trial_id: object
    run_id: object
    starting_model_id: object
    population_ready: object
    population_vector_count: object
    population_vector_sha256: object
    trial_model_repository: object
    trial_model_revision: object
    population_model_repository: object
    population_model_revision: object
    population_model_manifest_sha256: object
    trial_dataset_repository: object
    trial_dataset_revision: object
    population_dataset_repository: object
    population_dataset_revision: object
    population_dataset_manifest_sha256: object
    population_manifest_sha256: object
    population_bundle_path: object
    source_run_status: object
    source_dataset_configuration: object
    source_state_root: object
    source_target_created_babels: object
    source_creator_count: object
    active_model_id: object
    active_model_version: object
    active_embedding_space_id: object
    pgvector_snapshot_sha256: object
    backend_snapshot_sha256: object
    registered_model_repository: object
    registered_model_revision: object
    registered_training_dataset_repository: object
    registered_training_dataset_revision: object
    registered_artifact_manifest_sha256: object
    registered_embedding_space: object
    latest_phase: object
    latest_seeded_articles: object
    latest_created_babels: object
    latest_indexed_babels: object
    approval_vector_count: object
    approval_vector_sha256: object

    @classmethod
    def from_row(cls, row: Sequence[object]) -> "_SourceEvidence":
        if len(row) != 40:
            raise PopulationTransferIntegrityError(
                "authoritative population evidence row has an unexpected shape"
            )
        return cls(*row)


_EVIDENCE_SQL = """
WITH latest_progress AS (
  SELECT phase,seeded_articles,created_babels,indexed_babels
  FROM performance_progress_snapshots
  WHERE experiment_id=%s AND phase IN ('population','population_ready')
  ORDER BY sequence DESC
  LIMIT 1
), latest_approval AS (
  SELECT population_vector_count,population_vector_sha256
  FROM performance_approvals
  WHERE experiment_id=%s AND action='start_matrix'
  ORDER BY approval_sequence DESC
  LIMIT 1
)
SELECT pe.id,pe.run_id,pe.starting_model_id,pe.population_ready,
       pe.population_vector_count,pe.population_vector_sha256,
       pe.model_repository,pe.model_revision,
       pe.population_model_repository,pe.population_model_revision,
       pe.population_model_sha256,
       pe.dataset_repository,pe.dataset_revision,
       pe.population_dataset_repository,pe.population_dataset_revision,
       pe.population_dataset_sha256,
       pe.population_manifest_sha256,pe.population_bundle_path,
       er.status,er.dataset_config,er.state_root,
       er.target_created_babels,er.creator_count,
       active.active_model_id,active.active_model_version,
       active.embedding_space_id,active.pgvector_snapshot_sha256,
       active.backend_snapshot_sha256,
       rm.encoder_repo,rm.encoder_revision,rm.dataset_repo,rm.dataset_revision,
       rm.checkpoint_sha256,rm.embedding_space,
       latest_progress.phase,latest_progress.seeded_articles,
       latest_progress.created_babels,latest_progress.indexed_babels,
       latest_approval.population_vector_count,
       latest_approval.population_vector_sha256
FROM performance_experiments AS pe
JOIN experiment_runs AS er ON er.id=pe.run_id
JOIN run_embedding_states AS active ON active.run_id=er.id
JOIN recommender_models AS rm ON rm.id=pe.starting_model_id
JOIN latest_progress ON true
LEFT JOIN latest_approval ON true
WHERE pe.id=%s
"""


_POPULATION_SQL = """
SELECT xb.babel_id,xb.creator_id,xb.source_article_key,xb.title,xb.article_text,
       xb.catalog_content_hash,xb.event_number,
       (extract(epoch from xb.created_at) * 1000000000)::bigint,
       (extract(epoch from xb.finalized_at) * 1000000000)::bigint,
       ws.schedule_index,ws.creator_event_number,ws.period,ws.root_babel_id,
       ws.traversal_session_id,ws.work_id,ws.workload_sha256,
       (extract(epoch from ws.created_at) * 1000000000)::bigint,
       eb.serving_model_id,eb.materialized_model_version,eb.embedding_space_id,
       public.vector_send(eb.embedding),
       eb.creator_id,eb.catalog_content_hash,
       er.dataset_repository,er.dataset_config,er.dataset_revision
FROM performance_experiments AS pe
JOIN experiment_runs AS er ON er.id=pe.run_id
JOIN experiment_babels AS xb ON xb.run_id=er.id
JOIN experiment_work_schedule AS ws
  ON ws.run_id=xb.run_id
 AND ws.root_babel_id=xb.babel_id
 AND ws.creator_id=xb.creator_id
 AND ws.source_article_key=xb.source_article_key
JOIN babel_embeddings AS eb
  ON eb.run_id=xb.run_id
 AND eb.babel_id=xb.babel_id
 AND eb.creator_id=xb.creator_id
 AND eb.catalog_content_hash=xb.catalog_content_hash
JOIN run_embedding_states AS active ON active.run_id=er.id
JOIN recommender_models AS rm ON rm.id=eb.serving_model_id
WHERE pe.id=%s AND er.id=%s
  AND pe.population_ready=true
  AND er.status='completed'
  AND xb.finalized_at IS NOT NULL
  AND xb.article_text IS NOT NULL
  AND xb.catalog_content_hash IS NOT NULL
  AND eb.serving_model_id=%s
  AND eb.materialized_model_version=%s
  AND eb.embedding_space_id=%s
  AND active.active_model_id=eb.serving_model_id
  AND active.active_model_version=eb.materialized_model_version
  AND active.embedding_space_id=eb.embedding_space_id
  AND rm.id=pe.starting_model_id
ORDER BY xb.babel_id
"""


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256 for character in value)
    )


def _read_regular_json(path: Path, label: str) -> Mapping[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as error:
        raise PopulationTransferIntegrityError(f"{label} is unavailable") from error
    try:
        details = os.fstat(file_descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise PopulationTransferIntegrityError(f"{label} is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        document = json.loads(b"".join(chunks))
    except PopulationTransferIntegrityError:
        raise
    except Exception as error:
        raise PopulationTransferIntegrityError(f"{label} is invalid") from error
    finally:
        os.close(file_descriptor)
    if not isinstance(document, Mapping):
        raise PopulationTransferIntegrityError(f"{label} is not a JSON object")
    return document


def _frozen_manifest_sha256(root: Path) -> str:
    path = root / "manifest.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PopulationTransferIntegrityError("frozen manifest is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise PopulationTransferIntegrityError(
                "frozen manifest is not a regular file"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _load_and_validate_frozen(
    evidence: _SourceEvidence,
) -> tuple[FrozenPopulationManifestV1, str]:
    if not isinstance(evidence.population_bundle_path, str) or not evidence.population_bundle_path:
        raise PopulationTransferIntegrityError("immutable population binding is absent")
    root = Path(evidence.population_bundle_path)
    try:
        manifest = load_frozen_population(root)
    except Exception as error:
        raise PopulationTransferIntegrityError("frozen manifest validation failed") from error
    manifest_sha = _frozen_manifest_sha256(root)
    if manifest_sha != evidence.population_manifest_sha256:
        raise PopulationTransferIntegrityError("frozen manifest binding differs")
    return manifest, manifest_sha


def _validate_evidence(
    evidence: _SourceEvidence, manifest: FrozenPopulationManifestV1
) -> None:
    exact_pairs = (
        (evidence.trial_id, ORIGIN_TRIAL_ID, "trial identity"),
        (evidence.run_id, ORIGIN_RUN_ID, "immutable population binding"),
        (evidence.starting_model_id, SERVING_MODEL_ID, "starting model"),
        (evidence.population_vector_count, 10_000, "population vector count"),
        (evidence.trial_model_repository, MODEL_REPOSITORY, "trial model repository"),
        (evidence.trial_model_revision, MODEL_REVISION, "trial model revision"),
        (
            evidence.population_model_repository,
            MODEL_REPOSITORY,
            "population model repository",
        ),
        (
            evidence.population_model_revision,
            MODEL_REVISION,
            "population model revision",
        ),
        (
            evidence.trial_dataset_repository,
            DATASET_REPOSITORY,
            "trial dataset repository",
        ),
        (evidence.trial_dataset_revision, DATASET_REVISION, "trial dataset revision"),
        (
            evidence.population_dataset_repository,
            DATASET_REPOSITORY,
            "population dataset repository",
        ),
        (
            evidence.population_dataset_revision,
            DATASET_REVISION,
            "population dataset revision",
        ),
        (
            evidence.source_dataset_configuration,
            DATASET_CONFIGURATION,
            "source dataset configuration",
        ),
        (evidence.source_target_created_babels, 10_000, "source target count"),
        (evidence.source_creator_count, 50, "source creator count"),
        (evidence.active_model_id, SERVING_MODEL_ID, "active model"),
        (evidence.active_model_version, 0, "model version"),
        (evidence.active_embedding_space_id, EMBEDDING_SPACE_ID, "embedding space"),
        (
            evidence.registered_model_repository,
            MODEL_REPOSITORY,
            "model repository",
        ),
        (evidence.registered_model_revision, MODEL_REVISION, "model revision"),
        (
            evidence.registered_training_dataset_repository,
            _TRAINING_DATASET_REPOSITORY,
            "training dataset repository",
        ),
        (
            evidence.registered_training_dataset_revision,
            _TRAINING_DATASET_REVISION,
            "training dataset revision",
        ),
        (
            evidence.registered_artifact_manifest_sha256,
            _ARTIFACT_MANIFEST_SHA256,
            "artifact manifest",
        ),
    )
    for actual, expected, label in exact_pairs:
        if str(actual) != str(expected):
            raise PopulationTransferIntegrityError(f"{label} differs")
    if evidence.population_ready is not True:
        raise PopulationTransferIntegrityError("population_ready evidence is false")
    if evidence.source_run_status != "completed":
        raise PopulationTransferIntegrityError("source run is not completed")
    progress = (
        (evidence.latest_phase, "population_ready", "latest durable phase"),
        (evidence.latest_seeded_articles, 10_000, "seeded count"),
        (evidence.latest_created_babels, 10_000, "created count"),
        (evidence.latest_indexed_babels, 10_000, "indexed count"),
    )
    for actual, expected, label in progress:
        if actual != expected:
            raise PopulationTransferIntegrityError(f"{label} differs")
    if evidence.approval_vector_count != 10_000:
        raise PopulationTransferIntegrityError("approval count differs")
    if evidence.approval_vector_sha256 != evidence.population_vector_sha256:
        raise PopulationTransferIntegrityError("approval vector hash differs")
    if evidence.population_vector_sha256 != manifest.vectorsSha256:
        raise PopulationTransferIntegrityError("ordered population hash differs")
    if evidence.population_model_manifest_sha256 != manifest.modelManifestSha256:
        raise PopulationTransferIntegrityError("population model manifest differs")
    if evidence.population_dataset_manifest_sha256 != manifest.datasetManifestSha256:
        raise PopulationTransferIntegrityError("population dataset manifest differs")
    if (
        evidence.pgvector_snapshot_sha256 != manifest.pgvectorSnapshotSha256
        or evidence.backend_snapshot_sha256 != manifest.pgvectorSnapshotSha256
    ):
        raise PopulationTransferIntegrityError("active snapshot hash differs")
    embedding = evidence.registered_embedding_space
    if not isinstance(embedding, Mapping):
        raise PopulationTransferIntegrityError("registered embedding space is invalid")
    expected_embedding = {
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "dimension": 100,
        "distance": "cosine",
        "compatibilityVersion": "babel-qwen-100d-v1",
        "datasetRevision": _TRAINING_DATASET_REVISION,
        "distilledEncoderArtifact": (
            f"hf://{MODEL_REPOSITORY}@{MODEL_REVISION}/artifacts/{MODEL_ARTIFACT_ID}"
        ),
    }
    for field, expected in expected_embedding.items():
        if str(embedding.get(field)) != str(expected):
            raise PopulationTransferIntegrityError(
                f"registered embedding space {field} differs"
            )
    frozen_pairs = (
        (manifest.experimentId, str(ORIGIN_TRIAL_ID), "frozen trial"),
        (manifest.sourcePopulationRunId, ORIGIN_RUN_ID, "frozen source run"),
        (manifest.modelId, SERVING_MODEL_ID, "frozen model"),
        (manifest.modelVersion, 0, "frozen model version"),
        (manifest.artifactRepo, MODEL_REPOSITORY, "frozen model repository"),
        (manifest.artifactRevision, MODEL_REVISION, "frozen model revision"),
        (manifest.artifactId, MODEL_ARTIFACT_ID, "frozen artifact"),
        (
            manifest.artifactManifestSha256,
            evidence.registered_artifact_manifest_sha256,
            "frozen artifact manifest",
        ),
        (
            manifest.trainingDatasetRevision,
            evidence.registered_training_dataset_revision,
            "frozen training dataset revision",
        ),
        (manifest.datasetRepo, DATASET_REPOSITORY, "frozen dataset repository"),
        (manifest.datasetConfig, DATASET_CONFIGURATION, "frozen dataset configuration"),
        (manifest.datasetRevision, DATASET_REVISION, "frozen dataset revision"),
        (manifest.embeddingSpaceId, EMBEDDING_SPACE_ID, "frozen embedding space"),
        (manifest.creatorCount, 50, "frozen creator count"),
        (manifest.juneCount, 5_000, "frozen June count"),
        (manifest.julyCount, 5_000, "frozen July count"),
    )
    for actual, expected, label in frozen_pairs:
        if str(actual) != str(expected):
            raise PopulationTransferIntegrityError(f"{label} differs")


def _load_and_validate_journal(
    evidence: _SourceEvidence, manifest: FrozenPopulationManifestV1
) -> Mapping[str, object]:
    if not isinstance(evidence.source_state_root, str) or not evidence.source_state_root:
        raise PopulationTransferIntegrityError("source state root is absent")
    path = (
        Path(evidence.source_state_root)
        / str(ORIGIN_RUN_ID)
        / "population"
        / "journal.json"
    )
    journal = _read_regular_json(path, "population journal")
    checks = (
        (journal.get("complete"), True, "journal complete flag"),
        (journal.get("committed_count"), 10_000, "journal count"),
        (journal.get("failure_count"), 0, "journal current failure count"),
        (
            journal.get("unresolved_failure_count"),
            0,
            "journal unresolved failure count",
        ),
        (journal.get("hnsw_used"), True, "journal HNSW evidence"),
        (
            journal.get("snapshot_sha256"),
            manifest.pgvectorSnapshotSha256,
            "journal snapshot hash",
        ),
        (
            journal.get("identity"),
            manifest.population_identity().document(),
            "journal identity",
        ),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise PopulationTransferIntegrityError(f"{label} differs")
    if not _is_sha256(journal.get("committed_prefix_sha256")):
        raise PopulationTransferIntegrityError("journal committed prefix is invalid")
    try:
        UUID(str(journal.get("last_committed_babel_id")))
    except (TypeError, ValueError, AttributeError) as error:
        raise PopulationTransferIntegrityError(
            "journal last committed Babel ID is invalid"
        ) from error
    return journal


def _rows_from_database(rows: Sequence[Sequence[object]]) -> tuple[PopulationTransferRow, ...]:
    if len(rows) != 10_000:
        raise PopulationTransferIntegrityError(
            "authoritative selector did not return exactly 10,000 rows"
        )
    converted: list[PopulationTransferRow] = []
    for row in rows:
        if len(row) != 26:
            raise PopulationTransferIntegrityError(
                "authoritative population row has an unexpected shape"
            )
        try:
            _wire, f32le = RuntimeDatabase._decode_vector_send(row[20])
        except (PopulationIntegrityError, TypeError, ValueError) as error:
            raise PopulationTransferIntegrityError(
                "authoritative pgvector binary row is invalid"
            ) from error
        if str(row[21]) != str(row[1]):
            raise PopulationTransferIntegrityError(
                "embedding creator differs from catalog creator"
            )
        if str(row[22]) != str(row[5]):
            raise PopulationTransferIntegrityError(
                "embedding content hash differs from catalog content hash"
            )
        converted.append(
            PopulationTransferRow(
                babel_id=str(row[0]),
                creator_id=str(row[1]),
                source_article_key=str(row[2]),
                title=str(row[3]),
                article_text=str(row[4]),
                catalog_content_hash=str(row[5]),
                event_number=int(row[6]),
                created_at_ns=int(row[7]),
                finalized_at_ns=int(row[8]),
                schedule_index=int(row[9]),
                creator_event_number=int(row[10]),
                period=str(row[11]),
                root_babel_id=str(row[12]),
                traversal_session_id=str(row[13]),
                work_id=str(row[14]),
                workload_sha256=str(row[15]),
                schedule_created_at_ns=int(row[16]),
                serving_model_id=str(row[17]),
                materialized_model_version=int(row[18]),
                embedding_space_id=str(row[19]),
                vector=np.frombuffer(f32le, dtype="<f4").copy(),
                dataset_repository=str(row[23]),
                dataset_configuration=str(row[24]),
                dataset_revision=str(row[25]),
                model_artifact_id=MODEL_ARTIFACT_ID,
            )
        )
    return tuple(converted)


def _metadata(created_at: datetime) -> PopulationTransferMetadataV1:
    return PopulationTransferMetadataV1(
        originTrialId=ORIGIN_TRIAL_ID,
        originRunId=ORIGIN_RUN_ID,
        modelRepository=MODEL_REPOSITORY,
        modelRevision=MODEL_REVISION,
        modelArtifactId=MODEL_ARTIFACT_ID,
        servingModelId=SERVING_MODEL_ID,
        materializedModelVersion=0,
        embeddingSpaceId=EMBEDDING_SPACE_ID,
        embeddingSpaceVersion="babel-qwen-100d-v1",
        baseModelRepository=BASE_MODEL_REPOSITORY,
        baseModelRevision=BASE_MODEL_REVISION,
        datasetRepository=DATASET_REPOSITORY,
        datasetConfiguration=DATASET_CONFIGURATION,
        datasetRevision=DATASET_REVISION,
        createdAt=created_at,
        rebinding=OriginToFreshRebindingV1(
            originRunId=ORIGIN_RUN_ID,
            freshTrialIdBinding="allocate_uuid4",
            freshPopulationRunIdBinding="uuid5(fresh_trial_id,'population')",
            preserveBabelIds=True,
            preserveCreatorIds=True,
            preserveSourceIdentity=True,
            preserveModelIdentity=True,
            preserveArtifactIdentity=True,
            preserveEmbeddingSpaceIdentity=True,
            preserveContentIdentities=True,
            preserveScheduleIdentities=True,
            preserveVectorIdentities=True,
        ),
    )


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically install a directory without replacing any destination."""

    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:  # pragma: no cover - Linux deployment requirement
        raise PopulationTransferIntegrityError(
            "create-only atomic bundle installation is unavailable"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        rename_noreplace,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise PopulationTransferIntegrityError(
            "bundle digest destination collision"
        )
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _write_authoritative_bundle(
    source: PopulationTransferBundleInput,
    output_root: str | Path,
    frozen_manifest: FrozenPopulationManifestV1,
) -> BundleFiles:
    """Install only after bundle and source-frozen hashes agree."""

    output_parent = Path(output_root)
    if output_parent.is_symlink():
        raise PopulationTransferIntegrityError("bundle output root must not be a symlink")
    output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not output_parent.is_dir():
        raise PopulationTransferIntegrityError("bundle output root is not a directory")
    if stat.S_IMODE(output_parent.stat().st_mode) & 0o077:
        raise PopulationTransferIntegrityError(
            "bundle output root must be mode 0700 or stricter"
        )
    temporary_parent = Path(
        tempfile.mkdtemp(prefix=".population-export.", dir=output_parent)
    )
    temporary_parent.chmod(0o700)
    staged = temporary_parent / "bundle"
    try:
        bundle = write_bundle_payloads(source, staged)
        verified = verify_bundle(bundle.root, bundle.digest)
        transfer_manifest = verified.manifest_contract
        frozen_hashes = (
            (
                transfer_manifest.orderedPopulationSha256,
                frozen_manifest.vectorsSha256,
                "ordered population hash",
            ),
            (
                transfer_manifest.snapshotSha256,
                frozen_manifest.pgvectorSnapshotSha256,
                "snapshot hash",
            ),
            (
                transfer_manifest.scheduleSha256,
                frozen_manifest.scheduleSha256,
                "schedule hash",
            ),
            (
                transfer_manifest.contentSha256,
                frozen_manifest.babelsSha256,
                "content hash",
            ),
        )
        for actual, expected, label in frozen_hashes:
            if actual != expected:
                raise PopulationTransferIntegrityError(
                    f"exported {label} differs from frozen population"
                )
        destination = output_parent / verified.digest
        _rename_directory_noreplace(staged, destination)
        return verify_bundle(destination, verified.digest)
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)


def export_population(
    database_url: str, trial_id: UUID, output_root: str | Path
) -> ExportReceiptV1:
    """Export the exact accepted origin population without issuing write SQL."""

    if trial_id != ORIGIN_TRIAL_ID:
        raise PopulationTransferIntegrityError("trial ID is not the accepted origin trial")
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("database URL must be non-empty")
    exported_at = _utc_now()
    manifest: FrozenPopulationManifestV1
    source_manifest_sha: str
    with _connect_psycopg(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        cursor.execute(_EVIDENCE_SQL, (trial_id, trial_id, trial_id))
        evidence_row = cursor.fetchone()
        if evidence_row is None:
            raise PopulationTransferIntegrityError(
                "authoritative population evidence is absent"
            )
        evidence = _SourceEvidence.from_row(evidence_row)
        manifest, source_manifest_sha = _load_and_validate_frozen(evidence)
        _validate_evidence(evidence, manifest)
        journal = _load_and_validate_journal(evidence, manifest)
        cursor.execute(
            _POPULATION_SQL,
            (
                ORIGIN_TRIAL_ID,
                ORIGIN_RUN_ID,
                SERVING_MODEL_ID,
                0,
                EMBEDDING_SPACE_ID,
            ),
        )
        rows = _rows_from_database(cursor.fetchall())
        current_manifest_sha = _frozen_manifest_sha256(
            Path(str(evidence.population_bundle_path))
        )
        if current_manifest_sha != source_manifest_sha:
            raise PopulationTransferIntegrityError("frozen manifest changed during export")
        if _load_and_validate_journal(evidence, manifest) != journal:
            raise PopulationTransferIntegrityError("population journal changed during export")

    verified = _write_authoritative_bundle(
        PopulationTransferBundleInput(metadata=_metadata(exported_at), rows=rows),
        output_root,
        manifest,
    )
    return ExportReceiptV1(
        schemaVersion=1,
        originTrialId=ORIGIN_TRIAL_ID,
        originRunId=ORIGIN_RUN_ID,
        bundlePath=str(verified.root.resolve()),
        bundleDigest=verified.digest,
        rowCount=verified.manifest_contract.rowCount,
        sourceFrozenManifestSha256=source_manifest_sha,
        exportedAt=exported_at,
    )


def _deterministic_sample_ordinals(
    bundle_digest: str, *, row_count: int, count: int
) -> tuple[int, ...]:
    """Choose stable distinct sample positions without trusting process randomness."""

    if not _is_sha256(bundle_digest):
        raise PopulationTransferIntegrityError("sample digest is not a SHA-256")
    if row_count <= 0 or count <= 0 or count > row_count:
        raise PopulationTransferIntegrityError("sample dimensions are invalid")
    selected: list[int] = []
    seen: set[int] = set()
    counter = 0
    while len(selected) < count:
        value = hashlib.sha256(
            f"{bundle_digest}:sample:{counter}".encode("ascii")
        ).digest()
        ordinal = int.from_bytes(value, "big") % row_count
        if ordinal not in seen:
            seen.add(ordinal)
            selected.append(ordinal)
        counter += 1
    return tuple(selected)


def _load_verified_transfer_rows(verified: BundleFiles) -> tuple[PopulationTransferRow, ...]:
    """Load two already-verified Parquet payloads without changing vector bytes."""

    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - deployment setup
        raise RuntimeError("population import requires babel-online[parquet]") from error
    embeddings = pq.read_table(verified.embeddings).to_pylist()
    catalog = pq.read_table(verified.catalog).to_pylist()
    if len(embeddings) != 10_000 or len(catalog) != 10_000:
        raise PopulationTransferIntegrityError(
            "verified transfer payload does not contain exactly 10,000 rows"
        )
    embedding_ids = [row["babel_id"] for row in embeddings]
    catalog_ids = [row["babel_id"] for row in catalog]
    if (
        embedding_ids != catalog_ids
        or embedding_ids != sorted(embedding_ids)
        or len(set(embedding_ids)) != 10_000
    ):
        raise PopulationTransferIntegrityError(
            "verified transfer payload identities are not aligned and ordered"
        )
    rows: list[PopulationTransferRow] = []
    for embedding, item in zip(embeddings, catalog, strict=True):
        for field in (
            "creator_id",
            "catalog_content_hash",
            "dataset_revision",
        ):
            if embedding[field] != item[field]:
                raise PopulationTransferIntegrityError(
                    f"verified transfer {field} differs between payloads"
                )
        encoded = vector_f32le(embedding["vector"])
        if hashlib.sha256(encoded).hexdigest() != embedding["vector_sha256"]:
            raise PopulationTransferIntegrityError(
                "verified transfer vector checksum differs"
            )
        rows.append(
            PopulationTransferRow(
                babel_id=str(embedding["babel_id"]),
                creator_id=str(embedding["creator_id"]),
                serving_model_id=str(embedding["serving_model_id"]),
                materialized_model_version=int(
                    embedding["materialized_model_version"]
                ),
                embedding_space_id=str(embedding["embedding_space_id"]),
                catalog_content_hash=str(embedding["catalog_content_hash"]),
                model_artifact_id=str(embedding["model_artifact_id"]),
                dataset_revision=str(embedding["dataset_revision"]),
                vector=np.frombuffer(encoded, dtype="<f4").copy(),
                source_article_key=str(item["source_article_key"]),
                title=str(item["title"]),
                article_text=str(item["article_text"]),
                event_number=int(item["event_number"]),
                created_at_ns=int(item["created_at_ns"]),
                finalized_at_ns=int(item["finalized_at_ns"]),
                schedule_index=int(item["schedule_index"]),
                creator_event_number=int(item["creator_event_number"]),
                period=str(item["period"]),
                root_babel_id=str(item["root_babel_id"]),
                traversal_session_id=str(item["traversal_session_id"]),
                work_id=str(item["work_id"]),
                workload_sha256=str(item["workload_sha256"]),
                schedule_created_at_ns=int(item["schedule_created_at_ns"]),
                dataset_repository=str(item["dataset_repository"]),
                dataset_configuration=str(item["dataset_configuration"]),
            )
        )
    return tuple(rows)


def _build_rebound_frozen_manifest(
    transfer_manifest: object,
    *,
    fresh_trial_id: UUID,
    fresh_run_id: UUID,
    babels_bytes: int,
    schedule_bytes: int,
) -> FrozenPopulationManifestV1:
    """Rebind only execution IDs around the already-verified population hashes."""

    return FrozenPopulationManifestV1(
        schemaVersion=1,
        experimentId=str(fresh_trial_id),
        sourcePopulationRunId=fresh_run_id,
        babelCount=10_000,
        scheduleCount=10_000,
        juneCount=transfer_manifest.periodCounts["2026-06"],
        julyCount=transfer_manifest.periodCounts["2026-07"],
        creatorCount=transfer_manifest.creatorCount,
        modelId=transfer_manifest.servingModelId,
        modelVersion=transfer_manifest.materializedModelVersion,
        modelManifestSha256=_MODEL_MANIFEST_SHA256,
        artifactManifestSha256=_ARTIFACT_MANIFEST_SHA256,
        artifactRepo=transfer_manifest.modelRepository,
        artifactRevision=transfer_manifest.modelRevision,
        artifactId=transfer_manifest.modelArtifactId,
        trainingDatasetRevision=_TRAINING_DATASET_REVISION,
        datasetRepo=transfer_manifest.datasetRepository,
        datasetConfig=transfer_manifest.datasetConfiguration,
        datasetRevision=transfer_manifest.datasetRevision,
        datasetManifestSha256=_SOURCE_DATASET_MANIFEST_SHA256,
        embeddingSpaceId=transfer_manifest.embeddingSpaceId,
        embeddingSpaceVersion=transfer_manifest.embeddingSpaceVersion,
        embeddingDimension=100,
        babelsSha256=transfer_manifest.contentSha256,
        vectorsSha256=transfer_manifest.orderedPopulationSha256,
        pgvectorSnapshotSha256=transfer_manifest.snapshotSha256,
        scheduleSha256=transfer_manifest.scheduleSha256,
        babelsBytes=babels_bytes,
        vectorBytes=4_000_000,
        scheduleBytes=schedule_bytes,
    )


def _canonical_line(document: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def materialize_rebound_frozen_population(
    verified: BundleFiles,
    rows: Sequence[PopulationTransferRow],
    output_root: str | Path,
    destination_experiment_id: UUID,
    destination_population_run_id: UUID,
) -> tuple[FrozenPopulationManifestV1, Path, str]:
    """Create serving files from verified Parquet bytes without any encoder call."""

    if len(rows) != 10_000:
        raise PopulationTransferIntegrityError(
            "rebound frozen population requires exactly 10,000 rows"
        )
    babels = bytearray()
    vectors = bytearray()
    schedule = bytearray()
    for ordinal, row in enumerate(rows):
        encoded = vector_f32le(row.vector)
        babels.extend(
            _canonical_line(
                {
                    "babelId": row.babel_id,
                    "catalogContentHash": row.catalog_content_hash,
                    "createdAtNs": row.created_at_ns,
                    "creatorId": row.creator_id,
                    "eventNumber": row.event_number,
                    "ordinal": ordinal,
                    "sourceArticleKey": row.source_article_key,
                    "text": row.article_text,
                    "title": row.title,
                }
            )
        )
        vectors.extend(encoded)
        schedule.extend(
            _canonical_line(
                {
                    "creatorEventNumber": row.creator_event_number,
                    "creatorId": row.creator_id,
                    "ordinal": ordinal,
                    "period": row.period,
                    "rootBabelId": row.root_babel_id,
                    "scheduleIndex": row.schedule_index,
                    "sourceArticleKey": row.source_article_key,
                    "traversalSessionId": row.traversal_session_id,
                    "workId": row.work_id,
                    "workloadSha256": row.workload_sha256,
                }
            )
        )
    transfer = verified.manifest_contract
    comparisons = (
        (hashlib.sha256(babels).hexdigest(), transfer.contentSha256, "content"),
        (
            hashlib.sha256(vectors).hexdigest(),
            transfer.orderedPopulationSha256,
            "ordered vector",
        ),
        (hashlib.sha256(schedule).hexdigest(), transfer.scheduleSha256, "schedule"),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            raise PopulationTransferIntegrityError(
                f"rebound frozen {label} hash differs from transfer"
            )
    manifest = _build_rebound_frozen_manifest(
        transfer,
        fresh_trial_id=destination_experiment_id,
        fresh_run_id=destination_population_run_id,
        babels_bytes=len(babels),
        schedule_bytes=len(schedule),
    )
    manifest_bytes = _canonical_line(manifest.model_dump(mode="json"))
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    root = Path(output_root)
    if root.is_symlink():
        raise PopulationTransferIntegrityError("frozen output root must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    trial_root = root / str(destination_experiment_id)
    trial_root.mkdir(mode=0o700, exist_ok=True)
    destination = trial_root / "population"
    if destination.exists():
        existing = load_frozen_population(destination)
        existing_bytes = (destination / "manifest.json").read_bytes()
        if existing != manifest or hashlib.sha256(existing_bytes).hexdigest() != manifest_sha:
            raise PopulationTransferIntegrityError(
                "existing rebound frozen population differs"
            )
        return existing, destination, manifest_sha
    temporary = Path(tempfile.mkdtemp(prefix=".population-import.", dir=trial_root))
    temporary.chmod(0o700)
    try:
        for name, payload in (
            ("babels.jsonl", bytes(babels)),
            ("vectors.f32le", bytes(vectors)),
            ("schedule.jsonl", bytes(schedule)),
            ("manifest.json", manifest_bytes),
        ):
            path = temporary / name
            path.write_bytes(payload)
            path.chmod(0o600)
        load_frozen_population(temporary)
        _rename_directory_noreplace(temporary, destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return manifest, destination, manifest_sha


def _validate_operator_receipt(
    path: Path, bundle_root: Path, trusted_digest: str
) -> Mapping[str, object]:
    document = _read_regular_json(path, "operator receipt")
    if (
        set(document) != {"schemaVersion", "bundleDigest", "objects"}
        or document.get("schemaVersion") != 1
        or document.get("bundleDigest") != trusted_digest
        or not isinstance(document.get("objects"), Mapping)
    ):
        raise PopulationTransferIntegrityError("operator receipt contract differs")
    objects = document["objects"]
    expected_names = {
        "SHA256SUMS",
        "babel_catalog.parquet",
        "babel_embeddings.parquet",
        "import_population.py",
        "manifest.json",
    }
    if set(objects) != expected_names:
        raise PopulationTransferIntegrityError("operator receipt object coverage differs")
    for name in sorted(expected_names):
        record = objects[name]
        if not isinstance(record, Mapping) or set(record) != {
            "generation",
            "gsUrl",
            "sha256",
            "size",
        }:
            raise PopulationTransferIntegrityError("operator receipt object contract differs")
        file_path = bundle_root / name
        try:
            payload = file_path.read_bytes()
        except OSError as error:
            raise PopulationTransferIntegrityError(
                "operator receipt object is unavailable"
            ) from error
        if (
            not isinstance(record["generation"], str)
            or not record["generation"].isdigit()
            or not str(record["gsUrl"]).startswith("gs://")
            or record["size"] != len(payload)
            or record["sha256"] != hashlib.sha256(payload).hexdigest()
        ):
            raise PopulationTransferIntegrityError("operator receipt object differs")
    return document


def _write_import_receipt(receipt: ImportReceiptV1, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(destination.parent.stat().st_mode) & 0o077:
        raise PermissionError("import receipt directory must be mode 0700 or stricter")
    payload = (
        json.dumps(
            receipt.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.close(temporary_fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _validate_quarantined_catalog_rows(
    expected_rows: Sequence[PopulationTransferRow | object],
    database_rows: Sequence[Sequence[object]],
) -> None:
    if len(expected_rows) != len(database_rows):
        raise PopulationTransferIntegrityError(
            "quarantined database catalog or schedule count differs"
        )
    for expected, actual in zip(expected_rows, database_rows, strict=True):
        expected_values = (
            str(expected.babel_id),
            str(expected.creator_id),
            expected.source_article_key,
            expected.title,
            expected.article_text,
            expected.catalog_content_hash,
            int(expected.event_number),
            int(expected.created_at_ns),
            int(expected.finalized_at_ns),
            int(expected.schedule_index),
            int(expected.creator_event_number),
            expected.period,
            str(expected.root_babel_id),
            str(expected.traversal_session_id),
            str(expected.work_id),
            expected.workload_sha256,
            int(expected.schedule_created_at_ns),
        )
        actual_values = (
            str(actual[0]),
            str(actual[1]),
            str(actual[2]),
            str(actual[3]),
            str(actual[4]),
            str(actual[5]),
            int(actual[6]),
            int(actual[7]),
            int(actual[8]),
            int(actual[9]),
            int(actual[10]),
            str(actual[11]),
            str(actual[12]),
            str(actual[13]),
            str(actual[14]),
            str(actual[15]),
            int(actual[16]),
        )
        if actual_values != expected_values:
            raise PopulationTransferIntegrityError(
                "quarantined database catalog or schedule identity differs"
            )


def _ready_database_state_matches(
    row: Sequence[object] | None,
    *,
    fresh_run_id: UUID,
    frozen_manifest_sha: str,
    frozen_directory: Path,
    ordered_vector_sha: str,
) -> bool:
    if row is None or len(row) != 14:
        return False
    expected = (
        "completed",
        "population_ready",
        True,
        fresh_run_id,
        frozen_manifest_sha,
        str(frozen_directory),
        10_000,
        ordered_vector_sha,
        MODEL_REPOSITORY,
        MODEL_REVISION,
        _MODEL_MANIFEST_SHA256,
        DATASET_REPOSITORY,
        DATASET_REVISION,
        _SOURCE_DATASET_MANIFEST_SHA256,
    )
    normalized = (
        str(row[0]),
        str(row[1]),
        bool(row[2]),
        UUID(str(row[3])),
        str(row[4]),
        str(row[5]),
        int(row[6]),
        str(row[7]),
        str(row[8]),
        str(row[9]),
        str(row[10]),
        str(row[11]),
        str(row[12]),
        str(row[13]),
    )
    return normalized == expected


def _import_verified_bundle(
    *,
    database_url: str,
    verified: BundleFiles,
    fresh_trial_id: UUID,
    fresh_run_id: UUID,
    model_artifact_manifest: Path,
    model_checkpoint_root: Path,
    frozen_output_root: Path,
    import_receipt_path: Path,
) -> ImportReceiptV1:
    """Insert a verified population under quarantine, then activate after all gates."""

    if model_artifact_manifest.is_symlink() or not model_artifact_manifest.is_file():
        raise PopulationTransferIntegrityError("model artifact manifest is unavailable")
    if (
        hashlib.sha256(model_artifact_manifest.read_bytes()).hexdigest()
        != _ARTIFACT_MANIFEST_SHA256
    ):
        raise PopulationTransferIntegrityError("model artifact manifest checksum differs")
    if model_checkpoint_root.is_symlink() or not model_checkpoint_root.is_dir():
        raise PopulationTransferIntegrityError("model checkpoint root is unavailable")

    rows = _load_verified_transfer_rows(verified)
    frozen_manifest, frozen_directory, frozen_manifest_sha = (
        materialize_rebound_frozen_population(
            verified,
            rows,
            frozen_output_root,
            fresh_trial_id,
            fresh_run_id,
        )
    )
    transfer = verified.manifest_contract

    def receipt(state: Literal["planned", "quarantined", "ready"]) -> ImportReceiptV1:
        return ImportReceiptV1(
            schemaVersion=1,
            state=state,
            bundleDigest=verified.digest,
            originTrialId=transfer.originTrialId,
            originRunId=transfer.originRunId,
            freshTrialId=fresh_trial_id,
            freshPopulationRunId=fresh_run_id,
            rowCount=10_000,
            orderedVectorSha256=transfer.orderedPopulationSha256,
            snapshotSha256=transfer.snapshotSha256,
            frozenManifestSha256=frozen_manifest_sha,
            sampleCount=100,
            hnswIndex="babel_embeddings_cosine_hnsw",
            modelArtifactManifestPath=str(model_artifact_manifest),
            modelCheckpointRoot=str(model_checkpoint_root),
        )

    existing_state: Literal["planned", "quarantined", "ready"] | None = None
    if import_receipt_path.exists() or import_receipt_path.is_symlink():
        try:
            existing = ImportReceiptV1.model_validate(
                _read_regular_json(import_receipt_path, "import receipt")
            )
        except Exception as error:
            raise PopulationTransferIntegrityError("import receipt is invalid") from error
        expected = receipt(existing.state)
        if existing != expected:
            raise PopulationTransferIntegrityError(
                "existing import receipt differs from requested import"
            )
        existing_state = existing.state
    else:
        _write_import_receipt(receipt("planned"), import_receipt_path)
        existing_state = "planned"

    run_seed = int.from_bytes(
        hashlib.sha256(f"{fresh_trial_id}:population".encode("utf-8")).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    config = RunConfigV2(
        schemaVersion=2,
        runId=fresh_run_id,
        datasetRepo=transfer.datasetRepository,
        datasetConfig=transfer.datasetConfiguration,
        datasetRevision=transfer.datasetRevision,
        startingModelId=transfer.servingModelId,
        retrievalBackend="pgvector",
        creatorCount=50,
        embeddingDimension=100,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
        runSeed=run_seed,
        recommendationK=10,
        topL=100,
        kafkaTopic="babel.feedback.v1",
        kafkaGroup=f"babel-performance-population-{fresh_trial_id}",
        checkpointEveryEvents=100,
        syncEverySteps=10,
        artifactRoot=str(frozen_output_root / str(fresh_trial_id) / "artifacts"),
        stateRoot=str(frozen_output_root / str(fresh_trial_id) / "state"),
        sourceArticlesPerMonth=5_000,
        targetCreatedBabels=10_000,
        concurrentUsers=50,
        recommendationStartProbability=0.4,
        continuationProbability=0.4,
        maximumTraversalDepth=2,
        maximumRequestsPerTraversal=10,
        interleaveCreationAndRecommendations=True,
        trainingMicroBatchSize=8,
    )
    launch_document = config.model_dump(mode="json")
    launch_json = json.dumps(
        launch_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    launch_sha = hashlib.sha256(launch_json.encode("utf-8")).hexdigest()
    embedding_space = {
        "schemaVersion": 1,
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "dimension": 100,
        "distance": "cosine",
        "distilledEncoderArtifact": (
            f"hf://{MODEL_REPOSITORY}@{MODEL_REVISION}/artifacts/{MODEL_ARTIFACT_ID}"
        ),
        "datasetRevision": _TRAINING_DATASET_REVISION,
        "compatibilityVersion": "babel-qwen-100d-v1",
    }

    def register_vector(connection: object) -> None:
        try:
            from pgvector.psycopg import register_vector as register
        except ImportError as error:  # pragma: no cover - deployment setup
            raise RuntimeError("population import requires babel-online[pgvector]") from error
        register(connection)

    if existing_state == "planned":
        with _connect_psycopg(database_url) as connection:
            register_vector(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM performance_experiments WHERE id=%s),"
                    "EXISTS(SELECT 1 FROM experiment_runs WHERE id=%s)",
                    (fresh_trial_id, fresh_run_id),
                )
                present = cursor.fetchone()
                if present not in {(False, False), (True, True)}:
                    raise PopulationTransferIntegrityError(
                        "partial quarantined import identity exists"
                    )
                if present == (False, False):
                    cursor.execute(
                        """
                        INSERT INTO recommender_models(
                          id,label,parent_model_id,producing_run_id,encoder_repo,
                          encoder_revision,dataset_repo,dataset_revision,
                          environment_sequence,training_examples,checkpoint_path,
                          checkpoint_sha256,embedding_space,immutable
                        ) VALUES (%s,%s,NULL,NULL,%s,%s,%s,%s,'[]'::jsonb,50000,%s,%s,%s::jsonb,true)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            SERVING_MODEL_ID,
                            "2016 interview Qwen original",
                            MODEL_REPOSITORY,
                            MODEL_REVISION,
                            _TRAINING_DATASET_REPOSITORY,
                            _TRAINING_DATASET_REVISION,
                            str(model_artifact_manifest),
                            _ARTIFACT_MANIFEST_SHA256,
                            json.dumps(embedding_space, sort_keys=True, separators=(",", ":")),
                        ),
                    )
                    cursor.execute(
                        """
                        SELECT encoder_repo,encoder_revision,dataset_repo,dataset_revision,
                               checkpoint_path,checkpoint_sha256,embedding_space
                        FROM recommender_models WHERE id=%s
                        """,
                        (SERVING_MODEL_ID,),
                    )
                    model_row = cursor.fetchone()
                    if model_row is None or (
                        str(model_row[0]) != MODEL_REPOSITORY
                        or str(model_row[1]) != MODEL_REVISION
                        or str(model_row[2]) != _TRAINING_DATASET_REPOSITORY
                        or str(model_row[3]) != _TRAINING_DATASET_REVISION
                        or str(model_row[4]) != str(model_artifact_manifest)
                        or str(model_row[5]) != _ARTIFACT_MANIFEST_SHA256
                        or dict(model_row[6]) != embedding_space
                    ):
                        raise PopulationTransferIntegrityError(
                            "registered GCP model identity differs"
                        )
                    cursor.execute(
                        """
                        INSERT INTO experiment_runs(
                          id,status,retrieval_backend,creator_count,scenario,
                          environment_sequence,event_budget_per_month,run_seed,
                          dataset_repository,dataset_config,dataset_revision,
                          recommendation_k,top_l,kafka_topic,kafka_group,
                          checkpoint_every_events,sync_every_steps,artifact_root,state_root,
                          starting_model_id,active_model_id,contract_version,
                          source_articles_per_month,target_created_babels,concurrent_users,
                          recommendation_start_probability,continuation_probability,
                          maximum_traversal_depth,maximum_requests_per_traversal,
                          interleave_creation_and_recommendations,
                          source_vector_qwen_encode_count,source_vector_cache_hit_count,
                          source_vector_pgvector_load_count,source_vector_eviction_count,
                          launch_config,launch_sha256,created_babel_count
                        ) VALUES (
                          %s,'starting','pgvector',50,'june_to_july',%s::jsonb,5000,%s,
                          %s,%s,%s,10,100,%s,%s,100,10,%s,%s,%s,%s,2,
                          5000,10000,50,0.4,0.4,2,10,true,0,0,10000,0,%s::jsonb,%s,10000
                        )
                        """,
                        (
                            fresh_run_id,
                            json.dumps(["2026-06", "2026-07"]),
                            run_seed,
                            transfer.datasetRepository,
                            transfer.datasetConfiguration,
                            transfer.datasetRevision,
                            config.kafkaTopic,
                            config.kafkaGroup,
                            config.artifactRoot,
                            config.stateRoot,
                            SERVING_MODEL_ID,
                            SERVING_MODEL_ID,
                            launch_json,
                            launch_sha,
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO performance_experiments(
                          id,status,topology,starting_model_id,model_repository,
                          model_revision,dataset_repository,dataset_revision,
                          retrieval_backend,creator_count,seeded_articles,
                          target_created_babels,concurrent_users,
                          recommendation_start_probability,continuation_probability,
                          maximum_traversal_depth,maximum_requests_per_traversal,
                          training_micro_batch_size,sync_every_steps,
                          interleave_creation_and_recommendations,auto_advance,
                          warmup_seconds,duration_seconds,target_rps,
                          latency_safety_threshold_ms,population_ready,operator_approved,
                          run_id,population_manifest_sha256,population_bundle_path
                        ) VALUES (
                          %s,'population_pending','same_host_split',%s,%s,%s,%s,%s,
                          'pgvector',50,10000,10000,50,0.4,0.4,2,10,8,10,true,false,
                          30,120,5,5000,false,false,%s,%s,%s
                        )
                        """,
                        (
                            fresh_trial_id,
                            SERVING_MODEL_ID,
                            MODEL_REPOSITORY,
                            MODEL_REVISION,
                            transfer.datasetRepository,
                            transfer.datasetRevision,
                            fresh_run_id,
                            frozen_manifest_sha,
                            str(frozen_directory),
                        ),
                    )
                    condition_rows = []
                    index = 0
                    for topology in (
                        "same_process",
                        "same_host_split",
                        "same_host_isolated",
                    ):
                        for training, synchronization in (
                            (False, False),
                            (True, False),
                            (True, True),
                        ):
                            index += 1
                            condition_id = uuid5(fresh_trial_id, f"condition:{index}")
                            condition_document = {
                                "schemaVersion": 1,
                                "experimentId": str(fresh_trial_id),
                                "conditionIndex": index,
                                "topology": topology,
                                "trainingEnabled": training,
                                "synchronizationEnabled": synchronization,
                                "trainingMicroBatchSize": 8,
                                "syncEverySteps": 10,
                            }
                            condition_json = json.dumps(
                                condition_document,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            condition_rows.append(
                                (
                                    condition_id,
                                    fresh_trial_id,
                                    index,
                                    topology,
                                    training,
                                    synchronization,
                                    condition_json,
                                    hashlib.sha256(condition_json.encode()).hexdigest(),
                                )
                            )
                    cursor.executemany(
                        """
                        INSERT INTO performance_conditions(
                          id,experiment_id,condition_index,topology,training_enabled,
                          synchronization_enabled,launch_config,launch_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        """,
                        condition_rows,
                    )
                    cursor.execute(
                        """
                        INSERT INTO performance_progress_snapshots(
                          experiment_id,sequence,phase,condition_count,
                          seeded_articles,created_babels,indexed_babels,telemetry
                        ) VALUES (%s,0,'population',9,10000,10000,0,%s::jsonb)
                        """,
                        (
                            fresh_trial_id,
                            json.dumps(
                                {
                                    "bundleDigest": verified.digest,
                                    "state": "quarantined",
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    cursor.execute(
                        """
                        CREATE TEMP TABLE import_catalog(
                          babel_id uuid,creator_id uuid,source_article_key text,title text,
                          article_text text,catalog_content_hash text,event_number bigint,
                          created_at_ns bigint,finalized_at_ns bigint,schedule_index bigint,
                          creator_event_number bigint,period text,root_babel_id uuid,
                          traversal_session_id uuid,work_id uuid,workload_sha256 text,
                          schedule_created_at_ns bigint
                        ) ON COMMIT DROP
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TEMP TABLE import_embeddings(
                          babel_id uuid,creator_id uuid,embedding_space_id uuid,
                          serving_model_id uuid,materialized_model_version bigint,
                          catalog_content_hash text,embedding public.vector(100)
                        ) ON COMMIT DROP
                        """
                    )
                    cursor.executemany(
                        """
                        INSERT INTO import_catalog VALUES (
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        [
                            (
                                UUID(row.babel_id),
                                UUID(row.creator_id),
                                row.source_article_key,
                                row.title,
                                row.article_text,
                                row.catalog_content_hash,
                                row.event_number,
                                row.created_at_ns,
                                row.finalized_at_ns,
                                row.schedule_index,
                                row.creator_event_number,
                                row.period,
                                UUID(row.root_babel_id),
                                UUID(row.traversal_session_id),
                                UUID(row.work_id),
                                row.workload_sha256,
                                row.schedule_created_at_ns,
                            )
                            for row in rows
                        ],
                    )
                    cursor.executemany(
                        """
                        INSERT INTO import_embeddings VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                UUID(row.babel_id),
                                UUID(row.creator_id),
                                UUID(row.embedding_space_id),
                                UUID(row.serving_model_id),
                                row.materialized_model_version,
                                row.catalog_content_hash,
                                np.asarray(row.vector, dtype="<f4"),
                            )
                            for row in rows
                        ],
                    )
                    cursor.execute(
                        """
                        INSERT INTO experiment_babels(
                          run_id,babel_id,creator_id,source_article_key,title,article_text,
                          catalog_content_hash,event_number,created_at,finalized_at
                        ) SELECT %s,babel_id,creator_id,source_article_key,title,article_text,
                          catalog_content_hash,event_number,
                          to_timestamp(created_at_ns / 1000000000.0),
                          to_timestamp(finalized_at_ns / 1000000000.0)
                        FROM import_catalog ORDER BY babel_id
                        """,
                        (fresh_run_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO experiment_work_schedule(
                          run_id,schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,created_at
                        ) SELECT %s,schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,to_timestamp(schedule_created_at_ns / 1000000000.0)
                        FROM import_catalog ORDER BY schedule_index
                        """,
                        (fresh_run_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO babel_embeddings(
                          run_id,babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,embedding
                        ) SELECT %s,babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,embedding
                        FROM import_embeddings ORDER BY babel_id
                        """,
                        (fresh_run_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO run_embedding_states(
                          run_id,active_model_id,active_model_version,embedding_space_id,
                          pgvector_snapshot_sha256,backend_snapshot_sha256
                        ) VALUES (%s,%s,0,%s,%s,%s)
                        """,
                        (
                            fresh_run_id,
                            SERVING_MODEL_ID,
                            EMBEDDING_SPACE_ID,
                            transfer.snapshotSha256,
                            transfer.snapshotSha256,
                        ),
                    )
        _write_import_receipt(receipt("quarantined"), import_receipt_path)

    expected_vectors = [vector_f32le(row.vector) for row in rows]
    samples = _deterministic_sample_ordinals(
        verified.digest, row_count=10_000, count=100
    )
    with _connect_psycopg(database_url) as connection:
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT babel_id,public.vector_send(embedding)
                FROM babel_embeddings
                WHERE run_id=%s AND serving_model_id=%s
                  AND materialized_model_version=0 AND embedding_space_id=%s
                ORDER BY babel_id
                """,
                (fresh_run_id, SERVING_MODEL_ID, EMBEDDING_SPACE_ID),
            )
            database_rows = cursor.fetchall()
            if len(database_rows) != 10_000:
                raise PopulationTransferIntegrityError(
                    "quarantined database population row count differs"
                )
            database_vectors: list[bytes] = []
            for ordinal, (babel_id, wire) in enumerate(database_rows):
                if str(babel_id) != rows[ordinal].babel_id:
                    raise PopulationTransferIntegrityError(
                        "quarantined database Babel ordering differs"
                    )
                try:
                    _wire, encoded = RuntimeDatabase._decode_vector_send(wire)
                except Exception as error:
                    raise PopulationTransferIntegrityError(
                        "quarantined database vector binary is invalid"
                    ) from error
                database_vectors.append(encoded)
            if (
                hashlib.sha256(b"".join(database_vectors)).hexdigest()
                != transfer.orderedPopulationSha256
            ):
                raise PopulationTransferIntegrityError(
                    "quarantined database ordered vector hash differs"
                )
            for ordinal in samples:
                if database_vectors[ordinal] != expected_vectors[ordinal]:
                    raise PopulationTransferIntegrityError(
                        "deterministic database vector sample differs"
                    )
            cursor.execute(
                """
                SELECT xb.babel_id,xb.creator_id,xb.source_article_key,xb.title,
                       xb.article_text,xb.catalog_content_hash,xb.event_number,
                       (extract(epoch from xb.created_at) * 1000000000)::bigint,
                       (extract(epoch from xb.finalized_at) * 1000000000)::bigint,
                       ws.schedule_index,ws.creator_event_number,ws.period,
                       ws.root_babel_id,ws.traversal_session_id,ws.work_id,
                       ws.workload_sha256,
                       (extract(epoch from ws.created_at) * 1000000000)::bigint
                FROM experiment_babels xb
                JOIN experiment_work_schedule ws
                  ON ws.run_id=xb.run_id AND ws.root_babel_id=xb.babel_id
                WHERE xb.run_id=%s ORDER BY xb.babel_id
                """,
                (fresh_run_id,),
            )
            _validate_quarantined_catalog_rows(rows, cursor.fetchall())
            cursor.execute("REINDEX INDEX babel_embeddings_cosine_hnsw")
            cursor.execute("ANALYZE babel_embeddings")
            cursor.execute("SET LOCAL enable_seqscan=off")
            cursor.execute(
                """
                EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON)
                SELECT babel_id FROM babel_embeddings
                WHERE run_id=%s AND serving_model_id=%s
                  AND materialized_model_version=0 AND embedding_space_id=%s
                ORDER BY embedding <=> %s LIMIT 10
                """,
                (
                    fresh_run_id,
                    SERVING_MODEL_ID,
                    EMBEDDING_SPACE_ID,
                    np.asarray(rows[samples[0]].vector, dtype="<f4"),
                ),
            )
            plan = cursor.fetchone()
            if plan is None or "babel_embeddings_cosine_hnsw" not in json.dumps(plan):
                raise PopulationTransferIntegrityError(
                    "HNSW EXPLAIN did not use babel_embeddings_cosine_hnsw"
                )

    with _connect_psycopg(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE experiment_runs SET status='completed',completed_at=now()
            WHERE id=%s AND status='starting' RETURNING id
            """,
            (fresh_run_id,),
        )
        run_activated = cursor.fetchone()
        cursor.execute(
            """
            UPDATE performance_experiments SET
              status='population_ready',population_ready=true,
              population_vector_count=10000,population_vector_sha256=%s,
              population_model_repository=%s,population_model_revision=%s,
              population_model_sha256=%s,population_dataset_repository=%s,
              population_dataset_revision=%s,population_dataset_sha256=%s
            WHERE id=%s AND population_ready=false AND run_id=%s
              AND population_manifest_sha256=%s AND population_bundle_path=%s
            RETURNING id
            """,
            (
                transfer.orderedPopulationSha256,
                MODEL_REPOSITORY,
                MODEL_REVISION,
                _MODEL_MANIFEST_SHA256,
                transfer.datasetRepository,
                transfer.datasetRevision,
                _SOURCE_DATASET_MANIFEST_SHA256,
                fresh_trial_id,
                fresh_run_id,
                frozen_manifest_sha,
                str(frozen_directory),
            ),
        )
        trial_activated = cursor.fetchone()
        if run_activated is None or trial_activated is None:
            cursor.execute(
                """
                SELECT er.status,pe.status,pe.population_ready,pe.run_id,
                       pe.population_manifest_sha256,pe.population_bundle_path,
                       pe.population_vector_count,pe.population_vector_sha256,
                       pe.population_model_repository,pe.population_model_revision,
                       pe.population_model_sha256,pe.population_dataset_repository,
                       pe.population_dataset_revision,pe.population_dataset_sha256
                FROM performance_experiments pe
                JOIN experiment_runs er ON er.id=pe.run_id
                WHERE pe.id=%s AND er.id=%s
                """,
                (fresh_trial_id, fresh_run_id),
            )
            if not _ready_database_state_matches(
                cursor.fetchone(),
                fresh_run_id=fresh_run_id,
                frozen_manifest_sha=frozen_manifest_sha,
                frozen_directory=frozen_directory,
                ordered_vector_sha=transfer.orderedPopulationSha256,
            ):
                raise PopulationTransferIntegrityError(
                    "final imported population readiness transition was rejected"
                )
        cursor.execute(
            """
            INSERT INTO performance_progress_snapshots(
              experiment_id,sequence,phase,condition_count,seeded_articles,
              created_babels,indexed_babels,telemetry
            ) SELECT %s,COALESCE(MAX(sequence),-1)+1,'population_ready',9,
                     10000,10000,10000,%s::jsonb
              FROM performance_progress_snapshots WHERE experiment_id=%s
              HAVING count(*) FILTER (WHERE phase='population_ready')=0
            """,
            (
                fresh_trial_id,
                json.dumps(
                    {
                        "bundleDigest": verified.digest,
                        "frozenManifestSha256": frozen_manifest_sha,
                        "hnswIndex": "babel_embeddings_cosine_hnsw",
                        "sampleCount": 100,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                fresh_trial_id,
            ),
        )
    ready = receipt("ready")
    _write_import_receipt(ready, import_receipt_path)
    return ready


def import_population(
    database_url: str,
    bundle_root: str | Path,
    trusted_digest: str,
    operator_receipt: str | Path,
    fresh_trial_id: UUID,
    fresh_run_id: UUID,
    model_artifact_manifest: str | Path,
    model_checkpoint_root: str | Path,
    frozen_output_root: str | Path,
    import_receipt: str | Path,
) -> ImportReceiptV1:
    """Verify independent trust inputs before entering the database adapter."""

    if fresh_trial_id.version != 4:
        raise PopulationTransferIntegrityError("fresh trial ID must be UUIDv4")
    if fresh_run_id != uuid5(fresh_trial_id, "population"):
        raise PopulationTransferIntegrityError(
            "fresh run ID must equal uuid5(fresh trial ID,'population')"
        )
    root = Path(bundle_root).resolve()
    _validate_operator_receipt(Path(operator_receipt), root, trusted_digest)
    verified = verify_bundle(root, trusted_digest)
    receipt = _import_verified_bundle(
        database_url=database_url,
        verified=verified,
        fresh_trial_id=fresh_trial_id,
        fresh_run_id=fresh_run_id,
        model_artifact_manifest=Path(model_artifact_manifest).resolve(),
        model_checkpoint_root=Path(model_checkpoint_root).resolve(),
        frozen_output_root=Path(frozen_output_root).resolve(),
        import_receipt_path=Path(import_receipt).resolve(),
    )
    _write_import_receipt(receipt, Path(import_receipt))
    return receipt


__all__ = ["ExportReceiptV1", "export_population"]
