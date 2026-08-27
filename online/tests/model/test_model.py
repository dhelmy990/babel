from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from babel_online.contracts import ModelManifestV1
from babel_online.model.context_tower import CreatorContextTower
from babel_online.model.item_tower import ItemTower
from babel_online.model.registry import DuplicateModel, ModelRegistry


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def original_manifest() -> ModelManifestV1:
    return ModelManifestV1.model_validate_json(
        (REPOSITORY_ROOT / "fixtures/online/tiny/original-model.json").read_text()
    )


def test_item_and_context_towers_are_deterministic_100d_and_identity_bound() -> None:
    original = original_manifest()
    tower = ItemTower(original.embeddingSpace)
    vector = tower.encode("A stable observable note.")

    assert vector.shape == (100,)
    assert vector.dtype == np.dtype("<f4")
    assert np.linalg.norm(vector) == pytest.approx(1.0)
    assert np.array_equal(vector, tower.encode("A stable observable note."))

    context = CreatorContextTower.original(dimension=100)
    query = context(new=vector, history=np.stack([tower.encode("History note.")]))
    assert query.shape == (100,)
    assert np.linalg.norm(query) == pytest.approx(1.0)


def test_registry_keeps_original_immutable_and_selects_child_explicitly() -> None:
    original = original_manifest()
    registry = ModelRegistry()
    registry.register_original(original)
    with pytest.raises(DuplicateModel):
        registry.register_original(original)

    child_document = original.model_dump(mode="json")
    child_document.update(
        {
            "modelId": "00000000-0000-5000-8000-000000000099",
            "label": "Explicit child",
            "parentModelId": str(original.modelId),
            "producingRunId": "00000000-0000-5000-8000-000000000001",
            "trainingExamples": 12,
            "checkpointPath": "models/child/model.safetensors",
            "checkpointSha256": "e" * 64,
        }
    )
    child = ModelManifestV1.model_validate(child_document)
    registry.register_child(child)

    assert registry.select(original.modelId).modelId == original.modelId
    assert registry.select(child.modelId).modelId == child.modelId
    assert registry.original.modelId == original.modelId


def test_scale_selection_rejects_the_friday_fixture_manifest() -> None:
    original = original_manifest()
    registry = ModelRegistry()
    registry.register_original(original)

    assert hasattr(registry, "select_for_scale")
    with pytest.raises(ValueError, match="real.*Qwen|scale"):
        registry.select_for_scale(original.modelId)
