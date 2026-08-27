from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import UUID

import numpy as np

from babel_online.model.context_tower import CreatorContextTower
from babel_online.training.torch_working import TorchOnlineRecommender


SOURCE = UUID("00000000-0000-5000-8000-000000000201")
HISTORY = UUID("00000000-0000-5000-8000-000000000202")
INCLUDED = UUID("00000000-0000-5000-8000-000000000203")
EXCLUDED = UUID("00000000-0000-5000-8000-000000000204")
UNTOUCHED = UUID("00000000-0000-5000-8000-000000000205")
CREATOR = UUID("00000000-0000-5000-8000-000000000206")


def _vectors() -> dict[UUID, np.ndarray]:
    result = {}
    for index, item_id in enumerate((SOURCE, HISTORY, INCLUDED, EXCLUDED, UNTOUCHED)):
        vector = np.zeros(100, dtype=np.float32)
        vector[index] = 1.0
        result[item_id] = vector
    return result


def _event(*, source: UUID = SOURCE, creator: UUID = CREATOR):
    return SimpleNamespace(
        creatorId=creator,
        sourceBabelId=source,
        traversalDepth=0,
        candidateActions=(
            SimpleNamespace(babelId=INCLUDED, action="include"),
            SimpleNamespace(babelId=EXCLUDED, action="exclude"),
        ),
    )


def test_original_torch_context_matches_frozen_compatible_initialization() -> None:
    model = TorchOnlineRecommender(_vectors(), learning_rate=0.05)
    new = _vectors()[SOURCE]
    history = np.stack([_vectors()[HISTORY], _vectors()[INCLUDED]])

    expected = CreatorContextTower.original()(new=new, history=history)

    np.testing.assert_allclose(model(new=new, history=history), expected, atol=1e-7)
    assert model.context_parameter_names() == {
        "attention_query.weight",
        "attention_key.weight",
        "fusion.weight",
        "fusion.bias",
    }


def test_training_updates_context_and_only_touched_item_residuals() -> None:
    model = TorchOnlineRecommender(_vectors(), learning_rate=0.05)
    model.observe_event(
        SimpleNamespace(
            creatorId=CREATOR,
            sourceBabelId=HISTORY,
            traversalDepth=0,
            candidateActions=(),
        )
    )
    context_before = copy.deepcopy(model.transfer_state_dict()["contextState"])
    frozen_before = model.frozen_bytes()

    losses = [model.train_events([_event()]) for _ in range(8)]

    assert np.isfinite(losses).all()
    assert losses[-1] < losses[0]
    assert model.frozen_bytes() == frozen_before
    assert model.touched_item_ids() == {INCLUDED, EXCLUDED}
    assert np.linalg.norm(model.residual(INCLUDED)) > 0
    assert np.linalg.norm(model.residual(EXCLUDED)) > 0
    assert np.allclose(model.residual(UNTOUCHED), np.zeros(100))
    assert model.transfer_state_dict()["contextState"] != context_before


def test_complete_state_round_trip_restores_optimizer_scheduler_and_predictions() -> None:
    first = TorchOnlineRecommender(
        _vectors(), learning_rate=0.02, scheduler_gamma=0.97
    )
    first.observe_event(
        SimpleNamespace(
            creatorId=CREATOR,
            sourceBabelId=HISTORY,
            traversalDepth=0,
            candidateActions=(),
        )
    )
    first.train_events([_event()])
    saved = first.state_dict()

    restored = TorchOnlineRecommender(
        _vectors(), learning_rate=0.5, scheduler_gamma=1.0
    )
    restored.load_state_dict(saved)

    assert restored.state_dict() == saved
    assert saved["modelKind"] == "torch_online_recommender_v1"
    assert saved["optimizerState"]["kind"] == "adam"
    assert saved["schedulerState"] == {
        "kind": "exponential",
        "gamma": 0.97,
        "step": 1,
    }
    assert saved["creatorHistories"][str(CREATOR)] == [str(HISTORY), str(SOURCE)]
    np.testing.assert_array_equal(
        restored.materialized_vector(INCLUDED), first.materialized_vector(INCLUDED)
    )
    np.testing.assert_array_equal(
        restored(new=_vectors()[SOURCE], history=np.stack([_vectors()[HISTORY]])),
        first(new=_vectors()[SOURCE], history=np.stack([_vectors()[HISTORY]])),
    )

    next_first = first.train_events([_event()])
    next_restored = restored.train_events([_event()])
    assert next_restored == next_first
    assert restored.state_dict() == first.state_dict()
