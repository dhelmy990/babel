"""Real Slice 3 trainer/Kafka interference adapters for the Friday matrix."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import UUID

import numpy as np


def parse_vector(value: str) -> tuple[float, ...]:
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ValueError("pgvector text must be bracketed")
    return tuple(float(part) for part in text[1:-1].split(",") if part)


def load_feedback_events(path: str | Path) -> tuple[Any, ...]:
    from babel_online.contracts import FeedbackEventV1

    transport_fields = {"topic", "partition", "offset", "key"}
    events = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        document = json.loads(line)
        events.append(
            FeedbackEventV1.model_validate(
                {key: value for key, value in document.items() if key not in transport_fields}
            )
        )
    if not events:
        raise ValueError("feedback replay cannot be empty")
    return tuple(events)


class TimedTrainingModel:
    """Measure exactly the model call made by the real OnlineTrainer."""

    def __init__(self, model: Any, recorder: Any, *, monotonic_ns=time.monotonic_ns) -> None:
        self._model = model
        self._recorder = recorder
        self._clock = monotonic_ns
        self._step = 0

    def train_pairs(self, pairs: Any) -> float:
        started = self._clock()
        result = self._model.train_pairs(pairs)
        duration = self._clock() - started
        self._step += 1
        self._recorder.trainer_step(step=self._step, duration_ns=duration)
        return float(result)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


def synchronize_if_due(
    *,
    trainer: Any,
    last_sync: int,
    every_steps: int,
    operation: Callable[[Any], None],
    telemetry: Any,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> int:
    """Publish and identify one exact trainer snapshot captured under its lock."""
    captured = trainer.capture_sync_state()
    if captured.version < last_sync + every_steps:
        return last_sync
    started = monotonic_ns()
    operation(captured)
    telemetry.synchronization(
        version=captured.version,
        duration_ns=monotonic_ns() - started,
    )
    return captured.version


def isolated_kafka_lag(
    *,
    high_watermarks: dict[Any, int],
    next_offsets: dict[Any, int],
    start_offsets: dict[Any, int],
) -> int:
    """Report only records published after this condition's frozen watermark."""
    return sum(
        max(
            0,
            high - max(start_offsets.get(partition, 0), next_offsets.get(partition, 0)),
        )
        for partition, high in high_watermarks.items()
    )


def _vector_sha(vector: Sequence[float]) -> str:
    return hashlib.sha256(np.asarray(vector, dtype="<f4").tobytes()).hexdigest()


def _snapshot_sha(records: Sequence[Any]) -> str:
    from babel_online.contracts import canonical_pgvector_snapshot_sha256

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


class AtomicSyncOperation:
    """Materialize captured vectors and execute the real atomic sync publisher."""

    def __init__(
        self,
        *,
        template_records: Sequence[Any],
        selected_model_id: UUID,
        candidate_index: Any,
        synchronizer: Any,
    ) -> None:
        if not template_records:
            raise ValueError("sync requires a nonempty created-Babel universe")
        self.template_records = tuple(template_records)
        self.selected_model_id = selected_model_id
        self.candidate_index = candidate_index
        self.synchronizer = synchronizer

    def __call__(self, captured: Any) -> None:
        from babel_online.model import MaterializedServingState
        from babel_online.observable import VectorRecord

        expected = {record.babel.babelId for record in self.template_records}
        if set(captured.materialized_vectors) != expected:
            raise ValueError("captured vectors differ from the created-Babel universe")
        records = [
            VectorRecord(
                babel=record.babel,
                catalogContentHash=record.catalogContentHash,
                embeddingSpaceId=record.embeddingSpaceId,
                servingModelId=record.servingModelId,
                materializedModelVersion=captured.version,
                vector=tuple(
                    float(value)
                    for value in np.asarray(
                        captured.materialized_vectors[record.babel.babelId], dtype="<f4"
                    )
                ),
            )
            for record in self.template_records
        ]
        snapshot_sha = _snapshot_sha(records)
        first = records[0]
        state = MaterializedServingState(
            run_id=first.babel.runId,
            model_id=self.selected_model_id,
            model_version=captured.version,
            embedding_space_id=first.embeddingSpaceId,
            pgvector_snapshot_sha256=snapshot_sha,
            backend_snapshot_sha256=snapshot_sha,
        )
        self.synchronizer.publish(
            model_state=captured.model_state,
            selected_model_id=self.selected_model_id,
            materialized_state=state,
            candidate_index=self.candidate_index,
            vector_records=records,
        )


def build_atomic_sync_operation(
    *,
    dsn: str,
    run_id: UUID,
    model_id: UUID,
    model_version: int,
    pgvector_snapshot_sha256: str,
    sync_root: str | Path,
) -> AtomicSyncOperation:
    """Build an isolated real synchronizer from the active PostgreSQL snapshot."""
    import psycopg

    from babel_online.model import InMemoryCreatedBabelIndex, MaterializedServingState
    from babel_online.model.registry import ModelRegistry
    from babel_online.observable import VectorRecord
    from babel_online.runtime.database import RuntimeDatabase
    from babel_online.serving import ServingState
    from babel_online.training import AtomicSynchronizer

    database = RuntimeDatabase(dsn)
    created = database.created_babels(run_id)
    vectors = load_active_vectors(
        dsn,
        run_id=run_id,
        model_id=model_id,
        model_version=model_version,
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT babel_id, catalog_content_hash FROM experiment_babels "
            "WHERE run_id=%s AND finalized_at IS NOT NULL",
            (run_id,),
        )
        content_hashes = {UUID(str(row[0])): str(row[1]) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT embedding_space_id FROM run_embedding_states WHERE run_id=%s",
            (run_id,),
        )
        state_row = cursor.fetchone()
    if state_row is None:
        raise ValueError("run does not have an active embedding state")
    embedding_space_id = UUID(str(state_row[0]))
    records = [
        VectorRecord(
            babel=babel,
            catalogContentHash=content_hashes[babel.babelId],
            embeddingSpaceId=embedding_space_id,
            servingModelId=model_id,
            materializedModelVersion=model_version,
            vector=tuple(float(value) for value in vectors[babel.babelId]),
        )
        for babel in created
    ]
    registry = ModelRegistry()
    lineage = database.load_model_lineage(model_id)
    registry.register_original(lineage[0].manifest)
    for artifact in lineage[1:]:
        registry.register_child(artifact.manifest)
    candidate_index = InMemoryCreatedBabelIndex(records)
    state = MaterializedServingState(
        run_id=run_id,
        model_id=model_id,
        model_version=model_version,
        embedding_space_id=embedding_space_id,
        pgvector_snapshot_sha256=pgvector_snapshot_sha256,
        backend_snapshot_sha256=pgvector_snapshot_sha256,
    )
    serving_state = ServingState(
        registry=registry,
        selected_model_id=model_id,
        materialized_state=state,
        candidate_index=candidate_index,
        vector_records=records,
    )
    synchronizer = AtomicSynchronizer(sync_root, serving_state=serving_state)
    return AtomicSyncOperation(
        template_records=records,
        selected_model_id=model_id,
        candidate_index=candidate_index,
        synchronizer=synchronizer,
    )


def load_active_vectors(
    dsn: str,
    *,
    run_id: UUID,
    model_id: UUID,
    model_version: int,
) -> dict[UUID, np.ndarray]:
    import psycopg

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (babel_id) babel_id, embedding::text
            FROM babel_embeddings
            WHERE run_id=%s AND serving_model_id=%s
              AND materialized_model_version <= %s
            ORDER BY babel_id, materialized_model_version DESC
            """,
            (run_id, model_id, model_version),
        )
        rows = cursor.fetchall()
    vectors = {
        UUID(str(babel_id)): np.asarray(parse_vector(text), dtype="<f4")
        for babel_id, text in rows
    }
    if not vectors or any(vector.shape != (100,) for vector in vectors.values()):
        raise ValueError("active pgvector snapshot is not a nonempty 100d universe")
    return vectors


class LiveTrainingDriver:
    """Run real Kafka feedback and OnlineTrainer while the HTTP replay executes."""

    def __init__(
        self,
        *,
        dsn: str,
        kafka_bootstrap_servers: str,
        feedback_path: str | Path,
        run_id: UUID,
        model_id: UUID,
        model_version: int,
        sync_operation: Callable[[Any], None] | None = None,
        sync_every_steps: int = 50,
        publish_limit: int = 4_000,
    ) -> None:
        self.dsn = dsn
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.events = load_feedback_events(feedback_path)
        if {event.runId for event in self.events} != {run_id}:
            raise ValueError("feedback replay does not belong to the serving run")
        self.run_id = run_id
        self.model_id = model_id
        self.model_version = model_version
        self.sync_operation = sync_operation
        self.sync_every_steps = sync_every_steps
        self.publish_limit = publish_limit
        self.training_steps = 0
        self.processed_events = 0
        self.sync_count = 0

    @contextmanager
    def activate(self, condition: Any, telemetry: Any):
        if not condition.trainingEnabled:
            yield
            return

        from babel_online.feedback import KafkaFeedbackConsumer, KafkaFeedbackProducer
        from babel_online.runtime.worker import RunScopedConsumer, isolate_new_run_offsets
        from babel_online.training import NumpyWorkingModel, OnlineTrainer

        frozen = load_active_vectors(
            self.dsn,
            run_id=self.run_id,
            model_id=self.model_id,
            model_version=self.model_version,
        )
        query = np.mean(np.stack(list(frozen.values())), axis=0)
        base_model = NumpyWorkingModel(frozen, query_vector=query, learning_rate=0.05)
        measured_model = TimedTrainingModel(base_model, telemetry)
        group = f"babel-friday-performance.{condition.name}.{time.time_ns()}"
        raw_consumer = KafkaFeedbackConsumer(
            self.kafka_bootstrap_servers,
            group_id=group,
        )
        start_offsets = isolate_new_run_offsets(raw_consumer)
        consumer = RunScopedConsumer(raw_consumer, run_id=self.run_id)
        producer = KafkaFeedbackProducer(self.kafka_bootstrap_servers)
        trainer = OnlineTrainer(
            model=measured_model,
            consumer=consumer,
            checkpoint_root=Path(tempfile.mkdtemp(prefix="babel-perf-checkpoints-")),
        )
        stop = threading.Event()
        errors: list[BaseException] = []

        def train() -> None:
            try:
                trainer.run_until_stopped(
                    stop_requested=stop.is_set,
                    checkpoint_every_events=1_000_000_000,
                    poll_timeout_seconds=0.01,
                )
            except BaseException as error:  # surfaced after threads join
                errors.append(error)
                stop.set()

        def publish() -> None:
            try:
                for index in range(self.publish_limit):
                    if stop.is_set():
                        break
                    event = self.events[index % len(self.events)]
                    producer.publish(key=str(event.creatorId), event=event)
            except BaseException as error:
                errors.append(error)
                stop.set()

        def observe() -> None:
            last_sync = 0
            try:
                while not stop.wait(0.01):
                    high = consumer.high_watermarks()
                    lag = isolated_kafka_lag(
                        high_watermarks=high,
                        next_offsets=trainer.next_offsets,
                        start_offsets=start_offsets,
                    )
                    telemetry.kafka_lag(lag)
                    version = trainer.training_version
                    if (
                        condition.syncEnabled
                        and self.sync_operation is not None
                        and version >= last_sync + self.sync_every_steps
                    ):
                        previous_sync = last_sync
                        last_sync = synchronize_if_due(
                            trainer=trainer,
                            last_sync=last_sync,
                            every_steps=self.sync_every_steps,
                            operation=self.sync_operation,
                            telemetry=telemetry,
                        )
                        self.sync_count += int(last_sync != previous_sync)
            except BaseException as error:
                errors.append(error)
                stop.set()

        threads = [
            threading.Thread(target=train, daemon=True),
            threading.Thread(target=publish, daemon=True),
            threading.Thread(target=observe, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=10.0)
            self.training_steps = trainer.global_step
            self.processed_events = trainer.processed_events
            producer.close()
            consumer.close()
        if errors:
            raise RuntimeError(f"live training load failed: {errors[0]}") from errors[0]


__all__ = [
    "AtomicSyncOperation",
    "LiveTrainingDriver",
    "TimedTrainingModel",
    "build_atomic_sync_operation",
    "isolated_kafka_lag",
    "load_active_vectors",
    "load_feedback_events",
    "parse_vector",
    "synchronize_if_due",
]
