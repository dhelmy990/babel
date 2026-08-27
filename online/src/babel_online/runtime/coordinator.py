"""Simulator-only coordinator for the split serving/trainer topology.

Formal topology comparisons consume the Task 10 population-ready snapshot and
Task 12 cloned population/schedule. Depth-zero work re-encodes those frozen
roots; it never creates a topology-specific candidate universe.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid5

from ..contracts import (
    ActivityLogV2,
    CandidateActionV1,
    FeedbackEventV2,
    RecommendationRequestV2,
    RecommendationActivityV2,
)
from ..observable import CreatedBabel
from ..simulation.client import RecommendationClient
from ..simulation.decisions import (
    action_probabilities,
    combined_relevance,
    decide_candidate,
    deterministic_draw,
)
from ..simulation.scheduler import (
    BoundedCreatorScheduler,
    ScheduledSession,
)
from ..simulation.walk import (
    IncludedWalkTarget,
    RecommendationWalk,
    WalkExpansion,
    WalkNode,
    WalkRollEvidence,
)


class WorkloadTraceSink(Protocol):
    """Optional observation boundary used to freeze one reference workload."""

    def record_request(self, request: RecommendationRequestV2) -> None: ...

    def record_feedback(self, event: FeedbackEventV2) -> None: ...

    def record_response(
        self,
        request: RecommendationRequestV2,
        response: Any,
        client_total_ns: int,
    ) -> None: ...

    def record_rolls(
        self,
        traversal_session_id: UUID,
        rolls: Sequence[WalkRollEvidence],
    ) -> None: ...


class StandaloneCoordinator:
    """Issue deterministic HTTP work and Kafka feedback without owning a model."""

    def __init__(
        self,
        *,
        config: Any,
        database: Any,
        schedule: Sequence[ScheduledSession],
        babels: Mapping[UUID, CreatedBabel],
        hidden_edges: Mapping[str, set[tuple[str, str]]],
        producer: Any,
        client_factory: Callable[[], RecommendationClient],
        stop_event: Any,
        decide: Callable[[ScheduledSession, WalkNode, int, Any, str], str]
        | None = None,
        trace_sink: WorkloadTraceSink | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.schedule = tuple(schedule)
        self.babels = dict(babels)
        self.hidden_edges = hidden_edges
        self.producer = producer
        self.client_factory = client_factory
        self.stop_event = stop_event
        self._decide = decide or self._default_decision
        self._trace_sink = trace_sink
        self._lock = Lock()
        self._feedback_count = 0
        self._kafka_offset = 0
        self._started = time.monotonic()
        prior_roots: dict[UUID, list[UUID]] = {}
        self._history_by_session: dict[UUID, tuple[UUID, ...]] = {}
        for scheduled in self.schedule:
            history = prior_roots.setdefault(scheduled.creator_id, [])
            self._history_by_session[scheduled.traversal_session_id] = tuple(history)
            history.append(scheduled.root_babel_id)

    def _default_decision(
        self,
        scheduled: ScheduledSession,
        source: WalkNode,
        source_depth: int,
        candidate: Any,
        period: str,
    ) -> str:
        related = (
            0.95
            if (source.source_article_key, candidate.sourceArticleKey)
            in self.hidden_edges[period]
            else 0.55
        )
        preference = (
            0.7
            if deterministic_draw(
                self.config.runSeed,
                "preference",
                self.config.runId,
                scheduled.creator_id,
                scheduled.traversal_session_id,
                source.babel_id,
                source_depth,
                candidate.babelId,
                candidate.sourceArticleKey,
                candidate.rank,
            )
            < 0.5
            else 0.4
        )
        probabilities = action_probabilities(
            relevance=combined_relevance(
                relatedness_rank=related, preference_rank=preference
            ),
            epsilon=0.2,
            exclusion_propensity=0.25,
        )
        return decide_candidate(
            probabilities,
            draw=deterministic_draw(
                self.config.runSeed,
                "action",
                self.config.runId,
                scheduled.creator_id,
                scheduled.traversal_session_id,
                source.babel_id,
                source_depth,
                candidate.babelId,
                candidate.sourceArticleKey,
                candidate.rank,
            ),
        )

    def _run_session(self, scheduled: ScheduledSession) -> None:
        if self.stop_event.is_set() or self.database.stop_requested(self.config.runId):
            return
        root_babel = self.babels.get(scheduled.root_babel_id)
        if root_babel is None:
            raise RuntimeError("frozen schedule root is absent from serving population")
        client = self.client_factory()

        def recommend(
            source: WalkNode,
            session_id: UUID,
            parent_request_id: UUID | None,
            source_depth: int,
        ) -> WalkExpansion:
            request_id = uuid5(
                self.config.runId,
                f"request:v2:{session_id}:{source.babel_id}:{source_depth}",
            )
            request = RecommendationRequestV2(
                schemaVersion=2,
                requestId=request_id,
                runId=self.config.runId,
                creatorId=scheduled.creator_id,
                sourceBabelId=source.babel_id,
                sourceArticleKey=source.source_article_key,
                traversalSessionId=session_id,
                parentRequestId=parent_request_id,
                traversalDepth=source_depth,
                title=root_babel.title if source_depth == 0 else None,
                text=root_babel.text if source_depth == 0 else None,
                historyBabelIds=[
                    babel_id
                    for babel_id in self._history_by_session[session_id]
                    if babel_id != source.babel_id
                ],
                candidateCount=self.config.recommendationK,
            )
            if self._trace_sink is not None:
                self._trace_sink.record_request(request)
            client_started_ns = time.perf_counter_ns()
            response = client.recommend(request)
            client_total_ns = time.perf_counter_ns() - client_started_ns
            if self._trace_sink is not None:
                self._trace_sink.record_response(request, response, client_total_ns)
            if response.requestId != request_id or response.runId != self.config.runId:
                raise RuntimeError("split recommendation response identity differs")
            actions = []
            included = []
            for candidate in response.candidates:
                action = self._decide(
                    scheduled, source, source_depth, candidate, scheduled.period
                )
                checked = CandidateActionV1(
                    babelId=candidate.babelId,
                    sourceArticleKey=candidate.sourceArticleKey,
                    rank=candidate.rank,
                    modelScore=candidate.modelScore,
                    action=action,
                )
                actions.append(checked)
                if action == "include":
                    included.append(
                        IncludedWalkTarget(
                            node=WalkNode(
                                babel_id=candidate.babelId,
                                source_article_key=candidate.sourceArticleKey,
                            ),
                            rank=candidate.rank,
                        )
                    )
            event = FeedbackEventV2(
                schemaVersion=2,
                eventId=uuid5(
                    self.config.runId,
                    f"feedback:v2:{session_id}:{source.babel_id}:{source_depth}",
                ),
                requestId=request_id,
                runId=self.config.runId,
                creatorId=scheduled.creator_id,
                sourceBabelId=source.babel_id,
                sourceArticleKey=source.source_article_key,
                traversalSessionId=session_id,
                parentRequestId=parent_request_id,
                traversalDepth=source_depth,
                modelId=response.modelId,
                modelVersion=response.modelVersion,
                embeddingSpaceId=response.embeddingSpaceId,
                retrievalBackend=response.retrievalBackend,
                sourceVectorOrigin=response.sourceVectorOrigin,
                candidateActions=actions,
                occurredAtNs=time.time_ns(),
            )
            record = self.producer.publish(key=str(scheduled.creator_id), event=event)
            if self._trace_sink is not None:
                self._trace_sink.record_feedback(event)
            self.database.persist_feedback_edges(event)
            grouped = {"include": [], "exclude": [], "ignore": []}
            for action in actions:
                grouped[action.action].append(action.babelId)
            self.database.append_activity(
                ActivityLogV2(
                    schemaVersion=2,
                    runId=self.config.runId,
                    sequence=1,
                    occurredAtNs=time.time_ns(),
                    level="info",
                    component="serving",
                    event="frozen_population_root_recommended",
                    message=(
                        "Re-encoded a frozen population root for the timed "
                        "creation/recommendation workload; population was not mutated."
                    ),
                    metrics={
                        "frozenPopulationRootReplay": 1,
                        "traversalDepth": source_depth,
                    },
                    details=RecommendationActivityV2(
                        kind="recommendation",
                        creatorId=scheduled.creator_id,
                        newBabelId=source.babel_id,
                        newBabelTitle=self.babels[source.babel_id].title,
                        candidateBabelIds=[row.babelId for row in response.candidates],
                        includeBabelIds=grouped["include"],
                        excludeBabelIds=grouped["exclude"],
                        ignoreBabelIds=grouped["ignore"],
                        acceptedEdgeCount=len(grouped["include"]),
                        modelId=response.modelId,
                        modelVersion=response.modelVersion,
                        requestId=request_id,
                        traversalSessionId=session_id,
                        sourceVectorOrigin=response.sourceVectorOrigin,
                    ),
                )
            )
            with self._lock:
                self._feedback_count += 1
                self._kafka_offset = max(self._kafka_offset, int(record.offset) + 1)
                self.database.update_metrics(
                    self.config.runId,
                    feedback_count=self._feedback_count,
                    event_rate=self._feedback_count
                    / max(time.monotonic() - self._started, 1e-6),
                    kafka_offset=self._kafka_offset,
                )
            return WalkExpansion(request_id=request_id, included=tuple(included))

        try:
            walk = RecommendationWalk(
                start_probability=self.config.recommendationStartProbability,
                continuation_probability=self.config.continuationProbability,
                max_depth=self.config.maximumTraversalDepth,
                max_requests=self.config.maximumRequestsPerTraversal,
                draw=lambda *identity: deterministic_draw(
                    self.config.runSeed, *identity
                ),
            )
            trace = walk.run(
                run_id=self.config.runId,
                creator_id=scheduled.creator_id,
                session_id=scheduled.traversal_session_id,
                root=WalkNode(
                    babel_id=root_babel.babelId,
                    source_article_key=root_babel.sourceArticleKey,
                ),
                recommend=recommend,
            )
            if self._trace_sink is not None:
                self._trace_sink.record_rolls(
                    scheduled.traversal_session_id, trace.rolls
                )
            self.database.persist_traversal_rolls(
                self.config.runId, scheduled.traversal_session_id, trace.rolls
            )
        finally:
            client.close()

    def run(self) -> None:
        try:
            BoundedCreatorScheduler(
                concurrent_users=self.config.concurrentUsers
            ).run(self.schedule, self._run_session)
        finally:
            self.producer.close()


def coordinator_from_environment(run_id: UUID, stop_event: Any) -> StandaloneCoordinator:
    """Build the simulator-only role from pinned DB/Hugging Face identities."""
    from ..feedback.kafka import KafkaFeedbackProducer
    from .database import RuntimeDatabase
    from .dataset_bundle import acquire_pinned_bundle, load_scale_dataset_bundle

    database_url = os.environ.get("BABEL_DATABASE_URL")
    token = os.environ.get("HF_TOKEN")
    if not database_url or not token:
        raise RuntimeError("coordinator requires BABEL_DATABASE_URL and HF_TOKEN")
    database = RuntimeDatabase(database_url)
    persisted = database.load_run(run_id)
    config = persisted.config
    root = acquire_pinned_bundle(
        repo_id=config.datasetRepo,
        revision=config.datasetRevision,
        token=token,
        cache_dir=os.environ.get("BABEL_ONLINE_HF_CACHE", "state/online/cache/dataset"),
    )
    bundle = load_scale_dataset_bundle(
        root,
        dataset_repository=config.datasetRepo,
        dataset_config=config.datasetConfig,
        dataset_revision=config.datasetRevision,
    )
    hidden: dict[str, set[tuple[str, str]]] = {}
    for period in config.environmentSequence:
        rows = bundle.configs[f"simulator_{period.replace('-', '_')}_hidden"]
        edges = set()
        for row in rows:
            if row.get("record_type") != "pagelink":
                continue
            payload = json.loads(row["payload_json"])
            edges.add(
                (payload["source_article_key"], payload["target_article_key"])
            )
        hidden[period] = edges
    created = database.created_babels(run_id)
    babels = {babel.babelId: babel for babel in created}
    schedule = database.load_work_schedule(run_id)
    if not schedule:
        raise RuntimeError(
            "split measurement requires Task10 population-ready approval and "
            "a persisted Task8 schedule"
        )
    if len(schedule) != sum(config.perMonthEventBudget.values()):
        raise RuntimeError("persisted schedule does not match the approved trial size")
    if any(row.root_babel_id not in babels for row in schedule):
        raise RuntimeError("persisted schedule differs from frozen serving population")
    endpoint = os.environ.get(
        "BABEL_ONLINE_SERVING_ENDPOINT", "http://127.0.0.1:8791"
    )
    return StandaloneCoordinator(
        config=config,
        database=database,
        schedule=schedule,
        babels=babels,
        hidden_edges=hidden,
        producer=KafkaFeedbackProducer(
            os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
        ),
        client_factory=lambda: RecommendationClient(endpoint),
        stop_event=stop_event,
    )


__all__ = ["StandaloneCoordinator", "coordinator_from_environment"]
