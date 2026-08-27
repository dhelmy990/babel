from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from babel_online.contracts import (
    RecommendationCandidateV1,
    RecommendationResponseV1,
)
from babel_online.feedback.bus import InMemoryFeedbackBus
from babel_online.simulation.engine import SimulationArticle, SimulationEngine
from babel_online.simulation.sampling import EligibleSupportExhausted, SourceSampler


RUN = UUID("00000000-0000-5000-8000-000000000001")
MODEL = UUID("00000000-0000-5000-8000-000000000002")
SPACE = UUID("00000000-0000-5000-8000-000000000003")
CREATOR = UUID("00000000-0000-5000-8000-000000000020")
OTHER_CREATOR = UUID("00000000-0000-5000-8000-000000000021")
CANDIDATE = UUID("00000000-0000-5000-8000-000000000040")
NEW_BABEL = UUID("00000000-0000-5000-8000-000000000050")


class Store:
    def __init__(self) -> None:
        self.ids = {CANDIDATE}
        self.calls = []
        self.sources = set()
        self.histories = {}
        self.pending = {}

    def source_is_available(self, *, run_id, creator_id, source_article_key):
        return (run_id, creator_id, source_article_key) not in self.sources

    def stage_babel(self, **values):
        self.calls.append(values)
        self.sources.add(
            (
                values["run_id"],
                values["creator_id"],
                values["source_article_key"],
            )
        )
        babel_id = UUID(
            f"00000000-0000-5000-8000-{50 + values['event_number']:012d}"
        )
        self.ids.add(babel_id)
        staged = SimpleNamespace(
            babel_id=babel_id,
            source_article_key=values["source_article_key"],
            event_number=values["event_number"],
        )
        self.pending[(values["run_id"], values["creator_id"])] = staged
        return staged

    def creator_history(self, *, run_id, creator_id):
        return tuple(self.histories.get((run_id, creator_id), ()))

    def pending_babel(self, *, run_id, creator_id):
        return self.pending.get((run_id, creator_id))

    def finalize_babel(self, *, run_id, creator_id, babel_id, request_id):
        del request_id
        key = (run_id, creator_id)
        assert self.pending[key].babel_id == babel_id
        self.histories.setdefault(key, []).append(babel_id)
        del self.pending[key]

    def created_babel_ids(self, run_id):
        assert run_id == RUN
        return set(self.ids)


class Client:
    def __init__(self) -> None:
        self.calls = []

    def recommend(self, request):
        self.calls.append(request)
        return RecommendationResponseV1(
            schemaVersion=1,
            requestId=request.requestId,
            runId=request.runId,
            modelId=MODEL,
            modelVersion=0,
            retrievalBackend="pgvector",
            embeddingSpaceId=SPACE,
            pgvectorSnapshotSha256="a" * 64,
            backendSnapshotSha256="a" * 64,
            queryVectorSha256="b" * 64,
            candidates=[
                RecommendationCandidateV1(
                    babelId=CANDIDATE,
                    creatorId=OTHER_CREATOR,
                    sourceArticleKey="enwiki:99",
                    rank=1,
                    modelScore=0.6,
                )
            ],
            timingsNs={
                "queue": 1,
                "encode": 1,
                "context": 1,
                "ann": 1,
                "filtering": 1,
                "serialization": 1,
                "serverTotal": 6,
            },
        )


def engine(*, producer=None, store=None, source_keys=("enwiki:1",)):
    bus = producer or InMemoryFeedbackBus()
    return (
        SimulationEngine(
            run_id=RUN,
            creator_id=CREATOR,
            model_id=MODEL,
            embedding_space_id=SPACE,
            retrieval_backend="pgvector",
            sampler=SourceSampler(source_keys, seed=7),
            articles={
                key: SimulationArticle(key, f"New {key}", "Observable text")
                for key in source_keys
            },
            store=store or Store(),
            client=Client(),
            producer=bus,
            hidden_ranks=lambda _new, _candidate: (0.8, 0.8),
            draw_for=lambda *_identity: 0.1,
            candidate_count=1,
            clock_ns=lambda: 123,
        ),
        bus,
    )


def test_step_posts_before_publishing_and_exposes_only_include_edges() -> None:
    runtime, bus = engine()

    result = runtime.step()

    assert runtime.client.calls[0].requestId == result.request_id
    record = bus.records(next(iter(result.offset_ranges))) [0]
    assert record.event.requestId == result.request_id
    assert record.event.candidateActions[0].action == "include"
    assert {edge.target_babel_id for edge in result.accepted_edges} == {CANDIDATE}
    assert CANDIDATE in runtime.store.created_babel_ids(RUN)
    assert "hidden" not in record.event.model_dump_json().lower()


class UnavailableProducer:
    def publish(self, **_values):
        raise RuntimeError("broker unavailable")

    def flush(self):
        return None

    def close(self):
        return None


def test_publish_failure_exposes_no_edge_and_retry_keeps_request_identity() -> None:
    runtime, _ = engine(producer=UnavailableProducer())

    with pytest.raises(RuntimeError, match="broker unavailable"):
        runtime.step()
    first_request = runtime.client.calls[0].requestId

    runtime.producer = InMemoryFeedbackBus()
    result = runtime.step()
    assert result.request_id == first_request
    assert len(runtime.store.calls) == 1


def test_crash_after_stage_reconstructs_and_retries_the_pending_event() -> None:
    store = Store()
    crashed, _ = engine(producer=UnavailableProducer(), store=store)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        crashed.step()
    first_request = crashed.client.calls[0]

    restarted, _ = engine(store=store)
    result = restarted.step()

    assert result.request_id == first_request.requestId
    assert result.new_babel_id == first_request.newBabelId
    assert restarted.client.calls[0].historyBabelIds == []
    assert len(store.calls) == 1
    assert store.pending_babel(run_id=RUN, creator_id=CREATOR) is None


def test_restart_skips_creator_sources_already_in_persistent_store() -> None:
    store = Store()
    first, _ = engine(store=store, source_keys=("enwiki:1", "enwiki:2"))
    first_result = first.step()
    restarted, _ = engine(store=store, source_keys=("enwiki:1", "enwiki:2"))
    second_result = restarted.step()
    exhausted, _ = engine(store=store, source_keys=("enwiki:1", "enwiki:2"))

    with pytest.raises(EligibleSupportExhausted):
        exhausted.step()
    assert len(store.calls) == 2
    assert {call["source_article_key"] for call in store.calls} == {
        "enwiki:1",
        "enwiki:2",
    }
    assert second_result.request_id != first_result.request_id
    assert second_result.event_id != first_result.event_id
    assert restarted.client.calls[0].historyBabelIds == [first_result.new_babel_id]


def test_response_must_match_the_configured_model_identity() -> None:
    runtime, bus = engine()
    normal_client = runtime.client

    class WrongModelClient:
        def recommend(self, request):
            return normal_client.recommend(request).model_copy(
                update={"modelId": UUID("00000000-0000-5000-8000-000000000099")}
            )

    runtime.client = WrongModelClient()

    with pytest.raises(ValueError, match="model identity"):
        runtime.step()
    assert bus.high_watermarks().popitem()[1] == 0
