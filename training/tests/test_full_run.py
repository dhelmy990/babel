from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "training" / "src"))

from babel_training.full_run import (  # noqa: E402
    audit_hnsw_against_exact,
    build_exact_index,
    persist_interview_training_selection,
    persist_validation_selection,
)
from babel_training.validation import (  # noqa: E402
    FullValidationPlanV1,
    InterviewTrainingPlanV1,
)


def test_interview_training_defaults() -> None:
    plan = InterviewTrainingPlanV1()

    assert (plan.smoke_rows, plan.train_rows) == (1_000, 50_000)
    assert (plan.validation_rows, plan.test_rows) == (5_000, 5_000)
    assert (plan.epochs, plan.max_length) == (1, 384)
    assert plan.exact_index == "faiss.IndexFlatIP"
    assert plan.validation_candidate_rows == 5_000
    assert plan.seed == "babel-interview-2016-v1"


def test_interview_selection_preserves_splits_order_and_smoke_prefix(
    tmp_path: Path,
) -> None:
    plan = InterviewTrainingPlanV1(
        smoke_rows=2,
        train_rows=4,
        validation_rows=2,
        test_rows=2,
        validation_candidate_rows=2,
    )
    rows = [
        {"article_key": f"train-{index}", "split": "train"}
        for index in range(7)
    ] + [
        {"article_key": f"validation-{index}", "split": "validation"}
        for index in range(4)
    ] + [
        {"article_key": f"test-{index}", "split": "test"}
        for index in range(4)
    ]

    selection = persist_interview_training_selection(
        reversed(rows),
        plan,
        "a" * 40,
        tmp_path / "interview-training-selection-v1.json",
    )

    expected_train = tuple(
        sorted(
            (f"train-{index}" for index in range(7)),
            key=lambda key: (
                hashlib.sha256(
                    b"babel-interview-2016-v1\0" + key.encode("utf-8")
                ).hexdigest(),
                key,
            ),
        )[:4]
    )
    assert selection.train_article_keys == expected_train
    assert selection.smoke_article_keys == expected_train[:2]
    assert len(selection.validation_article_keys) == 2
    assert len(selection.test_article_keys) == 2
    document = json.loads(
        (tmp_path / "interview-training-selection-v1.json").read_text()
    )
    assert document["policy_version"] == "interview-training-selection-v1"
    assert document["seed"] == "babel-interview-2016-v1"
    assert document["dataset_commit_sha"] == "a" * 40
    for name in ("smoke", "train", "validation", "test"):
        keys = document["selections"][name]["article_keys"]
        payload = json.dumps(keys, separators=(",", ":")).encode()
        assert document["selections"][name]["sha256"] == hashlib.sha256(
            payload
        ).hexdigest()


def test_interview_selection_rejects_insufficient_or_duplicate_rows(
    tmp_path: Path,
) -> None:
    plan = InterviewTrainingPlanV1(
        smoke_rows=1,
        train_rows=2,
        validation_rows=1,
        test_rows=1,
        validation_candidate_rows=1,
    )
    rows = [
        {"article_key": "one", "split": "train"},
        {"article_key": "one", "split": "train"},
        {"article_key": "validation", "split": "validation"},
        {"article_key": "test", "split": "test"},
    ]

    with pytest.raises(ValueError, match="unique"):
        persist_interview_training_selection(
            rows, plan, "a" * 40, tmp_path / "selection.json"
        )


def test_validation_defaults_for_complete_corpus() -> None:
    plan = FullValidationPlanV1.for_corpus(400_000)

    assert (plan.monitor_query_count, plan.monitor_candidate_count) == (2_000, 50_000)
    assert (plan.final_query_count, plan.final_candidate_count) == (20_000, 100_000)
    assert plan.exact_index == "faiss.IndexFlatIP"
    assert plan.exact_dtype == "normalized_float32"
    assert plan.metrics == (
        "recall_at_10",
        "recall_at_50",
        "ndcg_at_10",
        "ndcg_at_50",
        "mean_paired_cosine",
        "invalid_vector_count",
        "norm_statistics",
        "examples",
    )
    assert plan.hnsw_index_scope == "full_corpus"
    assert plan.hnsw_exact_audit_queries == 2_000


def test_validation_counts_clamp_to_available_rows() -> None:
    small = FullValidationPlanV1.for_corpus(137)
    boundary = FullValidationPlanV1.for_corpus(200_000)

    assert (small.monitor_query_count, small.monitor_candidate_count) == (137, 137)
    assert (small.final_query_count, small.final_candidate_count) == (137, 137)
    assert small.hnsw_exact_audit_queries == 137
    assert boundary.final_query_count == 10_000
    assert boundary.final_candidate_count == 100_000


def test_selected_article_keys_and_checksum_are_deterministic_and_persisted(
    tmp_path: Path,
) -> None:
    keys = [f"enwiki:2016-10-01:{index}" for index in range(1, 401)]
    plan = FullValidationPlanV1.for_corpus(len(keys))

    first = persist_validation_selection(keys, plan, tmp_path / "selection.json")
    second = persist_validation_selection(reversed(keys), plan, tmp_path / "same.json")

    assert first == second
    document = json.loads((tmp_path / "selection.json").read_text())
    assert document["schema_version"] == 1
    assert document["monitor_query_article_keys"] == list(first.monitor_query_article_keys)
    assert document["final_query_article_keys"] == list(first.final_query_article_keys)
    canonical = json.dumps(
        {
            "final_candidate_article_keys": document["final_candidate_article_keys"],
            "final_query_article_keys": document["final_query_article_keys"],
            "monitor_candidate_article_keys": document["monitor_candidate_article_keys"],
            "monitor_query_article_keys": document["monitor_query_article_keys"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert document["article_keys_sha256"] == hashlib.sha256(canonical).hexdigest()


def test_exact_index_receives_normalized_contiguous_float32(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class IndexFlatIP:
        def __init__(self, dimension: int) -> None:
            captured["dimension"] = dimension

        def add(self, values: np.ndarray) -> None:
            captured["values"] = values

    monkeypatch.setitem(sys.modules, "faiss", SimpleNamespace(IndexFlatIP=IndexFlatIP))
    index = build_exact_index([[3.0, 4.0], [0.0, 2.0]])

    assert isinstance(index, IndexFlatIP)
    values = captured["values"]
    assert isinstance(values, np.ndarray)
    assert values.dtype == np.float32
    assert values.flags.c_contiguous
    np.testing.assert_allclose(np.linalg.norm(values, axis=1), np.ones(2), atol=1e-6)


def test_hnsw_audit_requires_full_index_and_uses_exact_queries(monkeypatch) -> None:
    exact_labels = np.tile(np.arange(50), (60, 1))

    class Exact:
        def add(self, values: np.ndarray) -> None:
            assert values.shape == (60, 2)

        def search(self, queries: np.ndarray, k: int):
            assert queries.shape == (60, 2)
            assert k == 50
            return np.ones((2, 50), dtype=np.float32), exact_labels

    monkeypatch.setitem(
        sys.modules,
        "faiss",
        SimpleNamespace(IndexFlatIP=lambda dimension: Exact()),
    )

    class Hnsw:
        def get_current_count(self) -> int:
            return 60

        def knn_query(self, queries: np.ndarray, k: int):
            return exact_labels.copy(), np.zeros((2, k), dtype=np.float32)

    candidates = np.column_stack((np.ones(60), np.arange(1, 61)))
    queries = np.column_stack((np.ones(60), np.arange(1, 61)))

    report = audit_hnsw_against_exact(Hnsw(), candidates, queries)

    assert report == {"audit_queries": 60, "recall_at_10": 1.0, "recall_at_50": 1.0}


def test_hnsw_audit_rejects_fewer_than_the_frozen_exact_query_count() -> None:
    class Hnsw:
        def get_current_count(self) -> int:
            return 60

    candidates = np.column_stack((np.ones(60), np.arange(1, 61)))
    queries = np.asarray([[1.0, 2.0], [2.0, 1.0]])

    with pytest.raises(ValueError, match="exactly 60"):
        audit_hnsw_against_exact(Hnsw(), candidates, queries)
