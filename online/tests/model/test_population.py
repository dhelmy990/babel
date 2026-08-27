from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest

from babel_online.model.population import (
    PopulationActivationEvidence,
    PopulationIdentity,
    PopulationIntegrityError,
    PopulationSource,
    populate_created_babel_vectors,
)
from babel_online.model.qwen_encoder import Qwen100Encoder
from babel_online.observable import CreatedBabel, VectorRecord


RUN = UUID("00000000-0000-5000-8000-000000000001")
MODEL = UUID("00000000-0000-5000-8000-000000000002")
SPACE = UUID("00000000-0000-5000-8000-000000000003")


def source(number: int) -> PopulationSource:
    text = f"Lead text {number}"
    return PopulationSource(
        babel=CreatedBabel(
            babelId=UUID(f"00000000-0000-5000-8000-{number:012d}"),
            runId=RUN,
            creatorId=UUID(f"00000000-0000-5000-8001-{number:012d}"),
            sourceArticleKey=f"enwiki:{number + 1}",
            title=f"Article {number}",
            text=text,
            createdAtNs=number,
        ),
        catalog_content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def identity(*, artifact="a" * 64, dataset="b" * 40) -> PopulationIdentity:
    return PopulationIdentity(
        run_id=RUN,
        dataset_revision=dataset,
        model_id=MODEL,
        model_version=0,
        model_manifest_sha256="c" * 64,
        artifact_manifest_sha256=artifact,
        artifact_repo="dhelmy990/babel-qwen-navigation-2016-interview",
        artifact_revision="1" * 40,
        artifact_id="2" * 64,
        training_dataset_revision="3" * 40,
        embedding_space_id=SPACE,
        embedding_space_version="babel-qwen-100d-v1",
    )


class FakeQwen(Qwen100Encoder):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.contract = SimpleNamespace(
            artifactRepo="dhelmy990/babel-qwen-navigation-2016-interview",
            artifactRevision="1" * 40,
            artifactId="2" * 64,
            datasetRevision="3" * 40,
            embeddingDimension=100,
        )

    def encode(self, texts):
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            value = np.zeros(100, dtype="<f4")
            value[int(text.split("Lead text ")[-1]) % 100] = 1.0
            rows.append(value)
        return np.stack(rows)


class FakePopulationDatabase:
    def __init__(self, sources: list[PopulationSource]) -> None:
        self.sources = sorted(sources, key=lambda row: str(row.babel.babelId))
        self.vectors: dict[tuple[UUID, int], VectorRecord] = {}
        self.activations = []
        self.batch_sizes: list[int] = []
        self.explain_plan = [
            {
                "Plan": {
                    "Node Type": "Index Scan",
                    "Index Name": "babel_embeddings_cosine_hnsw",
                }
            }
        ]
        self.fail_activation_evidence = False

    def population_sources(self, run_id, *, after_babel_id, limit):
        assert run_id == RUN
        rows = [
            row
            for row in self.sources
            if after_babel_id is None
            or str(row.babel.babelId) > str(after_babel_id)
        ]
        return rows[:limit]

    def write_population_batch(self, records, expected):
        self.batch_sizes.append(len(records))
        for record in records:
            key = (record.babel.babelId, record.materializedModelVersion)
            previous = self.vectors.get(key)
            if previous is not None and previous != record:
                raise PopulationIntegrityError("existing vector bytes or identity differ")
            self.vectors[key] = record

    def population_vectors(self, expected, *, after_babel_id, limit):
        rows = [
            record for (_babel_id, version), record in self.vectors.items()
            if version == expected.model_version
            and (after_babel_id is None or str(record.babel.babelId) > str(after_babel_id))
        ]
        return sorted(rows, key=lambda row: str(row.babel.babelId))[:limit]

    def activate_population(self, expected, *, snapshot_sha256):
        if self.fail_activation_evidence:
            raise RuntimeError("EXPLAIN evidence failed before transaction commit")
        self.activations.append((expected, snapshot_sha256))
        return PopulationActivationEvidence(
            table_bytes=1234,
            index_bytes=5678,
            explain_plan=self.explain_plan,
        )


def run_population(tmp_path: Path, count: int, **kwargs):
    database = FakePopulationDatabase([source(number) for number in range(1, count + 1)])
    receipt = populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=128,
        **kwargs,
    )
    return database, receipt


@pytest.mark.parametrize(("count", "ready"), [(9_999, False), (10_000, True)])
def test_formal_readiness_requires_ten_thousand_exact_created_ids(tmp_path, count, ready) -> None:
    database, receipt = run_population(tmp_path, count)

    assert receipt.created_count == receipt.indexed_count == count
    assert receipt.failure_count == 0
    assert receipt.formal_ready is ready
    assert receipt.hnsw_used is True
    assert receipt.table_bytes == 1234
    assert receipt.index_bytes == 5678
    assert len(database.activations) == 1
    assert max(database.batch_sizes) <= 128
    explain_path = tmp_path / str(RUN) / "population/explain.json"
    assert explain_path.exists() is ready


def test_population_only_reads_finalized_created_babel_source_boundary(tmp_path) -> None:
    database, receipt = run_population(tmp_path, 3)

    assert receipt.created_count == 3
    assert {record.babel.babelId for record in database.vectors.values()} == {
        row.babel.babelId for row in database.sources
    }
    assert all(
        record.babel.sourceArticleKey.startswith("enwiki:")
        for record in database.vectors.values()
    )


def test_resume_rechecks_existing_vectors_and_continues_after_committed_id(tmp_path) -> None:
    sources = [source(number) for number in range(1, 6)]
    database = FakePopulationDatabase(sources)
    encoder = FakeQwen()
    first = populate_created_babel_vectors(
        database=database,
        encoder=encoder,
        identity=identity(),
        state_root=tmp_path,
        batch_size=2,
        stop_after_batches=1,
    )
    assert first.complete is False
    assert len(database.vectors) == 2

    second_encoder = FakeQwen()
    result = populate_created_babel_vectors(
        database=database,
        encoder=second_encoder,
        identity=identity(),
        state_root=tmp_path,
        batch_size=2,
    )

    assert result.complete is True
    assert len(database.vectors) == 5
    assert second_encoder.calls[0] == ["Article 1\n\nLead text 1", "Article 2\n\nLead text 2"]
    assert len(database.activations) == 1


def test_resume_rejects_dataset_model_artifact_or_created_manifest_change(tmp_path) -> None:
    database = FakePopulationDatabase([source(1), source(2)])
    populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=1,
        stop_after_batches=1,
    )

    with pytest.raises(PopulationIntegrityError, match="journal identity"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(artifact="d" * 64),
            state_root=tmp_path,
        )
    database.sources[1] = source(3)
    with pytest.raises(PopulationIntegrityError, match="created-content manifest"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(),
            state_root=tmp_path,
        )


def test_resume_rejects_existing_vector_byte_change(tmp_path) -> None:
    database = FakePopulationDatabase([source(1), source(2)])
    populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=1,
        stop_after_batches=1,
    )
    previous = next(iter(database.vectors.values()))
    bad = np.asarray(previous.vector, dtype=np.float32)
    bad[0], bad[1] = bad[1], bad[0]
    database.vectors[(previous.babel.babelId, 0)] = previous.model_copy(
        update={"vector": tuple(float(value) for value in bad)}
    )

    with pytest.raises(PopulationIntegrityError, match="existing vector"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(),
            state_root=tmp_path,
            batch_size=1,
        )
    assert database.activations == []


def test_activation_is_withheld_for_missing_extra_or_failed_rows(tmp_path) -> None:
    database = FakePopulationDatabase([source(1), source(2)])
    extra = source(3)
    vector = np.zeros(100, dtype=np.float32)
    vector[0] = 1
    database.vectors[(extra.babel.babelId, 0)] = VectorRecord(
        babel=extra.babel,
        catalogContentHash=extra.catalog_content_hash,
        embeddingSpaceId=SPACE,
        servingModelId=MODEL,
        materializedModelVersion=0,
        vector=tuple(float(item) for item in vector),
    )

    with pytest.raises(PopulationIntegrityError, match="created and indexed IDs"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(),
            state_root=tmp_path,
            batch_size=2,
        )
    assert database.activations == []


def test_database_batch_is_committed_before_atomic_journal_advances(tmp_path) -> None:
    database = FakePopulationDatabase([source(1)])
    populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=1,
    )

    journal = json.loads((tmp_path / str(RUN) / "population/journal.json").read_text())
    assert journal["last_committed_babel_id"] == str(source(1).babel.babelId)
    assert journal["committed_count"] == 1
    assert len(journal["committed_prefix_sha256"]) == 64
    assert journal["complete"] is True


def test_transient_failed_attempt_can_resume_cleanly_and_activate(tmp_path) -> None:
    database = FakePopulationDatabase([source(1), source(2)])

    class FailsOnce(FakeQwen):
        def encode(self, texts):
            raise RuntimeError("temporary encoder failure")

    with pytest.raises(RuntimeError, match="temporary"):
        populate_created_babel_vectors(
            database=database,
            encoder=FailsOnce(),
            identity=identity(),
            state_root=tmp_path,
            batch_size=1,
        )
    failed = json.loads(
        (tmp_path / str(RUN) / "population/journal.json").read_text()
    )
    assert failed["unresolved_failure_count"] == 1

    receipt = populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=1,
    )

    assert receipt.complete is True
    assert receipt.failure_count == 0
    assert len(database.activations) == 1
    recovered = json.loads(
        (tmp_path / str(RUN) / "population/journal.json").read_text()
    )
    assert recovered["failure_attempt_count"] == 1
    assert recovered["unresolved_failure_count"] == 0


def test_formal_threshold_cannot_be_lowered_below_ten_thousand(tmp_path) -> None:
    database = FakePopulationDatabase([source(1)])

    with pytest.raises(ValueError, match="10,000"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(),
            state_root=tmp_path,
            formal_minimum=1,
        )
    assert database.activations == []


def test_evidence_failure_rolls_back_activation_and_clean_resume_commits(tmp_path) -> None:
    database = FakePopulationDatabase([source(1), source(2)])
    database.fail_activation_evidence = True

    with pytest.raises(RuntimeError, match="EXPLAIN evidence"):
        populate_created_babel_vectors(
            database=database,
            encoder=FakeQwen(),
            identity=identity(),
            state_root=tmp_path,
            batch_size=1,
        )
    assert database.activations == []

    database.fail_activation_evidence = False
    receipt = populate_created_babel_vectors(
        database=database,
        encoder=FakeQwen(),
        identity=identity(),
        state_root=tmp_path,
        batch_size=1,
    )

    assert receipt.complete is True
    assert receipt.failure_count == 0
    assert len(database.activations) == 1
