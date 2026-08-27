"""Hidden three-way candidate decisions."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal


Action = Literal["include", "exclude", "ignore"]


@dataclass(frozen=True, slots=True)
class ActionProbabilities:
    include: float
    exclude: float
    ignore: float


def combined_relevance(*, relatedness_rank: float, preference_rank: float) -> float:
    if not 0.0 <= relatedness_rank <= 1.0 or not 0.0 <= preference_rank <= 1.0:
        raise ValueError("hidden ranks must be within [0, 1]")
    return (0.60 * relatedness_rank) + (0.40 * preference_rank)


def action_probabilities(
    *,
    relevance: float,
    epsilon: float,
    exclusion_propensity: float,
) -> ActionProbabilities:
    values = (relevance, epsilon, exclusion_propensity)
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
        raise ValueError("decision inputs must be finite values within [0, 1]")
    include = ((1.0 - epsilon) * relevance) + (epsilon * 0.5)
    conditional_exclude = exclusion_propensity * (
        ((1.0 - epsilon) * (1.0 - relevance)) + (epsilon * 0.5)
    )
    exclude = (1.0 - include) * conditional_exclude
    ignore = 1.0 - include - exclude
    return ActionProbabilities(include, exclude, ignore)


def deterministic_draw(*identity_parts: object) -> float:
    identity = "\x1f".join(str(part) for part in identity_parts).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
    return value / 2**64


def decide_candidate(probabilities: ActionProbabilities, *, draw: float) -> Action:
    if not math.isfinite(draw) or not 0.0 <= draw < 1.0:
        raise ValueError("decision draw must be finite and within [0, 1)")
    if draw < probabilities.include:
        return "include"
    if draw < probabilities.include + probabilities.exclude:
        return "exclude"
    return "ignore"


__all__ = [
    "Action",
    "ActionProbabilities",
    "action_probabilities",
    "combined_relevance",
    "decide_candidate",
    "deterministic_draw",
]
