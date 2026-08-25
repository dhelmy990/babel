"""Original equal-weight new-Babel/history context tower."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


def _unit(vector: NDArray[np.floating[object]], dimension: int) -> NDArray[np.float32]:
    value = np.asarray(vector, dtype=np.float64)
    if value.shape != (dimension,):
        raise ValueError(f"expected vector shape ({dimension},)")
    norm = float(np.linalg.norm(value))
    if norm == 0.0:
        raise ValueError("context vector must be nonzero")
    return np.asarray(value / norm, dtype="<f4")


@dataclass(frozen=True)
class CreatorContextTower:
    dimension: int
    new_weight: float
    history_weight: float

    @classmethod
    def original(cls, *, dimension: int = 100) -> "CreatorContextTower":
        if dimension != 100:
            raise ValueError("the online embedding dimension is fixed at 100")
        return cls(dimension=dimension, new_weight=0.5, history_weight=0.5)

    def __call__(
        self,
        *,
        new: NDArray[np.floating[object]],
        history: NDArray[np.floating[object]],
    ) -> NDArray[np.float32]:
        new_unit = _unit(new, self.dimension)
        history_values = np.asarray(history, dtype=np.float64)
        if history_values.size == 0:
            return new_unit
        if history_values.ndim != 2 or history_values.shape[1] != self.dimension:
            raise ValueError("history must have shape (n, 100)")
        row_norms = np.linalg.norm(history_values, axis=1, keepdims=True)
        if np.any(row_norms == 0.0):
            raise ValueError("history vectors must be nonzero")
        normalized = history_values / row_norms
        scores = normalized @ new_unit.astype(np.float64) / np.sqrt(self.dimension)
        scores -= np.max(scores)
        weights = np.exp(scores)
        weights /= np.sum(weights)
        attended = weights @ normalized
        fused = self.new_weight * new_unit + self.history_weight * attended
        return _unit(fused, self.dimension)


__all__ = ["CreatorContextTower"]
