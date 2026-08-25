from __future__ import annotations

from uuid import UUID

import pytest

from babel_online.contracts import CandidateActionV1, FeedbackEventV1
from babel_online.feedback.bus import InMemoryFeedbackBus, TopicPartition


CREATOR_ID = UUID("00000000-0000-5000-8000-000000000020")


def feedback_event(*, event_number: int = 1) -> FeedbackEventV1:
    suffix = f"{event_number:012d}"
    return FeedbackEventV1(
        schemaVersion=1,
        eventId=f"00000000-0000-5000-8000-{suffix}",
        requestId=f"10000000-0000-5000-8000-{suffix}",
        runId="00000000-0000-5000-8000-000000000001",
        creatorId=CREATOR_ID,
        newBabelId=f"20000000-0000-5000-8000-{suffix}",
        newSourceArticleKey=f"enwiki:{event_number}",
        modelId="00000000-0000-5000-8000-000000000002",
        modelVersion=0,
        embeddingSpaceId="00000000-0000-5000-8000-000000000003",
        retrievalBackend="pgvector",
        candidateActions=[
            CandidateActionV1(
                babelId=f"30000000-0000-5000-8000-{suffix}",
                sourceArticleKey=f"enwiki:{event_number + 100}",
                rank=1,
                modelScore=0.5,
                action="include",
            )
        ],
        occurredAtNs=event_number,
    )


def test_feedback_is_keyed_and_consumed_before_manual_commit() -> None:
    bus = InMemoryFeedbackBus(topic="babel.feedback.v1")
    event = feedback_event()

    record = bus.publish(key=str(event.creatorId), event=event)
    consumer = bus.consumer(group_id="trainer", auto_commit=False)

    assert record.offset == 0
    assert consumer.poll() == record
    partition = TopicPartition("babel.feedback.v1", 0)
    assert consumer.position() == {partition: 1}
    assert consumer.committed() == {partition: 0}

    consumer.commit({partition: 1})
    assert consumer.committed() == {partition: 1}


def test_bus_rejects_wrong_topic_auto_commit_and_wrong_creator_key() -> None:
    with pytest.raises(ValueError, match="only topic"):
        InMemoryFeedbackBus(topic="another-topic")

    bus = InMemoryFeedbackBus(topic="babel.feedback.v1")
    with pytest.raises(ValueError, match="automatic offset commits"):
        bus.consumer(group_id="trainer", auto_commit=True)
    with pytest.raises(ValueError, match="creator ID"):
        bus.publish(key="wrong", event=feedback_event())
