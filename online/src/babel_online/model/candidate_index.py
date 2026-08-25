"""Created-Babel-only candidate retrieval boundary and fixture adapter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from ..config import RetrievalBackend
from ..observable import VectorRecord


class StaleServingState(ValueError):
    pass


@dataclass(frozen=True)
class MaterializedServingState:
    run_id: UUID
    model_id: UUID
    model_version: int
    embedding_space_id: UUID
    pgvector_snapshot_sha256: str
    backend_snapshot_sha256: str


@dataclass(frozen=True)
class RetrievedCandidate:
    babel_id: UUID
    creator_id: UUID
    source_article_key: str
    score: float


class CandidateIndex(Protocol):
    backend: RetrievalBackend

    def search(
        self,
        query: NDArray[np.float32],
        *,
        run_id: UUID,
        state: MaterializedServingState,
        exclude_creator_id: UUID,
        k: int,
    ) -> list[RetrievedCandidate]: ...

    def activate(self, state: MaterializedServingState) -> None: ...


def normalized_query(query: NDArray[np.float32]) -> NDArray[np.float32]:
    value = np.asarray(query, dtype=np.float32)
    if value.shape != (100,) or not np.isfinite(value).all():
        raise ValueError("candidate query must be one finite 100d vector")
    norm = float(np.linalg.norm(value))
    if norm == 0.0:
        raise ValueError("candidate query must be nonzero")
    return np.asarray(value / norm, dtype="<f4")


class InMemoryCreatedBabelIndex:
    """Deterministic fixture adapter with pgvector-equivalent cosine semantics."""

    backend: RetrievalBackend = "pgvector"

    def __init__(self, records: Sequence[VectorRecord]) -> None:
        self._records = tuple(records)
        self._active: MaterializedServingState | None = None

    def activate(self, state: MaterializedServingState) -> None:
        self._active = state

    def search(
        self,
        query: NDArray[np.float32],
        *,
        run_id: UUID,
        state: MaterializedServingState,
        exclude_creator_id: UUID,
        k: int,
    ) -> list[RetrievedCandidate]:
        if self._active != state or state.run_id != run_id:
            raise StaleServingState("candidate index state does not match request snapshot")
        if k <= 0:
            raise ValueError("candidate count must be positive")
        unit = normalized_query(query)
        candidates: list[RetrievedCandidate] = []
        for record in self._records:
            babel = record.babel
            if (
                babel.runId != run_id
                or babel.creatorId == exclude_creator_id
                or record.embeddingSpaceId != state.embedding_space_id
                or record.servingModelId != state.model_id
                or record.materializedModelVersion > state.model_version
            ):
                continue
            candidate_vector = normalized_query(np.asarray(record.vector, dtype=np.float32))
            candidates.append(
                RetrievedCandidate(
                    babel_id=babel.babelId,
                    creator_id=babel.creatorId,
                    source_article_key=babel.sourceArticleKey,
                    score=float(np.dot(unit, candidate_vector)),
                )
            )
        candidates.sort(key=lambda row: (-row.score, str(row.babel_id).lower()))
        return candidates[:k]


__all__ = [
    "CandidateIndex",
    "InMemoryCreatedBabelIndex",
    "MaterializedServingState",
    "RetrievedCandidate",
    "StaleServingState",
    "normalized_query",
]
