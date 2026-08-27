from __future__ import annotations

import asyncio
from uuid import UUID

from babel_benchmark.resources import (
    PeriodicResourceCollector,
    ResourceObservationV1,
    ResourceSampler,
)
from babel_benchmark.cache import (
    ExactCosineOracleEvidence,
    RetrievalRunEvidence,
    compare_retrieval_backends,
    retrieval_input_identity,
)


RUN_ID = UUID("00000000-0000-5000-8000-000000000901")


def test_resource_contract_distinguishes_missing_gpu_from_zero() -> None:
    row = ResourceObservationV1(
        schemaVersion=1,
        benchmarkRunId=RUN_ID,
        conditionId="same_host_split.training.activation.pgvector",
        observedAtMonotonicNs=10,
        service="host",
        hostMemoryUsedBytes=100,
        hostDiskReadBytes=20,
        hostDiskWriteBytes=30,
        hostNetworkRxBytes=40,
        hostNetworkTxBytes=50,
        gpuAvailable=False,
    )
    assert row.gpuAvailable is False
    assert row.gpuUtilizationPercent is None
    assert row.gpuMemoryUsedBytes is None


def test_sampler_records_service_host_and_learning_health() -> None:
    sampler = ResourceSampler(
        benchmark_run_id=RUN_ID,
        condition_id="same_host_split.training.activation.pgvector",
        clock=lambda: 123,
        host_provider=lambda: {
            "hostMemoryUsedBytes": 1_000,
            "hostDiskReadBytes": 2_000,
            "hostDiskWriteBytes": 3_000,
            "hostNetworkRxBytes": 4_000,
            "hostNetworkTxBytes": 5_000,
        },
        process_provider=lambda service, pid: {
            "cpuPercent": 12.5,
            "rssBytes": 600,
            "threadCount": 7,
            "processReadBytes": 8,
            "processWriteBytes": 9,
        },
        gpu_provider=lambda: None,
    )

    rows = sampler.sample(
        services={"serving": 101, "trainer": 102},
        kafka_lag=11,
        training_step_rate=2.5,
        checkpoint_version=4,
        trainer_version=6,
        serving_version=4,
    )

    assert [row.service for row in rows] == ["host", "serving", "trainer"]
    assert rows[0].gpuAvailable is False
    assert rows[0].kafkaLag == 11
    assert rows[0].versionStaleness == 2
    assert rows[1].rssBytes == 600


def test_retrieval_comparison_requires_identical_inputs_and_separates_preparation() -> (
    None
):
    identity = retrieval_input_identity(["a", "b"], b"vectors", "c" * 64, b"queries")
    exact = tuple(str(index) for index in range(60))
    reversed_exact = tuple(reversed(exact))
    oracle = ExactCosineOracleEvidence(
        inputIdentity=identity,
        neighborsByQuery=(exact, reversed_exact),
    )
    pgvector = RetrievalRunEvidence(
        backend="pgvector",
        inputIdentity=identity,
        preparationNs=10,
        steadyLatencyNs=(2, 3),
        memoryBytes=100,
        neighborsByQuery=(exact, reversed_exact[1:] + reversed_exact[:1]),
    )
    hnswlib = RetrievalRunEvidence(
        backend="hnswlib",
        inputIdentity=identity,
        preparationNs=20,
        steadyLatencyNs=(1, 1),
        memoryBytes=80,
        neighborsByQuery=(exact[:9] + ("missing",) + exact[10:], reversed_exact),
    )
    comparison = compare_retrieval_backends(oracle, pgvector, hnswlib)
    assert comparison.pgvectorRecallAt10 == 0.95
    assert comparison.pgvectorRecallAt50 == 0.99
    assert comparison.hnswlibRecallAt10 == 0.95
    assert comparison.hnswlibRecallAt50 == 0.99
    assert comparison.pgvectorPreparationNs == 10
    assert comparison.hnswlibPreparationNs == 20


def test_periodic_collector_produces_bounded_raw_resource_rows() -> None:
    sampler = ResourceSampler(
        benchmark_run_id=RUN_ID,
        condition_id="same_host_split.training.activation.pgvector",
        clock=lambda: 123,
        host_provider=lambda: {
            "hostMemoryUsedBytes": 1,
            "hostDiskReadBytes": 2,
            "hostDiskWriteBytes": 3,
            "hostNetworkRxBytes": 4,
            "hostNetworkTxBytes": 5,
        },
        process_provider=lambda service, pid: {
            "cpuPercent": 0,
            "rssBytes": 1,
            "threadCount": 1,
            "processReadBytes": 0,
            "processWriteBytes": 0,
        },
        gpu_provider=lambda: None,
    )
    collector = PeriodicResourceCollector(
        sampler,
        services={"serving": 1},
        interval_seconds=0.001,
        maximum_samples=3,
    )

    async def collect() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(collector.run(stop))
        await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(collect())
    assert 1 <= collector.sample_count <= 3
    assert len(collector.rows) == collector.sample_count * 2
