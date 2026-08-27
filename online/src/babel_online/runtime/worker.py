"""Runnable 50-creator Friday-demo worker composed across HTTP, Kafka, and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import numpy as np

from ..contracts import (
    ActivityLogV1,
    ActivityLogV2,
    CandidateActionV1,
    FeedbackEventV1,
    FeedbackEventV2,
    ModelManifestV1,
    ModelManifestV2,
    RecommendationActivityV1,
    RecommendationActivityV2,
    RecommendationRequestV1,
    RecommendationRequestV2,
    RunConfigV2,
    canonical_pgvector_snapshot_sha256,
)
from ..feedback import (
    KafkaFeedbackConsumer,
    KafkaFeedbackProducer,
    OffsetRange,
    TopicPartition,
    export_offset_ranges,
)
from ..model.candidate_index import MaterializedServingState
from ..model.item_tower import ItemTower
from ..model.pgvector_index import PgvectorCandidateIndex
from ..model.qwen_encoder import Qwen100Encoder, format_article_input
from ..model.registry import ModelRegistry
from ..model.source_vector_cache import SourceVectorResolver
from ..model.state_distributor import (
    KnownVectorProbeV1,
    export_real_qwen_child,
    semantic_vector_sha256,
)
from ..observable import CreatedBabel, VectorRecord
from ..serving import ServingState, create_app
from ..simulation.client import RecommendationClient
from ..simulation.decisions import (
    action_probabilities,
    combined_relevance,
    decide_candidate,
    deterministic_draw,
)
from ..simulation.scheduler import (
    BoundedCreatorScheduler,
    ScheduledSession,
    ScheduledWork,
    deterministic_schedule,
)
from ..simulation.walk import (
    IncludedWalkTarget,
    RecommendationWalk,
    WalkExpansion,
    WalkNode,
)
from ..training.checkpoint import CheckpointIdentity, load_latest_checkpoint
from ..training.consumer import OnlineTrainer
from ..training.synchronization import AtomicSynchronizer, export_immutable_child
from ..training.working import NumpyWorkingModel
from .database import RuntimeDatabase, lifecycle_activity
from .dataset_bundle import DatasetBundle


def isolate_new_run_offsets(consumer: Any) -> dict[TopicPartition, int]:
    """Ignore historical topic records by starting at one captured watermark."""
    start = consumer.high_watermarks()
    consumer.seek(start)
    return start


class RunScopedConsumer:
    """Fail closed if another run appears after this run's start watermark."""

    def __init__(self, consumer: Any, *, run_id: UUID) -> None:
        self._consumer = consumer
        self.run_id = run_id

    def poll(self, timeout_seconds: float = 0.0):
        record = self._consumer.poll(timeout_seconds)
        if record is not None and record.event.runId != self.run_id:
            raise RuntimeError("cross-run Kafka feedback contamination detected")
        return record

    def __getattr__(self, name: str):
        return getattr(self._consumer, name)

    def records(self, offset_range: OffsetRange):
        records = self._consumer.records(offset_range)
        if any(record.event.runId != self.run_id for record in records):
            raise RuntimeError("cross-run Kafka export contamination detected")
        return records


@dataclass(frozen=True, slots=True)
class _CompletedScaledRoot:
    scheduled: ScheduledSession
    babel: CreatedBabel
    article: Mapping[str, Any]
    root_request_id: UUID | None


class _UvicornThread:
    def __init__(self, app: Any, *, host: str, port: int) -> None:
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 10.0
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("recommendation server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5.0)


def _vector_sha(vector: tuple[float, ...]) -> str:
    return hashlib.sha256(np.asarray(vector, dtype="<f4").tobytes()).hexdigest()


def _snapshot_sha(records: list[VectorRecord]) -> str:
    return canonical_pgvector_snapshot_sha256(
        {
            "babelId": row.babel.babelId,
            "creatorId": row.babel.creatorId,
            "sourceArticleKey": row.babel.sourceArticleKey,
            "catalogContentHash": row.catalogContentHash,
            "embeddingSpaceId": row.embeddingSpaceId,
            "servingModelId": row.servingModelId,
            "materializedModelVersion": row.materializedModelVersion,
            "vectorSha256": _vector_sha(row.vector),
        }
        for row in records
    )


def _activity(
    run_id: UUID,
    *,
    component: str,
    event: str,
    message: str,
    metrics: Mapping[str, int | float],
    details: object,
) -> ActivityLogV1 | ActivityLogV2:
    contract = ActivityLogV2 if isinstance(details, RecommendationActivityV2) else ActivityLogV1
    return contract(
        schemaVersion=2 if contract is ActivityLogV2 else 1,
        runId=run_id,
        sequence=1,
        occurredAtNs=time.time_ns(),
        level="info",
        component=component,
        event=event,
        message=message,
        metrics=dict(metrics),
        details=details,
    )


class FridayDemoRuntime:
    """One immutable run selected by the dashboard and persisted in PostgreSQL."""

    def __init__(
        self,
        *,
        config: Any,
        database: RuntimeDatabase,
        bundle: DatasetBundle,
        model_lineage: list[Any],
        kafka_bootstrap_servers: str,
        recommendation_port: int,
        stop_event: threading.Event,
        qwen_encoder: Qwen100Encoder | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.bundle = bundle
        if not model_lineage:
            raise ValueError("model lineage cannot be empty")
        self.model_lineage = model_lineage
        self.starting_artifact = model_lineage[-1]
        self.starting_model = self.starting_artifact.manifest
        self.scale_run = isinstance(self.starting_model, ModelManifestV2)
        if self.scale_run and not isinstance(qwen_encoder, Qwen100Encoder):
            raise ValueError("ModelManifestV2 runtime requires one Qwen100Encoder")
        if not self.scale_run and qwen_encoder is not None:
            raise ValueError("fixture ModelManifestV1 cannot receive a Qwen100Encoder")
        if self.scale_run:
            contract = qwen_encoder.contract
            model = self.starting_model
            if (
                contract.artifactRepo != model.encoderRepo
                or contract.artifactRevision != model.encoderRevision
                or contract.artifactId != model.artifactId
                or contract.baseModelRevision != model.baseModelRevision
                or contract.datasetRevision != model.datasetRevision
                or contract.adapterSha256 != model.adapterSha256
                or contract.projectionSha256 != model.projectionSha256
                or contract.validationSha256 != model.validationSha256
            ):
                raise ValueError("Qwen encoder identity differs from selected V2 model")
        self.qwen_encoder = qwen_encoder
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.recommendation_port = recommendation_port
        self.stop_event = stop_event
        self.recommendation_endpoint = f"http://127.0.0.1:{recommendation_port}"
        self._server: _UvicornThread | None = None
        self._trainer_thread: threading.Thread | None = None
        self._trainer_stop = threading.Event()
        self._records: list[VectorRecord] = []
        self._created: list[CreatedBabel] = []
        self._content_hashes: dict[UUID, str] = {}
        self._histories: dict[UUID, list[UUID]] = {}
        self._last_sync_version = 0
        self._serving_version = 0
        self._last_logged_training_step = 0
        self._serving_vectors: dict[UUID, np.ndarray] = {}
        self._babel_by_id: dict[UUID, CreatedBabel] = {}
        self._simulation_lock = threading.RLock()
        self._feedback_count = 0
        self._kafka_offset = 0

    def request_stop(self) -> None:
        self.stop_event.set()

    def stop_serving(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None

    def _catalogs(self) -> dict[str, list[dict[str, Any]]]:
        prefix = "catalog" if getattr(self, "scale_run", False) else "demo_catalog"
        return {
            "2026-06": list(self.bundle.configs[f"{prefix}_2026_06"]),
            "2026-07": list(self.bundle.configs[f"{prefix}_2026_07"]),
        }

    def _hidden_edges(self) -> dict[str, set[tuple[str, str]]]:
        result: dict[str, set[tuple[str, str]]] = {"2026-06": set(), "2026-07": set()}
        for period in result:
            prefix = (
                "simulator"
                if getattr(self, "scale_run", False)
                else "demo_simulator"
            )
            config = f"{prefix}_{period.replace('-', '_')}_hidden"
            for row in self.bundle.configs[config]:
                if row.get("record_type") not in {"edges", "pagelink"}:
                    continue
                payload = json.loads(row["payload_json"])
                result[period].add(
                    (payload["source_article_key"], payload["target_article_key"])
                )
        return result

    def _plan(self) -> list[tuple[str, UUID, dict[str, Any], UUID]]:
        catalogs = self._catalogs()
        used: dict[UUID, set[str]] = {}
        plan: list[tuple[str, UUID, dict[str, Any], UUID]] = []
        creators = [uuid5(self.config.runId, f"creator:{index}") for index in range(self.config.creatorCount)]
        self._creator_slots = {creator: index for index, creator in enumerate(creators)}
        sequence = 0
        for period in self.config.environmentSequence:
            rows = catalogs[period]
            budget = self.config.perMonthEventBudget[period]
            for month_index in range(budget):
                creator = creators[month_index % len(creators)]
                start = int(
                    self._simulation_draw(
                        "source", period, month_index, self._creator_slots[creator], "", 0
                    )
                    * len(rows)
                )
                chosen = None
                for shift in range(len(rows)):
                    row = rows[(start + shift) % len(rows)]
                    if row["article_key"] not in used.setdefault(creator, set()):
                        chosen = row
                        break
                if chosen is None:
                    raise RuntimeError("creator source support exhausted without replacement")
                used[creator].add(chosen["article_key"])
                babel_id = uuid5(self.config.runId, f"babel:{period}:{sequence}:{creator}")
                plan.append((period, creator, chosen, babel_id))
                sequence += 1
        return plan

    def _persist_scaled_schedule(
        self, plan: list[tuple[str, UUID, dict[str, Any], UUID]]
    ) -> tuple[ScheduledSession, ...]:
        creator_events: dict[UUID, int] = {}
        work = []
        for period, creator_id, article, babel_id in plan:
            event_number = creator_events.get(creator_id, 0)
            creator_events[creator_id] = event_number + 1
            work.append(
                ScheduledWork(
                    creator_id=creator_id,
                    creator_event_number=event_number,
                    period=period,
                    source_article_key=article["article_key"],
                    root_babel_id=babel_id,
                )
            )
        schedule = deterministic_schedule(self.config.runId, work)
        persisted = self.database.load_work_schedule(self.config.runId)
        if persisted:
            if persisted != schedule:
                raise RuntimeError(
                    "persisted work schedule differs from the current launch plan"
                )
            return persisted
        self.database.persist_work_schedule(schedule)
        return schedule

    def _encode_plan_vectors(
        self,
        plan: list[tuple[str, UUID, dict[str, Any], UUID]],
        *,
        batch_size: int = 16,
    ) -> dict[UUID, np.ndarray]:
        """Encode planned created Babels in bounded batches in the selected space."""
        if batch_size <= 0:
            raise ValueError("encoder batch size must be positive")
        if not self.scale_run:
            tower = ItemTower(self.starting_model.embeddingSpace)
            return {
                babel_id: tower.encode(
                    format_article_input(
                        article["canonical_title"],
                        article.get("lead_text") or article["article_text"],
                    )
                )
                for _period, _creator, article, babel_id in plan
            }
        if self.qwen_encoder is None:  # constructor invariant
            raise RuntimeError("real Qwen encoder is unavailable")
        result: dict[UUID, np.ndarray] = {}
        for start in range(0, len(plan), batch_size):
            batch = plan[start : start + batch_size]
            texts = [
                format_article_input(
                    article["canonical_title"],
                    article.get("lead_text") or article["article_text"],
                )
                for _period, _creator, article, _babel_id in batch
            ]
            vectors = self.qwen_encoder.encode(texts)
            if vectors.shape != (len(batch), 100) or not np.isfinite(vectors).all():
                raise ValueError("real Qwen plan encoding violated the 100d contract")
            for row, vector in zip(batch, vectors, strict=True):
                result[row[3]] = np.asarray(vector, dtype="<f4")
        return result

    def _create_serving_state(
        self,
        materialized_state: MaterializedServingState,
        records: list[VectorRecord],
    ) -> ServingState:
        return ServingState(
            registry=self.registry,
            selected_model_id=self.starting_model.modelId,
            materialized_state=materialized_state,
            candidate_index=self.index,
            vector_records=records,
            qwen_encoder=self.qwen_encoder,
            scale_run=self.scale_run,
        )

    def _simulation_draw(
        self,
        kind: str,
        period: str,
        event_number: int,
        creator_slot: int,
        candidate_source: str,
        candidate_rank: int,
    ) -> float:
        return deterministic_draw(
            self.config.runSeed,
            kind,
            period,
            event_number,
            creator_slot,
            candidate_source,
            candidate_rank,
        )

    def _materialized_records(self, version: int) -> list[VectorRecord]:
        return [
            self._materialized_record(babel, version)
            for babel in self._created
        ]

    def _materialized_record(
        self, babel: CreatedBabel, version: int
    ) -> VectorRecord:
        return VectorRecord(
            babel=babel,
            catalogContentHash=self._content_hashes[babel.babelId],
            embeddingSpaceId=self.starting_model.embeddingSpace.embeddingSpaceId,
            servingModelId=self.starting_model.modelId,
            materializedModelVersion=version,
            vector=tuple(float(value) for value in self._serving_vectors[babel.babelId]),
        )

    def _state(self, version: int, sha: str) -> MaterializedServingState:
        return MaterializedServingState(
            run_id=self.config.runId,
            model_id=self.starting_model.modelId,
            model_version=version,
            embedding_space_id=self.starting_model.embeddingSpace.embeddingSpaceId,
            pgvector_snapshot_sha256=sha,
            backend_snapshot_sha256=sha,
        )

    def _capture_training_sync(self) -> tuple[int, dict[UUID, np.ndarray], dict[str, Any]]:
        captured = self.trainer.capture_sync_state()
        return (
            captured.version,
            {
                babel.babelId: captured.materialized_vectors[babel.babelId]
                for babel in self._created
            },
            captured.model_state,
        )

    def _persist_and_activate(
        self, version: int | None = None, *, synchronize: bool
    ) -> None:
        captured_model_state: dict[str, Any] | None = None
        if synchronize:
            version, self._serving_vectors, captured_model_state = (
                self._capture_training_sync()
            )
            self._serving_version = version
        if version is None:
            raise ValueError("materialization version is required")
        records = self._materialized_records(version)
        self.database.insert_vectors(records)
        sha = _snapshot_sha(records)
        state = self._state(version, sha)
        self.database.activate_embedding_state(
            run_id=self.config.runId,
            model_id=self.starting_model.modelId,
            model_version=version,
            embedding_space_id=self.starting_model.embeddingSpace.embeddingSpaceId,
            pgvector_sha256=sha,
            backend_sha256=sha,
        )
        if synchronize:
            artifact = self.synchronizer.publish(
                model_state=captured_model_state,
                selected_model_id=self.starting_model.modelId,
                materialized_state=state,
                candidate_index=self.index,
                vector_records=records,
            )
            self._last_sync_version = version
            self.database.append_activity(
                _activity(
                    self.config.runId,
                    component="training",
                    event="serving_synchronized",
                    message=f"Serving synchronized to online version {version}.",
                    metrics={"modelVersion": version},
                    details={
                        "kind": "synchronization",
                        "checkpointPath": str(artifact.path),
                        "checkpointSha256": artifact.state_sha256,
                        "synchronizationVersion": version,
                        "modelId": self.starting_model.modelId,
                        "modelVersion": version,
                    },
                )
            )
        else:
            self.serving.apply_sync(
                selected_model_id=self.starting_model.modelId,
                materialized_state=state,
                candidate_index=self.index,
                vector_records=records,
            )
        self._records = records

    def _materialize_new_babel(self) -> None:
        """Index a new Babel without exposing unsynchronized trainer state."""
        if not self._created:
            raise RuntimeError("a new finalized Babel is required for materialization")
        record = self._materialized_record(self._created[-1], self._serving_version)
        # Insert only the new row.  Rebuilding every prior VectorRecord here made
        # a run quadratic before recommendation retrieval even began.
        self.database.insert_vectors([record])
        self._records.append(record)
        sha = _snapshot_sha(self._records)
        state = self._state(self._serving_version, sha)
        self.database.activate_embedding_state(
            run_id=self.config.runId,
            model_id=self.starting_model.modelId,
            model_version=self._serving_version,
            embedding_space_id=self.starting_model.embeddingSpace.embeddingSpaceId,
            pgvector_sha256=sha,
            backend_sha256=sha,
        )
        self.serving.apply_sync(
            selected_model_id=self.starting_model.modelId,
            materialized_state=state,
            candidate_index=self.index,
            vector_records=self._records,
        )

    def _record_recommendation(
        self,
        babel: CreatedBabel,
        response: Any,
        event: FeedbackEventV1 | FeedbackEventV2,
        client_ns: int,
    ) -> None:
        actions = {"include": [], "exclude": [], "ignore": []}
        for action in event.candidateActions:
            actions[action.action].append(action.babelId)
        server_ns = response.timingsNs["serverTotal"]
        if isinstance(event, FeedbackEventV2):
            if event.sourceVectorOrigin != response.sourceVectorOrigin:
                raise RuntimeError("feedback and response source-vector origins differ")
            details = RecommendationActivityV2(
                kind="recommendation",
                requestId=event.requestId,
                traversalSessionId=event.traversalSessionId,
                sourceVectorOrigin=event.sourceVectorOrigin,
                creatorId=event.creatorId,
                newBabelId=event.sourceBabelId,
                newBabelTitle=babel.title,
                candidateBabelIds=[row.babelId for row in response.candidates],
                includeBabelIds=actions["include"],
                excludeBabelIds=actions["exclude"],
                ignoreBabelIds=actions["ignore"],
                acceptedEdgeCount=len(actions["include"]),
                modelId=response.modelId,
                modelVersion=response.modelVersion,
            )
        else:
            details = RecommendationActivityV1(
                kind="recommendation",
                creatorId=event.creatorId,
                newBabelId=babel.babelId,
                newBabelTitle=babel.title,
                candidateBabelIds=[row.babelId for row in response.candidates],
                includeBabelIds=actions["include"],
                excludeBabelIds=actions["exclude"],
                ignoreBabelIds=actions["ignore"],
                acceptedEdgeCount=len(actions["include"]),
                modelId=response.modelId,
                modelVersion=response.modelVersion,
            )
        self.database.append_activity(
            _activity(
                self.config.runId,
                component="serving",
                event="recommendation_completed",
                message=(
                    f'Creator {str(event.creatorId)[:8]} requested recommendations '
                    f'for "{babel.title}".'
                ),
                metrics={
                    **{f"{name}Ns": value for name, value in response.timingsNs.items()},
                    "clientTotalNs": client_ns,
                    "clientOverheadNs": max(0, client_ns - server_ns),
                    **(
                        {"traversalDepth": event.traversalDepth}
                        if isinstance(event, FeedbackEventV2)
                        else {}
                    ),
                },
                details=details,
            )
        )

    def _scaled_decision_draw(
        self,
        kind: str,
        scheduled: ScheduledSession,
        source: WalkNode,
        source_depth: int,
        candidate: Any,
    ) -> float:
        return deterministic_draw(
            self.config.runSeed,
            kind,
            self.config.runId,
            scheduled.creator_id,
            scheduled.traversal_session_id,
            source.babel_id,
            source_depth,
            candidate.babelId,
            candidate.sourceArticleKey,
            candidate.rank,
        )

    def _after_scaled_feedback(self, record: Any) -> None:
        """Preserve the online training/sync path under concurrent callbacks."""
        with self._simulation_lock:
            self._feedback_count += 1
            self._kafka_offset = max(self._kafka_offset, record.offset + 1)
            vector_telemetry = self.source_vector_resolver.telemetry()
            trainer_metrics = self.trainer.metrics
            current_version = int(trainer_metrics["optimizerSteps"])
            lag = max(
                0, self._feedback_count - int(trainer_metrics["processedEvents"])
            )
            self.database.update_metrics(
                self.config.runId,
                feedback_count=self._feedback_count,
                event_rate=self._feedback_count
                / max(time.monotonic() - self._scaled_started, 1e-6),
                kafka_offset=self._kafka_offset,
                kafka_lag=lag,
                trainer_steps=current_version,
                rolling_rank_loss=trainer_metrics["rollingRankLoss"],
                source_vector_qwen_encode_count=vector_telemetry.qwen_encode,
                source_vector_cache_hit_count=vector_telemetry.cache_hit,
                source_vector_pgvector_load_count=vector_telemetry.pgvector_load,
                source_vector_eviction_count=vector_telemetry.evictions,
            )
            if current_version > self._last_logged_training_step:
                self._last_logged_training_step = current_version
                self.database.append_activity(
                    _activity(
                        self.config.runId,
                        component="training",
                        event="online_training_progress",
                        message=f"Online trainer reached step {current_version}.",
                        metrics={
                            "stepTimeMs": self.trainer.last_step_time_ms or 0.0
                        },
                        details={
                            "kind": "training",
                            "trainerStep": current_version,
                            "rollingRankLoss": trainer_metrics["rollingRankLoss"],
                        },
                    )
                )
            self.database.append_activity(
                _activity(
                    self.config.runId,
                    component="feedback",
                    event="feedback_acknowledged",
                    message=f"Feedback acknowledged at Kafka offset {record.offset}.",
                    metrics={"kafkaLag": lag},
                    details={
                        "kind": "feedback",
                        "kafkaOffset": record.offset,
                        "kafkaLag": lag,
                    },
                )
            )
            if current_version >= self._last_sync_version + self.config.syncEverySteps:
                self._persist_and_activate(synchronize=True)

    def _publish_scaled_root_batch(
        self,
        completed: tuple[_CompletedScaledRoot, ...],
        *,
        finalize: bool = True,
    ) -> None:
        """Publish one deterministic wave through a single vector/state boundary."""
        if not completed:
            return
        ordered = tuple(
            sorted(completed, key=lambda value: value.scheduled.schedule_index)
        )
        with self._simulation_lock:
            for value in ordered:
                babel = value.babel
                if finalize:
                    self.database.finalize_babel(
                        self.config.runId, babel.babelId, value.root_request_id
                    )
                self._created.append(babel)
                self._babel_by_id[babel.babelId] = babel
                self._content_hashes[babel.babelId] = str(
                    value.article["content_hash"]
                )
                self._histories.setdefault(babel.creatorId, []).append(
                    babel.babelId
                )
                self._serving_vectors[babel.babelId] = self._frozen_vectors[
                    babel.babelId
                ]
            new_records = [
                self._materialized_record(value.babel, self._serving_version)
                for value in ordered
            ]
            self.database.insert_vectors(new_records)
            self._records.extend(new_records)
            sha = _snapshot_sha(self._records)
            state = self._state(self._serving_version, sha)
            self.database.activate_embedding_state(
                run_id=self.config.runId,
                model_id=self.starting_model.modelId,
                model_version=self._serving_version,
                embedding_space_id=(
                    self.starting_model.embeddingSpace.embeddingSpaceId
                ),
                pgvector_sha256=sha,
                backend_sha256=sha,
            )
            self.serving.apply_sync(
                selected_model_id=self.starting_model.modelId,
                materialized_state=state,
                candidate_index=self.index,
                vector_records=self._records,
            )
            self.database.update_metrics(
                self.config.runId,
                created_babel_count=len(self._created),
            )

    def _execute_scaled_session(
        self,
        scheduled: ScheduledSession,
        article: Mapping[str, Any],
        hidden: Mapping[str, set[tuple[str, str]]],
        *,
        precreated: bool = False,
    ) -> _CompletedScaledRoot | None:
        if self.stop_event.is_set() or self.database.stop_requested(self.config.runId):
            return
        text = str(article.get("lead_text") or article["article_text"])
        babel = CreatedBabel(
            babelId=scheduled.root_babel_id,
            runId=self.config.runId,
            creatorId=scheduled.creator_id,
            sourceArticleKey=scheduled.source_article_key,
            title=str(article["canonical_title"]),
            text=text,
            createdAtNs=time.time_ns(),
        )
        if not precreated:
            self.database.stage_babel(
                babel=babel,
                content_hash=str(article["content_hash"]),
                event_number=scheduled.schedule_index,
            )
        client = RecommendationClient(self.recommendation_endpoint)
        request_events: dict[UUID, tuple[Any, FeedbackEventV2, int, WalkNode]] = {}

        def recommend(
            source: WalkNode,
            session_id: UUID,
            parent_request_id: UUID | None,
            source_depth: int,
        ) -> WalkExpansion:
            request_id = uuid5(
                self.config.runId,
                f"request:v2:{session_id}:{source.babel_id}:{source_depth}",
            )
            event_id = uuid5(
                self.config.runId,
                f"feedback:v2:{session_id}:{source.babel_id}:{source_depth}",
            )
            request = RecommendationRequestV2(
                schemaVersion=2,
                requestId=request_id,
                runId=self.config.runId,
                creatorId=scheduled.creator_id,
                sourceBabelId=source.babel_id,
                sourceArticleKey=source.source_article_key,
                traversalSessionId=session_id,
                parentRequestId=parent_request_id,
                traversalDepth=source_depth,
                title=babel.title if source_depth == 0 else None,
                text=babel.text if source_depth == 0 else None,
                historyBabelIds=[
                    value
                    for value in self._histories.setdefault(
                        scheduled.creator_id, []
                    )
                    if value != source.babel_id
                ],
                candidateCount=self.config.recommendationK,
            )
            client_started = time.perf_counter_ns()
            response = client.recommend(request)
            client_ns = time.perf_counter_ns() - client_started
            if (
                response.requestId != request_id
                or response.runId != self.config.runId
                or response.modelId != self.starting_model.modelId
            ):
                raise RuntimeError("scaled recommendation response identity differs")
            actions = []
            included = []
            for candidate in response.candidates:
                related = (
                    0.95
                    if (source.source_article_key, candidate.sourceArticleKey)
                    in hidden[scheduled.period]
                    else 0.55
                )
                preference = (
                    0.7
                    if self._scaled_decision_draw(
                        "preference", scheduled, source, source_depth, candidate
                    )
                    < 0.5
                    else 0.4
                )
                probabilities = action_probabilities(
                    relevance=combined_relevance(
                        relatedness_rank=related, preference_rank=preference
                    ),
                    epsilon=0.2,
                    exclusion_propensity=0.25,
                )
                action = decide_candidate(
                    probabilities,
                    draw=self._scaled_decision_draw(
                        "action", scheduled, source, source_depth, candidate
                    ),
                )
                candidate_action = CandidateActionV1(
                    babelId=candidate.babelId,
                    sourceArticleKey=candidate.sourceArticleKey,
                    rank=candidate.rank,
                    modelScore=candidate.modelScore,
                    action=action,
                )
                actions.append(candidate_action)
                if action == "include":
                    included.append(
                        IncludedWalkTarget(
                            WalkNode(
                                babel_id=candidate.babelId,
                                source_article_key=candidate.sourceArticleKey,
                            ),
                            candidate.rank,
                        )
                    )
            event = FeedbackEventV2(
                schemaVersion=2,
                eventId=event_id,
                requestId=request_id,
                runId=self.config.runId,
                creatorId=scheduled.creator_id,
                sourceBabelId=source.babel_id,
                sourceArticleKey=source.source_article_key,
                traversalSessionId=session_id,
                parentRequestId=parent_request_id,
                traversalDepth=source_depth,
                modelId=response.modelId,
                modelVersion=response.modelVersion,
                embeddingSpaceId=response.embeddingSpaceId,
                retrievalBackend=response.retrievalBackend,
                sourceVectorOrigin=response.sourceVectorOrigin,
                candidateActions=actions,
                occurredAtNs=time.time_ns(),
            )
            record = self.producer.publish(
                key=str(scheduled.creator_id), event=event
            )
            self.database.persist_feedback_edges(event)
            request_events[request_id] = (response, event, client_ns, source)
            self._after_scaled_feedback(record)
            return WalkExpansion(request_id=request_id, included=tuple(included))

        try:
            walk = RecommendationWalk(
                start_probability=self.config.recommendationStartProbability,
                continuation_probability=self.config.continuationProbability,
                max_depth=self.config.maximumTraversalDepth,
                max_requests=self.config.maximumRequestsPerTraversal,
                draw=lambda *identity: deterministic_draw(
                    self.config.runSeed, *identity
                ),
            )
            trace = walk.run(
                run_id=self.config.runId,
                creator_id=scheduled.creator_id,
                session_id=scheduled.traversal_session_id,
                root=WalkNode(
                    babel_id=babel.babelId,
                    source_article_key=babel.sourceArticleKey,
                ),
                recommend=recommend,
            )
            self.database.persist_traversal_rolls(
                self.config.runId,
                scheduled.traversal_session_id,
                trace.rolls,
            )
            root_request_id = trace.requests[0].request_id if trace.requests else None
            for request in trace.requests:
                response, event, client_ns, source = request_events[request.request_id]
                with self._simulation_lock:
                    source_babel = self._babel_by_id.get(source.babel_id, babel)
                self._record_recommendation(
                    source_babel, response, event, client_ns
                )
            if not precreated:
                return _CompletedScaledRoot(
                    scheduled=scheduled,
                    babel=babel,
                    article=article,
                    root_request_id=root_request_id,
                )
            return None
        finally:
            client.close()

    def _simulate_scaled(
        self,
        plan: list[tuple[str, UUID, dict[str, Any], UUID]],
        hidden: Mapping[str, set[tuple[str, str]]],
    ) -> None:
        schedule = self._persist_scaled_schedule(plan)
        articles = {
            row[3]: row[2]
            for row in plan
        }
        self._scaled_started = time.monotonic()
        if not self.config.interleaveCreationAndRecommendations:
            precreated = []
            for row in schedule:
                article = articles[row.root_babel_id]
                text = str(article.get("lead_text") or article["article_text"])
                babel = CreatedBabel(
                    babelId=row.root_babel_id,
                    runId=self.config.runId,
                    creatorId=row.creator_id,
                    sourceArticleKey=row.source_article_key,
                    title=str(article["canonical_title"]),
                    text=text,
                    createdAtNs=time.time_ns(),
                )
                self.database.stage_babel(
                    babel=babel,
                    content_hash=str(article["content_hash"]),
                    event_number=row.schedule_index,
                )
                precreated.append(
                    _CompletedScaledRoot(
                        scheduled=row,
                        babel=babel,
                        article=article,
                        root_request_id=None,
                    )
                )
            self._publish_scaled_root_batch(tuple(precreated))

        completed_by_index: dict[int, _CompletedScaledRoot] = {}
        completed_lock = threading.Lock()

        def execute(row: ScheduledSession) -> None:
            completed = self._execute_scaled_session(
                row,
                articles[row.root_babel_id],
                hidden,
                precreated=not self.config.interleaveCreationAndRecommendations,
            )
            if completed is not None:
                with completed_lock:
                    completed_by_index[row.schedule_index] = completed

        def publish_wave(wave: tuple[ScheduledSession, ...]) -> None:
            if not self.config.interleaveCreationAndRecommendations:
                return
            with completed_lock:
                completed = tuple(
                    completed_by_index.pop(row.schedule_index)
                    for row in wave
                    if row.schedule_index in completed_by_index
                )
            self._publish_scaled_root_batch(completed)

        BoundedCreatorScheduler(
            concurrent_users=self.config.concurrentUsers
        ).run(
            schedule,
            execute,
            after_wave=publish_wave,
        )

    def _simulate(self, plan: list[tuple[str, UUID, dict[str, Any], UUID]], hidden: dict[str, set[tuple[str, str]]]) -> None:
        client = RecommendationClient(self.recommendation_endpoint)
        started = time.monotonic()
        try:
            for event_number, (period, creator_id, article, babel_id) in enumerate(plan):
                if self.stop_event.is_set() or self.database.stop_requested(self.config.runId):
                    break
                text = article.get("lead_text") or article["article_text"]
                babel = CreatedBabel(
                    babelId=babel_id,
                    runId=self.config.runId,
                    creatorId=creator_id,
                    sourceArticleKey=article["article_key"],
                    title=article["canonical_title"],
                    text=text,
                    createdAtNs=time.time_ns(),
                )
                self.database.stage_babel(
                    babel=babel,
                    content_hash=article["content_hash"],
                    event_number=event_number,
                )
                request = RecommendationRequestV1(
                    schemaVersion=1,
                    requestId=uuid5(self.config.runId, f"request:{event_number}"),
                    runId=self.config.runId,
                    creatorId=creator_id,
                    newBabelId=babel_id,
                    newSourceArticleKey=babel.sourceArticleKey,
                    title=babel.title,
                    text=babel.text,
                    historyBabelIds=self._histories.setdefault(creator_id, []),
                    candidateCount=self.config.recommendationK,
                )
                client_started = time.perf_counter_ns()
                response = client.recommend(request)
                client_ns = time.perf_counter_ns() - client_started
                candidate_actions = []
                for candidate in response.candidates:
                    related = 0.95 if (babel.sourceArticleKey, candidate.sourceArticleKey) in hidden[period] else 0.55
                    creator_slot = self._creator_slots[creator_id]
                    preference = (
                        0.7
                        if self._simulation_draw(
                            "preference",
                            period,
                            event_number,
                            creator_slot,
                            candidate.sourceArticleKey,
                            candidate.rank,
                        )
                        < 0.5
                        else 0.4
                    )
                    probabilities = action_probabilities(
                        relevance=combined_relevance(
                            relatedness_rank=related, preference_rank=preference
                        ),
                        epsilon=0.2,
                        exclusion_propensity=0.25,
                    )
                    candidate_actions.append(
                        CandidateActionV1(
                            babelId=candidate.babelId,
                            sourceArticleKey=candidate.sourceArticleKey,
                            rank=candidate.rank,
                            modelScore=candidate.modelScore,
                            action=decide_candidate(
                                probabilities,
                                draw=self._simulation_draw(
                                    "action",
                                    period,
                                    event_number,
                                    creator_slot,
                                    candidate.sourceArticleKey,
                                    candidate.rank,
                                ),
                            ),
                        )
                    )
                feedback = FeedbackEventV1(
                    schemaVersion=1,
                    eventId=uuid5(self.config.runId, f"feedback:{event_number}"),
                    requestId=request.requestId,
                    runId=self.config.runId,
                    creatorId=creator_id,
                    newBabelId=babel_id,
                    newSourceArticleKey=babel.sourceArticleKey,
                    modelId=response.modelId,
                    modelVersion=response.modelVersion,
                    embeddingSpaceId=response.embeddingSpaceId,
                    retrievalBackend=response.retrievalBackend,
                    candidateActions=candidate_actions,
                    occurredAtNs=time.time_ns(),
                )
                record = self.producer.publish(key=str(creator_id), event=feedback)
                self.database.finalize_babel(self.config.runId, babel_id, request.requestId)
                self._created.append(babel)
                self._content_hashes[babel_id] = article["content_hash"]
                self._histories[creator_id].append(babel_id)
                self._serving_vectors[babel_id] = self._frozen_vectors[babel_id]
                self._materialize_new_babel()
                self._record_recommendation(babel, response, feedback, client_ns)
                trainer_metrics = self.trainer.metrics
                lag = max(
                    0, event_number + 1 - int(trainer_metrics["processedEvents"])
                )
                current_version = int(trainer_metrics["optimizerSteps"])
                self.database.update_metrics(
                    self.config.runId,
                    created_babel_count=len(self._created),
                    feedback_count=event_number + 1,
                    event_rate=(event_number + 1) / max(time.monotonic() - started, 1e-6),
                    kafka_offset=record.offset + 1,
                    kafka_lag=lag,
                    trainer_steps=current_version,
                    rolling_rank_loss=trainer_metrics["rollingRankLoss"],
                )
                if current_version > self._last_logged_training_step:
                    self._last_logged_training_step = current_version
                    step_metrics = {}
                    if self.trainer.last_step_time_ms is not None:
                        step_metrics["stepTimeMs"] = self.trainer.last_step_time_ms
                    self.database.append_activity(
                        _activity(
                            self.config.runId,
                            component="training",
                            event="online_training_progress",
                            message=f"Online trainer reached step {current_version}.",
                            metrics=step_metrics,
                            details={
                                "kind": "training",
                                "trainerStep": current_version,
                                "rollingRankLoss": trainer_metrics["rollingRankLoss"],
                            },
                        )
                    )
                self.database.append_activity(
                    _activity(
                        self.config.runId,
                        component="feedback",
                        event="feedback_acknowledged",
                        message=f"Feedback acknowledged at Kafka offset {record.offset}.",
                        metrics={"kafkaLag": lag},
                        details={
                            "kind": "feedback",
                            "kafkaOffset": record.offset,
                            "kafkaLag": lag,
                        },
                    )
                )
                if (
                    current_version >= self._last_sync_version + self.config.syncEverySteps
                ):
                    self._persist_and_activate(synchronize=True)
        finally:
            client.close()

    def _export_child_model(
        self, *, version: int
    ) -> tuple[ModelManifestV1 | ModelManifestV2, list[VectorRecord]]:
        child_id = uuid5(self.config.runId, "immutable-child-model")
        if not self.scale_run:
            child = export_immutable_child(
                Path(self.config.artifactRoot) / str(self.config.runId) / "models",
                model=self.model,
                parent=self.starting_model,
                registry=self.registry,
                run_id=self.config.runId,
                child_model_id=child_id,
                label=f"Post-run model {str(self.config.runId)[:8]}",
                training_examples=self.trainer.processed_events,
            )
            child_root = (
                Path(self.config.artifactRoot)
                / str(self.config.runId)
                / "models"
                / f"model-{child_id}"
            )
            self.database.register_child(child, child_root / child.checkpointPath)
            return child, []
        if not isinstance(self.starting_model, ModelManifestV2):
            raise TypeError("scale child requires a ModelManifestV2 parent")
        child_records = [
            VectorRecord(
                babel=record.babel,
                catalogContentHash=record.catalogContentHash,
                embeddingSpaceId=self.starting_model.embeddingSpace.embeddingSpaceId,
                servingModelId=child_id,
                materializedModelVersion=version,
                vector=tuple(
                    float(value) for value in self._serving_vectors[record.babel.babelId]
                ),
            )
            for record in self._records
        ]
        snapshot_sha = _snapshot_sha(child_records)
        state_bytes = (
            json.dumps(
                self.model.state_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        transfer = self.model.state_dict().get("transferState", {})
        probe_vector = transfer.get("queryVector", [1.0] + [0.0] * 99)
        exported = export_real_qwen_child(
            Path(self.config.artifactRoot) / str(self.config.runId) / "models",
            parent=self.starting_model,
            run_id=self.config.runId,
            child_model_id=child_id,
            label=f"Post-run real Qwen model {str(self.config.runId)[:8]}",
            online_state=state_bytes,
            processed_feedback_events=self.trainer.processed_events,
            model_version=version,
            vector_snapshot_sha256=snapshot_sha,
            probe=KnownVectorProbeV1(
                schemaVersion=1,
                inputVector=probe_vector,
                expectedSemanticSha256=semantic_vector_sha256(probe_vector),
            ),
            registry=self.registry,
        )
        self.database.register_real_child(exported.descriptor, exported.descriptor_path)
        return exported.descriptor.childManifest, child_records

    def _finalize(self) -> ModelManifestV1 | ModelManifestV2:
        self.database.transition(self.config.runId, "draining_feedback")
        self._trainer_stop.set()
        if self._trainer_thread is not None:
            self._trainer_thread.join(timeout=10.0)
        end_offsets = self.scoped_consumer.high_watermarks()
        self.trainer.drain_to(end_offsets)
        self.database.transition(self.config.runId, "checkpointing")
        checkpoint = self.trainer.checkpoint_and_commit()
        loaded_checkpoint = load_latest_checkpoint(checkpoint.parent)
        if loaded_checkpoint is None:
            raise RuntimeError("final online checkpoint is missing")
        ranges = [
            OffsetRange(partition, self.start_offsets.get(partition, 0), end)
            for partition, end in sorted(end_offsets.items())
        ]
        self.database.transition(self.config.runId, "exporting_interactions")
        export_offset_ranges(
            self.scoped_consumer,
            ranges,
            Path(self.config.artifactRoot) / str(self.config.runId) / "feedback",
            expected_edges=(
                self.database.canonical_edges(self.config.runId)
                if isinstance(self.config, RunConfigV2)
                else None
            ),
        )
        version = self.trainer.training_version
        if version > self._last_sync_version:
            self._persist_and_activate(synchronize=True)
        child_version = max(1, version) if self.scale_run else version
        child, child_records = self._export_child_model(version=child_version)
        if child_records:
            child_sha = _snapshot_sha(child_records)
            self.database.insert_vectors(child_records)
            self.database.activate_embedding_state(
                run_id=self.config.runId,
                model_id=child.modelId,
                model_version=child_version,
                embedding_space_id=child.embeddingSpace.embeddingSpaceId,
                pgvector_sha256=child_sha,
                backend_sha256=child_sha,
            )
            child_state = MaterializedServingState(
                run_id=self.config.runId,
                model_id=child.modelId,
                model_version=child_version,
                embedding_space_id=child.embeddingSpace.embeddingSpaceId,
                pgvector_snapshot_sha256=child_sha,
                backend_snapshot_sha256=child_sha,
            )
            self.serving.apply_sync(
                selected_model_id=child.modelId,
                materialized_state=child_state,
                candidate_index=self.index,
                vector_records=child_records,
            )
            self._records = child_records
        self.database.update_metrics(
            self.config.runId,
            kafka_lag=0,
            trainer_steps=self.trainer.global_step,
            rolling_rank_loss=self.trainer.metrics["rollingRankLoss"],
            checkpoint_path=str(checkpoint),
            checkpoint_sha256=loaded_checkpoint.manifest_sha256,
            serving_synced=True,
            active_model_id=child.modelId,
            active_model_version=child_version,
        )
        self.database.transition(self.config.runId, "completed")
        self.database.append_activity(
            lifecycle_activity(
                self.config.runId,
                "run_completed",
                f"Run completed; immutable child {child.modelId} is selectable.",
                metrics={"feedbackCount": self.trainer.processed_events, "modelVersion": child_version},
            )
        )
        self.producer.close()
        self.scoped_consumer.close()
        return child

    def _load_starting_online_state(self) -> dict[str, Any]:
        if self.scale_run:
            path = self.starting_artifact.online_state_path
            if path is None:
                return {}
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("selected real child online state is invalid") from error
            if not isinstance(state, dict):
                raise RuntimeError("selected real child online state is invalid")
            return state
        try:
            state = json.loads(self.starting_artifact.checkpoint_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}

    def _build_model_registry(self) -> ModelRegistry:
        registry = ModelRegistry()
        if self.scale_run:
            original = self.model_lineage[0].manifest
            if not isinstance(original, ModelManifestV2):
                raise TypeError("V2 lineage must begin with the real Qwen original")
            registry.register_real_original(original)
        else:
            registry.register_original(self.model_lineage[0].manifest)
        for artifact in self.model_lineage[1:]:
            registry.register_child(artifact.manifest)
        if registry.get(self.starting_model.modelId) != self.starting_model:
            raise ValueError("selected model is not the final immutable lineage member")
        return registry

    def run(self) -> ModelManifestV1 | ModelManifestV2:
        self.database.append_activity(
            lifecycle_activity(
                self.config.runId,
                "run_started",
                f"Started deterministic {self.config.creatorCount}-creator Friday demo.",
                metrics={"creatorCount": self.config.creatorCount},
            )
        )
        plan = self._plan()
        frozen = self._encode_plan_vectors(plan)
        self._frozen_vectors = {key: value.copy() for key, value in frozen.items()}
        query = np.mean(np.stack(list(frozen.values())), axis=0)
        self.model = NumpyWorkingModel(frozen, query_vector=query, learning_rate=0.05)
        starting_state = self._load_starting_online_state()
        if isinstance(starting_state, dict) and isinstance(
            starting_state.get("transferState"), dict
        ):
            self.model.load_transfer_state(starting_state["transferState"])
        self.registry = self._build_model_registry()
        self.index = PgvectorCandidateIndex(self.database.query_candidates)
        empty_sha = _snapshot_sha([])
        initial_state = self._state(0, empty_sha)
        self.database.activate_embedding_state(
            run_id=self.config.runId,
            model_id=self.starting_model.modelId,
            model_version=0,
            embedding_space_id=self.starting_model.embeddingSpace.embeddingSpaceId,
            pgvector_sha256=empty_sha,
            backend_sha256=empty_sha,
        )
        self.serving = self._create_serving_state(initial_state, [])
        self.source_vector_resolver = (
            SourceVectorResolver(
                self.qwen_encoder,
                load_active=self.database.load_active_source_vector,
                capacity=max(512, min(10_000, self.config.creatorCount * 10)),
            )
            if self.scale_run
            else None
        )
        self.synchronizer = AtomicSynchronizer(
            Path(self.config.stateRoot) / str(self.config.runId) / "sync",
            serving_state=self.serving,
        )
        self._server = _UvicornThread(
            create_app(
                self.serving,
                source_vector_resolver=self.source_vector_resolver,
            ),
            host="127.0.0.1",
            port=self.recommendation_port,
        )
        self._server.start()
        self.producer = KafkaFeedbackProducer(self.kafka_bootstrap_servers)
        raw_consumer = KafkaFeedbackConsumer(
            self.kafka_bootstrap_servers,
            group_id=f"{self.config.kafkaGroup}.{self.config.runId}",
        )
        self.start_offsets = isolate_new_run_offsets(raw_consumer)
        self.scoped_consumer = RunScopedConsumer(raw_consumer, run_id=self.config.runId)
        self.trainer = OnlineTrainer(
            model=self.model,
            consumer=self.scoped_consumer,
            checkpoint_root=Path(self.config.stateRoot) / str(self.config.runId) / "checkpoints",
            identity=CheckpointIdentity(
                run_id=self.config.runId,
                model_id=self.starting_model.modelId,
                embedding_space_id=self.starting_model.embeddingSpace.embeddingSpaceId,
            ),
        )
        self._trainer_thread = threading.Thread(
            target=self.trainer.run_until_stopped,
            kwargs={
                "stop_requested": self._trainer_stop.is_set,
                "checkpoint_every_events": self.config.checkpointEveryEvents,
            },
            daemon=True,
        )
        self._trainer_thread.start()
        if isinstance(self.config, RunConfigV2):
            self._simulate_scaled(plan, self._hidden_edges())
            telemetry = self.source_vector_resolver.telemetry()
            self.database.update_metrics(
                self.config.runId,
                source_vector_qwen_encode_count=telemetry.qwen_encode,
                source_vector_cache_hit_count=telemetry.cache_hit,
                source_vector_pgvector_load_count=telemetry.pgvector_load,
                source_vector_eviction_count=telemetry.evictions,
            )
        else:
            self._simulate(plan, self._hidden_edges())
        return self._finalize()


class WorkerManager:
    """Starts one persisted run asynchronously; dashboard remains the operator surface."""

    def __init__(
        self,
        *,
        database: RuntimeDatabase,
        dataset_bundle: DatasetBundle,
        runtime_factory: Callable[[Any, threading.Event], FridayDemoRuntime],
        placement_factory: Callable[[UUID], Any] | None = None,
    ) -> None:
        self.database = database
        self.dataset_bundle = dataset_bundle
        self.runtime_factory = runtime_factory
        self.placement_factory = placement_factory
        self._lock = threading.Lock()
        self._run_id: UUID | None = None
        self._runtime: FridayDemoRuntime | None = None
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._placement: Any | None = None
        self._phase = "idle"
        self._failure: str | None = None

    def start(self, run_id: UUID) -> None:
        persisted = self.database.load_run(run_id)
        if persisted.status != "starting":
            raise RuntimeError("persisted run is not in starting state")
        expected_identity = (
            persisted.config.datasetRepo,
            persisted.config.datasetConfig,
            persisted.config.datasetRevision,
        )
        loaded_identity = (
            self.dataset_bundle.dataset_repository,
            self.dataset_bundle.dataset_config,
            self.dataset_bundle.dataset_revision,
        )
        if expected_identity != loaded_identity:
            raise RuntimeError(
                "online experiment launch and loaded dataset bundle identity differ"
            )
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("another online run is active")
            if self._runtime is not None:
                self._runtime.stop_serving()
            stop_event = threading.Event()
            self._run_id = run_id
            self._stop_event = stop_event
            # Claim synchronously so the dashboard cannot receive Start success
            # before the persisted run is ready to accept graceful stop.
            self.database.claim_run(run_id)
            self._placement = (
                None if self.placement_factory is None else self.placement_factory(run_id)
            )
            self._phase = "starting"
            self._failure = None

            def execute() -> None:
                try:
                    runtime = self.runtime_factory(persisted.config, stop_event)
                    self._runtime = runtime
                    with self._lock:
                        self._phase = "running"
                    runtime.run()
                    with self._lock:
                        self._phase = "completed"
                except Exception as error:
                    with self._lock:
                        self._phase = "failed"
                        self._failure = str(error)
                    self.database.transition(run_id, "failed", failure=str(error)[:1000])
                    try:
                        self.database.append_activity(
                            lifecycle_activity(run_id, "run_failed", f"Online run failed: {error}")
                        )
                    except Exception:
                        pass

            self._thread = threading.Thread(target=execute, daemon=True)
            self._thread.start()

    def request_stop(self, run_id: UUID) -> None:
        with self._lock:
            if self._run_id != run_id or self._stop_event is None:
                raise KeyError(run_id)
            self._stop_event.set()
            self._phase = "stopping"
            if self._runtime is not None:
                self._runtime.request_stop()

    @property
    def active_runtime(self) -> FridayDemoRuntime | None:
        return self._runtime

    @property
    def placement(self) -> Any:
        if self._placement is None:
            raise RuntimeError("no run-scoped same-process placement is available")
        return self._placement

    @property
    def status(self) -> dict[str, str | None]:
        with self._lock:
            return {
                "runId": None if self._run_id is None else str(self._run_id),
                "phase": self._phase,
                "failure": self._failure,
            }


__all__ = [
    "FridayDemoRuntime",
    "RunScopedConsumer",
    "WorkerManager",
    "isolate_new_run_offsets",
]
