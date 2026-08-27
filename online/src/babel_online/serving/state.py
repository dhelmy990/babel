"""Atomic immutable snapshots for synchronous recommendation requests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from ..contracts import ModelManifest, ModelManifestV2
from ..model.artifact import model_manifest_sha256
from ..model.candidate_index import CandidateIndex, MaterializedServingState
from ..model.item_tower import ItemTower, QwenItemTower
from ..model.qwen_encoder import Qwen100Encoder
from ..model.registry import ModelRegistry
from ..observable import VectorRecord, ensure_unique_sources


@dataclass(frozen=True)
class ServingSnapshot:
    model: ModelManifest
    model_manifest_sha256: str
    materialized_state: MaterializedServingState
    candidate_index: CandidateIndex
    item_tower: ItemTower | QwenItemTower
    vectors_by_babel_id: Mapping[UUID, NDArray[np.float32]]
    source_keys_by_babel_id: Mapping[UUID, str]
    owners_by_babel_id: Mapping[UUID, UUID]
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
        qwen_encoder: Qwen100Encoder | None = None,
        scale_run: bool = False,
    ) -> None:
        self._lock = RLock()
        self._registry = registry
        self._scale_run = scale_run
        self._snapshot = self._build_snapshot(
            selected_model_id,
            materialized_state,
            candidate_index,
            vector_records,
            qwen_encoder,
        )

    def _build_snapshot(
        self,
        selected_model_id: UUID,
        materialized_state: MaterializedServingState,
        candidate_index: CandidateIndex,
        vector_records: list[VectorRecord],
        qwen_encoder: Qwen100Encoder | None,
    ) -> ServingSnapshot:
        model = (
            self._registry.select_for_scale(selected_model_id)
            if self._scale_run
            else self._registry.select(selected_model_id)
        )
        if qwen_encoder is None:
            if self._scale_run or isinstance(model, ModelManifestV2):
                raise ValueError("real Qwen serving requires one injected Qwen100Encoder")
            item_tower: ItemTower | QwenItemTower = ItemTower(model.embeddingSpace)
        else:
            if not isinstance(model, ModelManifestV2):
                raise ValueError("a Qwen encoder cannot be bound to the Friday fixture manifest")
            contract = qwen_encoder.contract
            if (
                contract.artifactRepo != model.encoderRepo
                or contract.artifactRevision != model.encoderRevision
                or contract.artifactId != model.artifactId
                or contract.baseModelRevision != model.baseModelRevision
                or contract.datasetRevision != model.datasetRevision
                or contract.adapterSha256 != model.adapterSha256
                or contract.projectionSha256 != model.projectionSha256
                or contract.validationSha256 != model.validationSha256
            ):
                raise ValueError("Qwen encoder identity does not match selected real model")
            item_tower = QwenItemTower(qwen_encoder)
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
        source_keys = {
            record.babel.babelId: record.babel.sourceArticleKey
            for record in vector_records
        }
        owners = {
            record.babel.babelId: record.babel.creatorId
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
            model_manifest_sha256=model_manifest_sha256(model),
            materialized_state=materialized_state,
            candidate_index=candidate_index,
            item_tower=item_tower,
            vectors_by_babel_id=MappingProxyType(vectors),
            source_keys_by_babel_id=MappingProxyType(source_keys),
            owners_by_babel_id=MappingProxyType(owners),
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
        qwen_encoder: Qwen100Encoder | None = None,
    ) -> None:
        if qwen_encoder is None:
            current = self.snapshot().item_tower
            if isinstance(current, QwenItemTower):
                qwen_encoder = current.encoder
        replacement = self._build_snapshot(
            selected_model_id,
            materialized_state,
            candidate_index,
            vector_records,
            qwen_encoder,
        )
        with self._lock:
            self._snapshot = replacement

    def source_is_available(
        self, *, run_id: UUID, creator_id: UUID, source_article_key: str
    ) -> bool:
        snapshot = self.snapshot()
        return (run_id, creator_id, source_article_key) not in snapshot.creator_sources


__all__ = ["ServingSnapshot", "ServingState"]
