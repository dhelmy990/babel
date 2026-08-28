"""PostgreSQL ownership for immutable launches and observable online state."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from ..contracts import (
    ActivityLogV1,
    ActivityLogV2,
    FeedbackEventV2,
    ModelManifestV1,
    ModelManifestV2,
    RunConfigV1,
    RunConfigV2,
)
from ..model.artifact import LoadedArtifact, load_artifact
from ..model.candidate_index import MaterializedServingState
from ..model.population import (
    PopulationActivationEvidence,
    PopulationIdentity,
    PopulationIntegrityError,
    PopulationSource,
)
from ..model.source_vector_cache import VectorCacheKey
from ..model.state_distributor import RealQwenChildStateV1
from ..observable import CreatedBabel, VectorRecord, reject_hidden_fields
from ..simulation.population_plan import PopulationPlan
from ..simulation.scheduler import ScheduledSession
from ..simulation.walk import WalkRollEvidence


class ArtifactConfigurationError(ValueError):
    pass


class LaunchIntegrityError(ValueError):
    pass


class PerformanceBindingConflict(ValueError):
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
    config: RunConfigV1 | RunConfigV2
    status: str
    launch_sha256: str


@dataclass(frozen=True, slots=True)
class FrozenPopulationRow:
    """One DB-authoritative population row with exact pgvector wire bytes."""

    babel: CreatedBabel
    catalog_content_hash: str
    event_number: int
    scheduled: ScheduledSession
    vector_send_bytes: bytes
    vector_f32le_bytes: bytes


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
        version = document.get("schemaVersion")
        config_type = RunConfigV2 if version == 2 else RunConfigV1
        return PersistedRun(config_type.model_validate(document), str(row[2]), digest)

    def create_scaled_run(self, config: RunConfigV2) -> PersistedRun:
        """Persist one canonical V2 launch without passing through the V1 dashboard path."""
        if not isinstance(config, RunConfigV2):
            raise TypeError("scaled run creation requires RunConfigV2")
        document = config.model_dump(mode="json")
        launch = _canonical_json(document).decode("utf-8")
        digest = hashlib.sha256(launch.encode("utf-8")).hexdigest()
        scenario = "june_only" if len(config.environmentSequence) == 1 else "june_to_july"
        legacy_month_budget = config.perMonthEventBudget[config.environmentSequence[0]]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiment_runs(
                  id, status, retrieval_backend, creator_count, scenario,
                  environment_sequence, event_budget_per_month, run_seed,
                  dataset_repository, dataset_config, dataset_revision,
                  recommendation_k, top_l, kafka_topic, kafka_group,
                  checkpoint_every_events, sync_every_steps, artifact_root, state_root,
                  starting_model_id, active_model_id, contract_version,
                  source_articles_per_month, target_created_babels, concurrent_users,
                  recommendation_start_probability, continuation_probability,
                  maximum_traversal_depth, maximum_requests_per_traversal,
                  interleave_creation_and_recommendations, launch_config, launch_sha256
                ) VALUES (
                  %s, 'starting', %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 2,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    config.runId,
                    config.retrievalBackend,
                    config.creatorCount,
                    scenario,
                    json.dumps(config.environmentSequence, separators=(",", ":")),
                    legacy_month_budget,
                    config.runSeed,
                    config.datasetRepo,
                    config.datasetConfig,
                    config.datasetRevision,
                    config.recommendationK,
                    config.topL,
                    config.kafkaTopic,
                    config.kafkaGroup,
                    config.checkpointEveryEvents,
                    config.syncEverySteps,
                    config.artifactRoot,
                    config.stateRoot,
                    config.startingModelId,
                    config.startingModelId,
                    config.sourceArticlesPerMonth,
                    config.targetCreatedBabels,
                    config.concurrentUsers,
                    config.recommendationStartProbability,
                    config.continuationProbability,
                    config.maximumTraversalDepth,
                    config.maximumRequestsPerTraversal,
                    config.interleaveCreationAndRecommendations,
                    launch,
                    digest,
                ),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "SELECT launch_config,launch_sha256,status "
                    "FROM experiment_runs WHERE id=%s",
                    (config.runId,),
                )
                row = cursor.fetchone()
                existing_document = (
                    row[0] if row is not None and isinstance(row[0], Mapping)
                    else json.loads(row[0]) if row is not None else None
                )
                if (
                    row is None
                    or existing_document != document
                    or str(row[1]) != digest
                ):
                    raise LaunchIntegrityError(
                        "existing scaled run differs from requested launch"
                    )
                return PersistedRun(
                    config=config,
                    status=str(row[2]),
                    launch_sha256=digest,
                )
        return PersistedRun(config=config, status="starting", launch_sha256=digest)

    def stage_population_plan(
        self, plan: PopulationPlan, *, batch_size: int = 500
    ) -> None:
        """Bulk insert a finalized plan, accepting only an identical retry."""
        if not isinstance(plan, PopulationPlan):
            raise TypeError("population staging requires PopulationPlan")
        if batch_size <= 0:
            raise ValueError("population staging batch size must be positive")
        babel_parameters = [
            (
                row.babel.runId,
                row.babel.babelId,
                row.babel.creatorId,
                row.babel.sourceArticleKey,
                row.babel.title,
                row.babel.text,
                row.catalog_content_hash,
                row.event_number,
                row.babel.createdAtNs // 1_000,
            )
            for row in plan.babels
        ]
        schedule_parameters = [
            (
                row.run_id,
                row.schedule_index,
                row.creator_id,
                row.creator_event_number,
                row.period,
                row.source_article_key,
                row.root_babel_id,
                row.traversal_session_id,
                row.work_id,
                row.workload_sha256,
            )
            for row in plan.schedule
        ]
        with self._connect() as connection, connection.cursor() as cursor:
            for offset in range(0, len(babel_parameters), batch_size):
                cursor.executemany(
                    """
                    INSERT INTO experiment_babels(
                      run_id,babel_id,creator_id,source_article_key,title,article_text,
                      catalog_content_hash,event_number,created_at,finalized_at
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,
                      TIMESTAMPTZ 'epoch' + %s * INTERVAL '1 microsecond',
                      TIMESTAMPTZ 'epoch' + %s * INTERVAL '1 microsecond'
                    ) ON CONFLICT (run_id,babel_id) DO NOTHING
                    """,
                    [values + (values[-1],) for values in babel_parameters[offset:offset + batch_size]],
                )
            for offset in range(0, len(schedule_parameters), batch_size):
                cursor.executemany(
                    """
                    INSERT INTO experiment_work_schedule(
                      run_id,schedule_index,creator_id,creator_event_number,period,
                      source_article_key,root_babel_id,traversal_session_id,work_id,
                      workload_sha256
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id,schedule_index) DO NOTHING
                    """,
                    schedule_parameters[offset:offset + batch_size],
                )
            cursor.execute(
                """
                SELECT babel_id,creator_id,source_article_key,title,article_text,
                       catalog_content_hash,event_number,
                       (extract(epoch from created_at) * 1000000000)::bigint,
                       finalized_at IS NOT NULL
                FROM experiment_babels WHERE run_id=%s ORDER BY event_number
                """,
                (plan.run_id,),
            )
            actual_babels = cursor.fetchall()
            expected_babels = [
                (
                    row.babel.babelId,
                    row.babel.creatorId,
                    row.babel.sourceArticleKey,
                    row.babel.title,
                    row.babel.text,
                    row.catalog_content_hash,
                    row.event_number,
                    row.babel.createdAtNs,
                    True,
                )
                for row in plan.babels
            ]
            if actual_babels != expected_babels:
                raise PopulationIntegrityError(
                    "existing finalized population Babel content differs from plan"
                )
            cursor.execute(
                """
                SELECT schedule_index,creator_id,creator_event_number,period,
                       source_article_key,root_babel_id,traversal_session_id,work_id,
                       workload_sha256
                FROM experiment_work_schedule WHERE run_id=%s ORDER BY schedule_index
                """,
                (plan.run_id,),
            )
            actual_schedule = cursor.fetchall()
            expected_schedule = [
                (
                    row.schedule_index,
                    row.creator_id,
                    row.creator_event_number,
                    row.period,
                    row.source_article_key,
                    row.root_babel_id,
                    row.traversal_session_id,
                    row.work_id,
                    row.workload_sha256,
                )
                for row in plan.schedule
            ]
            if actual_schedule != expected_schedule:
                raise PopulationIntegrityError(
                    "existing frozen work schedule differs from plan"
                )

    def bind_performance_population(
        self,
        experiment_id: str,
        run_id: UUID,
        manifest_sha256: str,
        bundle_path: str,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE performance_experiments
                SET run_id=%s, population_manifest_sha256=%s,
                    population_bundle_path=%s
                WHERE id=%s AND (
                  (run_id IS NULL AND population_manifest_sha256 IS NULL AND
                   population_bundle_path IS NULL) OR
                  (run_id=%s AND population_manifest_sha256=%s AND
                   population_bundle_path=%s)
                )
                RETURNING id
                """,
                (
                    run_id,
                    manifest_sha256,
                    bundle_path,
                    experiment_id,
                    run_id,
                    manifest_sha256,
                    bundle_path,
                ),
            )
            if cursor.fetchone() is None:
                raise PerformanceBindingConflict(
                    "performance population already has a different execution binding"
                )

    def bind_performance_condition(
        self, experiment_id: str, condition_id: str, run_id: UUID
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE performance_conditions SET run_id=%s
                WHERE experiment_id=%s AND id=%s AND (run_id IS NULL OR run_id=%s)
                RETURNING id
                """,
                (run_id, experiment_id, condition_id, run_id),
            )
            if cursor.fetchone() is None:
                raise PerformanceBindingConflict(
                    "performance condition already has a different run binding"
                )

    def load_performance_experiment(self, experiment_id: UUID):
        """Load the immutable dashboard trial plus its exact saved condition matrix."""
        from .performance_worker import PerformanceCondition, PerformanceExperiment

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id,status,starting_model_id,model_repository,model_revision,
                       dataset_repository,dataset_revision,creator_count,
                       target_created_babels,concurrent_users,
                       recommendation_start_probability,continuation_probability,
                       maximum_traversal_depth,maximum_requests_per_traversal,
                       interleave_creation_and_recommendations,warmup_seconds,duration_seconds,
                       target_rps,training_micro_batch_size,sync_every_steps,
                       operator_approved,population_ready,run_id,
                       population_bundle_path,population_manifest_sha256,
                       population_vector_count,population_vector_sha256,
                       population_model_repository,population_model_revision,
                       population_model_sha256,population_dataset_repository,
                       population_dataset_revision,population_dataset_sha256,
                       request_identity
                FROM performance_experiments WHERE id=%s
                """,
                (experiment_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"performance experiment does not exist: {experiment_id}")
            cursor.execute(
                """
                SELECT id,condition_index,topology,training_enabled,
                       synchronization_enabled,run_id,status
                FROM performance_conditions
                WHERE experiment_id=%s ORDER BY condition_index
                """,
                (experiment_id,),
            )
            condition_rows = cursor.fetchall()
        conditions = tuple(
            PerformanceCondition(
                id=UUID(str(value[0])),
                condition_index=int(value[1]),
                topology=str(value[2]),
                training_enabled=bool(value[3]),
                activation_enabled=bool(value[4]),
                run_id=None if value[5] is None else UUID(str(value[5])),
                status=str(value[6]),
            )
            for value in condition_rows
        )
        request_identity = (
            dict(row[33])
            if isinstance(row[33], Mapping)
            else json.loads(str(row[33]))
        )
        workload_identity = request_identity.get("workloadIdentity")
        return PerformanceExperiment(
            id=UUID(str(row[0])),
            status=str(row[1]),
            starting_model_id=UUID(str(row[2])),
            model_repository=str(row[3]),
            model_revision=str(row[4]),
            dataset_repository=str(row[5]),
            dataset_config="crosswalk_2026_06_07",
            dataset_revision=str(row[6]),
            creator_count=int(row[7]),
            target_created_babels=int(row[8]),
            concurrent_users=int(row[9]),
            recommendation_start_probability=float(row[10]),
            continuation_probability=float(row[11]),
            maximum_traversal_depth=int(row[12]),
            maximum_requests_per_traversal=int(row[13]),
            interleave_creation_and_recommendations=bool(row[14]),
            warmup_seconds=int(row[15]),
            duration_seconds=int(row[16]),
            target_rps=float(row[17]),
            training_micro_batch_size=int(row[18]),
            sync_every_steps=int(row[19]),
            operator_approved=bool(row[20]),
            population_ready=bool(row[21]),
            population_run_id=None if row[22] is None else UUID(str(row[22])),
            population_bundle_path=None if row[23] is None else str(row[23]),
            population_manifest_sha256=None if row[24] is None else str(row[24]),
            conditions=conditions,
            evidence_scope=str(request_identity.get("evidenceScope", "formal")),
            source_trial_id=(
                UUID(str(request_identity["sourceTrialId"]))
                if request_identity.get("sourceTrialId")
                else None
            ),
            source_workload_path=request_identity.get("sourceWorkloadPath"),
            source_workload_identity=(
                tuple(str(value) for value in workload_identity)
                if isinstance(workload_identity, list)
                else None
            ),
            population_vector_count=0 if row[25] is None else int(row[25]),
            population_vector_sha256=None if row[26] is None else str(row[26]),
            population_model_repository=None if row[27] is None else str(row[27]),
            population_model_revision=None if row[28] is None else str(row[28]),
            population_model_sha256=None if row[29] is None else str(row[29]),
            population_dataset_repository=None if row[30] is None else str(row[30]),
            population_dataset_revision=None if row[31] is None else str(row[31]),
            population_dataset_sha256=None if row[32] is None else str(row[32]),
            replay_request_limit=(
                int(request_identity["requestLimit"])
                if request_identity.get("requestLimit") is not None
                else None
            ),
        )

    def create_representative_performance_rerun(self, binding: Any) -> Any:
        """Atomically copy only immutable launch/population identity into a new trial."""
        request_identity = json.dumps(
            {
                "evidenceScope": binding.evidence_scope,
                "sourceTrialId": str(binding.source_trial_id),
                "sourceWorkloadPath": str(binding.workload_path),
                "workloadIdentity": list(binding.workload_identity),
                "requestLimit": binding.request_limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH source AS (
                  SELECT * FROM performance_experiments
                  WHERE id=%s AND population_ready=true AND run_id=%s
                    AND population_manifest_sha256=%s
                  FOR SHARE
                ), inserted AS (
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
                    latency_safety_threshold_ms,hardware_identity,resource_identity,
                    request_identity,feedback_identity,population_ready,
                    population_vector_count,population_vector_sha256,
                    population_model_repository,population_model_revision,
                    population_model_sha256,population_dataset_repository,
                    population_dataset_revision,population_dataset_sha256,
                    operator_approved,run_id,population_manifest_sha256,
                    population_bundle_path
                  )
                  SELECT %s,'population_ready','same_host_split',starting_model_id,
                    model_repository,model_revision,dataset_repository,dataset_revision,
                    retrieval_backend,creator_count,seeded_articles,target_created_babels,
                    concurrent_users,recommendation_start_probability,
                    continuation_probability,maximum_traversal_depth,
                    maximum_requests_per_traversal,training_micro_batch_size,
                    sync_every_steps,interleave_creation_and_recommendations,false,
                    %s,%s,%s,
                    latency_safety_threshold_ms,hardware_identity,resource_identity,
                    %s::jsonb,feedback_identity,true,population_vector_count,
                    population_vector_sha256,population_model_repository,
                    population_model_revision,population_model_sha256,
                    population_dataset_repository,population_dataset_revision,
                    population_dataset_sha256,false,run_id,
                    population_manifest_sha256,population_bundle_path
                  FROM source
                  ON CONFLICT (id) DO NOTHING
                  RETURNING id
                ) SELECT id FROM inserted
                """,
                (
                    binding.source_trial_id,
                    binding.population_run_id,
                    binding.population_manifest_sha256,
                    binding.rerun_id,
                    binding.warmup_seconds,
                    binding.duration_seconds,
                    binding.target_rps,
                    request_identity,
                ),
            )
            if cursor.fetchone() is None:
                raise PerformanceBindingConflict(
                    "representative rerun source changed or destination already exists"
                )
            representative_topologies = {
                "representative_same_process_vs_split": (
                    "same_process",
                    "same_host_split",
                ),
                "representative_split_smoke": ("same_host_split",),
                "representative_isolated_smoke": ("same_host_isolated",),
            }
            try:
                topologies = representative_topologies[binding.evidence_scope]
            except KeyError:
                raise ValueError(
                    "representative rerun evidence scope is unsupported"
                ) from None
            conditions = []
            condition_values = (
                (topology, training, activation)
                for topology in topologies
                for training, activation in (
                    (False, False),
                    (True, False),
                    (True, True),
                )
            )
            for index, (topology, training, activation) in enumerate(
                condition_values, start=1
            ):
                config = {
                    "schemaVersion": 1,
                    "experimentId": str(binding.rerun_id),
                    "conditionIndex": index,
                    "topology": topology,
                    "trainingEnabled": training,
                    "synchronizationEnabled": activation,
                    "evidenceScope": binding.evidence_scope,
                    "sourceTrialId": str(binding.source_trial_id),
                }
                encoded = json.dumps(config, sort_keys=True, separators=(",", ":"))
                conditions.append(
                    (
                        uuid5(binding.rerun_id, f"condition:{index}"),
                        binding.rerun_id,
                        index,
                        topology,
                        training,
                        activation,
                        encoded,
                        hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    )
                )
            cursor.executemany(
                """
                INSERT INTO performance_conditions(
                  id,experiment_id,condition_index,topology,training_enabled,
                  synchronization_enabled,launch_config,launch_sha256
                ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                conditions,
            )
            cursor.execute(
                """
                INSERT INTO performance_progress_snapshots(
                  experiment_id,sequence,phase,condition_count,seeded_articles,
                  created_babels,indexed_babels,telemetry
                ) VALUES (%s,0,'population_ready',%s,10000,10000,10000,%s::jsonb)
                """,
                (
                    binding.rerun_id,
                    len(conditions),
                    json.dumps(
                        {
                            "evidenceScope": binding.evidence_scope,
                            "sourceTrialId": str(binding.source_trial_id),
                            "workloadIdentity": list(binding.workload_identity),
                            "requestLimit": binding.request_limit,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        return binding

    def append_performance_progress(
        self,
        experiment_id: UUID,
        *,
        phase: str,
        condition_index: int | None,
        condition_count: int,
        seeded_articles: int,
        created_babels: int,
        indexed_babels: int,
        requested: int,
        completed: int,
        elapsed_seconds: float,
        recent_rate: float,
        draining: bool,
        telemetry: Mapping[str, object],
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO performance_progress_snapshots(
                  experiment_id,sequence,phase,condition_index,condition_count,
                  seeded_articles,created_babels,indexed_babels,requested,completed,
                  elapsed_seconds,recent_rate,draining,telemetry
                ) SELECT %s,COALESCE(MAX(sequence),-1)+1,%s,%s,%s,%s,%s,%s,%s,%s,
                         %s,%s,%s,%s::jsonb
                  FROM performance_progress_snapshots WHERE experiment_id=%s
                """,
                (
                    experiment_id,
                    phase,
                    condition_index,
                    condition_count,
                    seeded_articles,
                    created_babels,
                    indexed_babels,
                    requested,
                    completed,
                    elapsed_seconds,
                    recent_rate,
                    draining,
                    json.dumps(telemetry, sort_keys=True, separators=(",", ":")),
                    experiment_id,
                ),
            )

    def create_condition_run(self, trial: Any, condition: Any, run_id: UUID) -> UUID:
        """Persist one formal condition run with topology-independent semantics."""
        seed = int.from_bytes(
            hashlib.sha256(f"{trial.id}:workload".encode("utf-8")).digest()[:8],
            "big",
        ) & ((1 << 63) - 1)
        root = Path(
            os.environ.get("BABEL_PERFORMANCE_STATE_ROOT", "state/performance")
        ) / str(trial.id) / "conditions" / str(condition.condition_index)
        config = RunConfigV2(
            schemaVersion=2,
            runId=run_id,
            datasetRepo=trial.dataset_repository,
            datasetConfig=trial.dataset_config,
            datasetRevision=trial.dataset_revision,
            startingModelId=trial.starting_model_id,
            retrievalBackend="pgvector",
            creatorCount=trial.creator_count,
            embeddingDimension=100,
            environmentSequence=["2026-06", "2026-07"],
            perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
            runSeed=seed,
            recommendationK=10,
            topL=100,
            kafkaTopic="babel.feedback.v1",
            kafkaGroup=f"babel-performance-{trial.id}-{condition.condition_index}",
            checkpointEveryEvents=max(1, trial.training_micro_batch_size * 10),
            syncEverySteps=trial.sync_every_steps,
            artifactRoot=str(root / "artifacts"),
            stateRoot=str(root / "runtime"),
            sourceArticlesPerMonth=5_000,
            targetCreatedBabels=10_000,
            concurrentUsers=trial.concurrent_users,
            recommendationStartProbability=trial.recommendation_start_probability,
            continuationProbability=trial.continuation_probability,
            maximumTraversalDepth=2,
            maximumRequestsPerTraversal=trial.maximum_requests_per_traversal,
            interleaveCreationAndRecommendations=(
                trial.interleave_creation_and_recommendations
            ),
            trainingMicroBatchSize=trial.training_micro_batch_size,
        )
        self.create_scaled_run(config)
        return run_id

    def clone_performance_population(
        self, trial: Any, condition: Any, run_id: UUID
    ) -> None:
        """Validate the bound bundle and clone its exact DB snapshot without Qwen."""
        from ..model.frozen_population import load_frozen_population

        if trial.population_bundle_path is None or trial.population_run_id is None:
            raise PerformanceBindingConflict("performance population is not bound")
        manifest = load_frozen_population(trial.population_bundle_path)
        expected_experiment_id = (
            trial.source_trial_id
            if trial.evidence_scope.startswith("representative_")
            else trial.id
        )
        if (
            manifest.sourcePopulationRunId != trial.population_run_id
            or expected_experiment_id is None
            or manifest.experimentId != str(expected_experiment_id)
        ):
            raise PerformanceBindingConflict(
                "frozen population differs from the saved trial binding"
            )
        state = self.clone_population_transaction(
            manifest.population_identity(), run_id
        )
        if (
            state.model_id != manifest.modelId
            or state.model_version != manifest.modelVersion
            or state.embedding_space_id != manifest.embeddingSpaceId
            or state.pgvector_snapshot_sha256 != manifest.pgvectorSnapshotSha256
            or state.backend_snapshot_sha256 != manifest.pgvectorSnapshotSha256
        ):
            raise PopulationIntegrityError(
                "condition clone active snapshot differs from frozen population"
            )

    def transition_performance(
        self, experiment_id: UUID, status: str, failure: str | None = None
    ) -> None:
        allowed = {
            "population_pending", "population_ready", "approved", "running",
            "stop_requested", "draining", "completed", "failed", "interrupted",
        }
        if status not in allowed:
            raise ValueError("invalid performance experiment status")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE performance_experiments SET status=%s,failure=%s "
                "WHERE id=%s RETURNING id",
                (status, failure, experiment_id),
            )
            if cursor.fetchone() is None:
                raise KeyError(experiment_id)

    def mark_performance_population_ready(
        self, experiment_id: UUID, manifest: Any
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE performance_experiments SET
                  status='population_ready',population_ready=true,
                  population_vector_count=%s,population_vector_sha256=%s,
                  population_model_repository=%s,population_model_revision=%s,
                  population_model_sha256=%s,population_dataset_repository=%s,
                  population_dataset_revision=%s,population_dataset_sha256=%s
                WHERE id=%s AND (
                  status='population_pending' OR
                  (population_ready=true AND population_vector_count=%s AND
                   population_vector_sha256=%s)
                ) RETURNING id
                """,
                (
                    manifest.babelCount,
                    manifest.vectorsSha256,
                    manifest.artifactRepo,
                    manifest.artifactRevision,
                    manifest.modelManifestSha256,
                    manifest.datasetRepo,
                    manifest.datasetRevision,
                    manifest.datasetManifestSha256,
                    experiment_id,
                    manifest.babelCount,
                    manifest.vectorsSha256,
                ),
            )
            if cursor.fetchone() is None:
                raise PerformanceBindingConflict(
                    "performance population readiness already differs"
                )

    def transition_performance_condition(
        self, experiment_id: UUID, condition_id: UUID, status: str
    ) -> None:
        if status not in {
            "pending",
            "warmup",
            "running",
            "draining",
            "completed",
            "failed",
            "interrupted",
        }:
            raise ValueError("invalid performance condition status")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE performance_conditions SET status=%s "
                "WHERE experiment_id=%s AND id=%s RETURNING id",
                (status, experiment_id, condition_id),
            )
            if cursor.fetchone() is None:
                raise KeyError(condition_id)

    def save_performance_condition_result(
        self,
        experiment_id: UUID,
        evidence: Any,
        *,
        serving_p95_ms: float,
        training_p95_ms: float,
        full_p95_ms: float,
    ) -> None:
        if min(serving_p95_ms, training_p95_ms, full_p95_ms) <= 0:
            raise ValueError("interference p95 values must be positive")
        ratios = (
            training_p95_ms / serving_p95_ms,
            full_p95_ms / serving_p95_ms,
            full_p95_ms / training_p95_ms,
        )
        raw = {
            **evidence.raw_evidence,
            "conditionId": str(evidence.condition_id),
            "runId": str(evidence.run_id),
            "requestCount": evidence.request_count,
            "conditionP95Ms": evidence.p95_ms,
        }
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO performance_results(
                  experiment_id,condition_id,raw_evidence,evidence_sha256,
                  serving_p95_ms,training_p95_ms,full_p95_ms,
                  itraining,ifull,iactivation_increment
                ) VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (experiment_id,condition_id) DO NOTHING
                """,
                (
                    experiment_id,
                    evidence.condition_id,
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    serving_p95_ms,
                    training_p95_ms,
                    full_p95_ms,
                    *ratios,
                ),
            )

    def verify_live_serving_identity(
        self,
        *,
        run_id: UUID,
        starting_model_id: UUID,
        model_id: UUID,
        model_version: int,
        embedding_space_id: UUID,
        pgvector_sha256: str,
        backend_sha256: str,
    ) -> bool:
        """Verify one response against the run's active immutable descendant state."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH RECURSIVE lineage AS (
                  SELECT id,parent_model_id,producing_run_id
                  FROM recommender_models WHERE id=%s
                  UNION ALL
                  SELECT child.id,child.parent_model_id,child.producing_run_id
                  FROM recommender_models AS child
                  JOIN lineage AS parent ON child.parent_model_id=parent.id
                  WHERE child.producing_run_id=%s
                )
                SELECT EXISTS(
                  SELECT 1 FROM run_embedding_states AS active
                  JOIN lineage ON lineage.id=active.active_model_id
                  WHERE active.run_id=%s AND active.active_model_id=%s
                    AND active.active_model_version=%s
                    AND active.embedding_space_id=%s
                    AND active.pgvector_snapshot_sha256=%s
                    AND active.backend_snapshot_sha256=%s
                )
                """,
                (
                    starting_model_id,
                    run_id,
                    run_id,
                    model_id,
                    model_version,
                    embedding_space_id,
                    pgvector_sha256,
                    backend_sha256,
                ),
            )
            row = cursor.fetchone()
        return row is not None and bool(row[0])

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

    def register_real_child(
        self, descriptor: RealQwenChildStateV1, descriptor_path: Path
    ) -> None:
        """Register a V2 child without pretending its online state is Qwen weights."""
        model = descriptor.childManifest
        if not isinstance(model, ModelManifestV2) or model.parentModelId is None:
            raise TypeError("real child registration requires V2 child lineage")
        path = Path(descriptor_path).resolve()
        if not path.is_file():
            raise ArtifactConfigurationError("real child descriptor path is missing")
        parsed = RealQwenChildStateV1.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if parsed != descriptor:
            raise ArtifactConfigurationError("real child descriptor bytes differ")
        descriptor_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        embedding = model.embeddingSpace.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO recommender_models(
                  id,label,parent_model_id,producing_run_id,encoder_repo,
                  encoder_revision,dataset_repo,dataset_revision,
                  environment_sequence,training_examples,checkpoint_path,
                  checkpoint_sha256,embedding_space,immutable
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s,%s::jsonb,true)
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
                    model.trainingExamples,
                    str(path),
                    descriptor_sha,
                    json.dumps(embedding, sort_keys=True, separators=(",", ":")),
                ),
            )
            cursor.execute(
                """
                SELECT parent_model_id,encoder_revision,embedding_space,
                       checkpoint_sha256
                FROM recommender_models WHERE id=%s
                """,
                (model.modelId,),
            )
            row = cursor.fetchone()
            registered_embedding = (
                row[2] if row is not None and isinstance(row[2], Mapping) else None
            )
            if row is None or (
                row[0] != model.parentModelId
                or str(row[1]) != model.encoderRevision
                or registered_embedding != embedding
                or str(row[3]) != descriptor_sha
            ):
                raise ArtifactConfigurationError(
                    "registered real child differs from immutable descriptor"
                )

    def load_real_child_descriptor(self, model_id: UUID) -> RealQwenChildStateV1:
        descriptor, _path = self.load_real_child_artifact(model_id)
        return descriptor

    def load_real_child_artifact(
        self, model_id: UUID
    ) -> tuple[RealQwenChildStateV1, Path]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT checkpoint_path,checkpoint_sha256 "
                "FROM recommender_models WHERE id=%s AND parent_model_id IS NOT NULL",
                (model_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"real recommender child does not exist: {model_id}")
        path = Path(row[0]).resolve()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != str(
            row[1]
        ):
            raise ArtifactConfigurationError("real child descriptor checksum differs")
        try:
            descriptor = RealQwenChildStateV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as error:
            raise ArtifactConfigurationError("real child descriptor is invalid") from error
        if descriptor.childManifest.modelId != model_id:
            raise ArtifactConfigurationError("real child descriptor resolves to another model")
        return descriptor, path

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
            "source_vector_qwen_encode_count",
            "source_vector_cache_hit_count",
            "source_vector_pgvector_load_count",
            "source_vector_eviction_count",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("unsupported experiment metric")
        assignments = ", ".join(f"{name}=%s" for name in values)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE experiment_runs SET {assignments} WHERE id=%s",  # noqa: S608
                (*values.values(), run_id),
            )

    def performance_runtime_health(self, run_id: UUID) -> dict[str, int | None]:
        """Return independently persisted trainer/serving health for sampling."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT kafka_lag,trainer_steps,active_model_version,"
                "checkpoint_path,serving_synced FROM experiment_runs WHERE id=%s",
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(run_id)
        trainer_version = int(row[1])
        serving_version = int(row[2])
        return {
            "kafka_lag": int(row[0]),
            "trainer_version": trainer_version,
            "serving_version": serving_version,
            "checkpoint_version": trainer_version if row[3] is not None else None,
            "activation_version": serving_version if bool(row[4]) else None,
        }

    def append_activity(self, activity: ActivityLogV1 | ActivityLogV2) -> int:
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
                  message, metrics, details, schema_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
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
                    document.schemaVersion,
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

    def finalize_babel(
        self, run_id: UUID, babel_id: UUID, request_id: UUID | None
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE experiment_babels SET request_id=%s, finalized_at=now() "
                "WHERE run_id=%s AND babel_id=%s AND finalized_at IS NULL",
                (request_id, run_id, babel_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("staged Babel could not be finalized")

    def persist_work_schedule(
        self, rows: Sequence[ScheduledSession]
    ) -> None:
        """Freeze the topology-independent creation/walk work for one run."""
        if not rows:
            raise ValueError("work schedule cannot be empty")
        run_id = rows[0].run_id
        if any(row.run_id != run_id for row in rows):
            raise ValueError("work schedule cannot cross runs")
        if [row.schedule_index for row in rows] != list(range(len(rows))):
            raise ValueError("work schedule indexes must be contiguous")
        with self._connect() as connection, connection.cursor() as cursor:
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO experiment_work_schedule(
                      run_id,schedule_index,creator_id,creator_event_number,period,
                      source_article_key,root_babel_id,traversal_session_id,work_id,
                      workload_sha256
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        row.run_id,
                        row.schedule_index,
                        row.creator_id,
                        row.creator_event_number,
                        row.period,
                        row.source_article_key,
                        row.root_babel_id,
                        row.traversal_session_id,
                        row.work_id,
                        row.workload_sha256,
                    ),
                )

    def load_work_schedule(self, run_id: UUID) -> tuple[ScheduledSession, ...]:
        """Load the frozen topology-independent work in its intended order."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT schedule_index,creator_id,creator_event_number,period,
                       source_article_key,root_babel_id,traversal_session_id,
                       work_id,workload_sha256
                FROM experiment_work_schedule
                WHERE run_id=%s
                ORDER BY schedule_index
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            ScheduledSession(
                run_id=run_id,
                schedule_index=int(row[0]),
                creator_id=row[1],
                creator_event_number=int(row[2]),
                period=row[3],
                source_article_key=row[4],
                root_babel_id=row[5],
                traversal_session_id=row[6],
                work_id=row[7],
                workload_sha256=row[8],
            )
            for row in rows
        )

    def persist_traversal_rolls(
        self,
        run_id: UUID,
        traversal_session_id: UUID,
        rolls: Sequence[WalkRollEvidence],
    ) -> None:
        """Persist the exact deterministic rolls and expansion outcomes for one walk."""
        if not rolls or [row.draw_index for row in rolls] != list(range(len(rolls))):
            raise ValueError("traversal roll indexes must be contiguous from zero")
        if rolls[0].kind != "start":
            raise ValueError("traversal evidence must begin with the start roll")
        with self._connect() as connection, connection.cursor() as cursor:
            for row in rolls:
                cursor.execute(
                    """
                    INSERT INTO experiment_traversal_rolls(
                      run_id,traversal_session_id,draw_index,kind,source_babel_id,
                      target_babel_id,target_rank,source_depth,draw_value,
                      probability,roll_succeeded,outcome
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id,traversal_session_id,draw_index) DO NOTHING
                    """,
                    (
                        run_id,
                        traversal_session_id,
                        row.draw_index,
                        row.kind,
                        row.source_babel_id,
                        row.target_babel_id,
                        row.target_rank,
                        row.source_depth,
                        row.draw_value,
                        row.probability,
                        row.roll_succeeded,
                        row.outcome,
                    ),
                )
                if cursor.rowcount == 1:
                    continue
                cursor.execute(
                    """
                    SELECT kind,source_babel_id,target_babel_id,target_rank,
                           source_depth,draw_value,probability,roll_succeeded,outcome
                    FROM experiment_traversal_rolls
                    WHERE run_id=%s AND traversal_session_id=%s AND draw_index=%s
                    """,
                    (run_id, traversal_session_id, row.draw_index),
                )
                existing = cursor.fetchone()
                expected = (
                    row.kind,
                    row.source_babel_id,
                    row.target_babel_id,
                    row.target_rank,
                    row.source_depth,
                    row.draw_value,
                    row.probability,
                    row.roll_succeeded,
                    row.outcome,
                )
                if existing != expected:
                    raise PopulationIntegrityError(
                        "traversal roll retry differs from persisted evidence"
                    )

    def load_traversal_rolls(
        self, run_id: UUID, traversal_session_id: UUID
    ) -> tuple[WalkRollEvidence, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT draw_index,kind,source_babel_id,target_babel_id,target_rank,
                       source_depth,draw_value,probability,roll_succeeded,outcome
                FROM experiment_traversal_rolls
                WHERE run_id=%s AND traversal_session_id=%s
                ORDER BY draw_index
                """,
                (run_id, traversal_session_id),
            )
            rows = cursor.fetchall()
        return tuple(
            WalkRollEvidence(
                draw_index=int(row[0]),
                kind=row[1],
                source_babel_id=row[2],
                target_babel_id=row[3],
                target_rank=None if row[4] is None else int(row[4]),
                source_depth=int(row[5]),
                draw_value=float(row[6]),
                probability=float(row[7]),
                roll_succeeded=bool(row[8]),
                outcome=row[9],
            )
            for row in rows
        )

    def persist_feedback_edges(self, event: FeedbackEventV2) -> None:
        """Upsert only includes, selecting canonical event-time provenance."""
        if not isinstance(event, FeedbackEventV2):
            raise TypeError("scaled experiment edges require FeedbackEventV2")
        includes = [
            action
            for action in event.candidateActions
            if action.action == "include" and action.babelId != event.sourceBabelId
        ]
        if not includes:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            for action in includes:
                cursor.execute(
                    """
                    INSERT INTO experiment_edges(
                      run_id,source_babel_id,target_babel_id,acting_creator_id,
                      request_id,feedback_event_id,feedback_occurred_at_ns,
                      traversal_session_id,traversal_depth
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id,source_babel_id,target_babel_id) DO UPDATE SET
                      acting_creator_id=EXCLUDED.acting_creator_id,
                      request_id=EXCLUDED.request_id,
                      feedback_event_id=EXCLUDED.feedback_event_id,
                      feedback_occurred_at_ns=EXCLUDED.feedback_occurred_at_ns,
                      traversal_session_id=EXCLUDED.traversal_session_id,
                      traversal_depth=EXCLUDED.traversal_depth
                    WHERE EXCLUDED.feedback_occurred_at_ns <
                            experiment_edges.feedback_occurred_at_ns
                       OR (EXCLUDED.feedback_occurred_at_ns =
                            experiment_edges.feedback_occurred_at_ns
                           AND EXCLUDED.feedback_event_id <
                            experiment_edges.feedback_event_id)
                    """,
                    (
                        event.runId,
                        event.sourceBabelId,
                        action.babelId,
                        event.creatorId,
                        event.requestId,
                        event.eventId,
                        event.occurredAtNs,
                        event.traversalSessionId,
                        event.traversalDepth + 1,
                    ),
                )

    def canonical_edges(self, run_id: UUID):
        from ..feedback.export import CanonicalExperimentEdge

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_babel_id,target_babel_id,acting_creator_id,
                       request_id,feedback_event_id,feedback_occurred_at_ns,
                       traversal_session_id,traversal_depth
                FROM experiment_edges
                WHERE run_id=%s
                ORDER BY source_babel_id,target_babel_id
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return tuple(
            CanonicalExperimentEdge(
                run_id=run_id,
                source_babel_id=row[0],
                target_babel_id=row[1],
                acting_creator_id=row[2],
                request_id=row[3],
                feedback_event_id=row[4],
                feedback_occurred_at_ns=int(row[5]),
                traversal_session_id=row[6],
                traversal_depth=int(row[7]),
            )
            for row in rows
        )

    def insert_vectors(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO babel_embeddings(
                  run_id,babel_id,creator_id,embedding_space_id,serving_model_id,
                  materialized_model_version,catalog_content_hash,embedding
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::public.vector)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        record.babel.runId,
                        record.babel.babelId,
                        record.babel.creatorId,
                        record.embeddingSpaceId,
                        record.servingModelId,
                        record.materializedModelVersion,
                        record.catalogContentHash,
                        "[" + ",".join(format(value, ".9g") for value in record.vector) + "]",
                    )
                    for record in records
                ],
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

    def load_active_embedding_state(self, run_id: UUID) -> MaterializedServingState:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT active_model_id,active_model_version,embedding_space_id,
                       pgvector_snapshot_sha256,backend_snapshot_sha256
                FROM run_embedding_states WHERE run_id=%s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"run has no active embedding state: {run_id}")
        return MaterializedServingState(
            run_id=run_id,
            model_id=row[0],
            model_version=int(row[1]),
            embedding_space_id=row[2],
            pgvector_snapshot_sha256=str(row[3]),
            backend_snapshot_sha256=str(row[4]),
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
    def _decode_vector_send(value: object) -> tuple[bytes, bytes]:
        """Validate pgvector's binary send format and return exact f32le payload."""
        wire = bytes(value)
        if len(wire) != 404:
            raise PopulationIntegrityError("pgvector wire row is not 100d")
        dimension, unused = struct.unpack(">hh", wire[:4])
        if dimension != 100 or unused != 0:
            raise PopulationIntegrityError("pgvector wire header is invalid")
        big_endian_values = wire[4:]
        little_endian_values = b"".join(
            big_endian_values[offset:offset + 4][::-1]
            for offset in range(0, len(big_endian_values), 4)
        )
        return wire, little_endian_values

    def frozen_population_rows(
        self,
        expected: PopulationIdentity,
        *,
        after_babel_id: UUID | None,
        limit: int,
    ) -> list[FrozenPopulationRow]:
        """Read exact active vector bytes and bound schedule rows in Babel-ID order."""
        if limit <= 0:
            raise ValueError("frozen population read limit must be positive")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT xb.babel_id,xb.creator_id,xb.source_article_key,xb.title,
                       xb.article_text,xb.catalog_content_hash,xb.event_number,
                       (extract(epoch from xb.created_at) * 1000000000)::bigint,
                       ws.schedule_index,ws.creator_event_number,ws.period,
                       ws.traversal_session_id,ws.work_id,ws.workload_sha256,
                       public.vector_send(eb.embedding)
                FROM experiment_babels AS xb
                JOIN experiment_work_schedule AS ws
                  ON ws.run_id=xb.run_id AND ws.root_babel_id=xb.babel_id
                JOIN babel_embeddings AS eb
                  ON eb.run_id=xb.run_id AND eb.babel_id=xb.babel_id
                WHERE xb.run_id=%s AND xb.finalized_at IS NOT NULL
                  AND eb.serving_model_id=%s
                  AND eb.materialized_model_version=%s
                  AND eb.embedding_space_id=%s
                  AND (%s::uuid IS NULL OR xb.babel_id > %s::uuid)
                ORDER BY xb.babel_id LIMIT %s
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
        result: list[FrozenPopulationRow] = []
        for row in rows:
            wire, f32le = self._decode_vector_send(row[14])
            babel = CreatedBabel(
                babelId=row[0],
                runId=expected.run_id,
                creatorId=row[1],
                sourceArticleKey=str(row[2]),
                title=str(row[3]),
                text=str(row[4]),
                createdAtNs=max(0, int(row[7])),
            )
            scheduled = ScheduledSession(
                run_id=expected.run_id,
                schedule_index=int(row[8]),
                creator_id=row[1],
                creator_event_number=int(row[9]),
                period=str(row[10]),
                source_article_key=str(row[2]),
                root_babel_id=row[0],
                traversal_session_id=row[11],
                work_id=row[12],
                workload_sha256=str(row[13]),
            )
            result.append(
                FrozenPopulationRow(
                    babel=babel,
                    catalog_content_hash=str(row[5]),
                    event_number=int(row[6]),
                    scheduled=scheduled,
                    vector_send_bytes=wire,
                    vector_f32le_bytes=f32le,
                )
            )
        return result

    def clone_population_transaction(
        self, source: PopulationIdentity, destination_run_id: UUID
    ) -> MaterializedServingState:
        """Clone active population tables through INSERT...SELECT without Python vectors."""
        if destination_run_id == source.run_id:
            raise ValueError("population clone destination must differ from source")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiment_babels(
                  run_id,babel_id,creator_id,source_article_key,title,created_at,
                  article_text,catalog_content_hash,event_number,request_id,finalized_at
                )
                SELECT %s,babel_id,creator_id,source_article_key,title,created_at,
                       article_text,catalog_content_hash,event_number,request_id,finalized_at
                FROM experiment_babels WHERE run_id=%s AND finalized_at IS NOT NULL
                ON CONFLICT (run_id,babel_id) DO NOTHING
                """,
                (destination_run_id, source.run_id),
            )
            cursor.execute(
                """
                INSERT INTO babel_embeddings(
                  run_id,babel_id,creator_id,embedding_space_id,serving_model_id,
                  materialized_model_version,catalog_content_hash,embedding,created_at
                )
                SELECT %s,eb.babel_id,eb.creator_id,eb.embedding_space_id,
                       eb.serving_model_id,eb.materialized_model_version,
                       eb.catalog_content_hash,eb.embedding,eb.created_at
                FROM babel_embeddings AS eb
                WHERE eb.run_id=%s AND eb.serving_model_id=%s
                  AND eb.materialized_model_version=%s AND eb.embedding_space_id=%s
                ON CONFLICT (run_id,babel_id,materialized_model_version) DO NOTHING
                """,
                (
                    destination_run_id,
                    source.run_id,
                    source.model_id,
                    source.model_version,
                    source.embedding_space_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO run_embedding_states(
                  run_id,active_model_id,active_model_version,embedding_space_id,
                  pgvector_snapshot_sha256,backend_snapshot_sha256,synchronized_at
                )
                SELECT %s,active_model_id,active_model_version,embedding_space_id,
                       pgvector_snapshot_sha256,backend_snapshot_sha256,synchronized_at
                FROM run_embedding_states WHERE run_id=%s
                ON CONFLICT (run_id) DO NOTHING
                """,
                (destination_run_id, source.run_id),
            )
            cursor.execute(
                """
                INSERT INTO experiment_work_schedule(
                  run_id,schedule_index,creator_id,creator_event_number,period,
                  source_article_key,root_babel_id,traversal_session_id,work_id,
                  workload_sha256,created_at
                )
                SELECT %s,schedule_index,creator_id,creator_event_number,period,
                       source_article_key,root_babel_id,traversal_session_id,work_id,
                       workload_sha256,created_at
                FROM experiment_work_schedule WHERE run_id=%s
                ON CONFLICT (run_id,schedule_index) DO NOTHING
                """,
                (destination_run_id, source.run_id),
            )
            cursor.execute(
                """
                WITH babel_diff AS (
                  (SELECT babel_id,creator_id,source_article_key,title,created_at,
                          article_text,catalog_content_hash,event_number,request_id,finalized_at
                   FROM experiment_babels WHERE run_id=%s
                   EXCEPT
                   SELECT babel_id,creator_id,source_article_key,title,created_at,
                          article_text,catalog_content_hash,event_number,request_id,finalized_at
                   FROM experiment_babels WHERE run_id=%s)
                  UNION ALL
                  (SELECT babel_id,creator_id,source_article_key,title,created_at,
                          article_text,catalog_content_hash,event_number,request_id,finalized_at
                   FROM experiment_babels WHERE run_id=%s
                   EXCEPT
                   SELECT babel_id,creator_id,source_article_key,title,created_at,
                          article_text,catalog_content_hash,event_number,request_id,finalized_at
                   FROM experiment_babels WHERE run_id=%s)
                ), vector_diff AS (
                  (SELECT babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,
                          public.vector_send(embedding),created_at
                   FROM babel_embeddings WHERE run_id=%s
                     AND serving_model_id=%s
                     AND materialized_model_version=%s
                     AND embedding_space_id=%s
                   EXCEPT
                   SELECT babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,
                          public.vector_send(embedding),created_at
                   FROM babel_embeddings WHERE run_id=%s
                     AND serving_model_id=%s
                     AND materialized_model_version=%s
                     AND embedding_space_id=%s)
                  UNION ALL
                  (SELECT babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,
                          public.vector_send(embedding),created_at
                   FROM babel_embeddings WHERE run_id=%s
                     AND serving_model_id=%s
                     AND materialized_model_version=%s
                     AND embedding_space_id=%s
                   EXCEPT
                   SELECT babel_id,creator_id,embedding_space_id,serving_model_id,
                          materialized_model_version,catalog_content_hash,
                          public.vector_send(embedding),created_at
                   FROM babel_embeddings WHERE run_id=%s
                     AND serving_model_id=%s
                     AND materialized_model_version=%s
                     AND embedding_space_id=%s)
                ), schedule_diff AS (
                  (SELECT schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,created_at
                   FROM experiment_work_schedule WHERE run_id=%s
                   EXCEPT
                   SELECT schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,created_at
                   FROM experiment_work_schedule WHERE run_id=%s)
                  UNION ALL
                  (SELECT schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,created_at
                   FROM experiment_work_schedule WHERE run_id=%s
                   EXCEPT
                   SELECT schedule_index,creator_id,creator_event_number,period,
                          source_article_key,root_babel_id,traversal_session_id,work_id,
                          workload_sha256,created_at
                   FROM experiment_work_schedule WHERE run_id=%s)
                )
                SELECT (SELECT count(*) FROM babel_diff),
                       (SELECT count(*) FROM vector_diff),
                       (SELECT count(*) FROM schedule_diff)
                """,
                (
                    source.run_id, destination_run_id,
                    destination_run_id, source.run_id,
                    source.run_id, source.model_id, source.model_version,
                    source.embedding_space_id,
                    destination_run_id, source.model_id, source.model_version,
                    source.embedding_space_id,
                    destination_run_id, source.model_id, source.model_version,
                    source.embedding_space_id,
                    source.run_id, source.model_id, source.model_version,
                    source.embedding_space_id,
                    source.run_id, destination_run_id,
                    destination_run_id, source.run_id,
                ),
            )
            differences = cursor.fetchone()
            if differences is None or tuple(int(value) for value in differences) != (0, 0, 0):
                raise PopulationIntegrityError("cloned population bytes or schedule differ")
            cursor.execute(
                """
                SELECT active_model_id,active_model_version,embedding_space_id,
                       pgvector_snapshot_sha256,backend_snapshot_sha256
                FROM run_embedding_states WHERE run_id=%s
                """,
                (destination_run_id,),
            )
            state = cursor.fetchone()
            expected_state = (
                source.model_id,
                source.model_version,
                source.embedding_space_id,
            )
            if state is None or tuple(state[:3]) != expected_state:
                raise PopulationIntegrityError("cloned active embedding state differs")
        return MaterializedServingState(
            run_id=destination_run_id,
            model_id=state[0],
            model_version=int(state[1]),
            embedding_space_id=state[2],
            pgvector_snapshot_sha256=str(state[3]),
            backend_snapshot_sha256=str(state[4]),
        )

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
        """Load the exact immutable snapshot key without following a moving pointer."""
        import numpy as np

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT embedding::text
                FROM babel_embeddings
                WHERE run_id=%s AND babel_id=%s
                  AND serving_model_id=%s AND materialized_model_version=%s
                  AND embedding_space_id=%s
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

    def explain_population_query(
        self,
        expected: PopulationIdentity,
        *,
        query_vector: object | None = None,
        exclude_creator_id: UUID | None = None,
        limit: int = 10,
    ) -> object:
        from ..model.pgvector_index import PGVECTOR_CREATED_BABEL_QUERY

        if limit <= 0:
            raise ValueError("population EXPLAIN limit must be positive")

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
            query = row[0]
            if query_vector is not None:
                import numpy as np

                values = np.asarray(query_vector, dtype="<f4")
                if values.shape != (100,) or not np.isfinite(values).all():
                    raise ValueError("population EXPLAIN query must be finite 100d")
                query = "[" + ",".join(
                    format(float(value), ".9g") for value in values
                ) + "]"
            parameters = {
                "query": query,
                "run_id": expected.run_id,
                "model_id": expected.model_id,
                "model_version": expected.model_version,
                "embedding_space_id": expected.embedding_space_id,
                "snapshot_sha256": row[1],
                "exclude_creator_id": exclude_creator_id or UUID(int=0),
                "limit": limit,
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
