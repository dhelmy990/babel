"""Optional run-scoped hnswlib adapter for retrieval-only comparisons."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from ..observable import VectorRecord
from .candidate_index import (
    MaterializedServingState,
    RetrievedCandidate,
    StaleServingState,
    normalized_query,
)


@dataclass(frozen=True, slots=True)
class RecallAudit:
    recallAt10: float
    recallAt50: float


def _default_factory(*, space: str, dim: int):
    try:
        import hnswlib
    except ImportError as error:  # pragma: no cover - optional deployment extra
        raise RuntimeError(
            "hnswlib retrieval requires the optional hnswlib extra"
        ) from error
    return hnswlib.Index(space=space, dim=dim)


class HnswlibCandidateIndex:
    """In-process optional index; pgvector remains the runtime default."""

    backend = "hnswlib"

    def __init__(
        self,
        *,
        index_factory: Callable[..., object] = _default_factory,
        ef_construction: int = 200,
        search_ef: int = 100,
        m: int = 16,
    ) -> None:
        self._factory = index_factory
        self._ef_construction = ef_construction
        self._search_ef = search_ef
        self._m = m
        self._state: MaterializedServingState | None = None
        self._index: object | None = None
        self._records: tuple[VectorRecord, ...] = ()
        self.ordered_vector_sha256 = ""

    def activate(
        self, state: MaterializedServingState, records: Sequence[VectorRecord]
    ) -> None:
        selected = tuple(
            row
            for row in records
            if row.babel.runId == state.run_id
            and row.embeddingSpaceId == state.embedding_space_id
            and row.servingModelId == state.model_id
            and row.materializedModelVersion <= state.model_version
        )
        if not selected:
            raise ValueError(
                "hnswlib activation requires compatible created-Babel vectors"
            )
        vectors = np.stack(
            [
                normalized_query(np.asarray(row.vector, dtype=np.float32))
                for row in selected
            ]
        ).astype("<f4", copy=False)
        identity = b"\0".join(str(row.babel.babelId).encode() for row in selected)
        self.ordered_vector_sha256 = hashlib.sha256(
            identity + b"\0" + vectors.tobytes()
        ).hexdigest()
        index = self._factory(space="cosine", dim=100)
        index.init_index(
            max_elements=len(selected),
            ef_construction=self._ef_construction,
            M=self._m,
        )
        labels = np.arange(len(selected), dtype=np.int64)
        index.add_items(vectors, labels)
        index.set_ef(self._search_ef)
        self._records = selected
        self._index = index
        self._state = state

    def search(
        self,
        query: NDArray[np.float32],
        *,
        run_id: UUID,
        state: MaterializedServingState,
        exclude_creator_id: UUID,
        k: int,
    ) -> list[RetrievedCandidate]:
        if k <= 0:
            raise ValueError("candidate count must be positive")
        if self._state != state or state.run_id != run_id or self._index is None:
            raise StaleServingState("hnswlib state does not match request snapshot")
        unit = normalized_query(query)
        query_k = min(len(self._records), max(k, k * 2))
        output: list[RetrievedCandidate] = []
        while True:
            self._index.set_ef(max(self._search_ef, query_k))
            labels, distances = self._index.knn_query(unit.reshape(1, -1), k=query_k)
            output = []
            for label, distance in zip(labels[0], distances[0], strict=True):
                row = self._records[int(label)]
                if row.babel.creatorId == exclude_creator_id:
                    continue
                output.append(
                    RetrievedCandidate(
                        babel_id=row.babel.babelId,
                        creator_id=row.babel.creatorId,
                        source_article_key=row.babel.sourceArticleKey,
                        score=float(1.0 - distance),
                    )
                )
            if len(output) >= k or query_k == len(self._records):
                break
            query_k = min(len(self._records), query_k * 2)
        output.sort(key=lambda row: (-row.score, str(row.babel_id).lower()))
        return output[:k]

    @staticmethod
    def audit_recall(exact: Sequence[str], approximate: Sequence[str]) -> RecallAudit:
        if len(exact) < 50:
            raise ValueError("recall audit requires at least 50 exact neighbors")

        def at(k: int) -> float:
            return len(set(exact[:k]) & set(approximate[:k])) / k

        return RecallAudit(recallAt10=at(10), recallAt50=at(50))


__all__ = ["HnswlibCandidateIndex", "RecallAudit"]
