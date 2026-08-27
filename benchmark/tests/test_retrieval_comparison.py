from __future__ import annotations

import json
import hashlib
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from babel_benchmark.retrieval_comparison import (
    BackendMemory,
    FrozenRetrievalSnapshot,
    FormalPgvectorResult,
    RetrievalQuery,
    _PgvectorSession,
    run_retrieval_comparison,
    validate_formal_pgvector_result,
)


RUN = UUID("00000000-0000-5000-8000-000000000001")
MODEL = UUID("00000000-0000-5000-8000-000000000002")
SPACE = UUID("00000000-0000-5000-8000-000000000003")
SNAPSHOT_SHA = "a" * 64


def _snapshot(size: int = 120) -> FrozenRetrievalSnapshot:
    vectors = np.zeros((size, 100), dtype="<f4")
    for index in range(size):
        vectors[index, index % 100] = 1.0
        vectors[index, (index * 17 + 3) % 100] += index / (size * 10)
        vectors[index] /= np.linalg.norm(vectors[index])
    return FrozenRetrievalSnapshot(
        run_id=RUN,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        snapshot_sha256=SNAPSHOT_SHA,
        ordered_babel_ids=tuple(
            f"00000000-0000-5000-8000-{index + 1000:012d}" for index in range(size)
        ),
        ordered_creator_ids=tuple(
            f"00000000-0000-5000-8000-{index % 3 + 2000:012d}"
            for index in range(size)
        ),
        ordered_source_article_keys=tuple(f"enwiki:{index + 1}" for index in range(size)),
        vectors=vectors,
        vector_bytes_sha256=hashlib.sha256(vectors.tobytes()).hexdigest(),
    )


def _formal_document(*, snapshot_sha: str = SNAPSHOT_SHA, training=False) -> dict:
    return {
        "conditionId": "00000000-0000-5000-8000-000000000010",
        "runId": str(RUN),
        "requestCount": 1,
        "p95Ms": 1.0,
        "rawEvidence": {
            "conditionIdentity": {
                "topology": "same_host_split",
                "trainingEnabled": training,
                "activationEnabled": False,
                "retrievalBackend": "pgvector",
            },
            "measurements": [{"isWarmup": False, "outcome": "success"}],
            "finalServingIdentity": {
                "modelId": str(MODEL),
                "modelVersion": 0,
                "embeddingSpaceId": str(SPACE),
                "pgvectorSnapshotSha256": snapshot_sha,
                "backendSnapshotSha256": snapshot_sha,
            },
        },
    }


def test_formal_gate_requires_completed_serving_only_pgvector_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "condition.json"
    path.write_text(json.dumps(_formal_document()))

    result = validate_formal_pgvector_result(path, _snapshot())

    assert result.run_id == RUN
    assert result.snapshot_sha256 == SNAPSHOT_SHA
    assert result.topology == "same_host_split"

    path.write_text(json.dumps(_formal_document(training=True)))
    with pytest.raises(ValueError, match="serving-only pgvector"):
        validate_formal_pgvector_result(path, _snapshot())

    path.write_text(json.dumps(_formal_document(snapshot_sha="c" * 64)))
    with pytest.raises(ValueError, match="frozen snapshot"):
        validate_formal_pgvector_result(path, _snapshot())


def test_queries_are_deterministic_ordered_and_bound_to_exact_vector_bytes() -> None:
    snapshot = _snapshot()

    first = snapshot.audit_queries(12)
    second = snapshot.audit_queries(12)

    assert first == second
    assert len({row.query_id for row in first}) == 12
    assert all(row.vector.dtype == np.dtype("<f4") for row in first)
    assert snapshot.input_identity(first).snapshot_sha256 == SNAPSHOT_SHA
    changed = list(first)
    changed.reverse()
    assert snapshot.input_identity(changed).queries_sha256 != snapshot.input_identity(
        first
    ).queries_sha256


class _Session:
    def __init__(self, backend: str, snapshot: FrozenRetrievalSnapshot) -> None:
        self.backend = backend
        self.snapshot = snapshot
        self.calls: list[str] = []
        self.memory = BackendMemory(
            measurement="index_footprint",
            indexFootprintBytes=111 if backend == "pgvector" else 222,
            processRssDeltaBytes=None if backend == "pgvector" else 333,
        )

    def search(self, query: RetrievalQuery, k: int) -> tuple[str, ...]:
        self.calls.append(query.query_id)
        return self.snapshot.exact_neighbors(query, k)


class _Tick:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 100
        return self.value


def test_comparison_separates_preparation_and_warmup_from_steady_metrics() -> None:
    snapshot = _snapshot()
    formal = FormalPgvectorResult(
        run_id=RUN,
        topology="same_host_split",
        snapshot_sha256=SNAPSHOT_SHA,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        evidence_sha256="d" * 64,
    )
    sessions: dict[str, _Session] = {}

    def factory(backend: str):
        def create(value: FrozenRetrievalSnapshot):
            sessions[backend] = _Session(backend, value)
            return sessions[backend]

        return create

    report = run_retrieval_comparison(
        snapshot=snapshot,
        formal_pgvector_result=formal,
        pgvector_factory=factory("pgvector"),
        hnswlib_factory=factory("hnswlib"),
        query_count=4,
        warmup_passes=1,
        measurement_passes=2,
        monotonic_ns=_Tick(),
    )

    assert report["scope"] == "retrieval_only"
    assert report["topologyConclusionEligible"] is False
    assert report["formalPgvectorGate"]["topology"] == "same_host_split"
    assert report["queryCount"] == 4
    assert report["orderedQueryIds"] == [
        row.query_id for row in snapshot.audit_queries(4)
    ]
    assert len(report["exactAuditSha256"]) == 64
    for backend in ("pgvector", "hnswlib"):
        evidence = report["backends"][backend]
        assert evidence["preparation"]["durationNs"] == 100
        assert evidence["steadyState"]["requestCount"] == 8
        assert evidence["steadyState"]["elapsedNs"] >= evidence["steadyState"][
            "sumRequestLatencyNs"
        ]
        assert evidence["steadyState"]["latencyNs"] == {
            "p50": 100,
            "p95": 100,
            "p99": 100,
        }
        assert evidence["steadyState"]["throughputQueriesPerSecond"] > 0
        assert evidence["recall"] == {"at10": 1.0, "at50": 1.0}
        assert len(sessions[backend].calls) == 12


def test_comparison_rejects_gate_from_another_snapshot() -> None:
    snapshot = _snapshot()
    formal = FormalPgvectorResult(
        run_id=RUN,
        topology="same_process",
        snapshot_sha256="f" * 64,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        evidence_sha256="d" * 64,
    )
    with pytest.raises(ValueError, match="formal pgvector result"):
        run_retrieval_comparison(
            snapshot=snapshot,
            formal_pgvector_result=formal,
            pgvector_factory=lambda value: _Session("pgvector", value),
            hnswlib_factory=lambda value: _Session("hnswlib", value),
            query_count=2,
            warmup_passes=0,
            measurement_passes=1,
        )


def test_pgvector_session_requires_explain_to_observe_real_hnsw() -> None:
    class Database:
        query_candidates = staticmethod(lambda *_args: [])

        @staticmethod
        def population_storage_bytes():
            return {"table_bytes": 100, "index_bytes": 200}

        @staticmethod
        def explain_population_query(_identity):
            return [{"Plan": {"Node Type": "Seq Scan"}}]

    with pytest.raises(ValueError, match="did not observe PostgreSQL HNSW"):
        _PgvectorSession(_snapshot(), Database())

    Database.explain_population_query = staticmethod(
        lambda _identity: [
            {"Plan": {"Node Type": "Index Scan", "Index Name": "babel_embeddings_cosine_hnsw"}}
        ]
    )
    session = _PgvectorSession(_snapshot(), Database())
    assert session.preparation_evidence["hnswObserved"] is True
    assert len(session.preparation_evidence["explainPlanSha256"]) == 64
