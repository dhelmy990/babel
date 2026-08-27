"""Observable article and created-Babel records used by serving."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import ARTICLE_KEY_PATTERN, FrozenContract


class DuplicateCreatorSource(ValueError):
    """One creator attempted to reuse a source article in one run."""


class HiddenFieldLeakage(ValueError):
    """An observable payload contains simulator-only state."""


class ObservableArticle(FrozenContract):
    articleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    period: Literal["2026-06", "2026-07"]
    canonicalTitle: str = Field(min_length=1)
    contentHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1)


class CreatedBabel(FrozenContract):
    babelId: UUID
    runId: UUID
    creatorId: UUID
    sourceArticleKey: str = Field(pattern=ARTICLE_KEY_PATTERN)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    createdAtNs: int = Field(ge=0)


class VectorRecord(FrozenContract):
    babel: CreatedBabel
    catalogContentHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    embeddingSpaceId: UUID
    servingModelId: UUID
    materializedModelVersion: int = Field(ge=0)
    vector: tuple[float, ...] = Field(min_length=100, max_length=100)


def ensure_unique_sources(rows: Iterable[CreatedBabel]) -> None:
    seen: set[tuple[UUID, UUID, str]] = set()
    for row in rows:
        key = (row.runId, row.creatorId, row.sourceArticleKey)
        if key in seen:
            raise DuplicateCreatorSource(
                "creator may not create the same source twice in one run"
            )
        seen.add(key)


def reject_hidden_fields(value: object) -> None:
    """Reject hidden simulator keys at any observable transport boundary."""
    forbidden = ("graph", "ppr", "clickstream", "profile", "random", "seedweight")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(part in str(key).casefold() for part in forbidden):
                raise HiddenFieldLeakage(f"hidden field in observable payload: {key}")
            reject_hidden_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_hidden_fields(item)


__all__ = [
    "CreatedBabel",
    "DuplicateCreatorSource",
    "HiddenFieldLeakage",
    "ObservableArticle",
    "VectorRecord",
    "ensure_unique_sources",
    "reject_hidden_fields",
]
