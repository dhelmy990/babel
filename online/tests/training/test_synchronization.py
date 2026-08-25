from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import numpy as np

from babel_online.contracts import ModelManifestV1
from babel_online.model.candidate_index import MaterializedServingState
from babel_online.model.artifact import load_artifact
from babel_online.model.registry import ModelRegistry
from babel_online.training.synchronization import (
    AtomicSynchronizer,
    export_immutable_child,
)
from babel_online.training.working import NumpyWorkingModel

from .test_checkpoint import working_model


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUN_ID = UUID("00000000-0000-5000-8000-000000000001")
CHILD_ID = UUID("00000000-0000-5000-8000-000000000099")


class _Index:
    backend = "pgvector"


class _Serving:
    def __init__(self, sync_root: Path) -> None:
        self.sync_root = sync_root
        self.calls = []

    def apply_sync(self, **kwargs) -> None:
        version = kwargs["materialized_state"].model_version
        assert (self.sync_root / f"sync-v{version:08d}").is_dir()
        assert not list(self.sync_root.glob("*.partial"))
        self.calls.append(kwargs)


def _original() -> ModelManifestV1:
    return ModelManifestV1.model_validate_json(
        (REPOSITORY_ROOT / "fixtures/online/tiny/original-model.json").read_text()
    )


def test_sync_publishes_complete_version_before_atomic_serving_swap(tmp_path) -> None:
    model = working_model()
    serving = _Serving(tmp_path)
    state = MaterializedServingState(
        run_id=RUN_ID,
        model_id=_original().modelId,
        model_version=1,
        embedding_space_id=_original().embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    stale = tmp_path / "sync-v00000001.partial"
    stale.mkdir()
    (stale / "interrupted").write_text("not complete")

    artifact = AtomicSynchronizer(tmp_path, serving_state=serving).publish(
        model=model,
        selected_model_id=_original().modelId,
        materialized_state=state,
        candidate_index=_Index(),
        vector_records=[],
    )

    assert artifact.path.name == "sync-v00000001"
    assert artifact.state_sha256 == hashlib.sha256(
        (artifact.path / "working-state.json").read_bytes()
    ).hexdigest()
    assert serving.calls[0]["materialized_state"] == state


def test_final_child_is_immutable_and_keeps_parent_lineage(tmp_path) -> None:
    fixture = REPOSITORY_ROOT / "fixtures/online/tiny/original-model.json"
    fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    original = _original()
    registry = ModelRegistry()
    registry.register_original(original)
    model: NumpyWorkingModel = working_model()
    stale = tmp_path / f"model-{CHILD_ID}.partial"
    stale.mkdir()
    (stale / "interrupted").write_text("not complete")

    child = export_immutable_child(
        tmp_path,
        model=model,
        parent=original,
        registry=registry,
        run_id=RUN_ID,
        child_model_id=CHILD_ID,
        label="Friday demo trained child",
        training_examples=1,
    )

    artifact = tmp_path / f"model-{CHILD_ID}"
    assert child.parentModelId == original.modelId
    assert child.producingRunId == RUN_ID
    assert child.immutable is True
    assert child.checkpointSha256 == hashlib.sha256(
        (artifact / "working-state.json").read_bytes()
    ).hexdigest()
    assert registry.select(original.modelId) == original
    assert registry.select(CHILD_ID) == child
    assert load_artifact(artifact).manifest == child
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == fixture_sha
    assert not list(tmp_path.glob("*.partial"))
    assert (artifact / "working-state.json").stat().st_mode & 0o222 == 0
