from __future__ import annotations

import pytest

from babel_online.simulation.decisions import action_probabilities, decide_candidate


def test_decision_probabilities_match_approved_three_way_formula() -> None:
    probabilities = action_probabilities(
        relevance=0.8,
        epsilon=0.2,
        exclusion_propensity=0.25,
    )

    assert probabilities.include == pytest.approx(0.74)
    assert probabilities.exclude == pytest.approx((1.0 - 0.74) * 0.25 * 0.26)
    assert probabilities.include + probabilities.exclude + probabilities.ignore == (
        pytest.approx(1.0)
    )
    assert decide_candidate(probabilities, draw=0.1) == "include"
    assert decide_candidate(probabilities, draw=0.75) == "exclude"
    assert decide_candidate(probabilities, draw=0.99) == "ignore"
