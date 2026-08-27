from __future__ import annotations

from collections import OrderedDict
import threading
from uuid import UUID

import numpy as np

from babel_online.model.qwen_encoder import Qwen100Encoder
from babel_online.model.source_vector_cache import SourceVectorResolver, VectorCacheKey


RUN_A = UUID("00000000-0000-5000-8000-000000000001")
RUN_B = UUID("00000000-0000-5000-8000-000000000002")
MODEL = UUID("00000000-0000-5000-8000-000000000003")
SPACE = UUID("00000000-0000-5000-8000-000000000004")
BABEL_A = UUID("00000000-0000-5000-8000-000000000101")
BABEL_B = UUID("00000000-0000-5000-8000-000000000102")


def key(babel_id: UUID, *, run_id: UUID = RUN_A) -> VectorCacheKey:
    return VectorCacheKey(
        run_id=run_id,
        babel_id=babel_id,
        model_id=MODEL,
        model_version=7,
        embedding_space_id=SPACE,
    )


def raw(axis: int, magnitude: float = 1.0) -> np.ndarray:
    value = np.zeros(100, dtype="<f4")
    value[axis] = magnitude
    return value


class FakeQwen(Qwen100Encoder):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.stack([raw(0, 2.0) for _ in texts])


def test_new_root_encodes_once_then_existing_walk_hits_exact_bytes() -> None:
    encoder = FakeQwen()
    loads: list[VectorCacheKey] = []

    def load_active(cache_key: VectorCacheKey) -> np.ndarray:
        loads.append(cache_key)
        return raw(1, 3.0)

    resolver = SourceVectorResolver(encoder, load_active=load_active, capacity=2)
    encoded = resolver.resolve_new_root(key(BABEL_A), title="Root", lead_text="Lead")
    cached = resolver.resolve_existing(key(BABEL_A))

    assert encoded.origin == "qwen_encode"
    assert cached.origin == "cache_hit"
    assert encoder.calls == [["Root\n\nLead"]]
    assert loads == []
    assert encoded.vector.tobytes() == cached.vector.tobytes() == raw(0, 2.0).tobytes()
    assert float(np.linalg.norm(cached.vector)) == 2.0  # resolver never renormalizes


def test_existing_miss_loads_pgvector_and_lru_evicts_oldest() -> None:
    loaded = {BABEL_A: raw(1, 3.0), BABEL_B: raw(2, 4.0)}
    calls: list[VectorCacheKey] = []

    def load_active(cache_key: VectorCacheKey) -> np.ndarray:
        calls.append(cache_key)
        return loaded[cache_key.babel_id]

    resolver = SourceVectorResolver(FakeQwen(), load_active=load_active, capacity=1)

    assert resolver.resolve_existing(key(BABEL_A)).origin == "pgvector_load"
    assert resolver.resolve_existing(key(BABEL_A)).origin == "cache_hit"
    assert resolver.resolve_existing(key(BABEL_B)).origin == "pgvector_load"
    assert resolver.resolve_existing(key(BABEL_A)).origin == "pgvector_load"
    assert [item.babel_id for item in calls] == [BABEL_A, BABEL_B, BABEL_A]


def test_cache_identity_includes_run_model_version_and_space() -> None:
    calls: list[VectorCacheKey] = []

    def load_active(cache_key: VectorCacheKey) -> np.ndarray:
        calls.append(cache_key)
        return raw(3, 5.0)

    resolver = SourceVectorResolver(FakeQwen(), load_active=load_active, capacity=4)
    resolver.resolve_existing(key(BABEL_A, run_id=RUN_A))
    other_run = resolver.resolve_existing(key(BABEL_A, run_id=RUN_B))

    assert other_run.origin == "pgvector_load"
    assert calls[0].run_id != calls[1].run_id
    assert other_run.vector.tobytes() == raw(3, 5.0).tobytes()


def test_resolver_rejects_non_float32_or_nonfinite_database_bytes() -> None:
    resolver = SourceVectorResolver(
        FakeQwen(),
        load_active=lambda _key: np.full(100, np.nan, dtype=np.float32),
        capacity=1,
    )

    try:
        resolver.resolve_existing(key(BABEL_A))
    except ValueError as error:
        assert "finite float32" in str(error)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("nonfinite vector was accepted")


def test_cache_hit_and_eviction_are_one_atomic_lru_operation() -> None:
    reader_fetched = threading.Event()
    allow_reader_move = threading.Event()
    eviction_completed = threading.Event()

    class ControlledCache(OrderedDict):
        def get(self, cache_key, default=None):
            value = super().get(cache_key, default)
            if (
                threading.current_thread().name == "cache-reader"
                and cache_key == key(BABEL_A)
            ):
                reader_fetched.set()
                assert allow_reader_move.wait(2)
            return value

        def popitem(self, last=True):
            value = super().popitem(last=last)
            eviction_completed.set()
            return value

    resolver = SourceVectorResolver(
        FakeQwen(), load_active=lambda cache_key: raw(cache_key.babel_id.int % 10), capacity=1
    )
    resolver._cache = ControlledCache({key(BABEL_A): raw(0)})
    results = []
    errors = []

    def read_a() -> None:
        try:
            results.append(resolver.resolve_existing(key(BABEL_A)))
        except Exception as error:  # pragma: no cover - assertion captures it
            errors.append(error)

    def load_b() -> None:
        try:
            resolver.resolve_existing(key(BABEL_B))
        except Exception as error:  # pragma: no cover - assertion captures it
            errors.append(error)

    reader = threading.Thread(target=read_a, name="cache-reader")
    evictor = threading.Thread(target=load_b, name="cache-evictor")
    reader.start()
    assert reader_fetched.wait(1)
    evictor.start()
    eviction_completed.wait(0.25)
    allow_reader_move.set()
    reader.join(2)
    evictor.join(2)

    assert errors == []
    assert results[0].origin == "cache_hit"
    assert results[0].vector.tobytes() == raw(0).tobytes()
