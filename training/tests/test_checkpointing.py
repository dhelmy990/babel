from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_training.checkpointing import (  # noqa: E402
    CheckpointManifest,
    load_checkpoint,
    save_checkpoint,
)


MODEL_REVISION = "9" * 40
DATASET_REVISION = "8" * 40


class RecordingAccelerator:
    def __init__(self) -> None:
        self.saved: Path | None = None
        self.loaded: Path | None = None

    def save_state(self, path: str) -> None:
        self.saved = Path(path)
        self.saved.mkdir(parents=True)
        (self.saved / "state.txt").write_text("complete", encoding="utf-8")

    def load_state(self, path: str) -> None:
        self.loaded = Path(path)
        assert (self.loaded / "state.txt").read_text(encoding="utf-8") == "complete"


def manifest() -> CheckpointManifest:
    return CheckpointManifest(
        schema_version=1,
        model_id="tiny/model",
        model_revision=MODEL_REVISION,
        dataset_revision=DATASET_REVISION,
        global_step=3,
        epoch=1,
        loader_state={"position": 4},
        training_config={"lambda_rel": 0.5},
        metrics={"validation_cosine": 0.75},
    )


def test_atomic_checkpoint_round_trip_ignores_interrupted_partial(tmp_path: Path) -> None:
    accelerator = RecordingAccelerator()
    target = tmp_path / "checkpoint"

    save_checkpoint(target, accelerator=accelerator, manifest=manifest())
    (tmp_path / "checkpoint.partial").mkdir()
    (tmp_path / "checkpoint.partial" / "junk").write_text("interrupted")
    restored = load_checkpoint(
        target,
        accelerator=accelerator,
        expected_model_revision=MODEL_REVISION,
        expected_dataset_revision=DATASET_REVISION,
    )

    assert restored == manifest()
    assert accelerator.saved == target.with_name("checkpoint.partial") / "accelerator_state"
    assert accelerator.loaded == target / "accelerator_state"
    document = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert document["loader_state"] == {"position": 4}


@pytest.mark.parametrize(
    ("model_revision", "dataset_revision", "schema_version"),
    [
        ("7" * 40, DATASET_REVISION, 1),
        (MODEL_REVISION, "7" * 40, 1),
        (MODEL_REVISION, DATASET_REVISION, 2),
    ],
)
def test_checkpoint_rejects_revision_or_schema_mismatch(
    tmp_path: Path,
    model_revision: str,
    dataset_revision: str,
    schema_version: int,
) -> None:
    accelerator = RecordingAccelerator()
    target = tmp_path / "checkpoint"
    save_checkpoint(target, accelerator=accelerator, manifest=manifest())

    with pytest.raises(ValueError, match="identity mismatch"):
        load_checkpoint(
            target,
            accelerator=accelerator,
            expected_model_revision=model_revision,
            expected_dataset_revision=dataset_revision,
            expected_schema_version=schema_version,
        )
