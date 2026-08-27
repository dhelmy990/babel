from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import numpy as np

from babel_online.contracts import ModelManifestV1
from babel_online.feedback import InMemoryFeedbackBus, TopicPartition
from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.registry import ModelRegistry
from babel_online.observable import CreatedBabel, VectorRecord
from babel_online.runtime import OnlineDemoSupervisor
from babel_online.serving.state import ServingState
from babel_online.training import (
    AtomicSynchronizer,
    OnlineTrainer,
    export_immutable_child,
)

from tests.training.test_checkpoint import working_model
from tests.training.test_pairs import EXCLUDED, IGNORED, INCLUDED, event_with_three_actions


ROOT = Path(__file__).resolve().parents[3]
RUN = UUID("00000000-0000-5000-8000-000000000001")
CHILD = UUID("00000000-0000-5000-8000-000000000099")
ITEMS = (INCLUDED, EXCLUDED, IGNORED)


def _original() -> ModelManifestV1:
    return ModelManifestV1.model_validate_json(
        (ROOT / "fixtures/online/tiny/original-model.json").read_text()
    )


def _records(original, *, version: int, model=None) -> list[VectorRecord]:
    rows = []
    for number, item_id in enumerate(ITEMS, start=1):
        if model is None:
            vector = np.zeros(100, dtype=np.float32)
            vector[number] = 1.0
        else:
            vector = model.materialized_vector(item_id)
        rows.append(
            VectorRecord(
                babel=CreatedBabel(
                    babelId=item_id,
                    runId=RUN,
                    creatorId=UUID(f"00000000-0000-5000-8000-{number + 100:012d}"),
                    sourceArticleKey=f"enwiki:{number + 100}",
                    title=f"Candidate {number}",
                    text=f"Observable candidate text {number}.",
                    createdAtNs=number,
                ),
                catalogContentHash=f"{number:x}" * 64,
                embeddingSpaceId=original.embeddingSpace.embeddingSpaceId,
                servingModelId=original.modelId,
                materializedModelVersion=version,
                vector=tuple(float(value) for value in vector),
            )
        )
    return rows


def _state(original, version: int) -> MaterializedServingState:
    return MaterializedServingState(
        run_id=RUN,
        model_id=original.modelId,
        model_version=version,
        embedding_space_id=original.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="b" * 64,
    )


def test_feedback_changes_working_parameters_syncs_child_and_restarts(tmp_path) -> None:
    original = _original()
    registry = ModelRegistry()
    registry.register_original(original)
    initial_records = _records(original, version=0)
    serving = ServingState(
        registry=registry,
        selected_model_id=original.modelId,
        materialized_state=_state(original, 0),
        candidate_index=InMemoryCreatedBabelIndex(initial_records),
        vector_records=initial_records,
    )
    bus = InMemoryFeedbackBus()
    event = event_with_three_actions()
    bus.publish(key=str(event.creatorId), event=event)
    model = working_model()
    frozen_sha = hashlib.sha256(model.frozen_bytes()).hexdigest()
    trainer = OnlineTrainer(
        model=model,
        consumer=bus.consumer(group_id="trainer", auto_commit=False),
        checkpoint_root=tmp_path / "checkpoints",
    )
    synchronizer = AtomicSynchronizer(tmp_path / "sync", serving_state=serving)

    def publish_sync():
        records = _records(original, version=1, model=model)
        return synchronizer.publish(
            model=model,
            selected_model_id=original.modelId,
            materialized_state=_state(original, 1),
            candidate_index=InMemoryCreatedBabelIndex(records),
            vector_records=records,
        )

    supervisor = OnlineDemoSupervisor(
        producer=bus,
        trainer=trainer,
        feedback_source=bus,
        export_root=tmp_path / "export",
        publish_sync=publish_sync,
        export_child=lambda: export_immutable_child(
            tmp_path / "models",
            model=model,
            parent=original,
            registry=registry,
            run_id=RUN,
            child_model_id=CHILD,
            label="Friday demo online child",
            training_examples=trainer.processed_events,
        ),
    )

    result = supervisor.graceful_stop()

    assert trainer.global_step == 1
    assert result.sync_artifact.model_version == 1
    assert serving.snapshot().materialized_state.model_version == 1
    assert registry.select(CHILD).parentModelId == original.modelId
    assert registry.select(original.modelId) == original
    assert hashlib.sha256(model.frozen_bytes()).hexdigest() == frozen_sha
    restarted = OnlineTrainer(
        model=working_model(),
        consumer=bus.consumer(group_id="trainer", auto_commit=False),
        checkpoint_root=tmp_path / "checkpoints",
    )
    restarted.restore_latest()
    assert restarted.next_offsets == {TopicPartition("babel.feedback.v1", 0): 1}
    assert restarted.process_available() == 0
