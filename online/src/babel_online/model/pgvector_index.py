"""Default pgvector created-Babel candidate adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import RLock
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from .candidate_index import (
    MaterializedServingState,
    RetrievedCandidate,
    StaleServingState,
    normalized_query,
)


PGVECTOR_CREATED_BABEL_QUERY = """
SELECT eb.babel_id,
       eb.creator_id,
       xb.source_article_key,
       1 - (eb.embedding <=> %(query)s::public.vector) AS score
FROM run_embedding_states AS rs
JOIN babel_embeddings AS eb
  ON eb.run_id = rs.run_id
 AND eb.serving_model_id = rs.active_model_id
 AND eb.materialized_model_version = rs.active_model_version
 AND eb.embedding_space_id = rs.embedding_space_id
JOIN experiment_babels AS xb
  ON xb.run_id = eb.run_id AND xb.babel_id = eb.babel_id
WHERE rs.run_id = %(run_id)s
  AND rs.active_model_id = %(model_id)s
  AND rs.active_model_version = %(model_version)s
  AND rs.embedding_space_id = %(embedding_space_id)s
  AND rs.pgvector_snapshot_sha256 = %(snapshot_sha256)s
  AND eb.creator_id <> %(exclude_creator_id)s
ORDER BY eb.embedding <=> %(query)s::public.vector, eb.babel_id
LIMIT %(limit)s
""".strip()

PGVECTOR_TRANSACTION_SETTINGS = (
    "SET LOCAL hnsw.ef_search = 100",
    "SET LOCAL hnsw.iterative_scan = strict_order",
)

QueryRows = Callable[
    [Sequence[str], str, Mapping[str, object]], Iterable[Mapping[str, object]]
]


class PgvectorCandidateIndex:
    """Production-default adapter over a transaction-scoped query callback."""

    backend = "pgvector"

    def __init__(self, query_rows: QueryRows) -> None:
        self._query_rows = query_rows
        self._active_state_by_run: dict[UUID, MaterializedServingState] = {}
        self._lock = RLock()

    def activate(self, state: MaterializedServingState, records: Sequence[object]) -> None:
        with self._lock:
            self._active_state_by_run[state.run_id] = state

    def search(
        self,
        query: NDArray[np.float32],
        *,
        run_id: UUID,
        state: MaterializedServingState,
        exclude_creator_id: UUID,
        k: int,
    ) -> list[RetrievedCandidate]:
        with self._lock:
            active = self._active_state_by_run.get(run_id) == state
        if not active or state.run_id != run_id:
            raise StaleServingState("pgvector state does not match request snapshot")
        unit = normalized_query(query)
        parameters: dict[str, object] = {
            "query": [float(value) for value in unit],
            "run_id": run_id,
            "model_id": state.model_id,
            "model_version": state.model_version,
            "embedding_space_id": state.embedding_space_id,
            "snapshot_sha256": state.pgvector_snapshot_sha256,
            "exclude_creator_id": exclude_creator_id,
            "limit": k,
        }
        rows = self._query_rows(
            PGVECTOR_TRANSACTION_SETTINGS, PGVECTOR_CREATED_BABEL_QUERY, parameters
        )
        return [
            RetrievedCandidate(
                babel_id=UUID(str(row["babel_id"])),
                creator_id=UUID(str(row["creator_id"])),
                source_article_key=str(row["source_article_key"]),
                score=float(row["score"]),
            )
            for row in rows
        ]


__all__ = [
    "PGVECTOR_CREATED_BABEL_QUERY",
    "PGVECTOR_TRANSACTION_SETTINGS",
    "PgvectorCandidateIndex",
    "QueryRows",
]
