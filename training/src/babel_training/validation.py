"""Exact, deterministic validation for the small distillation pilot pool."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_DATASET_REPO = "dhelmy990/babel-wikipedia-experiment"
DEFAULT_DATASET_CONFIG = "distillation_2016"
DEFAULT_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"


@dataclass(frozen=True, slots=True)
class InterviewTrainingPlanV1:
    """Frozen pre-interview training and exact-validation budget."""

    smoke_rows: int = 1_000
    train_rows: int = 50_000
    validation_rows: int = 5_000
    test_rows: int = 5_000
    epochs: int = 1
    max_length: int = 384
    exact_index: str = "faiss.IndexFlatIP"
    validation_candidate_rows: int = 5_000
    seed: str = "babel-interview-2016-v1"
    policy_version: str = "interview-training-selection-v1"

    def __post_init__(self) -> None:
        counts = (
            self.smoke_rows,
            self.train_rows,
            self.validation_rows,
            self.test_rows,
            self.validation_candidate_rows,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in counts
        ):
            raise ValueError("interview row counts must be positive integers")
        if self.smoke_rows > self.train_rows:
            raise ValueError("smoke rows must be a prefix of the selected train rows")
        if self.validation_candidate_rows != self.validation_rows:
            raise ValueError("exact validation candidates must equal validation rows")
        if self.epochs != 1:
            raise ValueError("pre-interview training is exactly one epoch")
        if not 256 <= self.max_length <= 512:
            raise ValueError("max_length must be in the measured-memory range 256..512")
        if self.exact_index != "faiss.IndexFlatIP":
            raise ValueError("pre-interview validation requires faiss.IndexFlatIP")
        if self.seed != "babel-interview-2016-v1":
            raise ValueError("interview selection seed is frozen")
        if self.policy_version != "interview-training-selection-v1":
            raise ValueError("interview selection policy version is frozen")


@dataclass(frozen=True, slots=True)
class FullValidationPlanV1:
    """Frozen bounded-search contract for the complete 2016 corpus."""

    corpus_rows: int
    monitor_query_count: int
    monitor_candidate_count: int
    final_query_count: int
    final_candidate_count: int
    exact_index: str = "faiss.IndexFlatIP"
    exact_dtype: str = "normalized_float32"
    metrics: tuple[str, ...] = (
        "recall_at_10",
        "recall_at_50",
        "ndcg_at_10",
        "ndcg_at_50",
        "mean_paired_cosine",
        "invalid_vector_count",
        "norm_statistics",
        "examples",
    )
    hnsw_index_scope: str = "full_corpus"
    hnsw_exact_audit_queries: int = 2_000

    @classmethod
    def for_corpus(cls, corpus_rows: int) -> "FullValidationPlanV1":
        if (
            not isinstance(corpus_rows, int)
            or isinstance(corpus_rows, bool)
            or corpus_rows <= 0
        ):
            raise ValueError("corpus_rows must be a positive integer")
        final_five_percent = int(corpus_rows * 0.05)
        final_queries = min(
            corpus_rows,
            max(10_000, min(50_000, final_five_percent)),
        )
        return cls(
            corpus_rows=corpus_rows,
            monitor_query_count=min(2_000, corpus_rows),
            monitor_candidate_count=min(50_000, corpus_rows),
            final_query_count=final_queries,
            final_candidate_count=min(100_000, corpus_rows),
            hnsw_exact_audit_queries=min(2_000, corpus_rows),
        )


def recall_at_k(
    student_neighbors: Sequence[object],
    teacher_neighbors: Sequence[object],
    k: int,
) -> float:
    """Return top-k set overlap divided by the named k."""
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be a positive integer")
    student_top = list(student_neighbors[:k])
    teacher_top = list(teacher_neighbors[:k])
    return float(len(set(student_top).intersection(teacher_top)) / k)


def ndcg_at_k(
    student_neighbors: Sequence[object],
    teacher_cosines: Mapping[object, float],
    k: int,
) -> float:
    """Score a student ordering using ``(teacher cosine + 1) / 2`` relevance."""
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be a positive integer")
    relevance: dict[object, float] = {}
    for key, cosine in teacher_cosines.items():
        value = float(cosine)
        if not math.isfinite(value):
            raise ValueError("teacher cosine relevance must be finite")
        relevance[key] = min(1.0, max(0.0, (value + 1.0) / 2.0))

    def discounted_gain(items: Sequence[object]) -> float:
        return float(
            sum(
                relevance.get(item, 0.0) / math.log2(rank + 2.0)
                for rank, item in enumerate(items[:k])
            )
        )

    ideal = sorted(relevance, key=lambda item: (-relevance[item], str(item)))[:k]
    ideal_gain = discounted_gain(ideal)
    if ideal_gain == 0.0:
        return 0.0
    return min(1.0, max(0.0, discounted_gain(student_neighbors) / ideal_gain))


@dataclass(frozen=True)
class ValidationReport(Mapping[str, object]):
    """Closed version-1 validation report accepted by the artifact exporter."""

    dataset: dict[str, object]
    model: dict[str, object]
    pool_size: int
    metrics: dict[str, float]
    invalid_vector_count: int
    norm_statistics: dict[str, float]
    examples: list[dict[str, object]]
    report_version: int = 1

    @property
    def invalid_count(self) -> int:
        """Concise alias useful in interactive notebooks."""
        return self.invalid_vector_count

    def to_dict(self) -> dict[str, object]:
        return {
            "report_version": self.report_version,
            "dataset": dict(self.dataset),
            "model": dict(self.model),
            "pool_size": self.pool_size,
            "metrics": dict(self.metrics),
            "invalid_vector_count": self.invalid_vector_count,
            "norm_statistics": dict(self.norm_statistics),
            "examples": [
                {
                    "article_key": example["article_key"],
                    "student_neighbors": list(example["student_neighbors"]),
                    "teacher_neighbors": list(example["teacher_neighbors"]),
                }
                for example in self.examples
            ],
        }

    def write_json(self, destination: str | Path) -> None:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        Path(destination).write_text(payload + "\n", encoding="utf-8")

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 8


def _matrix(name: str, value: Any) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a rectangular numeric matrix") from error
    if matrix.ndim != 2:
        raise ValueError(f"{name} must be a two-dimensional matrix")
    return matrix


def _norm_summary(norms: np.ndarray) -> tuple[float, float, float]:
    usable = norms[np.isfinite(norms) & (norms > 0)]
    if usable.size == 0:
        return 0.0, 0.0, 0.0
    return (
        float(np.min(usable)),
        float(np.mean(usable, dtype=np.float32)),
        float(np.max(usable)),
    )


def _rank(scores: np.ndarray, keys: Sequence[str], query: int, limit: int) -> list[int]:
    candidates = (index for index in range(len(keys)) if index != query)
    return sorted(candidates, key=lambda index: (-float(scores[index]), keys[index]))[:limit]


def validate_embeddings(
    article_keys: Sequence[str],
    student: Any,
    teacher: Any,
    *,
    dataset_revision: str = "unresolved",
    model_revision: str = DEFAULT_MODEL_REVISION,
    tokenizer_revision: str | None = None,
    dataset_repo_id: str = DEFAULT_DATASET_REPO,
    dataset_config: str = DEFAULT_DATASET_CONFIG,
    dataset_manifest_sha256: str = "unavailable",
    dataset_readiness_sha256: str = "unavailable",
    model_id: str = DEFAULT_MODEL_ID,
    split: str = "validation",
    subset: str = "pilot",
    example_count: int = 5,
    chunk_size: int = 256,
) -> ValidationReport:
    """Compute exact FP32 cosine metrics for a bounded held-out pool.

    Rows with a non-finite or zero-norm vector on either side are counted and
    excluded from retrieval metrics. Norm summaries describe finite positive
    original norms, before normalization.
    """
    keys = list(article_keys)
    if not keys:
        raise ValueError("validation pool must be non-empty")
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("article keys must be non-empty strings")
    if len(set(keys)) != len(keys):
        raise ValueError("article keys must be unique")
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if not isinstance(example_count, int) or isinstance(example_count, bool) or example_count < 0:
        raise ValueError("example_count must be a nonnegative integer")

    student_matrix = _matrix("student", student)
    teacher_matrix = _matrix("teacher", teacher)
    if student_matrix.shape[0] != len(keys) or teacher_matrix.shape[0] != len(keys):
        raise ValueError("article key and vector row counts must match")
    if student_matrix.shape[1] != teacher_matrix.shape[1]:
        raise ValueError("student and teacher dimensions must match")
    if student_matrix.shape[1] == 0:
        raise ValueError("vector dimensions must be non-empty")

    student_norms = np.linalg.norm(student_matrix, axis=1).astype(np.float32)
    teacher_norms = np.linalg.norm(teacher_matrix, axis=1).astype(np.float32)
    valid = (
        np.all(np.isfinite(student_matrix), axis=1)
        & np.all(np.isfinite(teacher_matrix), axis=1)
        & np.isfinite(student_norms)
        & np.isfinite(teacher_norms)
        & (student_norms > 0)
        & (teacher_norms > 0)
    )
    valid_indices = np.flatnonzero(valid)
    valid_keys = [keys[int(index)] for index in valid_indices]
    student_valid = student_matrix[valid] / student_norms[valid, None]
    teacher_valid = teacher_matrix[valid] / teacher_norms[valid, None]

    paired = np.sum(student_valid * teacher_valid, axis=1, dtype=np.float32)
    metric_values: dict[int, list[tuple[float, float]]] = {10: [], 50: []}
    example_by_key: dict[str, dict[str, object]] = {}
    neighbor_limit = min(50, max(0, len(valid_keys) - 1))

    for start in range(0, len(valid_keys), chunk_size):
        stop = min(start + chunk_size, len(valid_keys))
        student_similarities = np.matmul(
            student_valid[start:stop], student_valid.T, dtype=np.float32
        )
        teacher_similarities = np.matmul(
            teacher_valid[start:stop], teacher_valid.T, dtype=np.float32
        )
        for offset, query in enumerate(range(start, stop)):
            student_rank = _rank(
                student_similarities[offset], valid_keys, query, neighbor_limit
            )
            teacher_rank = _rank(
                teacher_similarities[offset], valid_keys, query, neighbor_limit
            )
            relevance = {
                candidate: float(teacher_similarities[offset, candidate])
                for candidate in range(len(valid_keys))
                if candidate != query
            }
            for k in metric_values:
                metric_values[k].append(
                    (
                        recall_at_k(student_rank, teacher_rank, k),
                        ndcg_at_k(student_rank, relevance, k),
                    )
                )
            if valid_keys[query] in sorted(valid_keys)[:example_count]:
                example_by_key[valid_keys[query]] = {
                    "article_key": valid_keys[query],
                    "student_neighbors": [valid_keys[index] for index in student_rank],
                    "teacher_neighbors": [valid_keys[index] for index in teacher_rank],
                }

    def mean_metric(k: int, position: int) -> float:
        values = [pair[position] for pair in metric_values[k]]
        return float(sum(values) / len(values)) if values else 0.0

    student_min, student_mean, student_max = _norm_summary(student_norms)
    teacher_min, teacher_mean, teacher_max = _norm_summary(teacher_norms)
    paired_mean = float(np.mean(paired, dtype=np.float32)) if paired.size else 0.0
    metrics = {
        "mean_paired_cosine": min(1.0, max(-1.0, paired_mean)),
        "recall_at_10": mean_metric(10, 0),
        "recall_at_50": mean_metric(50, 0),
        "ndcg_at_10": mean_metric(10, 1),
        "ndcg_at_50": mean_metric(50, 1),
    }
    return ValidationReport(
        dataset={
            "repo_id": dataset_repo_id,
            "config": dataset_config,
            "commit_sha": dataset_revision,
            "manifest_sha256": dataset_manifest_sha256,
            "readiness_sha256": dataset_readiness_sha256,
            "split": split,
            "subset": subset,
            "example_count": len(keys),
        },
        model={
            "id": model_id,
            "revision": model_revision,
            "tokenizer_revision": tokenizer_revision or model_revision,
        },
        pool_size=len(keys),
        metrics=metrics,
        invalid_vector_count=int(len(keys) - len(valid_keys)),
        norm_statistics={
            "student_min": student_min,
            "student_mean": student_mean,
            "student_max": student_max,
            "teacher_min": teacher_min,
            "teacher_mean": teacher_mean,
            "teacher_max": teacher_max,
        },
        examples=[example_by_key[key] for key in sorted(example_by_key)],
    )


__all__ = [
    "InterviewTrainingPlanV1",
    "FullValidationPlanV1",
    "ValidationReport",
    "ndcg_at_k",
    "recall_at_k",
    "validate_embeddings",
]
