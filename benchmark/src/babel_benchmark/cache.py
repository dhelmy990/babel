"""Cache and fixed-vector identity contracts for retrieval-only comparisons."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RetrievalInputIdentity:
    ordered_ids_sha256: str
    vector_bytes_sha256: str
    snapshot_sha256: str
    queries_sha256: str


@dataclass(frozen=True, slots=True)
class RetrievalRunEvidence:
    backend: Literal["pgvector", "hnswlib"]
    inputIdentity: RetrievalInputIdentity
    preparationNs: int
    steadyLatencyNs: tuple[int, ...]
    memoryBytes: int
    neighborsByQuery: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ExactCosineOracleEvidence:
    """Exact cosine neighbors for the same frozen vectors and queries."""

    inputIdentity: RetrievalInputIdentity
    neighborsByQuery: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class RetrievalComparison:
    pgvectorRecallAt10: float
    pgvectorRecallAt50: float
    hnswlibRecallAt10: float
    hnswlibRecallAt50: float
    pgvectorPreparationNs: int
    hnswlibPreparationNs: int
    pgvectorSteadyLatencyNs: tuple[int, ...]
    hnswlibSteadyLatencyNs: tuple[int, ...]
    pgvectorMemoryBytes: int
    hnswlibMemoryBytes: int


def retrieval_input_identity(
    ordered_ids: Sequence[str],
    vector_bytes: bytes,
    snapshot_sha256: str,
    query_bytes: bytes,
) -> RetrievalInputIdentity:
    joined = b"\0".join(value.encode("utf-8") for value in ordered_ids)
    return RetrievalInputIdentity(
        ordered_ids_sha256=hashlib.sha256(joined).hexdigest(),
        vector_bytes_sha256=hashlib.sha256(vector_bytes).hexdigest(),
        snapshot_sha256=snapshot_sha256,
        queries_sha256=hashlib.sha256(query_bytes).hexdigest(),
    )


def recall_at(exact: Sequence[str], approximate: Iterable[str], k: int) -> float:
    if k <= 0 or len(exact) < k:
        raise ValueError("recall audit requires at least k exact results")
    expected = set(exact[:k])
    observed = set(list(approximate)[:k])
    return len(expected & observed) / k


def compare_retrieval_backends(
    oracle: ExactCosineOracleEvidence,
    pgvector: RetrievalRunEvidence,
    hnswlib: RetrievalRunEvidence,
) -> RetrievalComparison:
    if pgvector.backend != "pgvector" or hnswlib.backend != "hnswlib":
        raise ValueError("retrieval evidence must keep pgvector and hnswlib separate")
    if not (oracle.inputIdentity == pgvector.inputIdentity == hnswlib.inputIdentity):
        raise ValueError("retrieval comparison requires checksum-identical inputs")
    query_count = len(oracle.neighborsByQuery)
    if query_count == 0 or any(
        len(rows) != query_count
        for rows in (pgvector.neighborsByQuery, hnswlib.neighborsByQuery)
    ):
        raise ValueError("retrieval comparison requires one result list per query")

    def mean_recall(evidence: RetrievalRunEvidence, k: int) -> float:
        return (
            sum(
                recall_at(exact, approximate, k)
                for exact, approximate in zip(
                    oracle.neighborsByQuery, evidence.neighborsByQuery, strict=True
                )
            )
            / query_count
        )

    return RetrievalComparison(
        pgvectorRecallAt10=mean_recall(pgvector, 10),
        pgvectorRecallAt50=mean_recall(pgvector, 50),
        hnswlibRecallAt10=mean_recall(hnswlib, 10),
        hnswlibRecallAt50=mean_recall(hnswlib, 50),
        pgvectorPreparationNs=pgvector.preparationNs,
        hnswlibPreparationNs=hnswlib.preparationNs,
        pgvectorSteadyLatencyNs=pgvector.steadyLatencyNs,
        hnswlibSteadyLatencyNs=hnswlib.steadyLatencyNs,
        pgvectorMemoryBytes=pgvector.memoryBytes,
        hnswlibMemoryBytes=hnswlib.memoryBytes,
    )


__all__ = [
    "RetrievalComparison",
    "ExactCosineOracleEvidence",
    "RetrievalInputIdentity",
    "RetrievalRunEvidence",
    "compare_retrieval_backends",
    "recall_at",
    "retrieval_input_identity",
]
