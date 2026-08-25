"""Atomic immutable snapshots for synchronous recommendation requests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from ..contracts import ModelManifestV1
from ..model.candidate_index import CandidateIndex, MaterializedServingState
from ..model.registry import ModelRegistry
from ..observable import VectorRecord, ensure_unique_sources


@dataclass(frozen=True)
class ServingSnapshot:
    model: ModelManifestV1
    materialized_state: MaterializedServingState
    candidate_index: CandidateIndex
    vectors_by_babel_id: Mapping[UUID, NDArray[np.float32]]
    creator_sources: frozenset[tuple[UUID, UUID, str]]


class ServingState:
    """Swap complete model/vector state atomically; requests hold one snapshot."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        selected_model_id: UUID,
        materialized_state: MaterializedServingState,
        candidate_index: CandidateIndex,
        vector_records: list[VectorRecord],
    ) -> None:
        self._lock = RLock()
        self._registry = registry
        self._snapshot = self._build_snapshot(
            selected_model_id,
            materialized_state,
            candidate_index,
            vector_records,
        )

    def _build_snapshot(
        self,
        selected_model_id: UUID,
        materialized_state: MaterializedServingState,
        candidate_index: CandidateIndex,
        vector_records: list[VectorRecord],
    ) -> ServingSnapshot:
        model = self._registry.select(selected_model_id)
        if materialized_state.model_id != model.modelId:
            raise ValueError("materialized model does not match explicit selection")
        if materialized_state.embedding_space_id != model.embeddingSpace.embeddingSpaceId:
            raise ValueError("materialized embedding space is incompatible")
        if candidate_index.backend not in {"pgvector", "hnswlib"}:
            raise ValueError("candidate index backend is unsupported")
        ensure_unique_sources(record.babel for record in vector_records)
        for record in vector_records:
            if (
                record.babel.runId != materialized_state.run_id
                or record.embeddingSpaceId != materialized_state.embedding_space_id
                or record.servingModelId != materialized_state.model_id
                or record.materializedModelVersion > materialized_state.model_version
            ):
                raise ValueError("vector record is incompatible with serving state")
        candidate_index.activate(materialized_state, vector_records)
        vectors = {
            record.babel.babelId: np.asarray(record.vector, dtype="<f4")
            for record in vector_records
        }
        sources = frozenset(
            (
                record.babel.runId,
                record.babel.creatorId,
                record.babel.sourceArticleKey,
            )
            for record in vector_records
        )
        return ServingSnapshot(
            model=model,
            materialized_state=materialized_state,
            candidate_index=candidate_index,
            vectors_by_babel_id=MappingProxyType(vectors),
            creator_sources=sources,
        )

    def snapshot(self) -> ServingSnapshot:
        with self._lock:
            return self._snapshot

    def apply_sync(
        self,
        *,
        selected_model_id: UUID,
        materialized_state: MaterializedServingState,
        candidate_index: CandidateIndex,
        vector_records: list[VectorRecord],
    ) -> None:
        replacement = self._build_snapshot(
            selected_model_id,
            materialized_state,
            candidate_index,
            vector_records,
        )
        with self._lock:
            self._snapshot = replacement

    def source_is_available(
        self, *, run_id: UUID, creator_id: UUID, source_article_key: str
    ) -> bool:
        snapshot = self.snapshot()
        return (run_id, creator_id, source_article_key) not in snapshot.creator_sources


__all__ = ["ServingSnapshot", "ServingState"]
