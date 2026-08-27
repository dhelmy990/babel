from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.pgvector_index import (
    PGVECTOR_CREATED_BABEL_QUERY,
    PGVECTOR_TRANSACTION_SETTINGS,
    PgvectorCandidateIndex,
)
from babel_online.observable import CreatedBabel, VectorRecord


RUN = UUID("00000000-0000-5000-8000-000000000001")
MODEL = UUID("00000000-0000-5000-8000-000000000002")
SPACE = UUID("00000000-0000-5000-8000-000000000003")
CREATOR_A = UUID("00000000-0000-5000-8000-000000000101")
CREATOR_B = UUID("00000000-0000-5000-8000-000000000102")
CREATOR_C = UUID("00000000-0000-5000-8000-000000000103")


def vector(axis: int) -> tuple[float, ...]:
    values = np.zeros(100, dtype=np.float32)
    values[axis] = 1.0
    return tuple(float(value) for value in values)


def record(number: int, creator: UUID, source: str, axis: int) -> VectorRecord:
    return VectorRecord(
        babel=CreatedBabel(
            babelId=UUID(f"00000000-0000-5000-8000-{number:012d}"),
            runId=RUN,
            creatorId=creator,
            sourceArticleKey=source,
            title=f"Babel {number}",
            text=f"Observable Babel {number}.",
            createdAtNs=number,
        ),
        catalogContentHash="a" * 64,
        embeddingSpaceId=SPACE,
        servingModelId=MODEL,
        materializedModelVersion=0,
        vector=vector(axis),
    )


def state() -> MaterializedServingState:
    return MaterializedServingState(
        run_id=RUN,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="b" * 64,
    )


def test_fixture_index_returns_only_other_creators_created_babels() -> None:
    index = InMemoryCreatedBabelIndex(
        [
            record(201, CREATOR_A, "enwiki:593", 0),
            record(202, CREATOR_B, "enwiki:2032", 0),
            record(203, CREATOR_C, "enwiki:2032", 1),
        ]
    )
    index.activate(state())

    result = index.search(
        np.asarray(vector(0), dtype=np.float32),
        run_id=RUN,
        state=state(),
        exclude_creator_id=CREATOR_A,
        k=10,
    )

    assert {row.babel_id for row in result} == {
        UUID("00000000-0000-5000-8000-000000000202"),
        UUID("00000000-0000-5000-8000-000000000203"),
    }
    same_source = [row for row in result if row.source_article_key == "enwiki:2032"]
    assert {row.creator_id for row in same_source} == {CREATOR_B, CREATOR_C}
    assert all(row.creator_id != CREATOR_A for row in result)


def test_pgvector_query_joins_created_babels_not_catalog_candidates() -> None:
    lowered = " ".join(PGVECTOR_CREATED_BABEL_QUERY.casefold().split())
    assert "join experiment_babels" in lowered
    assert "run_embedding_states" not in lowered
    assert "eb.serving_model_id = %(model_id)s" in lowered
    assert "eb.materialized_model_version = %(model_version)s" in lowered
    assert "eb.embedding_space_id = %(embedding_space_id)s" in lowered
    assert "any(%(babel_ids)s::uuid[])" not in lowered
    assert "catalog_embeddings" not in lowered
    assert "eb.creator_id <>" in lowered


def test_pgvector_adapter_applies_cosine_hnsw_settings() -> None:
    captured: dict[str, object] = {}

    def query_rows(settings, sql, parameters):
        captured.update(settings=settings, sql=sql, parameters=parameters)
        return [
            {
                "babel_id": "00000000-0000-5000-8000-000000000202",
                "creator_id": str(CREATOR_B),
                "source_article_key": "enwiki:2032",
                "score": 0.5,
            }
        ]

    index = PgvectorCandidateIndex(query_rows)
    eligible = [record(202, CREATOR_B, "enwiki:2032", 0)]
    index.activate(state(), eligible)
    result = index.search(
        np.asarray(vector(0), dtype=np.float32),
        run_id=RUN,
        state=state(),
        exclude_creator_id=CREATOR_A,
        k=3,
    )

    assert tuple(captured["settings"]) == PGVECTOR_TRANSACTION_SETTINGS
    assert captured["sql"] == PGVECTOR_CREATED_BABEL_QUERY
    assert "babel_ids" not in captured["parameters"]
    assert result[0].creator_id == CREATOR_B


def test_pgvector_activation_rejects_superseded_run_snapshot() -> None:
    captured = []

    def query_rows(_settings, _sql, parameters):
        captured.append(
            (parameters["model_version"], parameters["snapshot_sha256"])
        )
        return []

    old_state = state()
    new_state = MaterializedServingState(
        run_id=RUN,
        model_id=MODEL,
        model_version=1,
        embedding_space_id=SPACE,
        pgvector_snapshot_sha256="c" * 64,
        backend_snapshot_sha256="c" * 64,
    )
    old_record = record(201, CREATOR_A, "enwiki:593", 0)
    new_record = record(202, CREATOR_B, "enwiki:2032", 0)
    index = PgvectorCandidateIndex(query_rows)
    index.activate(old_state, [old_record])
    index.activate(new_state, [old_record, new_record])

    with pytest.raises(ValueError, match="does not match"):
        index.search(
            np.asarray(vector(0), dtype=np.float32),
            run_id=RUN,
            state=old_state,
            exclude_creator_id=CREATOR_C,
            k=3,
        )
    index.search(
        np.asarray(vector(0), dtype=np.float32),
        run_id=RUN,
        state=new_state,
        exclude_creator_id=CREATOR_C,
        k=3,
    )

    assert captured == [(1, "c" * 64)]
