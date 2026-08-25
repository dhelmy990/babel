from __future__ import annotations

from uuid import UUID

from babel_online.contracts import CandidateActionV1, FeedbackEventV1
from babel_online.training.pairs import pairs_from_event


INCLUDED = UUID("00000000-0000-5000-8000-000000000101")
EXCLUDED = UUID("00000000-0000-5000-8000-000000000102")
IGNORED = UUID("00000000-0000-5000-8000-000000000103")


def event_with_three_actions() -> FeedbackEventV1:
    actions = [
        CandidateActionV1(
            babelId=INCLUDED,
            sourceArticleKey="enwiki:101",
            rank=1,
            modelScore=0.8,
            action="include",
        ),
        CandidateActionV1(
            babelId=EXCLUDED,
            sourceArticleKey="enwiki:102",
            rank=2,
            modelScore=0.5,
            action="exclude",
        ),
        CandidateActionV1(
            babelId=IGNORED,
            sourceArticleKey="enwiki:103",
            rank=3,
            modelScore=0.2,
            action="ignore",
        ),
    ]
    return FeedbackEventV1(
        schemaVersion=1,
        eventId="00000000-0000-5000-8000-000000000110",
        requestId="00000000-0000-5000-8000-000000000111",
        runId="00000000-0000-5000-8000-000000000001",
        creatorId="00000000-0000-5000-8000-000000000020",
        newBabelId="00000000-0000-5000-8000-000000000050",
        newSourceArticleKey="enwiki:1",
        modelId="00000000-0000-5000-8000-000000000002",
        modelVersion=0,
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        retrievalBackend="pgvector",
        candidateActions=actions,
        occurredAtNs=123,
    )


def test_include_pairs_with_hard_and_soft_negatives() -> None:
    pairs = pairs_from_event(event_with_three_actions())

    assert {(pair.positive_id, pair.negative_id, pair.weight) for pair in pairs} == {
        (INCLUDED, EXCLUDED, 1.0),
        (INCLUDED, IGNORED, 0.25),
    }
