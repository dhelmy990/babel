from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from babel_benchmark.contracts import (
    BenchmarkManifestV1,
    BenchmarkManifestV2,
    CreatedBabelV1,
    ReplayRequestV1,
)
from babel_benchmark.replay import CandidateUniverse, ReplayCorpus
from babel_benchmark.runner import run_concurrent_condition


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "performance"


class AsyncTransport:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.closed = False

    async def post_json(
        self, path: str, payload: dict[str, Any], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.25)
        self.active -= 1
        candidate_number = 203 if payload["creatorId"].endswith("102") else 202
        creator_number = 103 if candidate_number == 203 else 102
        return 200, {
            "schemaVersion": 1,
            "requestId": payload["requestId"],
            "runId": payload["runId"],
            "modelId": "00000000-0000-5000-8000-000000000002",
            "modelVersion": 2,
            "retrievalBackend": "pgvector",
            "embeddingSpaceId": "00000000-0000-5000-8000-000000000003",
            "pgvectorSnapshotSha256": "a" * 64,
            "backendSnapshotSha256": "a" * 64,
            "queryVectorSha256": "b" * 64,
            "candidates": [
                {
                    "babelId": f"00000000-0000-5000-8000-{candidate_number:012d}",
                    "creatorId": f"00000000-0000-5000-8000-{creator_number:012d}",
                    "sourceArticleKey": "enwiki:2032",
                    "rank": 1,
                    "modelScore": 0.5,
                }
            ],
            "timingsNs": {
                "queue": 1,
                "encode": 1,
                "context": 1,
                "ann": 1,
                "filtering": 1,
                "serialization": 1,
                "serverTotal": 1_000,
            },
        }

    async def close(self) -> None:
        self.closed = True


def inputs():
    manifest = BenchmarkManifestV1.model_validate_json(
        (FIXTURES / "manifest.json").read_text()
    )
    replay = ReplayCorpus.from_jsonl(FIXTURES / "requests.jsonl", ReplayRequestV1)
    universe = CandidateUniverse.from_jsonl(
        FIXTURES / "created-babels.jsonl", CreatedBabelV1
    )
    return manifest, replay, universe


def test_open_loop_schedule_is_deterministic_and_bounded_with_real_concurrency() -> (
    None
):
    manifest, replay, universe = inputs()
    transport = AsyncTransport()

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=transport,
            schedule_mode="open_loop",
            max_in_flight=3,
        )
    )

    assert [row.scheduleIndex for row in rows] == list(range(len(replay.rows)))
    assert [row.requestId for row in rows] == [
        row.request.requestId for row in replay.rows
    ]
    assert transport.maximum_active == 3
    assert transport.closed
    assert all(
        row.actualStartMonotonicNs >= row.intendedStartMonotonicNs for row in rows
    )
    assert all(
        row.queueDelayNs == row.actualStartMonotonicNs - row.intendedStartMonotonicNs
        for row in rows
    )
    assert max(row.inFlightAtStart for row in rows) > 1
    assert all(row.outcome == "success" for row in rows)


def test_closed_loop_schedule_preserves_stable_identity_and_timeout_rows() -> None:
    manifest, replay, universe = inputs()

    class TimeoutTransport(AsyncTransport):
        async def post_json(self, path, payload, timeout_seconds):
            if payload["requestId"] == str(replay.rows[1].request.requestId):
                raise TimeoutError("bounded timeout")
            return await super().post_json(path, payload, timeout_seconds)

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=TimeoutTransport(),
            schedule_mode="closed_loop",
            max_in_flight=2,
        )
    )

    assert [row.scheduleIndex for row in rows] == list(range(len(replay.rows)))
    assert rows[1].outcome == "timeout"
    assert rows[1].errorType == "TimeoutError"
    assert rows[1].serverTimingsNs is None
    assert all(row.inFlightAtStart <= 2 for row in rows)


@pytest.mark.parametrize(
    ("retrieval_backend", "dataset_sha", "backend_sha"),
    [
        ("hnswlib", "a" * 64, "a" * 64),
        ("pgvector", "d" * 64, "a" * 64),
        ("pgvector", "a" * 64, "d" * 64),
    ],
)
def test_v2_condition_rejects_backend_or_frozen_snapshot_drift(
    retrieval_backend: str, dataset_sha: str, backend_sha: str
) -> None:
    legacy, replay, universe = inputs()
    source = legacy.model_dump(mode="json")
    source["schemaVersion"] = 2
    source["scheduleMode"] = "open_loop"
    source["maxInFlight"] = 2
    source["conditions"] = [
        {
            "identity": {
                "topology": "same_host_split",
                "trainingEnabled": False,
                "activationEnabled": False,
                "retrievalBackend": retrieval_backend,
            },
            "requestCorpusSha256": legacy.requestCorpusSha256,
            "scheduleOffsetsNs": list(legacy.scheduleOffsetsNs),
            "expectedModelId": str(legacy.conditions[0].expectedModelId),
            "expectedEmbeddingSpaceId": str(
                legacy.conditions[0].expectedEmbeddingSpaceId
            ),
            "expectedDatasetSnapshotSha256": dataset_sha,
            "expectedBackendSnapshotSha256": backend_sha,
        }
    ]
    manifest = BenchmarkManifestV2.model_validate(source)
    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=AsyncTransport(),
            schedule_mode="open_loop",
            max_in_flight=2,
        )
    )
    assert rows
    assert all(row.outcome == "error" and row.errorType == "ValueError" for row in rows)


def test_activation_condition_allows_versioned_backend_snapshot_change() -> None:
    legacy, replay, universe = inputs()
    source = legacy.model_dump(mode="json")
    source["schemaVersion"] = 2
    source["scheduleMode"] = "closed_loop"
    source["maxInFlight"] = 2
    source["conditions"] = [
        {
            "identity": {
                "topology": "same_host_split",
                "trainingEnabled": True,
                "activationEnabled": True,
                "retrievalBackend": "pgvector",
            },
            "requestCorpusSha256": legacy.requestCorpusSha256,
            "scheduleOffsetsNs": list(legacy.scheduleOffsetsNs),
            "expectedModelId": str(legacy.conditions[0].expectedModelId),
            "expectedEmbeddingSpaceId": str(
                legacy.conditions[0].expectedEmbeddingSpaceId
            ),
            "expectedDatasetSnapshotSha256": "a" * 64,
            "expectedBackendSnapshotSha256": "a" * 64,
        }
    ]
    manifest = BenchmarkManifestV2.model_validate(source)

    class ActivatedTransport(AsyncTransport):
        async def post_json(self, path, payload, timeout_seconds):
            status, body = await super().post_json(path, payload, timeout_seconds)
            body["backendSnapshotSha256"] = "c" * 64
            return status, body

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=ActivatedTransport(),
            schedule_mode="closed_loop",
            max_in_flight=2,
            trainer_model_version=3,
        )
    )
    assert all(row.outcome == "success" for row in rows)
    assert all(
        row.servingModelVersion == 2 and row.versionStaleness == 1 for row in rows
    )
    assert {row.backendSnapshotSha256 for row in rows} == {"c" * 64}
