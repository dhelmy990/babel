"""Deterministic 100-dimensional fixture encoder bound to one embedding space."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from ..contracts import EmbeddingSpaceV1
from .qwen_encoder import Qwen100Encoder, format_article_input


@dataclass(frozen=True)
class EncoderExecutionIdentity:
    mode: Literal["fixture", "real_qwen"]
    device: str
    cache_identity: str
    batch_size: int


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

    def encode_article(self, title: str, lead_text: str) -> NDArray[np.float32]:
        return self.encode(f"{title}\n\n{lead_text}")

    def execution_identity(self, *, batch_size: int) -> EncoderExecutionIdentity:
        return EncoderExecutionIdentity(
            mode="fixture",
            device="cpu",
            cache_identity="deterministic-friday-fixture",
            batch_size=batch_size,
        )


class QwenItemTower:
    """Article-shaped boundary around one already-loaded real Qwen encoder."""

    def __init__(self, encoder: Qwen100Encoder) -> None:
        if not isinstance(encoder, Qwen100Encoder):
            raise TypeError("real serving requires a Qwen100Encoder instance")
        self.encoder = encoder
        self.embedding_space = None
        self.dimension = encoder.contract.embeddingDimension

    def encode_article(self, title: str, lead_text: str) -> NDArray[np.float32]:
        text = format_article_input(title, lead_text)
        result = self.encoder.encode([text])
        if result.shape != (1, 100):
            raise ValueError("real item encoder must return one 100d vector")
        return np.asarray(result[0], dtype="<f4")

    def execution_identity(self, *, batch_size: int) -> EncoderExecutionIdentity:
        return EncoderExecutionIdentity(
            mode="real_qwen",
            device=self.encoder.device,
            cache_identity=self.encoder.cache_identity,
            batch_size=batch_size,
        )


__all__ = ["EncoderExecutionIdentity", "ItemTower", "QwenItemTower"]
