from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

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


def test_concurrent_progress_observer_reports_exact_lifecycle_without_changing_rows() -> None:
    manifest, replay, universe = inputs()
    snapshots = []

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=AsyncTransport(),
            schedule_mode="open_loop",
            max_in_flight=2,
            progress_callback=snapshots.append,
        )
    )

    assert len(rows) == len(replay.rows)
    assert snapshots[0].phase == "scheduled"
    assert snapshots[0].submitted == snapshots[0].completed == 0
    assert snapshots[0].in_flight == snapshots[0].errors == 0
    assert all(row.in_flight == row.submitted - row.completed for row in snapshots)
    assert all(row.completed <= row.submitted <= len(replay.rows) for row in snapshots)
    assert snapshots[-1].phase == "draining"
    assert snapshots[-1].submitted == snapshots[-1].completed == len(replay.rows)
    assert snapshots[-1].in_flight == snapshots[-1].errors == 0
    assert snapshots[-1].elapsed_seconds > 0
    assert snapshots[-1].recent_rate > 0


def test_concurrent_progress_counts_request_errors_without_aborting_observation() -> None:
    manifest, replay, universe = inputs()
    snapshots = []

    class ErrorTransport(AsyncTransport):
        async def post_json(self, path, payload, timeout_seconds):
            if payload["requestId"] == str(replay.rows[0].request.requestId):
                return 503, {}
            return await super().post_json(path, payload, timeout_seconds)

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=ErrorTransport(),
            schedule_mode="closed_loop",
            max_in_flight=2,
            progress_callback=snapshots.append,
        )
    )

    assert sum(row.outcome != "success" for row in rows) == 1
    assert snapshots[-1].errors == 1
    assert snapshots[-1].completed == len(replay.rows)


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
    ("retrieval_backend", "pgvector_sha", "backend_sha"),
    [
        ("hnswlib", "a" * 64, "a" * 64),
        ("pgvector", "d" * 64, "a" * 64),
        ("pgvector", "a" * 64, "d" * 64),
    ],
)
def test_v2_condition_rejects_backend_or_frozen_snapshot_drift(
    retrieval_backend: str, pgvector_sha: str, backend_sha: str
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
            "expectedModelVersion": 2,
            "expectedEmbeddingSpaceId": str(
                legacy.conditions[0].expectedEmbeddingSpaceId
            ),
            "expectedDatasetSnapshotSha256": legacy.candidateUniverseSha256,
            "expectedPgvectorSnapshotSha256": pgvector_sha,
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


def test_activation_condition_accepts_pinned_child_identity_and_v2_cache_origin(
    tmp_path: Path,
) -> None:
    legacy, legacy_replay, universe = inputs()
    request_path = tmp_path / "requests-v2.jsonl"
    request_path.write_text(
        "".join(
            json.dumps(
                {
                    "scheduleOffsetNs": row.scheduleOffsetNs,
                    "request": {
                        "schemaVersion": 2,
                        "requestId": str(row.request.requestId),
                        "runId": str(row.request.runId),
                        "creatorId": str(row.request.creatorId),
                        "sourceBabelId": str(row.request.newBabelId),
                        "sourceArticleKey": row.request.newSourceArticleKey,
                        "traversalSessionId": str(row.request.requestId),
                        "parentRequestId": None,
                        "traversalDepth": 0,
                        "title": row.request.title,
                        "text": row.request.text,
                        "historyBabelIds": [
                            str(value) for value in row.request.historyBabelIds
                        ],
                        "candidateCount": row.request.candidateCount,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in legacy_replay.rows
        )
    )
    replay = ReplayCorpus.from_jsonl(request_path)
    source = legacy.model_dump(mode="json")
    source["schemaVersion"] = 2
    source["requestPath"] = "/api/v2/recommendations"
    source["requestCorpusSha256"] = replay.sha256
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
            "requestCorpusSha256": replay.sha256,
            "scheduleOffsetsNs": list(legacy.scheduleOffsetsNs),
            "expectedModelId": str(legacy.conditions[0].expectedModelId),
            "expectedModelVersion": 2,
            "expectedEmbeddingSpaceId": str(
                legacy.conditions[0].expectedEmbeddingSpaceId
            ),
            "expectedDatasetSnapshotSha256": legacy.candidateUniverseSha256,
            "expectedPgvectorSnapshotSha256": "a" * 64,
            "expectedBackendSnapshotSha256": "a" * 64,
            "activationTargets": [
                {
                    "modelId": "00000000-0000-5000-8000-000000000099",
                    "parentModelId": str(legacy.conditions[0].expectedModelId),
                    "modelVersion": 3,
                    "pgvectorSnapshotSha256": "c" * 64,
                    "backendSnapshotSha256": "d" * 64,
                }
            ],
        }
    ]
    manifest = BenchmarkManifestV2.model_validate(source)

    class ActivatedTransport(AsyncTransport):
        async def post_json(self, path, payload, timeout_seconds):
            assert path == "/api/v2/recommendations"
            assert payload["schemaVersion"] == 2
            status, body = await super().post_json(path, payload, timeout_seconds)
            body.update(
                {
                    "schemaVersion": 2,
                    "modelId": "00000000-0000-5000-8000-000000000099",
                    "modelVersion": 3,
                    "pgvectorSnapshotSha256": "c" * 64,
                    "backendSnapshotSha256": "d" * 64,
                    "sourceVectorOrigin": "cache_hit",
                }
            )
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
            trainer_model_version=4,
        )
    )
    assert all(row.outcome == "success" for row in rows)
    assert all(
        row.modelId == UUID("00000000-0000-5000-8000-000000000099")
        and row.servingModelVersion == 3
        and row.versionStaleness == 1
        for row in rows
    )
    assert {row.datasetSnapshotSha256 for row in rows} == {
        legacy.candidateUniverseSha256
    }
    assert {row.pgvectorSnapshotSha256 for row in rows} == {"c" * 64}
    assert {row.backendSnapshotSha256 for row in rows} == {"d" * 64}
    assert {row.sourceVectorOrigin for row in rows} == {"cache_hit"}
    assert {row.cacheStatus for row in rows} == {"hit"}


def test_live_activation_uses_db_verified_ledger_without_predeclared_child(tmp_path: Path):
    legacy, legacy_replay, universe = inputs()
    request_path = tmp_path / "requests-live-v2.jsonl"
    request_path.write_text(
        "".join(
            json.dumps(
                {
                    "scheduleOffsetNs": row.scheduleOffsetNs,
                    "request": {
                        "schemaVersion": 2,
                        "requestId": str(row.request.requestId),
                        "runId": str(row.request.runId),
                        "creatorId": str(row.request.creatorId),
                        "sourceBabelId": str(row.request.newBabelId),
                        "sourceArticleKey": row.request.newSourceArticleKey,
                        "traversalSessionId": str(row.request.requestId),
                        "parentRequestId": None,
                        "traversalDepth": 0,
                        "title": row.request.title,
                        "text": row.request.text,
                        "historyBabelIds": [],
                        "candidateCount": row.request.candidateCount,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in legacy_replay.rows
        )
    )
    replay = ReplayCorpus.from_jsonl(request_path)
    source = legacy.model_dump(mode="json")
    source.update(
        schemaVersion=2,
        requestPath="/api/v2/recommendations",
        requestCorpusSha256=replay.sha256,
        scheduleMode="open_loop",
        maxInFlight=2,
        conditions=[
            {
                "identity": {
                    "topology": "same_process",
                    "trainingEnabled": True,
                    "activationEnabled": True,
                    "retrievalBackend": "pgvector",
                },
                "requestCorpusSha256": replay.sha256,
                "scheduleOffsetsNs": list(legacy.scheduleOffsetsNs),
                "expectedModelId": str(legacy.conditions[0].expectedModelId),
                "expectedModelVersion": 2,
                "expectedEmbeddingSpaceId": str(
                    legacy.conditions[0].expectedEmbeddingSpaceId
                ),
                "expectedDatasetSnapshotSha256": legacy.candidateUniverseSha256,
                "expectedPgvectorSnapshotSha256": "a" * 64,
                "expectedBackendSnapshotSha256": "a" * 64,
                "activationValidation": "verified_live_ledger",
            }
        ],
    )
    manifest = BenchmarkManifestV2.model_validate(source)
    verified = []

    class LiveTransport(AsyncTransport):
        async def post_json(self, path, payload, timeout_seconds):
            status, body = await super().post_json(path, payload, timeout_seconds)
            body.update(
                schemaVersion=2,
                modelId="00000000-0000-5000-8000-000000000099",
                modelVersion=4,
                pgvectorSnapshotSha256="c" * 64,
                backendSnapshotSha256="c" * 64,
                sourceVectorOrigin="cache_hit",
            )
            return status, body

    rows = asyncio.run(
        run_concurrent_condition(
            manifest,
            manifest.conditions[0],
            replay,
            universe,
            transport=LiveTransport(),
            schedule_mode="open_loop",
            max_in_flight=2,
            live_identity_validator=lambda response: verified.append(
                (response.modelId, response.modelVersion)
            ),
        )
    )

    assert all(row.outcome == "success" for row in rows)
    assert verified and set(verified) == {
        (UUID("00000000-0000-5000-8000-000000000099"), 4)
    }


def test_success_callback_failure_is_fatal_not_a_duplicate_error_measurement():
    manifest, replay, universe = inputs()

    def fail(*_values):
        raise RuntimeError("feedback publish failed")

    with pytest.raises(RuntimeError, match="condition success callback failed"):
        asyncio.run(
            run_concurrent_condition(
                manifest,
                manifest.conditions[0],
                replay,
                universe,
                transport=AsyncTransport(),
                schedule_mode="closed_loop",
                max_in_flight=1,
                success_callback=fail,
            )
        )
