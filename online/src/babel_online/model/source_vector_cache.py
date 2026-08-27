"""Run-scoped source-vector resolution with a byte-preserving bounded LRU."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from .qwen_encoder import Qwen100Encoder, format_article_input


@dataclass(frozen=True, slots=True)
class VectorCacheKey:
    run_id: UUID
    babel_id: UUID
    model_id: UUID
    model_version: int
    embedding_space_id: UUID

    def __post_init__(self) -> None:
        if self.model_version < 0:
            raise ValueError("model version must be nonnegative")


@dataclass(frozen=True, slots=True)
class ResolvedSourceVector:
    vector: NDArray[np.float32]
    origin: Literal["qwen_encode", "cache_hit", "pgvector_load"]


LoadActiveVector = Callable[[VectorCacheKey], NDArray[np.float32]]


def _exact_float32(vector: object) -> NDArray[np.float32]:
    if not isinstance(vector, np.ndarray) or vector.dtype != np.dtype(np.float32):
        raise ValueError("source vector must be finite float32 bytes")
    if vector.shape != (100,) or not vector.flags.c_contiguous or not np.isfinite(vector).all():
        raise ValueError("source vector must be one contiguous finite float32 vector")
    return vector


class SourceVectorResolver:
    """Resolve walk roots without silently changing persisted vector bytes."""

    def __init__(
        self,
        encoder: Qwen100Encoder,
        *,
        load_active: LoadActiveVector,
        capacity: int = 512,
    ) -> None:
        if not isinstance(encoder, Qwen100Encoder):
            raise TypeError("source-vector resolution requires the real Qwen encoder")
        if capacity <= 0:
            raise ValueError("source-vector cache capacity must be positive")
        self._encoder = encoder
        self._load_active = load_active
        self._capacity = capacity
        self._cache: OrderedDict[VectorCacheKey, NDArray[np.float32]] = OrderedDict()

    def _remember(
        self, key: VectorCacheKey, vector: NDArray[np.float32]
    ) -> NDArray[np.float32]:
        source = _exact_float32(vector)
        exact = np.array(source, dtype=np.float32, order="C", copy=True)
        exact.setflags(write=False)
        self._cache[key] = exact
        self._cache.move_to_end(key)
        while len(self._cache) > self._capacity:
            self._cache.popitem(last=False)
        return exact

    def resolve_new_root(
        self, key: VectorCacheKey, *, title: str, lead_text: str
    ) -> ResolvedSourceVector:
        encoded = self._encoder.encode([format_article_input(title, lead_text)])
        if (
            not isinstance(encoded, np.ndarray)
            or encoded.dtype != np.dtype(np.float32)
            or encoded.shape != (1, 100)
            or not encoded.flags.c_contiguous
        ):
            raise ValueError("Qwen root encoding must contain finite float32 bytes")
        vector = self._remember(key, encoded[0])
        return ResolvedSourceVector(vector=vector, origin="qwen_encode")

    def resolve_existing(self, key: VectorCacheKey) -> ResolvedSourceVector:
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return ResolvedSourceVector(vector=cached, origin="cache_hit")
        vector = self._remember(key, self._load_active(key))
        return ResolvedSourceVector(vector=vector, origin="pgvector_load")


__all__ = [
    "LoadActiveVector",
    "ResolvedSourceVector",
    "SourceVectorResolver",
    "VectorCacheKey",
]
