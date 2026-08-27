from __future__ import annotations

from uuid import uuid4

import pytest

from babel_benchmark.scale import (
    ControlledConditionReceipt,
    FrozenPopulationReceipt,
    FrozenWorkloadReceipt,
    ManualCohortGate,
    ScaleStopEvaluator,
    ScaleWindow,
    cohort_condition_matrix,
    validate_controlled_cohort,
)


def test_formal_receipts_require_complete_pinned_population_and_workload() -> None:
    population = FrozenPopulationReceipt(
        dataset_repository="dhelmy990/babel-wikipedia-experiment",
        dataset_revision="0" * 40,
        model_id=uuid4(),
        model_revision="1" * 40,
        june_created=5_000,
        july_created=5_000,
        distinct_babel_count=10_000,
        indexed_count=10_000,
        formal_threshold=10_000,
        ordered_manifest_path="population/ordered-babels.jsonl",
        ordered_manifest_sha256="a" * 64,
        vector_bytes_sha256="b" * 64,
        creator_source_unique=True,
        creator_count=50,
        round_robin_verified=True,
        cross_month_used_source_invariant_verified=True,
        creator_round_robin_manifest_sha256="3" * 64,
        cross_month_used_sources_manifest_sha256="4" * 64,
    )
    workload = FrozenWorkloadReceipt(
        request_count=10_000,
        request_corpus_sha256="c" * 64,
        feedback_sha256="d" * 64,
        creator_schedule_sha256="e" * 64,
        event_mix_sha256="f" * 64,
        start_draws_sha256="1" * 64,
        continuation_draws_sha256="2" * 64,
        creator_schedule_scope="creator_local",
        start_probability=0.4,
        continuation_probability=0.4,
        independent_draw_streams=True,
    )

    assert population.formal_measurement_ready
    assert workload.replay_identity == (
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "1" * 64,
        "2" * 64,
    )

    with pytest.raises(ValueError, match="created and indexed"):
        population.model_copy(update={"indexed_count": 9_999}).validate_for_formal()

    for update in (
        {"june_created": 4_999, "distinct_babel_count": 9_999},
        {"july_created": 4_999, "distinct_babel_count": 9_999},
        {"creator_count": 49},
        {"round_robin_verified": False},
        {"cross_month_used_source_invariant_verified": False},
        {"cross_month_used_sources_manifest_sha256": "3" * 64},
    ):
        with pytest.raises(ValueError, match="formal population"):
            population.model_copy(update=update).validate_for_formal()

    with pytest.raises(ValueError, match="independent"):
        FrozenWorkloadReceipt(
            **{
                **workload.model_dump(),
                "continuation_draws_sha256": workload.start_draws_sha256,
            }
        )


def test_first_cohort_uses_nine_conditions_and_higher_cohorts_use_six() -> None:
    first = cohort_condition_matrix(50)
    one_hundred = cohort_condition_matrix(100, selected_split="same_host_isolated")
    five_hundred = cohort_condition_matrix(500, selected_split="same_host_isolated")

    assert len(first) == 9
    assert {row.topology for row in first} == {
        "same_process",
        "same_host_split",
        "same_host_isolated",
    }
    assert len(one_hundred) == len(five_hundred) == 6
    assert {row.topology for row in one_hundred} == {
        "same_process",
        "same_host_isolated",
    }
    assert {row.load_mode for row in one_hundred} == {
        "serving_only",
        "training_no_activation",
        "training_and_activation",
    }
    assert len({row.condition_id for row in first}) == 9
    assert len({row.condition_id for row in one_hundred}) == 6

    with pytest.raises(ValueError, match="selected split"):
        cohort_condition_matrix(100, selected_split="same_process")  # type: ignore[arg-type]


def test_cohort_ladder_requires_explicit_operator_approval() -> None:
    gate = ManualCohortGate()

    with pytest.raises(PermissionError, match="operator approval"):
        gate.advance(completed_cohort=50, operator_approved=False)

    assert gate.advance(completed_cohort=50, operator_approved=True) == 100
    assert gate.advance(completed_cohort=100, operator_approved=True) == 500
    assert gate.required_ladder_reached

    with pytest.raises(RuntimeError, match="stop rule"):
        gate.advance(
            completed_cohort=500,
            operator_approved=True,
            stop_reasons=("host memory exceeded 90%",),
        )


def test_scale_stop_rules_halt_immediately_for_disk_or_health_failures() -> None:
    evaluator = ScaleStopEvaluator()
    disk = evaluator.observe(
        ScaleWindow(
            duration_seconds=1,
            memory_percent=50,
            disk_free_bytes=9 * 1024**3,
            error_timeout_rate=0,
            kafka_lag=0,
            maximum_backpressure_verified=False,
        )
    )
    assert disk.stop
    assert disk.reasons == ("disk free space is below 10 GiB",)

    failed = ScaleStopEvaluator().observe(
        ScaleWindow(
            duration_seconds=1,
            memory_percent=50,
            disk_free_bytes=20 * 1024**3,
            error_timeout_rate=0,
            kafka_lag=0,
            maximum_backpressure_verified=False,
            checkpoint_healthy=False,
        )
    )
    assert failed.stop
    assert "checkpoint failed" in failed.reasons


def test_memory_stop_rule_requires_full_configured_safety_window() -> None:
    evaluator = ScaleStopEvaluator(memory_safety_window_seconds=30)

    first = evaluator.observe(
        ScaleWindow(
            duration_seconds=15,
            memory_percent=91,
            disk_free_bytes=20 * 1024**3,
            error_timeout_rate=0,
            kafka_lag=0,
            maximum_backpressure_verified=False,
        )
    )
    second = evaluator.observe(
        ScaleWindow(
            duration_seconds=15,
            memory_percent=91,
            disk_free_bytes=20 * 1024**3,
            error_timeout_rate=0,
            kafka_lag=0,
            maximum_backpressure_verified=False,
        )
    )

    assert not first.stop
    assert second.stop
    assert second.reasons == ("memory exceeded 90% for 30 seconds",)


def test_error_timeout_stop_rule_requires_two_consecutive_windows() -> None:
    evaluator = ScaleStopEvaluator()

    def window(rate):
        return ScaleWindow(
            duration_seconds=10,
            memory_percent=50,
            disk_free_bytes=20 * 1024**3,
            error_timeout_rate=rate,
            kafka_lag=0,
            maximum_backpressure_verified=False,
        )

    assert not evaluator.observe(window(0.06)).stop
    assert not evaluator.observe(window(0.01)).stop
    assert not evaluator.observe(window(0.06)).stop
    decision = evaluator.observe(window(0.07))

    assert decision.stop
    assert decision.reasons == ("errors and timeouts exceeded 5% for two windows",)


def test_lag_stop_rule_requires_two_increases_at_verified_maximum_backpressure() -> None:
    evaluator = ScaleStopEvaluator()

    def window(lag, maximum=True):
        return ScaleWindow(
            duration_seconds=10,
            memory_percent=50,
            disk_free_bytes=20 * 1024**3,
            error_timeout_rate=0,
            kafka_lag=lag,
            maximum_backpressure_verified=maximum,
        )

    assert not evaluator.observe(window(10)).stop
    assert not evaluator.observe(window(20)).stop
    decision = evaluator.observe(window(30))

    assert decision.stop
    assert decision.reasons == (
        "Kafka lag increased for two windows at verified maximum backpressure",
    )


def test_controlled_conditions_must_clone_identical_population_and_workload() -> None:
    population = FrozenPopulationReceipt(
        dataset_repository="dhelmy990/babel-wikipedia-experiment",
        dataset_revision="0" * 40,
        model_id=uuid4(),
        model_revision="1" * 40,
        june_created=5_000,
        july_created=5_000,
        distinct_babel_count=10_000,
        indexed_count=10_000,
        formal_threshold=10_000,
        ordered_manifest_path="population/ordered-babels.jsonl",
        ordered_manifest_sha256="a" * 64,
        vector_bytes_sha256="b" * 64,
        creator_source_unique=True,
        creator_count=50,
        round_robin_verified=True,
        cross_month_used_source_invariant_verified=True,
        creator_round_robin_manifest_sha256="3" * 64,
        cross_month_used_sources_manifest_sha256="4" * 64,
    )
    workload = FrozenWorkloadReceipt(
        request_count=10_000,
        request_corpus_sha256="c" * 64,
        feedback_sha256="d" * 64,
        creator_schedule_sha256="e" * 64,
        event_mix_sha256="f" * 64,
        start_draws_sha256="1" * 64,
        continuation_draws_sha256="2" * 64,
        creator_schedule_scope="creator_local",
        start_probability=0.4,
        continuation_probability=0.4,
        independent_draw_streams=True,
    )
    conditions = cohort_condition_matrix(50)
    receipts = tuple(
        ControlledConditionReceipt(
            condition_id=row.condition_id,
            cohort_size=50,
            ordered_population_sha256="a" * 64,
            vector_bytes_sha256="b" * 64,
            workload_identity=(
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "2" * 64,
            ),
            created_count=10_000,
            indexed_count=10_000,
        )
        for row in conditions
    )

    expected_ids = {row.condition_id for row in conditions}
    validate_controlled_cohort(
        receipts,
        expected_condition_ids=expected_ids,
        expected_population=population,
        expected_workload=workload,
    )

    drifted = receipts[:-1] + (
        receipts[-1].model_copy(update={"vector_bytes_sha256": "9" * 64}),
    )
    with pytest.raises(ValueError, match="population/workload drift"):
        validate_controlled_cohort(
            drifted,
            expected_condition_ids=expected_ids,
            expected_population=population,
            expected_workload=workload,
        )

    with pytest.raises(ValueError, match="exactly once"):
        validate_controlled_cohort(
            receipts + (receipts[0],),
            expected_condition_ids=expected_ids,
            expected_population=population,
            expected_workload=workload,
        )

    wrong_expected = set(expected_ids)
    wrong_expected.remove(next(iter(wrong_expected)))
    wrong_expected.add("cohort-50.same_process.unknown.pgvector")
    with pytest.raises(ValueError, match="exactly once"):
        validate_controlled_cohort(
            receipts,
            expected_condition_ids=wrong_expected,
            expected_population=population,
            expected_workload=workload,
        )
