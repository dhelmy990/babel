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
FROM (
  SELECT DISTINCT ON (run_id, babel_id)
         run_id, babel_id, creator_id, embedding_space_id, serving_model_id,
         materialized_model_version, embedding
  FROM babel_embeddings
  WHERE run_id = %(run_id)s
    AND serving_model_id = %(model_id)s
    AND materialized_model_version <= %(model_version)s
  ORDER BY run_id, babel_id, materialized_model_version DESC
) AS eb
JOIN experiment_babels AS xb
  ON xb.run_id = eb.run_id AND xb.babel_id = eb.babel_id
WHERE eb.run_id = %(run_id)s
  AND eb.babel_id = ANY(%(babel_ids)s::uuid[])
  AND eb.serving_model_id = %(model_id)s
  AND eb.materialized_model_version <= %(model_version)s
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
        self._babel_ids_by_state: dict[MaterializedServingState, tuple[UUID, ...]] = {}
        self._lock = RLock()

    def activate(self, state: MaterializedServingState, records: Sequence[object]) -> None:
        babel_ids = tuple(record.babel.babelId for record in records)
        with self._lock:
            self._babel_ids_by_state[state] = babel_ids

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
            babel_ids = self._babel_ids_by_state.get(state)
        if babel_ids is None or state.run_id != run_id:
            raise StaleServingState("pgvector state does not match request snapshot")
        unit = normalized_query(query)
        parameters: dict[str, object] = {
            "query": [float(value) for value in unit],
            "run_id": run_id,
            "model_id": state.model_id,
            "model_version": state.model_version,
            "snapshot_sha256": state.pgvector_snapshot_sha256,
            "babel_ids": list(babel_ids),
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
