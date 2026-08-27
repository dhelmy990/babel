"""PostgreSQL ownership for immutable launches and observable online state."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ..contracts import ActivityLogV1, ModelManifestV1, ModelManifestV2, RunConfigV1
from ..model.artifact import LoadedArtifact, load_artifact
from ..model.population import (
    PopulationActivationEvidence,
    PopulationIdentity,
    PopulationIntegrityError,
    PopulationSource,
)
from ..model.source_vector_cache import VectorCacheKey
from ..observable import CreatedBabel, VectorRecord, reject_hidden_fields


class ArtifactConfigurationError(ValueError):
    pass


class LaunchIntegrityError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def load_configured_model_artifact(path: str | Path) -> LoadedArtifact:
    try:
        loaded = load_artifact(Path(path))
    except Exception as error:
        raise ArtifactConfigurationError(
            "configured online model artifact failed checksum validation"
        ) from error
    if "demo" not in loaded.manifest.label.casefold() and loaded.manifest.trainingExamples == 0:
        raise ArtifactConfigurationError(
            "zero-example model artifacts must be explicitly labeled as demo fixtures"
        )
    return loaded


def _connect_psycopg(dsn: str):
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - deployment setup
        raise RuntimeError("PostgreSQL runtime requires babel-online[pgvector]") from error
    return psycopg.connect(dsn)


@dataclass(frozen=True, slots=True)
class PersistedRun:
    config: RunConfigV1
    status: str
    launch_sha256: str


class RuntimeDatabase:
    """Small transaction boundary used by the loopback worker."""

    def __init__(
        self, dsn: str, *, connect: Callable[[], Any] | None = None
    ) -> None:
        self.dsn = dsn
        self._connect = connect or (lambda: _connect_psycopg(dsn))

    def load_run(self, run_id: UUID) -> PersistedRun:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT launch_config, launch_sha256, status "
                "FROM experiment_runs WHERE id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"experiment run does not exist: {run_id}")
        document = row[0] if isinstance(row[0], Mapping) else json.loads(row[0])
        reject_hidden_fields(document)
        digest = canonical_json_sha256(document)
        if digest != row[1]:
            raise LaunchIntegrityError("persisted launch checksum does not match")
        return PersistedRun(RunConfigV1.model_validate(document), str(row[2]), digest)

    def bootstrap_model(self, artifact: LoadedArtifact) -> None:
        model = artifact.manifest
        embedding = model.embeddingSpace.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO recommender_models(
                  id, label, parent_model_id, producing_run_id, encoder_repo,
                  encoder_revision, dataset_repo, dataset_revision,
                  environment_sequence, training_examples, checkpoint_path,
                  checkpoint_sha256, embedding_space, immutable
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,true)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    model.modelId,
                    model.label,
                    model.parentModelId,
                    model.producingRunId,
                    model.encoderRepo,
                    model.encoderRevision,
                    model.datasetRepo,
                    model.datasetRevision,
                    json.dumps(model.environmentSequence),
                    model.trainingExamples,
                    str(artifact.checkpoint_path),
                    model.checkpointSha256,
                    json.dumps(embedding, sort_keys=True, separators=(",", ":")),
                ),
            )
            cursor.execute(
                "SELECT checkpoint_sha256, dataset_revision, embedding_space "
                "FROM recommender_models WHERE id = %s",
                (model.modelId,),
            )
            row = cursor.fetchone()
            if row is None or row[0] != model.checkpointSha256 or row[1] != model.datasetRevision:
                raise ArtifactConfigurationError(
                    "registered model identity differs from configured artifact"
                )

    def bootstrap_real_model(
        self, model: ModelManifestV2, *, artifact_manifest_path: Path
    ) -> None:
        """Register the accepted V2 original in the existing generic model table."""
        if not isinstance(model, ModelManifestV2):
            raise TypeError("real model bootstrap requires ModelManifestV2")
        resolved = artifact_manifest_path.resolve()
        if not resolved.is_file() or hashlib.sha256(resolved.read_bytes()).hexdigest() != (
            model.artifactManifestSha256
        ):
            raise ArtifactConfigurationError(
                "real model artifact manifest path or checksum differs"
            )
        embedding = model.embeddingSpace.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO recommender_models(
                  id, label, parent_model_id, producing_run_id, encoder_repo,
                  encoder_revision, dataset_repo, dataset_revision,
                  environment_sequence, training_examples, checkpoint_path,
                  checkpoint_sha256, embedding_space, immutable
                ) VALUES (%s,%s,NULL,NULL,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s::jsonb,true)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    model.modelId,
                    model.label,
                    model.encoderRepo,
                    model.encoderRevision,
                    model.datasetRepo,
                    model.datasetRevision,
                    model.trainingExamples,
                    str(resolved),
                    model.artifactManifestSha256,
                    json.dumps(embedding, sort_keys=True, separators=(",", ":")),
                ),
            )
            cursor.execute(
                """
                SELECT encoder_repo, encoder_revision, dataset_revision,
                       checkpoint_sha256, embedding_space
                FROM recommender_models WHERE id=%s
                """,
                (model.modelId,),
            )
            row = cursor.fetchone()
            registered_embedding = (
                row[4] if row is not None and isinstance(row[4], Mapping) else None
            )
            if row is None or (
                str(row[0]) != model.encoderRepo
                or str(row[1]) != model.encoderRevision
                or str(row[2]) != model.datasetRevision
                or str(row[3]) != model.artifactManifestSha256
                or registered_embedding != embedding
            ):
                raise ArtifactConfigurationError(
                    "registered real model identity differs from accepted artifact"
                )

    def register_child(self, model: ModelManifestV1, checkpoint_path: Path) -> None:
        artifact = LoadedArtifact(manifest=model, checkpoint_path=checkpoint_path)
        self.bootstrap_model(artifact)

    def load_model_artifact(self, model_id: UUID) -> LoadedArtifact:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT checkpoint_path FROM recommender_models WHERE id=%s", (model_id,)
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"recommender model does not exist: {model_id}")
        checkpoint = Path(row[0]).resolve()
        loaded = load_configured_model_artifact(checkpoint.parent)
        if loaded.manifest.modelId != model_id:
            raise ArtifactConfigurationError("model registry path resolves to another model")
        return loaded

    def load_model_lineage(self, model_id: UUID) -> list[LoadedArtifact]:
        lineage: list[LoadedArtifact] = []
        current = model_id
        while True:
            loaded = self.load_model_artifact(current)
            lineage.append(loaded)
            if loaded.manifest.parentModelId is None:
                break
            current = loaded.manifest.parentModelId
        lineage.reverse()
        return lineage

    def claim_run(self, run_id: UUID) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE experiment_runs SET status='running', started_at=now() "
                "WHERE id=%s AND status='starting'",
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("experiment run is not startable")

    def stop_requested(self, run_id: UUID) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status FROM experiment_runs WHERE id=%s", (run_id,))
            row = cursor.fetchone()
        return row is not None and row[0] == "stop_requested"

    def transition(self, run_id: UUID, status: str, *, failure: str | None = None) -> None:
        completed = status in {"completed", "failed", "interrupted"}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE experiment_runs SET status=%s, failure=%s, "
                "completed_at=CASE WHEN %s THEN now() ELSE completed_at END WHERE id=%s",
                (status, failure, completed, run_id),
            )

    def update_metrics(self, run_id: UUID, **values: Any) -> None:
        allowed = {
            "created_babel_count",
            "feedback_count",
            "event_rate",
            "kafka_offset",
            "kafka_lag",
            "trainer_steps",
            "rolling_rank_loss",
            "checkpoint_path",
            "checkpoint_sha256",
            "serving_synced",
            "active_model_id",
            "active_model_version",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("unsupported experiment metric")
        assignments = ", ".join(f"{name}=%s" for name in values)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE experiment_runs SET {assignments} WHERE id=%s",  # noqa: S608
                (*values.values(), run_id),
            )

    def append_activity(self, activity: ActivityLogV1) -> int:
        reject_hidden_fields(activity.model_dump(mode="json"))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (str(activity.runId),)
            )
            cursor.execute(
                "SELECT COALESCE(max(sequence),0)+1 FROM experiment_activity_logs "
                "WHERE run_id=%s",
                (activity.runId,),
            )
            sequence = int(cursor.fetchone()[0])
            document = activity.model_copy(update={"sequence": sequence})
            cursor.execute(
                """
                INSERT INTO experiment_activity_logs(
                  run_id, sequence, occurred_at_ns, level, component, event,
                  message, metrics, details
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                """,
                (
                    document.runId,
                    sequence,
                    document.occurredAtNs,
                    document.level,
                    document.component,
                    document.event,
                    document.message,
                    json.dumps(document.metrics),
                    json.dumps(document.details.model_dump(mode="json")),
                ),
            )
        return sequence

    def stage_babel(
        self,
        *,
        babel: CreatedBabel,
        content_hash: str,
        event_number: int,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiment_babels(
                  run_id,babel_id,creator_id,source_article_key,title,article_text,
                  catalog_content_hash,event_number
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    babel.runId,
                    babel.babelId,
                    babel.creatorId,
                    babel.sourceArticleKey,
                    babel.title,
                    babel.text,
                    content_hash,
                    event_number,
                ),
            )

    def finalize_babel(self, run_id: UUID, babel_id: UUID, request_id: UUID) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE experiment_babels SET request_id=%s, finalized_at=now() "
                "WHERE run_id=%s AND babel_id=%s AND finalized_at IS NULL",
                (request_id, run_id, babel_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("staged Babel could not be finalized")

    def insert_vectors(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            for record in records:
                cursor.execute(
                    """
                    INSERT INTO babel_embeddings(
                      run_id,babel_id,creator_id,embedding_space_id,serving_model_id,
                      materialized_model_version,catalog_content_hash,embedding
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::public.vector)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        record.babel.runId,
                        record.babel.babelId,
                        record.babel.creatorId,
                        record.embeddingSpaceId,
                        record.servingModelId,
                        record.materializedModelVersion,
                        record.catalogContentHash,
                        "[" + ",".join(format(value, ".9g") for value in record.vector) + "]",
                    ),
                )

    def activate_embedding_state(
        self,
        *,
        run_id: UUID,
        model_id: UUID,
        model_version: int,
        embedding_space_id: UUID,
        pgvector_sha256: str,
        backend_sha256: str,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO run_embedding_states(
                  run_id,active_model_id,active_model_version,embedding_space_id,
                  pgvector_snapshot_sha256,backend_snapshot_sha256
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                  active_model_id=EXCLUDED.active_model_id,
                  active_model_version=EXCLUDED.active_model_version,
                  embedding_space_id=EXCLUDED.embedding_space_id,
                  pgvector_snapshot_sha256=EXCLUDED.pgvector_snapshot_sha256,
                  backend_snapshot_sha256=EXCLUDED.backend_snapshot_sha256,
                  synchronized_at=now()
                """,
                (
                    run_id,
                    model_id,
                    model_version,
                    embedding_space_id,
                    pgvector_sha256,
                    backend_sha256,
                ),
            )

    def created_babels(self, run_id: UUID) -> list[CreatedBabel]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT babel_id, creator_id, source_article_key, title, article_text,
                       (extract(epoch from created_at) * 1000000000)::bigint
                FROM experiment_babels
                WHERE run_id=%s AND finalized_at IS NOT NULL
                ORDER BY event_number, babel_id
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return [
            CreatedBabel(
                babelId=row[0],
                runId=run_id,
                creatorId=row[1],
                sourceArticleKey=row[2],
                title=row[3],
                text=row[4],
                createdAtNs=max(0, int(row[5])),
            )
            for row in rows
        ]

    def population_sources(
        self,
        run_id: UUID,
        *,
        after_babel_id: UUID | None,
        limit: int,
    ) -> list[PopulationSource]:
        """Read only finalized synthetic-created Babels in bounded ID order."""
        if limit <= 0:
            raise ValueError("population source limit must be positive")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT babel_id, creator_id, source_article_key, title, article_text,
                       catalog_content_hash,
                       (extract(epoch from created_at) * 1000000000)::bigint
                FROM experiment_babels
                WHERE run_id=%s
                  AND finalized_at IS NOT NULL
                  AND article_text IS NOT NULL
                  AND catalog_content_hash IS NOT NULL
                  AND (%s::uuid IS NULL OR babel_id > %s::uuid)
                ORDER BY babel_id
                LIMIT %s
                """,
                (run_id, after_babel_id, after_babel_id, limit),
            )
            rows = cursor.fetchall()
        return [
            PopulationSource(
                babel=CreatedBabel(
                    babelId=row[0],
                    runId=run_id,
                    creatorId=row[1],
                    sourceArticleKey=row[2],
                    title=row[3],
                    text=row[4],
                    createdAtNs=max(0, int(row[6])),
                ),
                catalog_content_hash=str(row[5]),
            )
            for row in rows
        ]

    @staticmethod
    def _vector_literal(values: Sequence[float]) -> str:
        if len(values) != 100:
            raise PopulationIntegrityError("population vector dimension is not 100")
        return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"

    @staticmethod
    def _vector_text_bytes(value: object) -> bytes:
        import numpy as np

        text = str(value).strip()
        if not text.startswith("[") or not text.endswith("]"):
            raise PopulationIntegrityError("pgvector returned malformed vector text")
        try:
            vector = np.asarray(
                [float(item) for item in text[1:-1].split(",")], dtype="<f4"
            )
        except ValueError as error:
            raise PopulationIntegrityError("pgvector returned malformed vector values") from error
        if vector.shape != (100,) or not np.isfinite(vector).all():
            raise PopulationIntegrityError("pgvector row is not finite 100d float32")
        return vector.tobytes()

    def write_population_batch(
        self,
        records: Sequence[VectorRecord],
        expected: PopulationIdentity,
    ) -> None:
        """Insert one batch or prove every pre-existing row is byte-identical."""
        if not records:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            for record in records:
                babel = record.babel
                if (
                    babel.runId != expected.run_id
                    or record.servingModelId != expected.model_id
                    or record.materializedModelVersion != expected.model_version
                    or record.embeddingSpaceId != expected.embedding_space_id
                ):
                    raise PopulationIntegrityError("population record identity differs")
                cursor.execute(
                    """
                    SELECT creator_id, source_article_key, title, article_text,
                           catalog_content_hash
                    FROM experiment_babels
                    WHERE run_id=%s AND babel_id=%s AND finalized_at IS NOT NULL
                    FOR SHARE
                    """,
                    (babel.runId, babel.babelId),
                )
                source = cursor.fetchone()
                if source is None or (
                    source[0] != babel.creatorId
                    or str(source[1]) != babel.sourceArticleKey
                    or str(source[2]) != babel.title
                    or str(source[3]) != babel.text
                    or str(source[4]) != record.catalogContentHash
                ):
                    raise PopulationIntegrityError(
                        "population record no longer matches finalized created content"
                    )
                cursor.execute(
                    """
                    SELECT creator_id, embedding_space_id, serving_model_id,
                           catalog_content_hash, embedding::text
                    FROM babel_embeddings
                    WHERE run_id=%s AND babel_id=%s
                      AND materialized_model_version=%s
                    FOR SHARE
                    """,
                    (babel.runId, babel.babelId, record.materializedModelVersion),
                )
                existing = cursor.fetchone()
                expected_bytes = self._vector_text_bytes(
                    self._vector_literal(record.vector)
                )
                if existing is not None:
                    if (
                        existing[0] != babel.creatorId
                        or existing[1] != record.embeddingSpaceId
                        or existing[2] != record.servingModelId
                        or str(existing[3]) != record.catalogContentHash
                        or self._vector_text_bytes(existing[4]) != expected_bytes
                    ):
                        raise PopulationIntegrityError(
                            "existing vector bytes or identity differ"
                        )
                    continue
                cursor.execute(
                    """
                    INSERT INTO babel_embeddings(
                      run_id,babel_id,creator_id,embedding_space_id,serving_model_id,
                      materialized_model_version,catalog_content_hash,embedding
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::public.vector)
                    """,
                    (
                        babel.runId,
                        babel.babelId,
                        babel.creatorId,
                        record.embeddingSpaceId,
                        record.servingModelId,
                        record.materializedModelVersion,
                        record.catalogContentHash,
                        self._vector_literal(record.vector),
                    ),
                )

    def population_vectors(
        self,
        expected: PopulationIdentity,
        *,
        after_babel_id: UUID | None,
        limit: int,
    ) -> list[VectorRecord]:
        if limit <= 0:
            raise ValueError("population vector limit must be positive")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT xb.babel_id, xb.creator_id, xb.source_article_key, xb.title,
                       xb.article_text, xb.catalog_content_hash,
                       (extract(epoch from xb.created_at) * 1000000000)::bigint,
                       eb.embedding::text
                FROM babel_embeddings AS eb
                JOIN experiment_babels AS xb
                  ON xb.run_id=eb.run_id AND xb.babel_id=eb.babel_id
                WHERE eb.run_id=%s
                  AND eb.serving_model_id=%s
                  AND eb.materialized_model_version=%s
                  AND eb.embedding_space_id=%s
                  AND xb.finalized_at IS NOT NULL
                  AND (%s::uuid IS NULL OR eb.babel_id > %s::uuid)
                ORDER BY eb.babel_id
                LIMIT %s
                """,
                (
                    expected.run_id,
                    expected.model_id,
                    expected.model_version,
                    expected.embedding_space_id,
                    after_babel_id,
                    after_babel_id,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        result: list[VectorRecord] = []
        for row in rows:
            vector_text = str(row[7]).strip()[1:-1]
            result.append(
                VectorRecord(
                    babel=CreatedBabel(
                        babelId=row[0],
                        runId=expected.run_id,
                        creatorId=row[1],
                        sourceArticleKey=row[2],
                        title=row[3],
                        text=row[4],
                        createdAtNs=max(0, int(row[6])),
                    ),
                    catalogContentHash=str(row[5]),
                    embeddingSpaceId=expected.embedding_space_id,
                    servingModelId=expected.model_id,
                    materializedModelVersion=expected.model_version,
                    vector=tuple(float(item) for item in vector_text.split(",")),
                )
            )
        return result

    def activate_population(
        self,
        expected: PopulationIdentity,
        *,
        snapshot_sha256: str,
    ) -> PopulationActivationEvidence:
        """Validate, activate, and gather evidence in one commit boundary."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH created AS (
                  SELECT babel_id, creator_id, catalog_content_hash
                  FROM experiment_babels
                  WHERE run_id=%s AND finalized_at IS NOT NULL
                    AND article_text IS NOT NULL AND catalog_content_hash IS NOT NULL
                ), indexed AS (
                  SELECT babel_id, creator_id, catalog_content_hash
                  FROM babel_embeddings
                  WHERE run_id=%s AND serving_model_id=%s
                    AND materialized_model_version=%s AND embedding_space_id=%s
                )
                SELECT
                  (SELECT count(*) FROM created),
                  (SELECT count(*) FROM indexed),
                  (SELECT count(*) FROM (
                    (SELECT * FROM created EXCEPT SELECT * FROM indexed)
                    UNION ALL
                    (SELECT * FROM indexed EXCEPT SELECT * FROM created)
                  ) AS differences)
                """,
                (
                    expected.run_id,
                    expected.run_id,
                    expected.model_id,
                    expected.model_version,
                    expected.embedding_space_id,
                ),
            )
            created_count, indexed_count, differences = cursor.fetchone()
            if created_count != indexed_count or differences != 0:
                raise PopulationIntegrityError(
                    "created and indexed IDs are not transactionally equal"
                )
            cursor.execute(
                """
                INSERT INTO run_embedding_states(
                  run_id,active_model_id,active_model_version,embedding_space_id,
                  pgvector_snapshot_sha256,backend_snapshot_sha256
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                  active_model_id=EXCLUDED.active_model_id,
                  active_model_version=EXCLUDED.active_model_version,
                  embedding_space_id=EXCLUDED.embedding_space_id,
                  pgvector_snapshot_sha256=EXCLUDED.pgvector_snapshot_sha256,
                  backend_snapshot_sha256=EXCLUDED.backend_snapshot_sha256,
                  synchronized_at=now()
                """,
                (
                    expected.run_id,
                    expected.model_id,
                    expected.model_version,
                    expected.embedding_space_id,
                    snapshot_sha256,
                    snapshot_sha256,
                ),
            )
            evidence = self._population_evidence_in_transaction(cursor, expected)
        return evidence

    def _population_evidence_in_transaction(
        self, cursor: Any, expected: PopulationIdentity
    ) -> PopulationActivationEvidence:
        """Gather required evidence before the activation transaction commits."""
        from ..model.pgvector_index import PGVECTOR_CREATED_BABEL_QUERY

        cursor.execute(
            "SELECT pg_table_size('babel_embeddings'), "
            "pg_indexes_size('babel_embeddings')"
        )
        table_bytes, index_bytes = cursor.fetchone()
        cursor.execute(
            """
            SELECT eb.embedding::text, rs.pgvector_snapshot_sha256
            FROM run_embedding_states AS rs
            JOIN babel_embeddings AS eb
              ON eb.run_id=rs.run_id
             AND eb.serving_model_id=rs.active_model_id
             AND eb.materialized_model_version=rs.active_model_version
             AND eb.embedding_space_id=rs.embedding_space_id
            WHERE rs.run_id=%s AND rs.active_model_id=%s
              AND rs.active_model_version=%s AND rs.embedding_space_id=%s
            ORDER BY eb.babel_id LIMIT 1
            """,
            (
                expected.run_id,
                expected.model_id,
                expected.model_version,
                expected.embedding_space_id,
            ),
        )
        row = cursor.fetchone()
        plan: object | None = None
        if row is not None:
            parameters = {
                "query": row[0],
                "run_id": expected.run_id,
                "model_id": expected.model_id,
                "model_version": expected.model_version,
                "embedding_space_id": expected.embedding_space_id,
                "snapshot_sha256": row[1],
                "exclude_creator_id": UUID(int=0),
                "limit": 10,
            }
            cursor.execute("SET LOCAL hnsw.ef_search = 100")
            cursor.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                + PGVECTOR_CREATED_BABEL_QUERY,
                parameters,
            )
            plan = cursor.fetchone()[0]
            if isinstance(plan, str):
                plan = json.loads(plan)
        return PopulationActivationEvidence(
            table_bytes=int(table_bytes),
            index_bytes=int(index_bytes),
            explain_plan=plan,
        )

    def load_active_source_vector(self, key: VectorCacheKey):
        """Load one exact active pgvector row without normalization or conversion."""
        import numpy as np

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT eb.embedding::text
                FROM run_embedding_states AS rs
                JOIN babel_embeddings AS eb
                  ON eb.run_id=rs.run_id
                 AND eb.serving_model_id=rs.active_model_id
                 AND eb.materialized_model_version=rs.active_model_version
                 AND eb.embedding_space_id=rs.embedding_space_id
                WHERE rs.run_id=%s AND eb.babel_id=%s
                  AND rs.active_model_id=%s AND rs.active_model_version=%s
                  AND rs.embedding_space_id=%s
                """,
                (
                    key.run_id,
                    key.babel_id,
                    key.model_id,
                    key.model_version,
                    key.embedding_space_id,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"active source vector does not exist: {key.babel_id}")
        values = str(row[0]).strip()[1:-1]
        result = np.asarray([float(item) for item in values.split(",")], dtype="<f4")
        if result.shape != (100,) or not np.isfinite(result).all():
            raise PopulationIntegrityError("active source vector is invalid")
        return result

    def population_storage_bytes(self) -> dict[str, int]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_table_size('babel_embeddings'), "
                "pg_indexes_size('babel_embeddings')"
            )
            row = cursor.fetchone()
        return {"table_bytes": int(row[0]), "index_bytes": int(row[1])}

    def explain_population_query(self, expected: PopulationIdentity) -> object:
        from ..model.pgvector_index import PGVECTOR_CREATED_BABEL_QUERY

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT eb.embedding::text, rs.pgvector_snapshot_sha256
                FROM run_embedding_states AS rs
                JOIN babel_embeddings AS eb
                  ON eb.run_id=rs.run_id
                 AND eb.serving_model_id=rs.active_model_id
                 AND eb.materialized_model_version=rs.active_model_version
                 AND eb.embedding_space_id=rs.embedding_space_id
                WHERE rs.run_id=%s
                ORDER BY eb.babel_id LIMIT 1
                """,
                (expected.run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            parameters = {
                "query": row[0],
                "run_id": expected.run_id,
                "model_id": expected.model_id,
                "model_version": expected.model_version,
                "embedding_space_id": expected.embedding_space_id,
                "snapshot_sha256": row[1],
                "exclude_creator_id": UUID(int=0),
                "limit": 10,
            }
            cursor.execute("SET LOCAL hnsw.ef_search = 100")
            cursor.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                + PGVECTOR_CREATED_BABEL_QUERY,
                parameters,
            )
            plan = cursor.fetchone()[0]
        return json.loads(plan) if isinstance(plan, str) else plan

    def query_candidates(
        self,
        settings: Sequence[str],
        sql: str,
        parameters: Mapping[str, object],
    ) -> list[dict[str, object]]:
        values = dict(parameters)
        query = values.get("query")
        if isinstance(query, list):
            values["query"] = "[" + ",".join(format(float(v), ".9g") for v in query) + "]"
        with self._connect() as connection, connection.cursor() as cursor:
            for statement in settings:
                cursor.execute(statement)
            cursor.execute(sql, values)
            return [
                {
                    "babel_id": row[0],
                    "creator_id": row[1],
                    "source_article_key": row[2],
                    "score": row[3],
                }
                for row in cursor.fetchall()
            ]


def lifecycle_activity(
    run_id: UUID, event: str, message: str, *, metrics: dict[str, int | float] | None = None
) -> ActivityLogV1:
    return ActivityLogV1(
        schemaVersion=1,
        runId=run_id,
        sequence=1,
        occurredAtNs=time.time_ns(),
        level="info",
        component="supervisor",
        event=event,
        message=message,
        metrics=metrics or {},
        details={"kind": "lifecycle"},
    )


__all__ = [
    "ArtifactConfigurationError",
    "LaunchIntegrityError",
    "PersistedRun",
    "RuntimeDatabase",
    "canonical_json_sha256",
    "lifecycle_activity",
    "load_configured_model_artifact",
]
