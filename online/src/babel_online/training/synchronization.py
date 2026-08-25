"""Atomic working-copy synchronization and immutable child export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from babel_online.contracts import ModelManifestV1


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_complete_directory(
    partial: Path, final: Path, files: dict[str, bytes]
) -> None:
    if final.exists():
        raise FileExistsError(f"artifact destination already exists: {final.name}")
    if partial.is_dir():
        shutil.rmtree(partial)
    elif partial.exists():
        partial.unlink()
    partial.mkdir(parents=True)
    for name, payload in files.items():
        path = partial / name
        path.write_bytes(payload)
        with path.open("rb") as source:
            os.fsync(source.fileno())
    directory_fd = os.open(partial, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    os.replace(partial, final)
    parent_fd = os.open(final.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


@dataclass(frozen=True, slots=True)
class SyncArtifact:
    path: Path
    model_version: int
    state_sha256: str


class AtomicSynchronizer:
    def __init__(self, root: str | Path, *, serving_state: Any) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.serving_state = serving_state

    def publish(
        self,
        *,
        model: Any | None = None,
        model_state: dict[str, Any] | None = None,
        selected_model_id: UUID,
        materialized_state: Any,
        candidate_index: Any,
        vector_records: list[Any],
    ) -> SyncArtifact:
        version = int(materialized_state.model_version)
        if version < 0 or materialized_state.model_id != selected_model_id:
            raise ValueError("sync model identity/version is invalid")
        if (model is None) == (model_state is None):
            raise ValueError("sync requires exactly one captured model state")
        state_bytes = _canonical_json(
            model_state if model_state is not None else model.state_dict()
        )
        state_sha = hashlib.sha256(state_bytes).hexdigest()
        name = f"sync-v{version:08d}"
        final = self.root / name
        _write_complete_directory(
            self.root / f"{name}.partial",
            final,
            {
                "working-state.json": state_bytes,
                "sync.json": _canonical_json(
                    {
                        "schemaVersion": 1,
                        "modelId": str(selected_model_id),
                        "modelVersion": version,
                        "workingStatePath": "working-state.json",
                        "workingStateSha256": state_sha,
                    }
                ),
            },
        )
        self.serving_state.apply_sync(
            selected_model_id=selected_model_id,
            materialized_state=materialized_state,
            candidate_index=candidate_index,
            vector_records=vector_records,
        )
        return SyncArtifact(final, version, state_sha)


def export_immutable_child(
    root: str | Path,
    *,
    model: Any,
    parent: ModelManifestV1,
    registry: Any,
    run_id: UUID,
    child_model_id: UUID,
    label: str,
    training_examples: int,
) -> ModelManifestV1:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    name = f"model-{child_model_id}"
    final = root_path / name
    state_bytes = _canonical_json(model.state_dict())
    state_sha = hashlib.sha256(state_bytes).hexdigest()
    child = ModelManifestV1(
        schemaVersion=1,
        modelId=child_model_id,
        label=label,
        parentModelId=parent.modelId,
        producingRunId=run_id,
        encoderRepo=parent.encoderRepo,
        encoderRevision=parent.encoderRevision,
        datasetRepo=parent.datasetRepo,
        datasetRevision=parent.datasetRevision,
        environmentSequence=parent.environmentSequence,
        trainingExamples=training_examples,
        checkpointPath="working-state.json",
        checkpointSha256=state_sha,
        embeddingSpace=parent.embeddingSpace,
        immutable=True,
    )
    _write_complete_directory(
        root_path / f"{name}.partial",
        final,
        {
            "working-state.json": state_bytes,
            "manifest.json": _canonical_json(child.model_dump(mode="json")),
        },
    )
    for path in final.iterdir():
        path.chmod(0o444)
    final.chmod(0o555)
    registry.register_child(child)
    return child


__all__ = ["AtomicSynchronizer", "SyncArtifact", "export_immutable_child"]
