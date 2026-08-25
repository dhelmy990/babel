from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from babel_training.config import DistillationConfig
from babel_training.validation import (
    ValidationReport,
    ndcg_at_k,
    recall_at_k,
    validate_embeddings,
)


DATASET_REVISION = "a" * 40
MODEL_REVISION = "b" * 40
ROOT = Path(__file__).resolve().parents[2]


def _validate(student: np.ndarray, teacher: np.ndarray) -> ValidationReport:
    return validate_embeddings(
        article_keys=["delta", "alpha", "charlie", "bravo"],
        student=student,
        teacher=teacher,
        dataset_revision=DATASET_REVISION,
        model_revision=MODEL_REVISION,
        dataset_manifest_sha256="c" * 64,
        dataset_readiness_sha256="d" * 64,
        example_count=4,
        chunk_size=2,
    )


def test_recall_excludes_self_and_matches_teacher_topk() -> None:
    assert recall_at_k(
        student_neighbors=[2, 3], teacher_neighbors=[2, 4], k=2
    ) == 0.5


def test_ndcg_uses_nonnegative_teacher_cosine_relevance() -> None:
    # Teacher cosines [1.0, 0.0, -1.0] become relevance [1.0, 0.5, 0.0].
    score = ndcg_at_k(
        student_neighbors=[1, 0, 2],
        teacher_cosines={0: 1.0, 1: 0.0, 2: -1.0},
        k=3,
    )
    expected = (0.5 + 1.0 / np.log2(3.0)) / (1.0 + 0.5 / np.log2(3.0))
    assert score == pytest.approx(expected)


def test_validation_is_exact_fp32_deterministic_and_excludes_self() -> None:
    teacher = np.array(
        [[1, 0, 0], [0.8, 0.6, 0], [0, 1, 0], [-1, 0, 0]],
        dtype=np.float64,
    )
    student = teacher.copy()

    first = _validate(student, teacher)
    second = _validate(student, teacher)

    assert first.to_dict() == second.to_dict()
    assert first.metrics == {
        "mean_paired_cosine": 1.0,
        "recall_at_10": 0.3,
        "recall_at_50": 0.06,
        "ndcg_at_10": 1.0,
        "ndcg_at_50": 1.0,
    }
    assert all(
        example["article_key"] not in example["student_neighbors"]
        and example["article_key"] not in example["teacher_neighbors"]
        for example in first.examples
    )
    assert [example["article_key"] for example in first.examples] == sorted(
        ["delta", "alpha", "charlie", "bravo"]
    )


def test_recall_uses_named_k_when_pool_is_smaller() -> None:
    assert recall_at_k(student_neighbors=[1], teacher_neighbors=[1], k=10) == 0.1


def test_validation_reports_invalid_vectors_and_original_norms() -> None:
    teacher = np.array([[1, 0], [0, 2], [1, 1], [-1, 0]], dtype=np.float32)
    student = np.array([[1, 0], [np.nan, 1], [0, 0], [-2, 0]], dtype=np.float32)

    report = _validate(student, teacher)

    assert report.pool_size == 4
    assert report.invalid_vector_count == 2
    assert report.metrics["mean_paired_cosine"] == 1.0
    assert report.norm_statistics == {
        "student_min": 1.0,
        "student_mean": 1.5,
        "student_max": 2.0,
        "teacher_min": 1.0,
        "teacher_mean": pytest.approx((1.0 + 2.0 + np.sqrt(2.0) + 1.0) / 4),
        "teacher_max": 2.0,
    }


def test_report_has_export_contract_and_canonical_json(tmp_path: Path) -> None:
    vectors = np.eye(4, dtype=np.float32)
    report = _validate(vectors, vectors)
    document = report.to_dict()

    assert set(document) == {
        "report_version",
        "dataset",
        "model",
        "pool_size",
        "metrics",
        "invalid_vector_count",
        "norm_statistics",
        "examples",
    }
    assert document["dataset"]["commit_sha"] == DATASET_REVISION
    assert document["model"]["revision"] == MODEL_REVISION
    destination = tmp_path / "validation-report.json"
    report.write_json(destination)
    assert destination.read_bytes().endswith(b"\n")
    assert json.loads(destination.read_bytes()) == document


def test_default_report_model_revision_matches_frozen_training_config() -> None:
    vectors = np.eye(2, dtype=np.float32)
    report = validate_embeddings(["a", "b"], vectors, vectors)

    assert report.model["revision"] == DistillationConfig().model_revision
    assert report.model["tokenizer_revision"] == DistillationConfig().model_revision


def test_fixed_neighbor_examples_match_checked_in_fixture() -> None:
    teacher = np.array(
        [[1, 0, 0], [0.8, 0.6, 0], [0, 1, 0], [-1, 0, 0]],
        dtype=np.float32,
    )
    report = _validate(teacher.copy(), teacher)
    expected = json.loads(
        (ROOT / "fixtures/distillation/expected-neighbors.json").read_bytes()
    )
    assert report.examples == expected


@pytest.mark.parametrize(
    ("article_keys", "student", "teacher", "message"),
    [
        (["a"], np.ones((1, 2)), np.ones((2, 2)), "row counts"),
        (["a", "a"], np.ones((2, 2)), np.ones((2, 2)), "unique"),
        (["a", "b"], np.ones((2, 2)), np.ones((2, 3)), "dimensions"),
        ([], np.empty((0, 2)), np.empty((0, 2)), "non-empty"),
    ],
)
def test_validation_rejects_malformed_pools(
    article_keys: list[str], student: np.ndarray, teacher: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_embeddings(article_keys=article_keys, student=student, teacher=teacher)
