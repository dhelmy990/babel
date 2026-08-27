"""Read-only export of the single accepted local performance population."""

from __future__ import annotations

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
from uuid import UUID

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..model.frozen_population import (
    FrozenPopulationManifestV1,
    load_frozen_population,
)
from ..model.population import PopulationIntegrityError
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
    verify_bundle,
    write_bundle_payloads,
)


_TRAINING_DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
_TRAINING_DATASET_REVISION = "b440e98b04ab77afed7caf0455eca3189235fc3b"
_ARTIFACT_MANIFEST_SHA256 = (
    "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
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
  ON eb.run_id=xb.run_id AND eb.babel_id=xb.babel_id
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
        if len(row) != 24:
            raise PopulationTransferIntegrityError(
                "authoritative population row has an unexpected shape"
            )
        try:
            _wire, f32le = RuntimeDatabase._decode_vector_send(row[20])
        except (PopulationIntegrityError, TypeError, ValueError) as error:
            raise PopulationTransferIntegrityError(
                "authoritative pgvector binary row is invalid"
            ) from error
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
                dataset_repository=str(row[21]),
                dataset_configuration=str(row[22]),
                dataset_revision=str(row[23]),
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


def _write_authoritative_bundle(
    source: PopulationTransferBundleInput,
    output_root: str | Path,
    frozen_manifest: FrozenPopulationManifestV1,
) -> BundleFiles:
    """Install only after bundle and source-frozen hashes agree."""

    destination = Path(output_root)
    if destination.exists() or destination.is_symlink():
        raise PopulationTransferIntegrityError("bundle destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.export.", dir=destination.parent)
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
        os.replace(staged, destination)
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


__all__ = ["ExportReceiptV1", "export_population"]
