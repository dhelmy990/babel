"""One deterministic observable feedback step over a hidden world."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from babel_online.feedback.bus import OffsetRange, TopicPartition

from .decisions import (
    action_probabilities,
    combined_relevance,
    decide_candidate,
    deterministic_draw,
)
from .sampling import SourceSampler


@dataclass(frozen=True, slots=True)
class SimulationArticle:
    article_key: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class AcceptedEdge:
    creator_id: UUID
    source_babel_id: UUID
    target_babel_id: UUID
    request_id: UUID


@dataclass(frozen=True, slots=True)
class SimulationStepResult:
    request_id: UUID
    event_id: UUID
    new_babel_id: UUID
    accepted_edges: tuple[AcceptedEdge, ...]
    offset_ranges: tuple[OffsetRange, ...]


@dataclass(slots=True)
class _PendingStep:
    article: SimulationArticle
    staged: Any
    request: Any
    event_id: UUID


class SimulationEngine:
    def __init__(
        self,
        *,
        run_id: UUID,
        creator_id: UUID,
        model_id: UUID,
        embedding_space_id: UUID,
        retrieval_backend: str,
        sampler: SourceSampler,
        articles: Mapping[str, SimulationArticle],
        store: Any,
        client: Any,
        producer: Any,
        hidden_ranks: Callable[[str, str], tuple[float, float]],
        draw_for: Callable[..., float] = deterministic_draw,
        candidate_count: int = 10,
        epsilon: float = 0.2,
        exclusion_propensity: float = 0.25,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.run_id = run_id
        self.creator_id = creator_id
        self.model_id = model_id
        self.embedding_space_id = embedding_space_id
        self.retrieval_backend = retrieval_backend
        self.sampler = sampler
        self.articles = dict(articles)
        self.store = store
        self.client = client
        self.producer = producer
        self.hidden_ranks = hidden_ranks
        self.draw_for = draw_for
        self.candidate_count = candidate_count
        self.epsilon = epsilon
        self.exclusion_propensity = exclusion_propensity
        self.clock_ns = clock_ns
        persisted_history = tuple(
            self.store.creator_history(
                run_id=self.run_id,
                creator_id=self.creator_id,
            )
        )
        self.event_number = len(persisted_history)
        self.history_babel_ids: list[UUID] = list(persisted_history)
        staged = self.store.pending_babel(
            run_id=self.run_id,
            creator_id=self.creator_id,
        )
        if staged is not None:
            if staged.event_number != self.event_number:
                raise ValueError("pending Babel event sequence is inconsistent")
            article = self.articles[staged.source_article_key]
            self._pending: _PendingStep | None = self._build_pending(article, staged)
        else:
            self._pending = None
        self._accepted_edges: list[AcceptedEdge] = []

    @property
    def accepted_edges(self) -> tuple[AcceptedEdge, ...]:
        return tuple(self._accepted_edges)

    def _start_pending(self) -> _PendingStep:
        while True:
            source_key = self.sampler.take()
            if self.store.source_is_available(
                run_id=self.run_id,
                creator_id=self.creator_id,
                source_article_key=source_key,
            ):
                break
        article = self.articles[source_key]
        staged = self.store.stage_babel(
            run_id=self.run_id,
            creator_id=self.creator_id,
            source_article_key=article.article_key,
            title=article.title,
            text=article.text,
            event_number=self.event_number,
        )
        return self._build_pending(article, staged)

    def _build_pending(self, article: SimulationArticle, staged: Any) -> _PendingStep:
        from babel_online.contracts import RecommendationRequestV1

        request_id = uuid5(
            self.run_id, f"request:{self.creator_id}:{self.event_number}"
        )
        event_id = uuid5(
            self.run_id, f"feedback:{self.creator_id}:{self.event_number}"
        )
        request = RecommendationRequestV1(
            schemaVersion=1,
            requestId=request_id,
            runId=self.run_id,
            creatorId=self.creator_id,
            newBabelId=staged.babel_id,
            newSourceArticleKey=article.article_key,
            title=article.title,
            text=article.text,
            historyBabelIds=list(self.history_babel_ids),
            candidateCount=self.candidate_count,
        )
        return _PendingStep(article, staged, request, event_id)

    def step(self) -> SimulationStepResult:
        from babel_online.contracts import CandidateActionV1, FeedbackEventV1

        pending = self._pending or self._start_pending()
        self._pending = pending
        response = self.client.recommend(pending.request)
        if response.requestId != pending.request.requestId or response.runId != self.run_id:
            raise ValueError("recommendation response identity mismatch")
        if (
            response.modelId != self.model_id
            or response.embeddingSpaceId != self.embedding_space_id
            or response.retrievalBackend != self.retrieval_backend
        ):
            raise ValueError("recommendation response model identity mismatch")
        created_ids = self.store.created_babel_ids(self.run_id)
        actions = []
        edges = []
        for candidate in response.candidates:
            if candidate.babelId not in created_ids:
                raise ValueError("recommendation contains an uncreated Babel candidate")
            if candidate.creatorId == self.creator_id:
                raise ValueError("recommendation contains the request creator's Babel")
            relatedness, preference = self.hidden_ranks(
                pending.article.article_key, candidate.sourceArticleKey
            )
            probabilities = action_probabilities(
                relevance=combined_relevance(
                    relatedness_rank=relatedness,
                    preference_rank=preference,
                ),
                epsilon=self.epsilon,
                exclusion_propensity=self.exclusion_propensity,
            )
            action = decide_candidate(
                probabilities,
                draw=self.draw_for(
                    self.run_id,
                    self.creator_id,
                    self.event_number,
                    candidate.babelId,
                ),
            )
            actions.append(
                CandidateActionV1(
                    babelId=candidate.babelId,
                    sourceArticleKey=candidate.sourceArticleKey,
                    rank=candidate.rank,
                    modelScore=candidate.modelScore,
                    action=action,
                )
            )
            if action == "include":
                edges.append(
                    AcceptedEdge(
                        self.creator_id,
                        pending.staged.babel_id,
                        candidate.babelId,
                        pending.request.requestId,
                    )
                )
        event = FeedbackEventV1(
            schemaVersion=1,
            eventId=pending.event_id,
            requestId=pending.request.requestId,
            runId=self.run_id,
            creatorId=self.creator_id,
            newBabelId=pending.staged.babel_id,
            newSourceArticleKey=pending.article.article_key,
            modelId=response.modelId,
            modelVersion=response.modelVersion,
            embeddingSpaceId=response.embeddingSpaceId,
            retrievalBackend=response.retrievalBackend,
            candidateActions=actions,
            occurredAtNs=self.clock_ns(),
        )
        record = self.producer.publish(key=str(self.creator_id), event=event)
        self.store.finalize_babel(
            run_id=self.run_id,
            creator_id=self.creator_id,
            babel_id=pending.staged.babel_id,
            request_id=pending.request.requestId,
        )
        self._accepted_edges.extend(edges)
        self.history_babel_ids.append(pending.staged.babel_id)
        self.event_number += 1
        self._pending = None
        offset_range = OffsetRange(
            TopicPartition(record.topic, record.partition),
            record.offset,
            record.offset + 1,
        )
        return SimulationStepResult(
            pending.request.requestId,
            pending.event_id,
            pending.staged.babel_id,
            tuple(edges),
            (offset_range,),
        )


def reconstruct_accepted_edges(events: list[Any]) -> tuple[tuple[UUID, UUID], ...]:
    return tuple(
        (event.newBabelId, action.babelId)
        for event in events
        for action in event.candidateActions
        if action.action == "include"
    )


__all__ = [
    "AcceptedEdge",
    "SimulationArticle",
    "SimulationEngine",
    "SimulationStepResult",
    "reconstruct_accepted_edges",
]
