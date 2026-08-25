"""Atomic, complete Accelerate checkpoints for trusted training runs."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CheckpointManifest:
    schema_version: int
    model_id: str
    model_revision: str
    dataset_revision: str
    global_step: int
    epoch: int
    loader_state: Mapping[str, Any]
    training_config: Mapping[str, Any]
    metrics: Mapping[str, Any]

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "CheckpointManifest":
        expected = {
            "schema_version",
            "model_id",
            "model_revision",
            "dataset_revision",
            "global_step",
            "epoch",
            "loader_state",
            "training_config",
            "metrics",
        }
        if set(document) != expected:
            raise ValueError("checkpoint manifest has missing or unknown fields")
        return cls(**{name: document[name] for name in expected})


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as source:
                os.fsync(source.fileno())
    directory_fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def save_checkpoint(
    path: str | Path,
    *,
    accelerator: object,
    manifest: CheckpointManifest,
) -> Path:
    """Save registered model/optimizer/scheduler/RNG and metadata atomically."""
    target = Path(path)
    partial = target.with_name(target.name + ".partial")
    if target.exists():
        raise FileExistsError(f"checkpoint already exists: {target}")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    accelerator_state = partial / "accelerator_state"
    accelerator.save_state(str(accelerator_state))
    manifest_path = partial / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as output:
        json.dump(asdict(manifest), output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    _fsync_tree(partial)
    os.replace(partial, target)
    parent_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return target


def load_checkpoint(
    path: str | Path,
    *,
    accelerator: object,
    expected_model_revision: str,
    expected_dataset_revision: str,
    expected_schema_version: int = CHECKPOINT_SCHEMA_VERSION,
) -> CheckpointManifest:
    """Restore one complete checkpoint after immutable identity checks."""
    target = Path(path)
    manifest_path = target / "manifest.json"
    if not target.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(f"complete checkpoint not found: {target}")
    with manifest_path.open(encoding="utf-8") as source:
        document = json.load(source)
    if not isinstance(document, dict):
        raise ValueError("checkpoint manifest must be a JSON object")
    manifest = CheckpointManifest.from_dict(document)
    if (
        manifest.schema_version != expected_schema_version
        or manifest.model_revision != expected_model_revision
        or manifest.dataset_revision != expected_dataset_revision
    ):
        raise ValueError("checkpoint identity mismatch")
    accelerator.load_state(str(target / "accelerator_state"))
    return manifest


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointManifest",
    "load_checkpoint",
    "save_checkpoint",
]
