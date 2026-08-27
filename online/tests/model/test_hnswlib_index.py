from __future__ import annotations

from uuid import UUID

import numpy as np

from babel_online.model.candidate_index import MaterializedServingState
from babel_online.model.hnswlib_index import HnswlibCandidateIndex
from babel_online.observable import CreatedBabel, VectorRecord


RUN = UUID("00000000-0000-5000-8000-000000000001")
MODEL = UUID("00000000-0000-5000-8000-000000000002")
SPACE = UUID("00000000-0000-5000-8000-000000000003")
CREATOR_A = UUID("00000000-0000-5000-8000-000000000101")
CREATOR_B = UUID("00000000-0000-5000-8000-000000000102")


class FakeHnswIndex:
    def __init__(self, space: str, dim: int) -> None:
        assert (space, dim) == ("cosine", 100)
        self.query_ks: list[int] = []

    def init_index(self, *, max_elements: int, ef_construction: int, M: int) -> None:
        self.max_elements = max_elements

    def add_items(self, vectors, labels) -> None:
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.labels = np.asarray(labels)

    def set_ef(self, value: int) -> None:
        self.ef = value

    def knn_query(self, query, k: int):
        self.query_ks.append(k)
        scores = self.vectors @ np.asarray(query[0], dtype=np.float32)
        order = np.argsort(-scores, kind="stable")[:k]
        return self.labels[order][None, :], (1.0 - scores[order])[None, :]


def record(number: int, creator: UUID, axis: int) -> VectorRecord:
    vector = np.zeros(100, dtype=np.float32)
    vector[axis] = 1
    return VectorRecord(
        babel=CreatedBabel(
            babelId=UUID(f"00000000-0000-5000-8000-{number:012d}"),
            runId=RUN,
            creatorId=creator,
            sourceArticleKey=f"enwiki:{number}",
            title=f"Babel {number}",
            text="text",
            createdAtNs=number,
        ),
        catalogContentHash="a" * 64,
        embeddingSpaceId=SPACE,
        servingModelId=MODEL,
        materializedModelVersion=0,
        vector=tuple(float(value) for value in vector),
    )


def test_optional_hnswlib_adapter_uses_same_records_and_filters_creator() -> None:
    records = [
        record(201, CREATOR_A, 0),
        record(202, CREATOR_B, 0),
        record(203, CREATOR_B, 1),
    ]
    state = MaterializedServingState(
        run_id=RUN,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="c" * 64,
    )
    index = HnswlibCandidateIndex(index_factory=FakeHnswIndex)
    index.activate(state, records)

    query = np.zeros(100, dtype=np.float32)
    query[0] = 1
    result = index.search(
        query,
        run_id=RUN,
        state=state,
        exclude_creator_id=CREATOR_A,
        k=2,
    )

    assert index.backend == "hnswlib"
    assert [row.babel_id for row in result] == [
        records[1].babel.babelId,
        records[2].babel.babelId,
    ]
    assert index.ordered_vector_sha256


def test_recall_audit_reports_recall_10_and_50() -> None:
    exact = [str(index) for index in range(60)]
    approximate = exact[:9] + ["missing"] + exact[10:49] + ["other"]
    audit = HnswlibCandidateIndex.audit_recall(exact, approximate)
    assert audit.recallAt10 == 0.9
    assert audit.recallAt50 == 0.96


def test_normal_top_10_search_uses_bounded_overfetch_not_the_full_corpus() -> None:
    records = [record(1_000 + number, CREATOR_B, number % 100) for number in range(60)]
    state = MaterializedServingState(
        run_id=RUN,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="c" * 64,
    )
    fake = FakeHnswIndex("cosine", 100)
    index = HnswlibCandidateIndex(index_factory=lambda **_: fake)
    index.activate(state, records)

    query = np.zeros(100, dtype=np.float32)
    query[0] = 1
    result = index.search(
        query,
        run_id=RUN,
        state=state,
        exclude_creator_id=CREATOR_A,
        k=10,
    )

    assert len(result) == 10
    assert fake.query_ks == [20]
    assert fake.ef == 100


def test_search_grows_overfetch_only_when_creator_filter_removes_too_many() -> None:
    records = [
        *[record(2_000 + number, CREATOR_A, 0) for number in range(15)],
        *[record(3_000 + number, CREATOR_B, 1) for number in range(45)],
    ]
    state = MaterializedServingState(
        run_id=RUN,
        model_id=MODEL,
        model_version=0,
        embedding_space_id=SPACE,
        pgvector_snapshot_sha256="b" * 64,
        backend_snapshot_sha256="c" * 64,
    )
    fake = FakeHnswIndex("cosine", 100)
    index = HnswlibCandidateIndex(index_factory=lambda **_: fake)
    index.activate(state, records)

    query = np.zeros(100, dtype=np.float32)
    query[0] = 1
    result = index.search(
        query,
        run_id=RUN,
        state=state,
        exclude_creator_id=CREATOR_A,
        k=10,
    )

    assert len(result) == 10
    assert fake.query_ks == [20, 40]
