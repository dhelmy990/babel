from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from babel_online.contracts import ModelManifestV1
from babel_online.model.context_tower import CreatorContextTower
from babel_online.model.item_tower import ItemTower
from babel_online.model.registry import (
    DuplicateModel,
    DuplicateModelPublication,
    ModelRegistry,
)


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


def test_publication_ledger_retains_original_child_lineage_and_returned_commits() -> None:
    original = original_manifest()
    registry = ModelRegistry()
    registry.register_original(original)
    child_document = original.model_dump(mode="json")
    child_document.update(
        {
            "modelId": "00000000-0000-5000-8000-000000000098",
            "label": "Published child",
            "parentModelId": str(original.modelId),
            "producingRunId": "00000000-0000-5000-8000-000000000013",
            "checkpointPath": "models/published-child/model.safetensors",
            "checkpointSha256": "f" * 64,
        }
    )
    child = ModelManifestV1.model_validate(child_document)
    registry.register_child(child)

    registry.record_publication(
        original.modelId,
        repository="dhelmy990/babel-qwen-navigation-2016-interview",
        commit_sha="a" * 40,
        manifest_path="artifacts/original/manifest.json",
        serving_artifact_path="artifacts/original",
    )
    registry.record_publication(
        child.modelId,
        repository="dhelmy990/babel-wikipedia-experiment",
        commit_sha="b" * 40,
        manifest_path="runs/00000000-0000-5000-8000-000000000013/model-manifest.json",
        serving_artifact_path=(
            "runs/00000000-0000-5000-8000-000000000013/"
            "model-artifact/state-descriptor.json"
        ),
    )

    ledger = registry.publication_ledger()
    assert [(row.role, row.model_id) for row in ledger] == [
        ("original", original.modelId),
        ("child", child.modelId),
    ]
    assert ledger[1].parent_model_id == original.modelId
    assert ledger[1].original_model_id == original.modelId
    assert ledger[1].commit_sha == "b" * 40
    assert ledger[1].serving_artifact_path.endswith("state-descriptor.json")
    assert registry.select(original.modelId) == original
    assert registry.select(child.modelId) == child
    with pytest.raises(DuplicateModelPublication):
        registry.record_publication(
            child.modelId,
            repository="dhelmy990/babel-wikipedia-experiment",
            commit_sha="c" * 40,
            manifest_path="runs/replacement/model-manifest.json",
            serving_artifact_path="runs/replacement/model-artifact/state-descriptor.json",
        )
