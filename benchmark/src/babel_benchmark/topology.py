"""Topology identity without conflating retrieval implementation."""

from __future__ import annotations

from .contracts import ConditionIdentityV2, ConditionSpecV1


def infer_v1_condition_identity(condition: ConditionSpecV1) -> ConditionIdentityV2:
    """Map an immutable Friday condition into the generalized identity."""
    return ConditionIdentityV2(
        topology="same_process",
        trainingEnabled=condition.trainingEnabled,
        activationEnabled=condition.syncEnabled,
        retrievalBackend="pgvector",
    )


__all__ = ["infer_v1_condition_identity"]
