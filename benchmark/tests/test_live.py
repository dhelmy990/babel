from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np

from babel_benchmark.live import (
    AtomicSyncOperation,
    TimedTrainingModel,
    isolated_kafka_lag,
    load_feedback_events,
    parse_vector,
    synchronize_if_due,
)
from babel_online.observable import CreatedBabel, VectorRecord


class Clock:
    def __init__(self) -> None:
        self.now = 100

    def __call__(self) -> int:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.rows = []

    def trainer_step(self, *, step: int, duration_ns: int) -> None:
        self.rows.append((step, duration_ns))


class Model:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def train_pairs(self, pairs):
        self.clock.now += 37
        return 0.25

    def state_dict(self):
        return {"ok": True}


def test_timed_training_model_measures_the_actual_train_pairs_call() -> None:
    clock = Clock()
    recorder = Recorder()
    wrapped = TimedTrainingModel(Model(clock), recorder, monotonic_ns=clock)

    assert wrapped.train_pairs([object()]) == 0.25
    assert recorder.rows == [(1, 37)]
    assert wrapped.state_dict() == {"ok": True}


def test_feedback_loader_strips_transport_envelope(tmp_path: Path) -> None:
    row = {
        "schemaVersion": 1,
        "eventId": "00000000-0000-5000-8000-000000000501",
        "requestId": "00000000-0000-5000-8000-000000000401",
        "runId": "00000000-0000-5000-8000-000000000001",
        "creatorId": "00000000-0000-5000-8000-000000000101",
        "newBabelId": "00000000-0000-5000-8000-000000000301",
        "newSourceArticleKey": "enwiki:5739",
        "modelId": "00000000-0000-5000-8000-000000000002",
        "modelVersion": 0,
        "embeddingSpaceId": "00000000-0000-5000-8000-000000000003",
        "retrievalBackend": "pgvector",
        "candidateActions": [],
        "occurredAtNs": 1,
        "topic": "babel.feedback.v1",
        "partition": 0,
        "offset": 1,
        "key": "00000000-0000-5000-8000-000000000101",
    }
    path = tmp_path / "feedback.jsonl"
    path.write_text(json.dumps(row) + "\n")

    events = load_feedback_events(path)

    assert len(events) == 1
    assert events[0].eventId.hex.endswith("501")


def test_pgvector_text_parser_returns_float_values() -> None:
    assert parse_vector("[1,-0.5,2.25]") == (1.0, -0.5, 2.25)


def test_kafka_lag_excludes_records_before_the_condition_watermark() -> None:
    partition = object()

    assert isolated_kafka_lag(
        high_watermarks={partition: 8263},
        next_offsets={partition: 0},
        start_offsets={partition: 4263},
    ) == 4000


class SyncRecorder:
    def __init__(self) -> None:
        self.rows = []

    def synchronization(self, *, version: int, duration_ns: int) -> None:
        self.rows.append((version, duration_ns))


def test_due_sync_uses_one_locked_capture_for_version_and_operation() -> None:
    clock = Clock()
    captured = SimpleNamespace(version=50, model_state={"version": 50})
    trainer = SimpleNamespace(
        training_version=51,
        capture_sync_state=lambda: captured,
    )
    seen = []
    recorder = SyncRecorder()

    def operation(snapshot) -> None:
        seen.append(snapshot)
        clock.now += 23

    last_sync = synchronize_if_due(
        trainer=trainer,
        last_sync=0,
        every_steps=50,
        operation=operation,
        telemetry=recorder,
        monotonic_ns=clock,
    )

    assert last_sync == 50
    assert seen == [captured]
    assert recorder.rows == [(50, 23)]


def test_atomic_sync_operation_materializes_exact_captured_vectors() -> None:
    run_id = UUID("00000000-0000-5000-8000-000000000001")
    model_id = UUID("00000000-0000-5000-8000-000000000002")
    space_id = UUID("00000000-0000-5000-8000-000000000003")
    babel_id = UUID("00000000-0000-5000-8000-000000000301")
    record = VectorRecord(
        babel=CreatedBabel(
            babelId=babel_id,
            runId=run_id,
            creatorId=UUID("00000000-0000-5000-8000-000000000101"),
            sourceArticleKey="enwiki:5739",
            title="Created Babel",
            text="Observable text",
            createdAtNs=1,
        ),
        catalogContentHash="a" * 64,
        embeddingSpaceId=space_id,
        servingModelId=model_id,
        materializedModelVersion=0,
        vector=tuple([0.0] * 100),
    )

    class Synchronizer:
        def __init__(self) -> None:
            self.kwargs = None

        def publish(self, **kwargs):
            self.kwargs = kwargs

    synchronizer = Synchronizer()
    operation = AtomicSyncOperation(
        template_records=[record],
        selected_model_id=model_id,
        candidate_index=object(),
        synchronizer=synchronizer,
    )
    captured = SimpleNamespace(
        version=50,
        materialized_vectors={babel_id: np.ones(100, dtype=np.float32)},
        model_state={"version": 50},
    )

    operation(captured)

    published = synchronizer.kwargs
    assert published["model_state"] == {"version": 50}
    assert published["materialized_state"].model_version == 50
    assert published["vector_records"][0].materializedModelVersion == 50
    assert published["vector_records"][0].vector == tuple([1.0] * 100)
    assert len(published["materialized_state"].pgvector_snapshot_sha256) == 64
