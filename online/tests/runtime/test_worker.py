from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4
from pathlib import Path
import json

import pytest

from babel_online.feedback import InMemoryFeedbackBus
from babel_online.runtime.worker import RunScopedConsumer, WorkerManager, isolate_new_run_offsets
from babel_online.runtime.worker import FridayDemoRuntime
from babel_online.observable import CreatedBabel
from babel_online.runtime.dataset_bundle import (
    DEMO_DATASET_CONFIG,
    DEMO_DATASET_REPOSITORY,
    DEMO_DATASET_REVISION,
)
from babel_online.config import default_run_config
from babel_online.contracts import FeedbackEventV1
from babel_online.contracts import ModelManifestV1
from babel_online.model.candidate_index import MaterializedServingState
from babel_online.model.item_tower import QwenItemTower
from babel_online.model.pgvector_index import PgvectorCandidateIndex
from babel_online.training.consumer import SyncTrainingState
import numpy as np
import threading


ROOT = Path(__file__).resolve().parents[3]


def bundle_identity(**updates):
    values = {
        "dataset_repository": DEMO_DATASET_REPOSITORY,
        "dataset_config": DEMO_DATASET_CONFIG,
        "dataset_revision": DEMO_DATASET_REVISION,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def event_with_three_actions():
    return FeedbackEventV1.model_validate_json(
        (ROOT / "fixtures/online/tiny/observable/feedback-event.json").read_text()
    )


def test_new_run_starts_at_current_high_watermark_and_rejects_cross_run_feedback() -> None:
    bus = InMemoryFeedbackBus()
    old = event_with_three_actions()
    bus.publish(key=str(old.creatorId), event=old)
    consumer = bus.consumer(group_id=f"trainer.{uuid4()}", auto_commit=False)

    start = isolate_new_run_offsets(consumer)

    assert next(iter(start.values())) == 1
    target = old.model_copy(update={"runId": uuid4(), "eventId": uuid4()})
    bus.publish(key=str(target.creatorId), event=target)
    scoped = RunScopedConsumer(consumer, run_id=target.runId)
    assert scoped.poll().event.runId == target.runId
    wrong = target.model_copy(update={"runId": uuid4(), "eventId": uuid4()})
    bus.publish(key=str(wrong.creatorId), event=wrong)
    with pytest.raises(RuntimeError, match="cross-run"):
        scoped.poll()


def test_worker_refuses_a_partial_catalog_pin_before_starting_thread() -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision="e1acc648fcace8820dd5ee70bae9216ea4334555",
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    ).model_copy(update={"datasetConfig": "demo_catalog_2026_06"})

    class Database:
        def load_run(self, requested_run_id):
            assert requested_run_id == run_id
            return SimpleNamespace(config=config, status="starting")

    manager = WorkerManager(
        database=Database(),
        dataset_bundle=bundle_identity(),
        runtime_factory=lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="identity"):
        manager.start(run_id)


@pytest.mark.parametrize(
    "bundle_update",
    [
        {"dataset_repository": "another/repository"},
        {"dataset_revision": "0" * 40},
    ],
)
def test_worker_rejects_loaded_bundle_identity_mismatch_before_claim(bundle_update) -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision=DEMO_DATASET_REVISION,
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    )

    class Database:
        claimed = False

        def load_run(self, _run_id):
            return SimpleNamespace(config=config, status="starting")

        def claim_run(self, _run_id):
            self.claimed = True

    database = Database()
    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle_identity(**bundle_update),
        runtime_factory=lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="identity"):
        manager.start(run_id)
    assert database.claimed is False


def test_new_babel_materialization_inserts_only_new_row_at_last_sync_version() -> None:
    run_id = uuid4()
    creator_id = uuid4()
    old = CreatedBabel(
        babelId=uuid4(), runId=run_id, creatorId=creator_id,
        sourceArticleKey="enwiki:1", title="Old", text="Old lead", createdAtNs=1,
    )
    new = CreatedBabel(
        babelId=uuid4(), runId=run_id, creatorId=creator_id,
        sourceArticleKey="enwiki:2", title="New", text="New lead", createdAtNs=2,
    )
    inserted = []
    activated = []
    applied = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime._serving_version = 3
    runtime.trainer = SimpleNamespace(training_version=9)
    runtime.config = SimpleNamespace(runId=run_id)
    runtime.starting_model = SimpleNamespace(
        modelId=UUID("00000000-0000-5000-8000-000000000002"),
        embeddingSpace=SimpleNamespace(
            embeddingSpaceId=UUID("00000000-0000-5000-8000-000000000003")
        ),
    )
    runtime._created = [old, new]
    runtime._records = []
    runtime._content_hashes = {old.babelId: "a" * 64, new.babelId: "b" * 64}
    runtime._serving_vectors = {
        old.babelId: np.eye(100, dtype=np.float32)[0],
        new.babelId: np.eye(100, dtype=np.float32)[1],
    }
    runtime.database = SimpleNamespace(
        insert_vectors=lambda records: inserted.append(list(records)),
        activate_embedding_state=lambda **values: activated.append(values),
    )
    runtime.serving = SimpleNamespace(
        apply_sync=lambda **values: applied.append(values)
    )
    runtime.index = object()

    runtime._materialize_new_babel()

    assert runtime.trainer.training_version == 9
    assert runtime._serving_version == 3
    assert [[record.babel.babelId for record in batch] for batch in inserted] == [[new.babelId]]
    assert [record.babel.babelId for record in runtime._records] == [new.babelId]
    assert activated[0]["model_version"] == 3
    assert [record.babel.babelId for record in applied[0]["vector_records"]] == [new.babelId]


def test_worker_sync_uses_one_locked_training_capture_for_version_vectors_and_state() -> None:
    babel_id = uuid4()
    vector = np.eye(100, dtype=np.float32)[4]
    captured = SyncTrainingState(
        version=11,
        materialized_vectors={babel_id: vector},
        model_state={"captured": 11},
    )
    runtime = object.__new__(FridayDemoRuntime)
    runtime.trainer = SimpleNamespace(capture_sync_state=lambda: captured)
    runtime._created = [SimpleNamespace(babelId=babel_id)]

    version, vectors, model_state = runtime._capture_training_sync()

    assert version == 11
    np.testing.assert_array_equal(vectors[babel_id], vector)
    assert model_state == {"captured": 11}


def test_v2_runtime_requires_qwen_and_selects_real_monthly_bundle(
    real_model_manifest, accepted_qwen_factory
) -> None:
    run_id = uuid4()
    model = real_model_manifest
    bundle = SimpleNamespace(
        configs={
            "catalog_2026_06": ({"article_key": "enwiki:1"},),
            "catalog_2026_07": ({"article_key": "enwiki:2"},),
            "simulator_2026_06_hidden": (
                {
                    "record_type": "pagelink",
                    "payload_json": json.dumps(
                        {
                            "source_article_key": "enwiki:1",
                            "target_article_key": "enwiki:2",
                        }
                    ),
                },
            ),
            "simulator_2026_07_hidden": (),
            "crosswalk_2026_06_07": (),
        }
    )
    arguments = dict(
        config=SimpleNamespace(runId=run_id),
        database=SimpleNamespace(),
        bundle=bundle,
        model_lineage=[SimpleNamespace(manifest=model)],
        kafka_bootstrap_servers="unused",
        recommendation_port=8791,
        stop_event=threading.Event(),
    )

    with pytest.raises(ValueError, match="Qwen100Encoder"):
        FridayDemoRuntime(**arguments)
    wrong_encoder = accepted_qwen_factory()
    wrong_encoder.contract = wrong_encoder.contract.model_copy(
        update={"artifactRevision": "0" * 40}
    )
    with pytest.raises(ValueError, match="identity"):
        FridayDemoRuntime(**arguments, qwen_encoder=wrong_encoder)
    encoder = accepted_qwen_factory()
    runtime = FridayDemoRuntime(**arguments, qwen_encoder=encoder)

    assert runtime.scale_run is True
    assert runtime._catalogs() == {
        "2026-06": [{"article_key": "enwiki:1"}],
        "2026-07": [{"article_key": "enwiki:2"}],
    }
    assert runtime._hidden_edges()["2026-06"] == {("enwiki:1", "enwiki:2")}


def test_v2_plan_vectors_and_serving_use_same_injected_qwen(
    real_model_manifest, accepted_qwen_factory
) -> None:
    run_id = uuid4()
    model = real_model_manifest
    encoder = accepted_qwen_factory()
    runtime = FridayDemoRuntime(
        config=SimpleNamespace(runId=run_id),
        database=SimpleNamespace(),
        bundle=SimpleNamespace(configs={}),
        model_lineage=[SimpleNamespace(manifest=model)],
        kafka_bootstrap_servers="unused",
        recommendation_port=8791,
        stop_event=threading.Event(),
        qwen_encoder=encoder,
    )
    babel_id = uuid4()
    article = {
        "canonical_title": "Real article",
        "lead_text": "Real prepared lead.",
        "article_text": "Real prepared lead.\n\nFirst section.",
    }

    frozen = runtime._encode_plan_vectors(
        [("2026-06", uuid4(), article, babel_id)], batch_size=1
    )

    expected = encoder.encode(["Real article\n\nReal prepared lead."])[0]
    np.testing.assert_array_equal(frozen[babel_id], expected)
    assert encoder.calls == 2
    runtime.registry = __import__(
        "babel_online.model.registry", fromlist=["ModelRegistry"]
    ).ModelRegistry()
    runtime.registry.register_real_original(model)
    runtime.index = PgvectorCandidateIndex(lambda *_args: [])
    state = MaterializedServingState(
        run_id=run_id,
        model_id=model.modelId,
        model_version=0,
        embedding_space_id=model.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="a" * 64,
    )

    serving = runtime._create_serving_state(state, [])

    assert isinstance(serving.snapshot().item_tower, QwenItemTower)
    assert serving.snapshot().item_tower.encoder is encoder
    babel = CreatedBabel(
        babelId=babel_id,
        runId=run_id,
        creatorId=uuid4(),
        sourceArticleKey="enwiki:42",
        title="Real article",
        text="Real prepared lead.",
        createdAtNs=1,
    )
    inserted = []
    activated = []
    runtime.database = SimpleNamespace(
        insert_vectors=lambda records: inserted.extend(records),
        activate_embedding_state=lambda **values: activated.append(values),
    )
    runtime.serving = serving
    runtime._created = [babel]
    runtime._content_hashes = {babel_id: "d" * 64}
    runtime._serving_vectors = {babel_id: frozen[babel_id]}
    runtime._records = []
    runtime._serving_version = 0

    runtime._materialize_new_babel()

    assert len(inserted) == 1
    assert np.asarray(inserted[0].vector, dtype="<f4").tobytes() == expected.astype(
        "<f4"
    ).tobytes()
    assert activated[0]["model_id"] == model.modelId


def test_v1_runtime_remains_fixture_smoke_only() -> None:
    fixture = ModelManifestV1.model_validate_json(
        (ROOT / "fixtures/online/tiny/original-model.json").read_text()
    )
    runtime = FridayDemoRuntime(
        config=SimpleNamespace(runId=uuid4()),
        database=SimpleNamespace(),
        bundle=SimpleNamespace(
            configs={
                "demo_catalog_2026_06": (),
                "demo_catalog_2026_07": (),
            }
        ),
        model_lineage=[SimpleNamespace(manifest=fixture)],
        kafka_bootstrap_servers="unused",
        recommendation_port=8791,
        stop_event=threading.Event(),
    )

    assert runtime.scale_run is False
    assert runtime.qwen_encoder is None
    assert runtime._catalogs() == {"2026-06": [], "2026-07": []}


def test_same_run_seed_reproduces_sources_and_decision_draws_across_run_ids() -> None:
    def runtime(run_id, seed):
        value = object.__new__(FridayDemoRuntime)
        value.config = default_run_config(
            run_id=run_id,
            dataset_revision="e1acc648fcace8820dd5ee70bae9216ea4334555",
            starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
            creator_count=2,
        ).model_copy(
            update={
                "runSeed": seed,
                "environmentSequence": ["2026-06"],
                "perMonthEventBudget": {"2026-06": 4},
            }
        )
        value.bundle = SimpleNamespace(
            configs={
                "demo_catalog_2026_06": tuple(
                    {"article_key": f"enwiki:{number}"} for number in range(1, 9)
                ),
                "demo_catalog_2026_07": (),
            }
        )
        return value

    first = runtime(uuid4(), 73)
    second = runtime(uuid4(), 73)
    different = runtime(uuid4(), 99)
    first_plan = first._plan()
    second_plan = second._plan()

    assert [row[2]["article_key"] for row in first_plan] == [
        row[2]["article_key"] for row in second_plan
    ]
    assert [row[2]["article_key"] for row in first_plan] != [
        row[2]["article_key"] for row in different._plan()
    ]
    assert [row[1] for row in first_plan] != [row[1] for row in second_plan]
    assert [row[3] for row in first_plan] != [row[3] for row in second_plan]
    assert first._simulation_draw("action", "2026-06", 2, 0, "enwiki:4", 1) == (
        second._simulation_draw("action", "2026-06", 2, 0, "enwiki:4", 1)
    )


def test_start_claims_run_before_return_so_immediate_stop_finishes_gracefully() -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision=DEMO_DATASET_REVISION,
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    )
    release = threading.Event()
    finished = threading.Event()
    observed_stop = []

    class Database:
        claimed = False
        transitions = []

        def load_run(self, _run_id):
            return SimpleNamespace(config=config, status="starting")

        def claim_run(self, _run_id):
            self.claimed = True

        def transition(self, _run_id, status, **_values):
            self.transitions.append(status)

        def append_activity(self, _activity):
            pass

    class Runtime:
        def __init__(self, stop_event):
            self.stop_event = stop_event

        def request_stop(self):
            self.stop_event.set()

        def stop_serving(self):
            pass

        def run(self):
            assert release.wait(1.0)
            observed_stop.append(self.stop_event.is_set())
            finished.set()

    database = Database()
    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle_identity(),
        runtime_factory=lambda _config, stop_event: Runtime(stop_event),
    )

    manager.start(run_id)
    assert database.claimed is True
    manager.request_stop(run_id)
    release.set()

    assert finished.wait(1.0)
    assert observed_stop == [True]
    assert "failed" not in database.transitions
