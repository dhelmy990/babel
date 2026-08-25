"""Immutable online run configuration."""

from __future__ import annotations

from typing import Literal
from uuid import UUID


RetrievalBackend = Literal["pgvector", "hnswlib"]
EnvironmentPeriod = Literal["2026-06", "2026-07"]
RunStatus = Literal[
    "starting",
    "running",
    "stop_requested",
    "draining_feedback",
    "checkpointing",
    "exporting_interactions",
    "completed",
    "failed",
    "interrupted",
]


def default_run_config(
    *,
    run_id: UUID,
    dataset_revision: str,
    starting_model_id: UUID,
    creator_count: int = 50,
) -> "RunConfigV1":
    """Create the frozen default June→July pgvector run."""
    from .contracts import RunConfigV1

    return RunConfigV1(
        schemaVersion=1,
        runId=run_id,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetRevision=dataset_revision,
        startingModelId=starting_model_id,
        retrievalBackend="pgvector",
        creatorCount=creator_count,
        environmentSequence=["2026-06", "2026-07"],
        perMonthEventBudget={"2026-06": 100, "2026-07": 100},
    )


if False:  # pragma: no cover - type-checking only without a runtime cycle
    from .contracts import RunConfigV1


__all__ = ["EnvironmentPeriod", "RetrievalBackend", "RunStatus", "default_run_config"]
