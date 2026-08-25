"""Deterministic 100-dimensional fixture encoder bound to one embedding space."""

from __future__ import annotations

import hashlib
import re

import numpy as np
from numpy.typing import NDArray

from ..contracts import EmbeddingSpaceV1


class ItemTower:
    """Small deterministic stand-in for the pinned frozen distilled encoder."""

    def __init__(self, embedding_space: EmbeddingSpaceV1) -> None:
        self.embedding_space = embedding_space
        self.dimension = embedding_space.dimension
        self._identity = "|".join(
            (
                str(embedding_space.embeddingSpaceId),
                embedding_space.distilledEncoderArtifact,
                embedding_space.datasetRevision,
                embedding_space.compatibilityVersion,
            )
        ).encode("utf-8")

    def encode(self, text: str) -> NDArray[np.float32]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("item text must be nonblank")
        vector = np.zeros(self.dimension, dtype=np.float64)
        tokens = re.findall(r"[\w']+", text.casefold())
        for position, token in enumerate(tokens):
            digest = hashlib.sha256(
                self._identity + b"\0" + str(position).encode("ascii") + b"\0" + token.encode()
            ).digest()
            for offset in range(0, len(digest), 4):
                value = int.from_bytes(digest[offset : offset + 4], "little")
                vector[value % self.dimension] += 1.0 if value & 1 else -1.0
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            vector[0] = 1.0
            norm = 1.0
        return np.asarray(vector / norm, dtype="<f4")


__all__ = ["ItemTower"]
