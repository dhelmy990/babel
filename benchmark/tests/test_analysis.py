from __future__ import annotations

from uuid import UUID

from babel_benchmark.analysis import analyze, compute_interference, render_markdown
from babel_benchmark.contracts import (
    ConditionTelemetryV1,
    RequestMeasurementV1,
    RequestMeasurementV2,
)


RUN_ID = UUID("00000000-0000-5000-8000-000000000901")
REQUEST_ID = UUID("00000000-0000-5000-8000-000000000401")
STAGES = ("queue", "encode", "context", "ann", "filtering", "serialization")


def test_all_three_interference_ratios_and_percentages_are_explicit() -> None:
    result = compute_interference(10, 12, 15)

    assert result.Itraining == 1.2
    assert result.Ifull == 1.5
    assert result.IActivationIncrement == 1.25
    assert result.ItrainingPercent == 20.0
    assert result.IfullPercent == 50.0
    assert result.IActivationIncrementPercent == 25.0


def measurement(condition: str, total: int, *, outcome: str = "success"):
    server = total - 10 if outcome == "success" else None
    timings = (
        {stage: 1 for stage in STAGES} | {"serverTotal": server}
        if server is not None
        else None
    )
    return RequestMeasurementV1(
        schemaVersion=1,
        benchmarkRunId=RUN_ID,
        condition=condition,
        requestId=REQUEST_ID,
        scheduleIndex=0,
        scheduleOffsetNs=0,
        startedAtMonotonicNs=100,
        completedAtMonotonicNs=100 + total,
        queueDelayNs=0,
        clientTotalNs=total,
        clientOverheadNs=10 if server is not None else None,
        outcome=outcome,
        httpStatus=200 if outcome == "success" else None,
        errorType=None if outcome == "success" else outcome,
        serverTimingsNs=timings,
        modelId=UUID("00000000-0000-5000-8000-000000000002")
        if server is not None
        else None,
        modelVersion=0 if server is not None else None,
        retrievalBackend="pgvector" if server is not None else None,
        pgvectorSnapshotSha256="a" * 64 if server is not None else None,
        backendSnapshotSha256="a" * 64 if server is not None else None,
        queryVectorSha256="b" * 64 if server is not None else None,
        candidateCount=1 if server is not None else None,
    )


def test_report_contains_latency_throughput_errors_and_slowdown() -> None:
    rows = (
        [measurement("pgvector_serving_only", value) for value in (100, 200, 300, 400)]
        + [
            measurement("pgvector_training_no_sync", value)
            for value in (200, 400, 600, 800)
        ]
        + [measurement("pgvector_training_no_sync", 500, outcome="timeout")]
    )

    summary = analyze(rows, [], baseline_condition="pgvector_serving_only")
    baseline, trained = summary.conditions

    assert baseline.endToEndNs.model_dump() == {
        "count": 4,
        "p50": 200,
        "p95": 400,
        "p99": 400,
        "max": 400,
    }
    assert trained.errors == 0
    assert trained.timeouts == 1
    assert trained.slowdownRatioP95 == 2.0
    assert set(trained.serverStagesNs) == {*STAGES, "serverTotal"}
    assert trained.rps > 0


def test_report_includes_step_time_lag_and_sync_spike_when_available() -> None:
    rows = [measurement("pgvector_serving_only", 100)] + [
        measurement("pgvector_training_and_sync", 200)
    ]
    telemetry = [
        ConditionTelemetryV1(
            schemaVersion=1,
            benchmarkRunId=RUN_ID,
            condition="pgvector_training_and_sync",
            observedAtMonotonicNs=1,
            kind="trainer_step",
            trainerStep=1,
            durationNs=5,
        ),
        ConditionTelemetryV1(
            schemaVersion=1,
            benchmarkRunId=RUN_ID,
            condition="pgvector_training_and_sync",
            observedAtMonotonicNs=2,
            kind="kafka_lag",
            kafkaLag=7,
        ),
        ConditionTelemetryV1(
            schemaVersion=1,
            benchmarkRunId=RUN_ID,
            condition="pgvector_training_and_sync",
            observedAtMonotonicNs=3,
            kind="synchronization",
            synchronizationVersion=1,
            durationNs=11,
        ),
    ]

    summary = analyze(rows, telemetry, baseline_condition="pgvector_serving_only")
    trained = summary.conditions[1]

    assert trained.trainerStepNs is not None
    assert trained.trainerStepNs.max == 5
    assert trained.kafkaLag is not None
    assert trained.kafkaLag.max == 7
    assert trained.syncSpikeNs == 11
    markdown = render_markdown(summary)
    assert "p50 / p95 / p99 / max" in markdown
    assert "Kafka lag" in markdown
    assert "Sync spike" in markdown


def test_report_names_all_interference_calculations() -> None:
    rows = (
        [measurement("pgvector_serving_only", 100)]
        + [measurement("pgvector_training_no_sync", 120)]
        + [measurement("pgvector_training_and_sync", 150)]
    )
    summary = analyze(rows, [])
    assert summary.interference is not None
    markdown = render_markdown(summary)
    assert "Itraining" in markdown
    assert "Ifull" in markdown
    assert "IActivationIncrement" in markdown


def test_concurrent_v2_rows_are_analyzed_by_generalized_condition_identity() -> None:
    def concurrent(condition: str, total: int) -> RequestMeasurementV2:
        server = total - 10
        return RequestMeasurementV2(
            schemaVersion=2,
            benchmarkRunId=RUN_ID,
            conditionId=condition,
            requestId=REQUEST_ID,
            scheduleIndex=0,
            scheduleMode="open_loop",
            intendedStartMonotonicNs=100,
            actualStartMonotonicNs=100,
            completedAtMonotonicNs=100 + total,
            queueDelayNs=0,
            inFlightAtStart=2,
            clientTotalNs=total,
            clientOverheadNs=10,
            outcome="success",
            httpStatus=200,
            serverTimingsNs={stage: 1 for stage in STAGES} | {"serverTotal": server},
            modelId=UUID("00000000-0000-5000-8000-000000000002"),
            servingModelVersion=0,
            retrievalBackend="pgvector",
            datasetSnapshotSha256="a" * 64,
            backendSnapshotSha256="a" * 64,
            queryVectorSha256="b" * 64,
            candidateCount=1,
        )

    prefix = "same_host_split"
    summary = analyze(
        [
            concurrent(f"{prefix}.serving.no_activation.pgvector", 100),
            concurrent(f"{prefix}.training.no_activation.pgvector", 120),
            concurrent(f"{prefix}.training.activation.pgvector", 150),
        ],
        [],
    )
    assert summary.baselineCondition == f"{prefix}.serving.no_activation.pgvector"
    assert summary.interference is not None
    assert summary.interference.IActivationIncrement == 1.25
