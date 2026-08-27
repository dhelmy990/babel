"""Observable feedback to weighted ranking pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TrainingPair:
    positive_id: UUID
    negative_id: UUID
    weight: float


def pairs_from_event(event: Any) -> tuple[TrainingPair, ...]:
    positives = [row.babelId for row in event.candidateActions if row.action == "include"]
    negatives = [
        (row.babelId, 1.0 if row.action == "exclude" else 0.25)
        for row in event.candidateActions
        if row.action in {"exclude", "ignore"}
    ]
    return tuple(
        TrainingPair(positive, negative, weight)
        for positive in positives
        for negative, weight in negatives
    )


__all__ = ["TrainingPair", "pairs_from_event"]
