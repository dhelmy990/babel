"""Fixed-snapshot pgvector/hnswlib comparison, separate from topology evidence."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Protocol
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from .cache import RetrievalInputIdentity, recall_at, retrieval_input_identity


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    query_id: str
    exclude_creator_id: str
    vector_ordinal: int
    vector_sha256: str
    vector: NDArray[np.float32] = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class BackendMemory:
    """Truthfully scoped footprint; RSS delta is signed and may be negative."""

    measurement: Literal["index_footprint", "postgres_relation_storage"]
    scope: Literal[
        "shared_database_relation_all_runs",
        "current_process_net_rss_and_serialized_index",
    ]
    indexFootprintBytes: int
    processRssDeltaBytes: int | None
    tableFootprintBytes: int | None = None


@dataclass(frozen=True, slots=True)
class FormalPgvectorResult:
    run_id: UUID
    topology: str
    snapshot_sha256: str
    model_id: UUID
    model_version: int
    embedding_space_id: UUID
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class FrozenRetrievalSnapshot:
    run_id: UUID
    model_id: UUID
    model_version: int
    embedding_space_id: UUID
    snapshot_sha256: str
    ordered_babel_ids: tuple[str, ...]
    ordered_creator_ids: tuple[str, ...]
    ordered_source_article_keys: tuple[str, ...]
    vectors: NDArray[np.float32] = field(compare=False, repr=False)
    vector_bytes_sha256: str
    ordered_titles: tuple[str, ...] = ()
    ordered_texts: tuple[str, ...] = ()
    ordered_content_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        rows = len(self.ordered_babel_ids)
        if (
            rows < 51
            or self.vectors.shape != (rows, 100)
            or self.vectors.dtype != np.dtype("<f4")
            or not np.isfinite(self.vectors).all()
            or len(self.ordered_creator_ids) != rows
            or len(self.ordered_source_article_keys) != rows
            or len(set(self.ordered_babel_ids)) != rows
        ):
            raise ValueError("retrieval snapshot must contain ordered finite 100d rows")
        raw = self.vectors.tobytes(order="C")
        if hashlib.sha256(raw).hexdigest() != self.vector_bytes_sha256:
            raise ValueError("retrieval vector bytes differ from their checksum")
        if len(self.snapshot_sha256) != 64:
            raise ValueError("retrieval snapshot checksum is invalid")

    def rebind_run(self, run_id: UUID) -> "FrozenRetrievalSnapshot":
        from dataclasses import replace

        return replace(self, run_id=run_id)

    def audit_queries(self, count: int) -> tuple[RetrievalQuery, ...]:
        if count <= 0 or count > len(self.ordered_babel_ids):
            raise ValueError("query count must fit the frozen snapshot")
        ranked = sorted(
            range(len(self.ordered_babel_ids)),
            key=lambda index: hashlib.sha256(
                b"babel-retrieval-audit-v1\0"
                + self.ordered_babel_ids[index].encode("utf-8")
            ).digest(),
        )[:count]
        return tuple(
            RetrievalQuery(
                query_id=self.ordered_babel_ids[index],
                exclude_creator_id=self.ordered_creator_ids[index],
                vector_ordinal=index,
                vector_sha256=hashlib.sha256(
                    self.vectors[index].tobytes()
                ).hexdigest(),
                vector=self.vectors[index],
            )
            for index in ranked
        )

    def input_identity(
        self, queries: Sequence[RetrievalQuery]
    ) -> RetrievalInputIdentity:
        query_bytes = b"".join(
            query.query_id.encode("utf-8")
            + b"\0"
            + query.exclude_creator_id.encode("utf-8")
            + b"\0"
            + query.vector.tobytes()
            for query in queries
        )
        return retrieval_input_identity(
            self.ordered_babel_ids,
            self.vectors.tobytes(order="C"),
            self.snapshot_sha256,
            query_bytes,
        )

    def exact_neighbors(self, query: RetrievalQuery, k: int) -> tuple[str, ...]:
        if k <= 0:
            raise ValueError("exact neighbor count must be positive")
        eligible = np.asarray(
            [
                index
                for index, creator in enumerate(self.ordered_creator_ids)
                if creator != query.exclude_creator_id
            ],
            dtype=np.int64,
        )
        if len(eligible) < k:
            raise ValueError("not enough cross-creator candidates for exact audit")
        query_vector = np.asarray(query.vector, dtype=np.float32)
        query_vector = query_vector / np.linalg.norm(query_vector)
        candidate_vectors = self.vectors[eligible]
        norms = np.linalg.norm(candidate_vectors, axis=1)
        scores = (candidate_vectors @ query_vector) / norms
        identifiers = np.asarray(
            [self.ordered_babel_ids[index] for index in eligible], dtype=str
        )
        order = np.lexsort((identifiers, -scores))[:k]
        return tuple(str(value) for value in identifiers[order])

    def vector_records(self):
        from babel_online.observable import CreatedBabel, VectorRecord

        count = len(self.ordered_babel_ids)
        titles = self.ordered_titles or self.ordered_babel_ids
        texts = self.ordered_texts or ("retrieval snapshot",) * count
        hashes = self.ordered_content_hashes or ("0" * 64,) * count
        return tuple(
            VectorRecord(
                babel=CreatedBabel(
                    babelId=UUID(self.ordered_babel_ids[index]),
                    runId=self.run_id,
                    creatorId=UUID(self.ordered_creator_ids[index]),
                    sourceArticleKey=self.ordered_source_article_keys[index],
                    title=titles[index],
                    text=texts[index],
                    createdAtNs=index,
                ),
                catalogContentHash=hashes[index],
                embeddingSpaceId=self.embedding_space_id,
                servingModelId=self.model_id,
                materializedModelVersion=self.model_version,
                vector=tuple(float(value) for value in self.vectors[index]),
            )
            for index in range(count)
        )


class RetrievalSession(Protocol):
    backend: Literal["pgvector", "hnswlib"]
    memory: BackendMemory

    def search(self, query: RetrievalQuery, k: int) -> tuple[str, ...]: ...


def load_frozen_retrieval_snapshot(path: str | Path) -> FrozenRetrievalSnapshot:
    """Load the canonical 10k population after its existing checksum validation."""
    from babel_online.model.frozen_population import load_frozen_population

    directory = Path(path)
    manifest = load_frozen_population(directory)
    babels = [
        json.loads(line)
        for line in (directory / manifest.babelsFile).read_text(encoding="utf-8").splitlines()
    ]
    raw = (directory / manifest.vectorsFile).read_bytes()
    vectors = np.frombuffer(raw, dtype="<f4").reshape(manifest.babelCount, 100).copy()
    return FrozenRetrievalSnapshot(
        run_id=manifest.sourcePopulationRunId,
        model_id=manifest.modelId,
        model_version=manifest.modelVersion,
        embedding_space_id=manifest.embeddingSpaceId,
        snapshot_sha256=manifest.pgvectorSnapshotSha256,
        ordered_babel_ids=tuple(str(row["babelId"]) for row in babels),
        ordered_creator_ids=tuple(str(row["creatorId"]) for row in babels),
        ordered_source_article_keys=tuple(str(row["sourceArticleKey"]) for row in babels),
        vectors=vectors,
        vector_bytes_sha256=manifest.vectorsSha256,
        ordered_titles=tuple(str(row["title"]) for row in babels),
        ordered_texts=tuple(str(row["text"]) for row in babels),
        ordered_content_hashes=tuple(str(row["catalogContentHash"]) for row in babels),
    )


def validate_formal_pgvector_result(
    path: str | Path, snapshot: FrozenRetrievalSnapshot
) -> FormalPgvectorResult:
    """Gate optional comparison until a successful formal pgvector result exists."""
    evidence_path = Path(path)
    try:
        raw_bytes = evidence_path.read_bytes()
        from .trial_bundle import _load_condition

        evidence = _load_condition(evidence_path)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ValueError("formal pgvector evidence is invalid") from error
    identity = evidence.identity
    final = evidence.final_serving_identity
    if (
        identity.retrievalBackend != "pgvector"
        or identity.trainingEnabled is not False
        or identity.activationEnabled is not False
    ):
        raise ValueError("comparison requires a formal serving-only pgvector result")
    if (
        final.get("pgvectorSnapshotSha256") != snapshot.snapshot_sha256
        or final.get("backendSnapshotSha256") != snapshot.snapshot_sha256
        or UUID(str(final.get("modelId"))) != snapshot.model_id
        or int(final.get("modelVersion", -1)) != snapshot.model_version
        or UUID(str(final.get("embeddingSpaceId"))) != snapshot.embedding_space_id
    ):
        raise ValueError("formal pgvector result differs from frozen snapshot")
    if any(
        row.retrievalBackend != "pgvector"
        or row.modelId != snapshot.model_id
        or row.servingModelVersion != snapshot.model_version
        or row.pgvectorSnapshotSha256 != snapshot.snapshot_sha256
        or row.backendSnapshotSha256 != snapshot.snapshot_sha256
        for row in evidence.measurements
    ):
        raise ValueError("formal pgvector requests differ from frozen snapshot")
    return FormalPgvectorResult(
        run_id=evidence.run_id,
        topology=str(identity.topology),
        snapshot_sha256=snapshot.snapshot_sha256,
        model_id=snapshot.model_id,
        model_version=snapshot.model_version,
        embedding_space_id=snapshot.embedding_space_id,
        evidence_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _nearest_rank(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _net_rss_delta(*, before: int, after: int) -> int:
    return after - before


def _measure_backend(
    *,
    backend: Literal["pgvector", "hnswlib"],
    factory: Callable[[FrozenRetrievalSnapshot], RetrievalSession],
    snapshot: FrozenRetrievalSnapshot,
    queries: Sequence[RetrievalQuery],
    exact: Sequence[tuple[str, ...]],
    warmup_passes: int,
    measurement_passes: int,
    monotonic_ns: Callable[[], int],
) -> dict[str, Any]:
    preparation_started = monotonic_ns()
    session = factory(snapshot)
    preparation_ns = monotonic_ns() - preparation_started
    if session.backend != backend:
        raise ValueError("retrieval factory returned the wrong backend")
    warmup_started = monotonic_ns()
    for _ in range(warmup_passes):
        for query in queries:
            session.search(query, 50)
    warmup_ns = monotonic_ns() - warmup_started
    latencies: list[int] = []
    observed: list[tuple[str, ...]] | None = None
    steady_started = monotonic_ns()
    for _ in range(measurement_passes):
        current: list[tuple[str, ...]] = []
        for query in queries:
            started = monotonic_ns()
            neighbors = session.search(query, 50)
            latencies.append(monotonic_ns() - started)
            if len(neighbors) < 50:
                raise ValueError("retrieval backend returned fewer than 50 neighbors")
            current.append(neighbors)
        if observed is not None and current != observed:
            raise ValueError("retrieval backend changed results across steady passes")
        observed = current
    steady_elapsed = monotonic_ns() - steady_started
    assert observed is not None
    recall10 = sum(
        recall_at(expected, actual, 10)
        for expected, actual in zip(exact, observed, strict=True)
    ) / len(queries)
    recall50 = sum(
        recall_at(expected, actual, 50)
        for expected, actual in zip(exact, observed, strict=True)
    ) / len(queries)
    summed_latency = sum(latencies)
    return {
        "preparation": {
            "operation": (
                "reuse_formal_pgvector_hnsw"
                if backend == "pgvector"
                else "build_hnswlib_from_frozen_snapshot"
            ),
            "durationNs": preparation_ns,
            "evidence": getattr(session, "preparation_evidence", None),
        },
        "warmup": {
            "passCount": warmup_passes,
            "requestCount": warmup_passes * len(queries),
            "durationNs": warmup_ns,
        },
        "steadyState": {
            "requestCount": len(latencies),
            "elapsedNs": steady_elapsed,
            "sumRequestLatencyNs": summed_latency,
            "throughputQueriesPerSecond": (
                len(latencies) * 1_000_000_000 / steady_elapsed
            ),
            "latencyNs": {
                "p50": _nearest_rank(latencies, 0.50),
                "p95": _nearest_rank(latencies, 0.95),
                "p99": _nearest_rank(latencies, 0.99),
            },
        },
        "memory": asdict(session.memory),
        "recall": {"at10": recall10, "at50": recall50},
    }


def run_retrieval_comparison(
    *,
    snapshot: FrozenRetrievalSnapshot,
    formal_pgvector_result: FormalPgvectorResult,
    pgvector_factory: Callable[[FrozenRetrievalSnapshot], RetrievalSession],
    hnswlib_factory: Callable[[FrozenRetrievalSnapshot], RetrievalSession],
    query_count: int = 100,
    warmup_passes: int = 1,
    measurement_passes: int = 3,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    if (
        formal_pgvector_result.snapshot_sha256 != snapshot.snapshot_sha256
        or formal_pgvector_result.model_id != snapshot.model_id
        or formal_pgvector_result.model_version != snapshot.model_version
        or formal_pgvector_result.embedding_space_id != snapshot.embedding_space_id
    ):
        raise ValueError("formal pgvector result is not bound to this snapshot")
    if warmup_passes < 0 or measurement_passes <= 0:
        raise ValueError("retrieval pass counts are invalid")
    snapshot = snapshot.rebind_run(formal_pgvector_result.run_id)
    queries = snapshot.audit_queries(query_count)
    identity = snapshot.input_identity(queries)
    exact = tuple(snapshot.exact_neighbors(query, 50) for query in queries)
    exact_audit_sha256 = hashlib.sha256(
        json.dumps(
            exact, sort_keys=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "scope": "retrieval_only",
        "topologyConclusionEligible": False,
        "disclosure": (
            "Fixed-topology candidate retrieval only; preparation is separate from "
            "steady state and these results are not serving/training topology evidence."
        ),
        "formalPgvectorGate": {
            "runId": str(formal_pgvector_result.run_id),
            "topology": formal_pgvector_result.topology,
            "evidenceSha256": formal_pgvector_result.evidence_sha256,
        },
        "inputIdentity": asdict(identity),
        "queryCount": len(queries),
        "orderedQueryIds": [query.query_id for query in queries],
        "exactAuditSha256": exact_audit_sha256,
        "warmupPasses": warmup_passes,
        "measurementPasses": measurement_passes,
        "backends": {
            "pgvector": _measure_backend(
                backend="pgvector",
                factory=pgvector_factory,
                snapshot=snapshot,
                queries=queries,
                exact=exact,
                warmup_passes=warmup_passes,
                measurement_passes=measurement_passes,
                monotonic_ns=monotonic_ns,
            ),
            "hnswlib": _measure_backend(
                backend="hnswlib",
                factory=hnswlib_factory,
                snapshot=snapshot,
                queries=queries,
                exact=exact,
                warmup_passes=warmup_passes,
                measurement_passes=measurement_passes,
                monotonic_ns=monotonic_ns,
            ),
        },
    }


class _PgvectorSession:
    backend: Literal["pgvector"] = "pgvector"

    def __init__(
        self,
        snapshot: FrozenRetrievalSnapshot,
        database: Any,
        plan_query: RetrievalQuery,
    ) -> None:
        from babel_online.model.candidate_index import MaterializedServingState
        from babel_online.model.pgvector_index import PgvectorCandidateIndex

        self._snapshot = snapshot
        self._state = MaterializedServingState(
            run_id=snapshot.run_id,
            model_id=snapshot.model_id,
            model_version=snapshot.model_version,
            embedding_space_id=snapshot.embedding_space_id,
            pgvector_snapshot_sha256=snapshot.snapshot_sha256,
            backend_snapshot_sha256=snapshot.snapshot_sha256,
        )
        self._index = PgvectorCandidateIndex(database.query_candidates)
        self._index.activate(self._state, ())
        plan = database.explain_population_query(
            SimpleNamespace(
                run_id=snapshot.run_id,
                model_id=snapshot.model_id,
                model_version=snapshot.model_version,
                embedding_space_id=snapshot.embedding_space_id,
            ),
            query_vector=plan_query.vector,
            exclude_creator_id=UUID(plan_query.exclude_creator_id),
            limit=50,
        )
        from babel_online.model.population import _plan_names_hnsw

        if not _plan_names_hnsw(plan):
            raise ValueError("formal pgvector query did not observe PostgreSQL HNSW")
        self.preparation_evidence = {
            "hnswObserved": True,
            "queryId": plan_query.query_id,
            "limit": 50,
            "explainPlan": plan,
            "explainPlanSha256": hashlib.sha256(
                json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        storage = database.population_storage_bytes()
        self.memory = BackendMemory(
            measurement="postgres_relation_storage",
            scope="shared_database_relation_all_runs",
            indexFootprintBytes=int(storage["index_bytes"]),
            tableFootprintBytes=int(storage["table_bytes"]),
            processRssDeltaBytes=None,
        )

    def search(self, query: RetrievalQuery, k: int) -> tuple[str, ...]:
        rows = self._index.search(
            query.vector,
            run_id=self._snapshot.run_id,
            state=self._state,
            exclude_creator_id=UUID(query.exclude_creator_id),
            k=k,
        )
        return tuple(str(row.babel_id) for row in rows)


class _HnswlibSession:
    backend: Literal["hnswlib"] = "hnswlib"

    def __init__(self, snapshot: FrozenRetrievalSnapshot) -> None:
        import psutil
        from babel_online.model.candidate_index import MaterializedServingState
        from babel_online.model.hnswlib_index import HnswlibCandidateIndex

        process = psutil.Process()
        rss_before = process.memory_info().rss
        self._snapshot = snapshot
        self._state = MaterializedServingState(
            run_id=snapshot.run_id,
            model_id=snapshot.model_id,
            model_version=snapshot.model_version,
            embedding_space_id=snapshot.embedding_space_id,
            pgvector_snapshot_sha256=snapshot.snapshot_sha256,
            backend_snapshot_sha256=snapshot.snapshot_sha256,
        )
        self._index = HnswlibCandidateIndex()
        self._index.activate(self._state, snapshot.vector_records())
        serialized_bytes = self._index.serialized_index_size_bytes()
        self.preparation_evidence = {
            "hnswObserved": True,
            "indexCount": len(snapshot.ordered_babel_ids),
            "space": "cosine",
            "dimension": 100,
        }
        self.memory = BackendMemory(
            measurement="index_footprint",
            scope="current_process_net_rss_and_serialized_index",
            indexFootprintBytes=serialized_bytes,
            processRssDeltaBytes=_net_rss_delta(
                before=rss_before, after=process.memory_info().rss
            ),
        )

    def search(self, query: RetrievalQuery, k: int) -> tuple[str, ...]:
        rows = self._index.search(
            query.vector,
            run_id=self._snapshot.run_id,
            state=self._state,
            exclude_creator_id=UUID(query.exclude_creator_id),
            k=k,
        )
        return tuple(str(row.babel_id) for row in rows)


def run_live_retrieval_comparison(
    *,
    population_path: str | Path,
    formal_pgvector_evidence_path: str | Path,
    dsn: str,
    query_count: int,
    warmup_passes: int,
    measurement_passes: int,
) -> dict[str, Any]:
    """Operator entry point. The formal evidence gate is checked before DB access."""
    snapshot = load_frozen_retrieval_snapshot(population_path)
    formal = validate_formal_pgvector_result(formal_pgvector_evidence_path, snapshot)
    from babel_online.runtime.database import RuntimeDatabase

    database = RuntimeDatabase(dsn)
    return run_retrieval_comparison(
        snapshot=snapshot,
        formal_pgvector_result=formal,
        pgvector_factory=lambda value: _PgvectorSession(
            value, database, value.audit_queries(query_count)[0]
        ),
        hnswlib_factory=_HnswlibSession,
        query_count=query_count,
        warmup_passes=warmup_passes,
        measurement_passes=measurement_passes,
    )


__all__ = [
    "BackendMemory",
    "FormalPgvectorResult",
    "FrozenRetrievalSnapshot",
    "RetrievalQuery",
    "load_frozen_retrieval_snapshot",
    "run_live_retrieval_comparison",
    "run_retrieval_comparison",
    "validate_formal_pgvector_result",
]
