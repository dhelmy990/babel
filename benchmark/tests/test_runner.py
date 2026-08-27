from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from babel_benchmark.contracts import BenchmarkManifestV1, CreatedBabelV1, ReplayRequestV1
from babel_benchmark.replay import CandidateUniverse, ReplayCorpus
from babel_benchmark.runner import (
    ConditionTelemetryRecorder,
    MeasuredConditionOperations,
    run_suite,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "performance"


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def __call__(self) -> int:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += round(seconds * 1_000_000_000)

    def advance(self, nanoseconds: int) -> None:
        self.value += nanoseconds


class FakeDriver:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.active: str | None = None
        self.activations: list[str] = []

    @contextmanager
    def activate(self, condition: Any, telemetry: ConditionTelemetryRecorder):
        self.active = condition.name
        self.activations.append(condition.name)
        if condition.trainingEnabled:
            telemetry.trainer_step(step=1, duration_ns=2_000_000)
            telemetry.kafka_lag(3)
        if condition.syncEnabled:
            telemetry.synchronization(version=1, duration_ns=7_000_000)
        try:
            yield
        finally:
            self.active = None


class FakeTransport:
    def __init__(self, clock: FakeClock, driver: FakeDriver) -> None:
        self.clock = clock
        self.driver = driver

    def post_json(self, path: str, payload: dict[str, Any], timeout_seconds: float):
        assert path == "/api/v1/recommendations"
        assert timeout_seconds == 1.0
        self.clock.advance(1_000_000)
        if payload["creatorId"] == "00000000-0000-5000-8000-000000000102":
            candidate_id = "00000000-0000-5000-8000-000000000203"
            candidate_creator = "00000000-0000-5000-8000-000000000103"
        else:
            candidate_id = "00000000-0000-5000-8000-000000000202"
            candidate_creator = "00000000-0000-5000-8000-000000000102"
        candidate = {
            "babelId": candidate_id,
            "creatorId": candidate_creator,
            "sourceArticleKey": "enwiki:2032",
            "rank": 1,
            "modelScore": 0.5,
        }
        return 200, {
            "schemaVersion": 1,
            "requestId": payload["requestId"],
            "runId": payload["runId"],
            "modelId": "00000000-0000-5000-8000-000000000002",
            "modelVersion": 1 if self.driver.active == "pgvector_training_and_sync" else 0,
            "retrievalBackend": "pgvector",
            "embeddingSpaceId": "00000000-0000-5000-8000-000000000003",
            "pgvectorSnapshotSha256": (
                "c" * 64
                if self.driver.active == "pgvector_training_and_sync"
                else "a" * 64
            ),
            "backendSnapshotSha256": (
                "c" * 64
                if self.driver.active == "pgvector_training_and_sync"
                else "a" * 64
            ),
            "queryVectorSha256": "b" * 64,
            "candidates": [candidate],
            "timingsNs": {
                "queue": 10_000,
                "encode": 100_000,
                "context": 100_000,
                "ann": 100_000,
                "filtering": 100_000,
                "serialization": 100_000,
                "serverTotal": 700_000,
            },
        }

    def close(self) -> None:
        return None


def fixture_inputs():
    manifest = BenchmarkManifestV1.model_validate_json(
        (FIXTURES / "manifest.json").read_text()
    )
    replay = ReplayCorpus.from_jsonl(FIXTURES / "requests.jsonl", ReplayRequestV1)
    universe = CandidateUniverse.from_jsonl(
        FIXTURES / "created-babels.jsonl", CreatedBabelV1
    )
    return manifest, replay, universe


def test_suite_replays_identical_schedule_and_uses_monotonic_nanoseconds() -> None:
    manifest, replay, universe = fixture_inputs()
    clock = FakeClock()
    driver = FakeDriver(clock)

    result = run_suite(
        manifest,
        replay,
        universe,
        transport_factory=lambda: FakeTransport(clock, driver),
        condition_driver=driver,
        monotonic_ns=clock,
        sleep=clock.sleep,
    )

    assert driver.activations == [condition.name for condition in manifest.conditions]
    assert len(result.measurements) == len(replay.rows) * 3
    for condition in manifest.conditions:
        rows = [row for row in result.measurements if row.condition == condition.name]
        assert [row.requestId for row in rows] == [
            request.request.requestId for request in replay.rows
        ]
        assert [row.scheduleOffsetNs for row in rows] == list(
            condition.scheduleOffsetsNs
        )
        assert all(row.clientTotalNs == 1_000_000 for row in rows)
        assert all(row.clientOverheadNs == 300_000 for row in rows)
        assert all(row.outcome == "success" for row in rows)


def test_suite_records_training_lag_and_sync_spike_through_injected_seam() -> None:
    manifest, replay, universe = fixture_inputs()
    clock = FakeClock()
    driver = FakeDriver(clock)

    result = run_suite(
        manifest,
        replay,
        universe,
        transport_factory=lambda: FakeTransport(clock, driver),
        condition_driver=driver,
        monotonic_ns=clock,
        sleep=clock.sleep,
    )

    assert [(row.condition, row.kind) for row in result.telemetry] == [
        ("pgvector_training_no_sync", "trainer_step"),
        ("pgvector_training_no_sync", "kafka_lag"),
        ("pgvector_training_and_sync", "trainer_step"),
        ("pgvector_training_and_sync", "kafka_lag"),
        ("pgvector_training_and_sync", "synchronization"),
    ]


def test_candidate_outside_created_universe_invalidates_success() -> None:
    manifest, replay, universe = fixture_inputs()
    clock = FakeClock()
    driver = FakeDriver(clock)
    transport = FakeTransport(clock, driver)
    original = transport.post_json

    def invalid_response(path: str, payload: dict[str, Any], timeout_seconds: float):
        status, response = original(path, payload, timeout_seconds)
        response["candidates"][0]["babelId"] = "00000000-0000-5000-8000-999999999999"
        return status, response

    transport.post_json = invalid_response  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="created synthetic Babel universe"):
        run_suite(
            manifest,
            replay,
            universe,
            transport_factory=lambda: transport,
            condition_driver=driver,
            monotonic_ns=clock,
            sleep=clock.sleep,
        )


def test_operation_adapter_measures_trainer_and_sync_with_the_same_monotonic_clock() -> None:
    manifest, _, _ = fixture_inputs()
    condition = manifest.conditions[2]
    clock = FakeClock()
    recorder = ConditionTelemetryRecorder(manifest, condition, clock)
    adapter = MeasuredConditionOperations(recorder, monotonic_ns=clock)

    def train():
        clock.advance(13)
        return "trained"

    def synchronize():
        clock.advance(29)
        return "synced"

    assert adapter.trainer_step(step=4, operation=train) == "trained"
    adapter.kafka_lag(8)
    assert adapter.synchronization(version=2, operation=synchronize) == "synced"
    assert [(row.kind, row.durationNs, row.kafkaLag) for row in recorder.rows] == [
        ("trainer_step", 13, None),
        ("kafka_lag", None, 8),
        ("synchronization", 29, None),
    ]
