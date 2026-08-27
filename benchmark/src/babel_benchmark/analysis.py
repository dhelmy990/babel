"""Deterministic summaries for Friday benchmark raw JSONL."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from .contracts import (
    ConditionSummaryV1,
    ConditionTelemetryV1,
    InterferenceSummaryV1,
    PerformanceSummaryV1,
    PercentileSummaryV1,
    RequestMeasurementV1,
    RequestMeasurementV2,
)


Measurement = RequestMeasurementV1 | RequestMeasurementV2


def _condition(row: Measurement) -> str:
    return row.condition if isinstance(row, RequestMeasurementV1) else row.conditionId


def _started(row: Measurement) -> int:
    return (
        row.startedAtMonotonicNs
        if isinstance(row, RequestMeasurementV1)
        else row.actualStartMonotonicNs
    )


def compute_interference(
    serving_only_p95: int, training_no_activation_p95: int, full_p95: int
) -> InterferenceSummaryV1:
    if serving_only_p95 <= 0 or training_no_activation_p95 <= 0 or full_p95 < 0:
        raise ValueError("interference ratios require positive baseline latencies")
    training = training_no_activation_p95 / serving_only_p95
    full = full_p95 / serving_only_p95
    activation = full_p95 / training_no_activation_p95
    return InterferenceSummaryV1(
        Itraining=training,
        Ifull=full,
        IActivationIncrement=activation,
        ItrainingPercent=round((training - 1) * 100, 10),
        IfullPercent=round((full - 1) * 100, 10),
        IActivationIncrementPercent=round((activation - 1) * 100, 10),
    )


def percentiles(values: Iterable[int]) -> PercentileSummaryV1:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty sample")

    def nearest_rank(percentile: float) -> int:
        return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]

    return PercentileSummaryV1(
        count=len(ordered),
        p50=nearest_rank(0.50),
        p95=nearest_rank(0.95),
        p99=nearest_rank(0.99),
        max=ordered[-1],
    )


def analyze(
    measurements: Iterable[Measurement],
    telemetry: Iterable[ConditionTelemetryV1],
    *,
    baseline_condition: str = "pgvector_serving_only",
) -> PerformanceSummaryV1:
    measured_rows = [row for row in measurements if not row.isWarmup]
    if not measured_rows:
        raise ValueError("analysis needs at least one non-warmup measurement")
    by_condition: dict[str, list[Measurement]] = defaultdict(list)
    order: list[str] = []
    for row in measured_rows:
        condition = _condition(row)
        if condition not in by_condition:
            order.append(condition)
        by_condition[condition].append(row)
    if baseline_condition not in by_condition:
        candidates = sorted(
            name for name in by_condition if ".serving.no_activation.pgvector" in name
        )
        if len(candidates) != 1:
            raise ValueError("analysis needs one explicit serving-only baseline")
        baseline_condition = candidates[0]
    telemetry_by_condition: dict[str, list[ConditionTelemetryV1]] = defaultdict(list)
    for row in telemetry:
        telemetry_by_condition[row.condition].append(row)

    baseline_success = [
        row for row in by_condition[baseline_condition] if row.outcome == "success"
    ]
    if not baseline_success:
        raise ValueError("serving-only baseline needs successful requests")
    baseline_latency = percentiles(row.clientTotalNs for row in baseline_success)

    summaries: list[ConditionSummaryV1] = []
    for condition in order:
        rows = by_condition[condition]
        successes = [row for row in rows if row.outcome == "success"]
        elapsed = max(row.completedAtMonotonicNs for row in rows) - min(
            _started(row) for row in rows
        )
        rps = len(rows) * 1_000_000_000 / elapsed if elapsed > 0 else 0.0
        end_to_end = (
            percentiles(row.clientTotalNs for row in successes) if successes else None
        )
        stage_names = sorted(
            {stage for row in successes for stage in (row.serverTimingsNs or {})}
        )
        stages = {
            stage: percentiles((row.serverTimingsNs or {})[stage] for row in successes)
            for stage in stage_names
        }
        condition_telemetry = telemetry_by_condition[condition]
        trainer_values = [
            row.durationNs
            for row in condition_telemetry
            if row.kind == "trainer_step" and row.durationNs is not None
        ]
        lag_values = [
            row.kafkaLag
            for row in condition_telemetry
            if row.kind == "kafka_lag" and row.kafkaLag is not None
        ]
        sync_values = [
            row.durationNs
            for row in condition_telemetry
            if row.kind == "synchronization" and row.durationNs is not None
        ]
        summaries.append(
            ConditionSummaryV1(
                condition=condition,
                requests=len(rows),
                successes=len(successes),
                errors=sum(row.outcome == "error" for row in rows),
                timeouts=sum(row.outcome == "timeout" for row in rows),
                rps=rps,
                endToEndNs=end_to_end,
                serverStagesNs=stages,
                trainerStepNs=percentiles(trainer_values) if trainer_values else None,
                kafkaLag=percentiles(lag_values) if lag_values else None,
                syncSpikeNs=max(sync_values) if sync_values else None,
                slowdownRatioP95=(
                    end_to_end.p95 / baseline_latency.p95 if end_to_end else None
                ),
            )
        )
    by_summary = {row.condition: row for row in summaries}
    required = (
        (
            "pgvector_serving_only",
            "pgvector_training_no_sync",
            "pgvector_training_and_sync",
        )
        if baseline_condition == "pgvector_serving_only"
        else (
            baseline_condition,
            baseline_condition.replace(
                ".serving.no_activation.", ".training.no_activation."
            ),
            baseline_condition.replace(
                ".serving.no_activation.", ".training.activation."
            ),
        )
    )
    interference = None
    if all(
        name in by_summary and by_summary[name].endToEndNs is not None
        for name in required
    ):
        interference = compute_interference(
            by_summary[required[0]].endToEndNs.p95,  # type: ignore[union-attr]
            by_summary[required[1]].endToEndNs.p95,  # type: ignore[union-attr]
            by_summary[required[2]].endToEndNs.p95,  # type: ignore[union-attr]
        )
    return PerformanceSummaryV1(
        schemaVersion=1,
        benchmarkRunId=measured_rows[0].benchmarkRunId,
        baselineCondition=baseline_condition,
        conditions=tuple(summaries),
        interference=interference,
    )


def _distribution_text(value: PercentileSummaryV1 | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.p50} / {value.p95} / {value.p99} / {value.max} (n={value.count})"


def render_markdown(summary: PerformanceSummaryV1) -> str:
    lines = [
        "# Friday demo synchronous POST performance report",
        "",
        f"Benchmark run: `{summary.benchmarkRunId}`",
        "",
        "All durations are monotonic nanoseconds. Percentiles use nearest rank "
        "and exclude warmup rows.",
        "",
        "## End-to-end summary",
        "",
        "| Condition | p50 / p95 / p99 / max (ns) | RPS | Errors | Timeouts | Slowdown p95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary.conditions:
        slowdown = (
            f"{row.slowdownRatioP95:.3f}x"
            if row.slowdownRatioP95 is not None
            else "n/a"
        )
        lines.append(
            f"| `{row.condition}` | {_distribution_text(row.endToEndNs)} | {row.rps:.3f} | "
            f"{row.errors} | {row.timeouts} | {slowdown} |"
        )
    lines.extend(["", "## Server stages", ""])
    if summary.interference is not None:
        value = summary.interference
        lines.extend(
            [
                "",
                "## Interference",
                "",
                "| Calculation | Ratio | Percent change |",
                "|---|---:|---:|",
                f"| Itraining | {value.Itraining:.3f}x | {value.ItrainingPercent:.1f}% |",
                f"| Ifull | {value.Ifull:.3f}x | {value.IfullPercent:.1f}% |",
                "| IActivationIncrement | "
                f"{value.IActivationIncrement:.3f}x | "
                f"{value.IActivationIncrementPercent:.1f}% |",
            ]
        )
    for row in summary.conditions:
        lines.extend(
            [
                f"### `{row.condition}`",
                "",
                "| Stage | p50 / p95 / p99 / max (ns) |",
                "|---|---:|",
            ]
        )
        for stage, values in row.serverStagesNs.items():
            lines.append(f"| {stage} | {_distribution_text(values)} |")
        lines.extend(
            [
                "",
                f"Trainer step: {_distribution_text(row.trainerStepNs)}",
                "",
                f"Kafka lag: {_distribution_text(row.kafkaLag)}",
                "",
                f"Sync spike: {row.syncSpikeNs if row.syncSpikeNs is not None else 'n/a'} ns",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["analyze", "compute_interference", "percentiles", "render_markdown"]
