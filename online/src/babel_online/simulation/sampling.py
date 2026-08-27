"""Creator-local source sampling without replacement."""

from __future__ import annotations

import random
from collections.abc import Iterable


class EligibleSupportExhausted(RuntimeError):
    pass


class SourceSampler:
    def __init__(self, source_article_keys: Iterable[str], *, seed: int) -> None:
        keys = list(source_article_keys)
        if not keys or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("source article keys must be nonblank")
        if len(set(keys)) != len(keys):
            raise ValueError("source article keys must be unique")
        random.Random(seed).shuffle(keys)
        self._keys = tuple(keys)
        self._next = 0

    @property
    def remaining(self) -> int:
        return len(self._keys) - self._next

    def take(self) -> str:
        if self._next >= len(self._keys):
            raise EligibleSupportExhausted("eligible source support is exhausted")
        key = self._keys[self._next]
        self._next += 1
        return key

    def state_dict(self) -> dict[str, object]:
        return {"keys": list(self._keys), "next": self._next}

    def load_state_dict(self, state: dict[str, object]) -> None:
        if state.get("keys") != list(self._keys):
            raise ValueError("sampler source identity mismatch")
        next_index = state.get("next")
        if (
            not isinstance(next_index, int)
            or isinstance(next_index, bool)
            or not 0 <= next_index <= len(self._keys)
        ):
            raise ValueError("sampler next index is invalid")
        self._next = next_index


__all__ = ["EligibleSupportExhausted", "SourceSampler"]
