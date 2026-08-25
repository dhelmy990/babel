"""Finite weighted pairwise ranking loss."""

from __future__ import annotations

import math

import numpy as np


def weighted_pairwise_loss(
    positive_scores: np.ndarray,
    negative_scores: np.ndarray,
    weights: np.ndarray,
) -> float:
    positive = np.asarray(positive_scores, dtype=np.float32)
    negative = np.asarray(negative_scores, dtype=np.float32)
    pair_weights = np.asarray(weights, dtype=np.float32)
    if positive.shape != negative.shape or positive.shape != pair_weights.shape:
        raise ValueError("pair score and weight arrays must have identical shapes")
    total_weight = float(pair_weights.sum(dtype=np.float64))
    if positive.size == 0 or total_weight <= 0.0:
        raise ValueError("weighted pairwise loss requires at least one positive weight")
    if not all(np.isfinite(value).all() for value in (positive, negative, pair_weights)):
        raise ValueError("pairwise loss inputs must be finite")
    losses = np.logaddexp(0.0, -(positive - negative)) * pair_weights
    value = float(losses.sum(dtype=np.float64) / total_weight)
    if not math.isfinite(value):
        raise FloatingPointError("weighted pairwise loss is non-finite")
    return value


__all__ = ["weighted_pairwise_loss"]
