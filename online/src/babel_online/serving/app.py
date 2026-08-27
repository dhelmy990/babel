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
    RecommendationResponseV1,
)
from ..model.context_tower import CreatorContextTower
from .state import ServingState
from .timings import server_timing_header


SERVING_HOST = "127.0.0.1"


def create_app(state: ServingState, *, max_concurrent_requests: int = 8) -> FastAPI:
    if max_concurrent_requests <= 0:
        raise ValueError("max_concurrent_requests must be positive")
    app = FastAPI(title="Babel online recommendations", version="1")
    semaphore = BoundedSemaphore(max_concurrent_requests)

    @app.post("/api/v1/recommendations")
    def recommend(request: RecommendationRequestV1) -> Response:
        request_started = perf_counter_ns()
        queued_at = request_started
        semaphore.acquire()
        try:
            queue_ns = perf_counter_ns() - queued_at
            snapshot = state.snapshot()
            materialized = snapshot.materialized_state
            if request.runId != materialized.run_id:
                raise HTTPException(status_code=409, detail="request run is not active")
            if (
                request.runId,
                request.creatorId,
                request.newSourceArticleKey,
            ) in snapshot.creator_sources:
                raise HTTPException(
                    status_code=409,
                    detail="creator already used this source article in the run",
                )

            started = perf_counter_ns()
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
                k=request.candidateCount,
            )
            ann_ns = perf_counter_ns() - started

            started = perf_counter_ns()
            filtered = []
            seen = set()
            for row in retrieved:
                if row.creator_id == request.creatorId or row.babel_id in seen:
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
                "schemaVersion": 1,
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
            serialization_started = perf_counter_ns()
            timings = {
                "queue": queue_ns,
                "encode": encode_ns,
                "context": context_ns,
                "ann": ann_ns,
                "filtering": filtering_ns,
                "serialization": 0,
                "serverTotal": perf_counter_ns() - request_started,
            }
            response = RecommendationResponseV1(**base, timingsNs=timings)
            timings["serialization"] = perf_counter_ns() - serialization_started
            timings["serverTotal"] = max(
                perf_counter_ns() - request_started,
                sum(value for name, value in timings.items() if name != "serverTotal"),
            )
            response = response.model_copy(update={"timingsNs": timings})
            payload = response.model_dump_json()
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
                },
            )
        finally:
            semaphore.release()

    return app


__all__ = ["SERVING_HOST", "create_app"]
