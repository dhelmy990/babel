from __future__ import annotations

import hashlib
from uuid import UUID

import numpy as np
import pytest

from babel_online.training.loss import weighted_pairwise_loss
from babel_online.training.pairs import pairs_from_event
from babel_online.training.working import NumpyWorkingModel

from .test_pairs import EXCLUDED, IGNORED, INCLUDED, event_with_three_actions


def test_weighted_pairwise_loss_and_tiny_update_are_finite_and_decrease() -> None:
    pairs = pairs_from_event(event_with_three_actions())
    base = {
        INCLUDED: np.zeros(100, dtype=np.float32),
        EXCLUDED: np.zeros(100, dtype=np.float32),
        IGNORED: np.zeros(100, dtype=np.float32),
    }
    query = np.zeros(100, dtype=np.float32)
    query[0] = 1.0
    model = NumpyWorkingModel(base, query_vector=query, learning_rate=0.5)
    frozen_before = hashlib.sha256(model.frozen_bytes()).hexdigest()

    positive, negative, weights = model.pair_scores(pairs)
    initial = weighted_pairwise_loss(positive, negative, weights)
    losses = [model.train_pairs(pairs) for _ in range(12)]

    assert np.isfinite(losses).all()
    assert losses[-1] < initial
    assert np.linalg.norm(model.residual(INCLUDED)) > 0
    materialized = model.materialized_vector(INCLUDED)
    assert materialized.shape == (100,)
    assert materialized.dtype == np.dtype("<f4")
    assert np.linalg.norm(materialized) == pytest.approx(1.0)
    assert hashlib.sha256(model.frozen_bytes()).hexdigest() == frozen_before


def test_child_transfers_learned_context_without_reusing_prior_run_item_ids() -> None:
    pairs = pairs_from_event(event_with_three_actions())
    first = NumpyWorkingModel(
        {key: np.eye(100, dtype=np.float32)[index] for index, key in enumerate((INCLUDED, EXCLUDED, IGNORED))},
        query_vector=np.eye(100, dtype=np.float32)[0],
    )
    first.train_pairs(pairs)
    transfer = first.transfer_state_dict()
    new_ids = [UUID(int=900 + index) for index in range(3)]
    second = NumpyWorkingModel(
        {key: np.eye(100, dtype=np.float32)[index] for index, key in enumerate(new_ids)},
        query_vector=np.eye(100, dtype=np.float32)[0],
    )

    second.load_transfer_state(transfer)

    np.testing.assert_allclose(
        second.transfer_state_dict()["queryVector"], transfer["queryVector"], atol=1e-7
    )
    assert set(second.state_dict()["residuals"]) == {str(key) for key in new_ids}
