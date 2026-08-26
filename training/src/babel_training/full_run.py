"""Deterministic, persisted sampling contract for complete-run validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .validation import FullValidationPlanV1


@dataclass(frozen=True, slots=True)
class ValidationSelectionV1:
    monitor_query_article_keys: tuple[str, ...]
    monitor_candidate_article_keys: tuple[str, ...]
    final_query_article_keys: tuple[str, ...]
    final_candidate_article_keys: tuple[str, ...]
    article_keys_sha256: str

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "monitor_query_article_keys": list(self.monitor_query_article_keys),
            "monitor_candidate_article_keys": list(self.monitor_candidate_article_keys),
            "final_query_article_keys": list(self.final_query_article_keys),
            "final_candidate_article_keys": list(self.final_candidate_article_keys),
            "article_keys_sha256": self.article_keys_sha256,
        }


def _ranked(keys: Iterable[str]) -> list[str]:
    values = list(keys)
    if any(not isinstance(key, str) or not key for key in values):
        raise ValueError("article keys must be nonempty strings")
    if len(set(values)) != len(values):
        raise ValueError("article keys must be unique")
    return sorted(
        values,
        key=lambda key: (hashlib.sha256(key.encode("utf-8")).hexdigest(), key),
    )


def _selection_digest(document: dict[str, list[str]]) -> str:
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def persist_validation_selection(
    article_keys: Iterable[str],
    plan: FullValidationPlanV1,
    destination: str | os.PathLike[str],
) -> ValidationSelectionV1:
    """Select hash-ranked fixed pools and atomically persist their checksum."""
    ranked = _ranked(article_keys)
    if len(ranked) != plan.corpus_rows:
        raise ValueError("article key count does not match validation plan corpus")
    pools = {
        "monitor_query_article_keys": ranked[: plan.monitor_query_count],
        "monitor_candidate_article_keys": ranked[: plan.monitor_candidate_count],
        "final_query_article_keys": ranked[: plan.final_query_count],
        "final_candidate_article_keys": ranked[: plan.final_candidate_count],
    }
    selection = ValidationSelectionV1(
        monitor_query_article_keys=tuple(pools["monitor_query_article_keys"]),
        monitor_candidate_article_keys=tuple(pools["monitor_candidate_article_keys"]),
        final_query_article_keys=tuple(pools["final_query_article_keys"]),
        final_candidate_article_keys=tuple(pools["final_candidate_article_keys"]),
        article_keys_sha256=_selection_digest(pools),
    )
    payload = json.dumps(
        selection.to_document(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    path = Path(destination)
    if path.exists() and path.read_bytes() != payload:
        raise ValueError("refusing to replace a different validation selection")
    _atomic_write(path, payload)
    return selection


def normalized_float32(vectors: object) -> np.ndarray:
    """Validate and L2-normalize a finite matrix for cosine-as-IP search."""
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError("vectors must be a rectangular numeric matrix") from error
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError("vectors must be a nonempty two-dimensional matrix")
    if not np.isfinite(matrix).all():
        raise ValueError("vectors must be finite")
    norms = np.linalg.norm(matrix, axis=1).astype(np.float32)
    if not np.isfinite(norms).all() or np.any(norms <= 0):
        raise ValueError("vectors must have positive finite norms")
    return np.ascontiguousarray(matrix / norms[:, None], dtype=np.float32)


def build_exact_index(vectors: object) -> object:
    """Build the frozen exact normalized float32 ``faiss.IndexFlatIP`` oracle."""
    normalized = normalized_float32(vectors)
    try:
        import faiss
    except ImportError as error:  # pragma: no cover - depends on optional runtime
        raise RuntimeError("full validation requires faiss-cpu") from error
    index = faiss.IndexFlatIP(int(normalized.shape[1]))
    index.add(normalized)
    return index


def audit_hnsw_against_exact(
    hnsw_index: object,
    candidate_vectors: object,
    query_vectors: object,
) -> dict[str, int | float]:
    """Audit a full-corpus HNSW index against the exact IP oracle."""
    candidates = normalized_float32(candidate_vectors)
    queries = normalized_float32(query_vectors)
    required_queries = min(2_000, candidates.shape[0])
    if queries.shape[0] != required_queries:
        raise ValueError(
            f"HNSW exact audit requires exactly {required_queries} queries"
        )
    get_count = getattr(hnsw_index, "get_current_count", None)
    if not callable(get_count) or int(get_count()) != candidates.shape[0]:
        raise ValueError("HNSW audit requires an index over the full corpus")
    query = getattr(hnsw_index, "knn_query", None)
    if not callable(query):
        raise TypeError("HNSW index must expose knn_query")
    limit = min(50, candidates.shape[0])
    exact = build_exact_index(candidates)
    _scores, exact_labels = exact.search(queries, limit)  # type: ignore[attr-defined]
    approximate_labels, _distances = query(queries, k=limit)
    exact_array = np.asarray(exact_labels)
    approximate_array = np.asarray(approximate_labels)
    if exact_array.shape != approximate_array.shape:
        raise ValueError("HNSW and exact audit result shapes disagree")

    def mean_recall(k: int) -> float:
        effective = min(k, limit)
        values = [
            len(set(approximate_array[row, :effective]).intersection(
                exact_array[row, :effective]
            ))
            / effective
            for row in range(queries.shape[0])
        ]
        return float(sum(values) / len(values))

    return {
        "audit_queries": int(queries.shape[0]),
        "recall_at_10": mean_recall(10),
        "recall_at_50": mean_recall(50),
    }


__all__ = [
    "ValidationSelectionV1",
    "audit_hnsw_against_exact",
    "build_exact_index",
    "normalized_float32",
    "persist_validation_selection",
]
