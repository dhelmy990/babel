"""Deterministic 100-dimensional working-copy model for the Friday demo."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import numpy as np

from .loss import weighted_pairwise_loss
from .pairs import TrainingPair


class NumpyWorkingModel:
    """Frozen item vectors plus trainable residuals and a fixed context query."""

    def __init__(
        self,
        frozen_vectors: Mapping[UUID, np.ndarray],
        *,
        query_vector: np.ndarray,
        learning_rate: float = 0.1,
    ) -> None:
        if not frozen_vectors:
            raise ValueError("working model needs frozen item vectors")
        self._frozen: dict[UUID, np.ndarray] = {}
        for item_id, vector in frozen_vectors.items():
            checked = np.asarray(vector, dtype="<f4").reshape(-1).copy()
            if checked.shape != (100,) or not np.isfinite(checked).all():
                raise ValueError("frozen vectors must be finite 100d float32")
            checked.flags.writeable = False
            self._frozen[item_id] = checked
        query = np.asarray(query_vector, dtype="<f4").reshape(-1).copy()
        if query.shape != (100,) or not np.isfinite(query).all():
            raise ValueError("query vector must be finite 100d float32")
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            raise ValueError("query vector must be nonzero")
        self._query = query / norm
        self._residuals = {
            item_id: np.zeros(100, dtype="<f4") for item_id in self._frozen
        }
        if learning_rate <= 0.0:
            raise ValueError("learning rate must be positive")
        self.learning_rate = float(learning_rate)

    def frozen_bytes(self) -> bytes:
        return b"".join(
            item_id.bytes + self._frozen[item_id].tobytes(order="C")
            for item_id in sorted(self._frozen, key=lambda value: value.hex)
        )

    def residual(self, item_id: UUID) -> np.ndarray:
        return self._residuals[item_id].copy()

    def materialized_vector(self, item_id: UUID) -> np.ndarray:
        value = self._frozen[item_id] + self._residuals[item_id]
        norm = float(np.linalg.norm(value))
        if norm == 0.0 or not np.isfinite(norm):
            raise FloatingPointError("working item vector cannot be normalized")
        return np.asarray(value / norm, dtype="<f4")

    def materialized_vectors(self) -> dict[UUID, np.ndarray]:
        return {
            item_id: self.materialized_vector(item_id)
            for item_id in sorted(self._frozen, key=lambda value: value.hex)
        }

    def score(self, item_id: UUID) -> float:
        return float(np.dot(self._query, self._frozen[item_id] + self._residuals[item_id]))

    def pair_scores(
        self, pairs: Sequence[TrainingPair]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        positive = np.asarray([self.score(pair.positive_id) for pair in pairs], dtype=np.float32)
        negative = np.asarray([self.score(pair.negative_id) for pair in pairs], dtype=np.float32)
        weights = np.asarray([pair.weight for pair in pairs], dtype=np.float32)
        return positive, negative, weights

    def train_pairs(self, pairs: Sequence[TrainingPair]) -> float:
        positive, negative, weights = self.pair_scores(pairs)
        loss = weighted_pairwise_loss(positive, negative, weights)
        total_weight = float(weights.sum(dtype=np.float64))
        differences = np.clip(positive - negative, -60.0, 60.0)
        coefficients = (-1.0 / (1.0 + np.exp(differences))) * weights / total_weight
        gradients = {
            item_id: np.zeros(100, dtype=np.float32) for item_id in self._residuals
        }
        for pair, coefficient in zip(pairs, coefficients, strict=True):
            gradients[pair.positive_id] += float(coefficient) * self._query
            gradients[pair.negative_id] -= float(coefficient) * self._query
        for item_id, gradient in gradients.items():
            self._residuals[item_id] -= self.learning_rate * gradient
            if not np.isfinite(self._residuals[item_id]).all():
                raise FloatingPointError("online residual update is non-finite")
        return loss

    def state_dict(self) -> dict[str, Any]:
        return {
            "learningRate": self.learning_rate,
            "residuals": {
                str(item_id): self._residuals[item_id].tolist()
                for item_id in sorted(self._residuals, key=lambda value: value.hex)
            },
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        learning_rate = state.get("learningRate")
        if (
            not isinstance(learning_rate, (int, float))
            or isinstance(learning_rate, bool)
            or not np.isfinite(learning_rate)
            or learning_rate <= 0.0
        ):
            raise ValueError("working learning rate is invalid")
        rows = state.get("residuals")
        if not isinstance(rows, Mapping) or set(rows) != {
            str(item_id) for item_id in self._residuals
        }:
            raise ValueError("working residual identity mismatch")
        for item_id in self._residuals:
            vector = np.asarray(rows[str(item_id)], dtype="<f4").reshape(-1)
            if vector.shape != (100,) or not np.isfinite(vector).all():
                raise ValueError("working residual must be finite 100d float32")
            self._residuals[item_id] = vector.copy()
        self.learning_rate = float(learning_rate)


__all__ = ["NumpyWorkingModel"]
