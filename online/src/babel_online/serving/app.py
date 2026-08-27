"""FastAPI synchronous recommendation endpoint."""

from __future__ import annotations

import hashlib
from threading import BoundedSemaphore
from time import perf_counter_ns

import numpy as np
from fastapi import FastAPI, HTTPException, Response

from ..contracts import (
    RecommendationCandidateV1,
    RecommendationRequestV1,
    RecommendationRequestV2,
    RecommendationResponseV1,
    RecommendationResponseV2,
)
from ..model.context_tower import CreatorContextTower
from ..model.source_vector_cache import SourceVectorResolver, VectorCacheKey
from .state import ServingState
from .timings import server_timing_header


SERVING_HOST = "127.0.0.1"
_SERIALIZATION_PLACEHOLDER_NS = 7_000_000_000_000_000_001
_SERVER_TOTAL_PLACEHOLDER_NS = 9_000_000_000_000_000_001
_SERIALIZATION_MEASUREMENT = "wire-json-template-with-timing-token-patch"


def _replace_timing_token(
    payload: bytes, *, name: str, placeholder: int, value: int
) -> bytes:
    """Replace one already-encoded timing number without JSON re-encoding."""
    token = f'"{name}":{placeholder}'.encode("ascii")
    if payload.count(token) != 1:
        raise RuntimeError(f"wire payload does not contain one {name} timing token")
    replacement = f'"{name}":{value}'.encode("ascii")
    return payload.replace(token, replacement, 1)


def create_app(
    state: ServingState,
    *,
    max_concurrent_requests: int = 8,
    source_vector_resolver: SourceVectorResolver | None = None,
) -> FastAPI:
    if max_concurrent_requests <= 0:
        raise ValueError("max_concurrent_requests must be positive")
    app = FastAPI(title="Babel online recommendations", version="1")
    semaphore = BoundedSemaphore(max_concurrent_requests)

    def recommend_document(
        request: RecommendationRequestV1 | RecommendationRequestV2,
    ) -> Response:
        request_started = perf_counter_ns()
        queued_at = request_started
        semaphore.acquire()
        try:
            queue_ns = perf_counter_ns() - queued_at
            snapshot = state.snapshot()
            materialized = snapshot.materialized_state
            if request.runId != materialized.run_id:
                raise HTTPException(status_code=409, detail="request run is not active")
            is_v2 = isinstance(request, RecommendationRequestV2)
            source_article_key = (
                request.sourceArticleKey if is_v2 else request.newSourceArticleKey
            )
            is_new_root = not is_v2 or request.traversalDepth == 0
            if is_new_root and (
                request.runId, request.creatorId, source_article_key
            ) in snapshot.creator_sources:
                is_same_persisted_root = (
                    is_v2
                    and snapshot.source_keys_by_babel_id.get(request.sourceBabelId)
                    == request.sourceArticleKey
                    and snapshot.owners_by_babel_id.get(request.sourceBabelId)
                    == request.creatorId
                )
                if not is_same_persisted_root:
                    raise HTTPException(
                        status_code=409,
                        detail="creator already used this source article in the run",
                    )
            if is_v2 and request.traversalDepth == 1:
                persisted_key = snapshot.source_keys_by_babel_id.get(
                    request.sourceBabelId
                )
                if persisted_key is None:
                    raise HTTPException(
                        status_code=422, detail="unknown existing source Babel"
                    )
                if persisted_key != request.sourceArticleKey:
                    raise HTTPException(
                        status_code=422,
                        detail="existing source Babel and article key differ",
                    )

            started = perf_counter_ns()
            source_origin = None
            if is_v2:
                if source_vector_resolver is None:
                    raise HTTPException(
                        status_code=503,
                        detail="V2 source-vector resolver is unavailable",
                    )
                key = VectorCacheKey(
                    run_id=request.runId,
                    babel_id=request.sourceBabelId,
                    model_id=snapshot.model.modelId,
                    model_version=materialized.model_version,
                    embedding_space_id=materialized.embedding_space_id,
                )
                if request.traversalDepth == 0:
                    resolved = source_vector_resolver.resolve_new_root(
                        key, title=request.title or "", lead_text=request.text or ""
                    )
                else:
                    resolved = source_vector_resolver.resolve_existing(key)
                new_vector = resolved.vector
                source_origin = resolved.origin
            else:
                new_vector = snapshot.item_tower.encode_article(request.title, request.text)
            encode_ns = perf_counter_ns() - started
            encoder_identity = snapshot.item_tower.execution_identity(batch_size=1)

            started = perf_counter_ns()
            try:
                history_vectors = [
                    snapshot.vectors_by_babel_id[babel_id]
                    for babel_id in request.historyBabelIds
                ]
            except KeyError as error:
                raise HTTPException(
                    status_code=422, detail=f"unknown history Babel: {error.args[0]}"
                ) from error
            history = (
                np.stack(history_vectors)
                if history_vectors
                else np.empty((0, 100), dtype="<f4")
            )
            query = CreatorContextTower.original()(new=new_vector, history=history)
            context_ns = perf_counter_ns() - started

            started = perf_counter_ns()
            retrieved = snapshot.candidate_index.search(
                query,
                run_id=request.runId,
                state=materialized,
                exclude_creator_id=request.creatorId,
                k=min(100, request.candidateCount + (1 if is_v2 else 0)),
            )
            ann_ns = perf_counter_ns() - started

            started = perf_counter_ns()
            filtered = []
            seen = set()
            for row in retrieved:
                if (
                    row.creator_id == request.creatorId
                    or row.babel_id in seen
                    or (is_v2 and row.babel_id == request.sourceBabelId)
                ):
                    continue
                seen.add(row.babel_id)
                filtered.append(row)
                if len(filtered) == request.candidateCount:
                    break
            candidates = [
                RecommendationCandidateV1(
                    babelId=row.babel_id,
                    creatorId=row.creator_id,
                    sourceArticleKey=row.source_article_key,
                    rank=rank,
                    modelScore=row.score,
                )
                for rank, row in enumerate(filtered, 1)
            ]
            filtering_ns = perf_counter_ns() - started

            query_sha = hashlib.sha256(
                np.asarray(query, dtype="<f4").tobytes(order="C")
            ).hexdigest()
            base = {
                "schemaVersion": 2 if is_v2 else 1,
                "requestId": request.requestId,
                "runId": request.runId,
                "modelId": snapshot.model.modelId,
                "modelVersion": materialized.model_version,
                "retrievalBackend": snapshot.candidate_index.backend,
                "embeddingSpaceId": materialized.embedding_space_id,
                "pgvectorSnapshotSha256": materialized.pgvector_snapshot_sha256,
                "backendSnapshotSha256": materialized.backend_snapshot_sha256,
                "queryVectorSha256": query_sha,
                "candidates": candidates,
            }
            if is_v2:
                base["sourceVectorOrigin"] = source_origin
            # Timing values are part of the JSON they measure, so ordinary
            # serialize-then-update would require a second, unmeasured JSON
            # encoding.  Encode the actual wire template once with unique
            # valid integer sentinels, measure that operation, then patch only
            # the two numeric tokens in the encoded bytes.  Response receives
            # bytes and therefore performs no second UTF-8/JSON encoding.
            serialization_started = perf_counter_ns()
            template_timings = {
                "queue": queue_ns,
                "encode": encode_ns,
                "context": context_ns,
                "ann": ann_ns,
                "filtering": filtering_ns,
                "serialization": _SERIALIZATION_PLACEHOLDER_NS,
                "serverTotal": _SERVER_TOTAL_PLACEHOLDER_NS,
            }
            response_type = RecommendationResponseV2 if is_v2 else RecommendationResponseV1
            response = response_type(**base, timingsNs=template_timings)
            payload = response.model_dump_json().encode("utf-8")
            serialization_ns = perf_counter_ns() - serialization_started
            timings = {**template_timings, "serialization": serialization_ns}
            payload = _replace_timing_token(
                payload,
                name="serialization",
                placeholder=_SERIALIZATION_PLACEHOLDER_NS,
                value=serialization_ns,
            )
            timings["serverTotal"] = max(
                perf_counter_ns() - request_started,
                sum(value for name, value in timings.items() if name != "serverTotal"),
            )
            payload = _replace_timing_token(
                payload,
                name="serverTotal",
                placeholder=_SERVER_TOTAL_PLACEHOLDER_NS,
                value=timings["serverTotal"],
            )
            return Response(
                content=payload,
                media_type="application/json",
                headers={
                    "Server-Timing": server_timing_header(timings),
                    "X-Babel-Model-Manifest-Sha256": snapshot.model_manifest_sha256,
                    "X-Babel-Encoder-Mode": encoder_identity.mode,
                    "X-Babel-Encoder-Device": encoder_identity.device,
                    "X-Babel-Encoder-Batch-Size": str(encoder_identity.batch_size),
                    "X-Babel-Encoder-Cache-Identity": encoder_identity.cache_identity,
                    "X-Babel-Serialization-Measurement": _SERIALIZATION_MEASUREMENT,
                },
            )
        finally:
            semaphore.release()

    @app.post("/api/v1/recommendations")
    def recommend_v1(request: RecommendationRequestV1) -> Response:
        return recommend_document(request)

    @app.post("/api/v2/recommendations")
    def recommend_v2(request: RecommendationRequestV2) -> Response:
        return recommend_document(request)

    return app


__all__ = ["SERVING_HOST", "create_app"]
