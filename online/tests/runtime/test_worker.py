from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4
from pathlib import Path
import json

import pytest
import babel_online.runtime.worker as worker_module

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
from babel_online.contracts import FeedbackEventV1, FeedbackEventV2
from babel_online.contracts import ModelManifestV1
from babel_online.model.candidate_index import MaterializedServingState
from babel_online.model.item_tower import QwenItemTower
from babel_online.model.pgvector_index import PgvectorCandidateIndex
from babel_online.training.consumer import SyncTrainingState
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule
from babel_online.simulation.walk import WalkNode
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
    assert scoped.position() == consumer.position()
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


def test_same_process_worker_exposes_run_scoped_placement_and_status(tmp_path) -> None:
    run_id = uuid4()
    config = default_run_config(
        run_id=run_id,
        dataset_revision=DEMO_DATASET_REVISION,
        starting_model_id=UUID("00000000-0000-5000-8000-000000000002"),
    )
    running = threading.Event()
    stop = threading.Event()
    placements = []

    class Database:
        def load_run(self, _run_id):
            return SimpleNamespace(config=config, status="starting")

        def claim_run(self, _run_id):
            pass

        def transition(self, *_args, **_kwargs):
            pass

        def append_activity(self, *_args, **_kwargs):
            pass

    class Runtime:
        def run(self):
            running.set()
            stop.wait(2)

        def request_stop(self):
            stop.set()

        def stop_serving(self):
            stop.set()

    placement = SimpleNamespace(
        actualTopology="same_process", path=tmp_path / str(run_id) / "placement.json"
    )
    manager = WorkerManager(
        database=Database(),
        dataset_bundle=bundle_identity(),
        runtime_factory=lambda *_args: Runtime(),
        placement_factory=lambda requested: (placements.append(requested), placement)[1],
    )

    manager.start(run_id)
    assert running.wait(1)
    assert manager.placement is placement
    assert manager.status == {
        "runId": str(run_id), "phase": "running", "failure": None
    }
    assert placements == [run_id]
    manager.request_stop(run_id)


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


def test_scale_worker_exports_v2_child_descriptor_not_fixture_manifest(
    tmp_path, real_model_manifest
) -> None:
    run_id = uuid4()
    babel = CreatedBabel(
        babelId=uuid4(), runId=run_id, creatorId=uuid4(),
        sourceArticleKey="enwiki:12", title="Twelve", text="Twelve lead", createdAtNs=1,
    )
    vector = np.eye(100, dtype=np.float32)[2]
    runtime = object.__new__(FridayDemoRuntime)
    runtime.scale_run = True
    runtime.starting_model = real_model_manifest
    runtime.config = SimpleNamespace(runId=run_id, artifactRoot=str(tmp_path))
    runtime.model = SimpleNamespace(
        state_dict=lambda: {
            "learningRate": 0.1,
            "transferState": {"queryVector": vector.tolist()},
            "residuals": {str(babel.babelId): [0.0] * 100},
        }
    )
    runtime.trainer = SimpleNamespace(processed_events=7)
    runtime._records = [SimpleNamespace(
        babel=babel,
        catalogContentHash="d" * 64,
    )]
    runtime._serving_vectors = {babel.babelId: vector}
    runtime.registry = __import__(
        "babel_online.model.registry", fromlist=["ModelRegistry"]
    ).ModelRegistry()
    runtime.registry.register_real_original(real_model_manifest)
    registered = []
    runtime.database = SimpleNamespace(
        register_real_child=lambda descriptor, path: registered.append((descriptor, path))
    )

    child, child_records = runtime._export_child_model(version=4)

    assert child.schemaVersion == 2
    assert child.parentModelId == real_model_manifest.modelId
    assert child.embeddingSpace == real_model_manifest.embeddingSpace
    assert all(record.servingModelId == child.modelId for record in child_records)
    assert all(record.materializedModelVersion == 4 for record in child_records)
    assert registered[0][0].childManifest == child


def test_scale_worker_accepts_original_to_v2_child_lineage_and_loads_child_state(
    tmp_path, real_model_manifest
) -> None:
    run_id = uuid4()
    child_document = real_model_manifest.model_dump(mode="json")
    child_document.update(
        modelId=uuid4(),
        parentModelId=real_model_manifest.modelId,
        producingRunId=run_id,
        label="selected immutable child",
    )
    child = type(real_model_manifest).model_validate(child_document)
    state_path = tmp_path / "online-state.json"
    state_path.write_text(json.dumps({
        "transferState": {"queryVector": [1.0] + [0.0] * 99}
    }))
    runtime = object.__new__(FridayDemoRuntime)
    runtime.scale_run = True
    runtime.model_lineage = [
        SimpleNamespace(manifest=real_model_manifest, online_state_path=None),
        SimpleNamespace(manifest=child, online_state_path=state_path),
    ]
    runtime.starting_artifact = runtime.model_lineage[-1]
    runtime.starting_model = child

    registry = runtime._build_model_registry()
    state = runtime._load_starting_online_state()

    assert registry.original == real_model_manifest
    assert registry.select_for_scale(child.modelId) == child
    assert state["transferState"]["queryVector"][0] == 1.0


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
    from babel_online.training.torch_working import TorchOnlineRecommender

    runtime.model = TorchOnlineRecommender({babel_id: expected})
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
    assert serving.snapshot().context_tower is not runtime.model
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


def test_v2_worker_freezes_period_source_root_and_creator_order_before_execution() -> None:
    run_id = uuid4()
    first_creator, second_creator = uuid4(), uuid4()
    captured = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runId=run_id)
    runtime.database = SimpleNamespace(
        load_work_schedule=lambda _run_id: (),
        persist_work_schedule=lambda rows: captured.append(tuple(rows))
    )
    plan = [
        ("2026-06", first_creator, {"article_key": "enwiki:1"}, uuid4()),
        ("2026-06", second_creator, {"article_key": "enwiki:2"}, uuid4()),
        ("2026-07", first_creator, {"article_key": "enwiki:3"}, uuid4()),
    ]

    schedule = runtime._persist_scaled_schedule(plan)

    assert captured == [schedule]
    assert [row.schedule_index for row in schedule] == [0, 1, 2]
    assert [
        row.creator_event_number for row in schedule if row.creator_id == first_creator
    ] == [0, 1]
    assert [row.period for row in schedule] == ["2026-06", "2026-06", "2026-07"]
    assert [row.source_article_key for row in schedule] == [
        "enwiki:1", "enwiki:2", "enwiki:3"
    ]
    assert [row.root_babel_id for row in schedule] == [row[3] for row in plan]


def test_v2_worker_reuses_and_validates_the_persisted_schedule() -> None:
    run_id = uuid4()
    creator_id = uuid4()
    plan = [
        ("2026-06", creator_id, {"article_key": "enwiki:1"}, uuid4()),
        ("2026-07", creator_id, {"article_key": "enwiki:2"}, uuid4()),
    ]
    expected = deterministic_schedule(
        run_id,
        [
            ScheduledWork(
                creator_id=creator_id,
                creator_event_number=index,
                period=period,
                source_article_key=article["article_key"],
                root_babel_id=babel_id,
            )
            for index, (period, _creator, article, babel_id) in enumerate(plan)
        ],
    )
    persisted = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runId=run_id)
    runtime.database = SimpleNamespace(
        load_work_schedule=lambda _run_id: expected,
        persist_work_schedule=lambda rows: persisted.append(tuple(rows)),
    )

    assert runtime._persist_scaled_schedule(plan) == expected
    assert persisted == []

    runtime.database.load_work_schedule = lambda _run_id: (
        expected[0].__class__(
            **{
                field: getattr(expected[0], field)
                for field in expected[0].__dataclass_fields__
                if field != "source_article_key"
            },
            source_article_key="enwiki:999",
        ),
        expected[1],
    )
    with pytest.raises(RuntimeError, match="persisted work schedule"):
        runtime._persist_scaled_schedule(plan)


def test_failed_v2_start_roll_returns_unpublished_root_without_a_request() -> None:
    run_id = uuid4()
    creator_id = uuid4()
    babel_id = uuid4()
    scheduled = __import__(
        "babel_online.simulation.scheduler", fromlist=["deterministic_schedule", "ScheduledWork"]
    ).deterministic_schedule(
        run_id,
        [__import__(
            "babel_online.simulation.scheduler", fromlist=["ScheduledWork"]
        ).ScheduledWork(
            creator_id=creator_id,
            creator_event_number=0,
            period="2026-06",
            source_article_key="enwiki:1",
            root_babel_id=babel_id,
        )],
    )[0]
    persisted_rolls = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(
        runId=run_id,
        recommendationStartProbability=0.0,
        continuationProbability=0.4,
        maximumTraversalDepth=2,
        maximumRequestsPerTraversal=10,
        runSeed=7,
        recommendationK=10,
    )
    runtime.stop_event = threading.Event()
    runtime._simulation_lock = threading.RLock()
    runtime._created = []
    runtime.database = SimpleNamespace(
        stop_requested=lambda _run_id: False,
        stage_babel=lambda **_values: None,
        persist_traversal_rolls=lambda *values: persisted_rolls.append(values),
        update_metrics=lambda *_args, **_values: None,
    )
    runtime.recommendation_endpoint = "http://127.0.0.1:8791"

    completed = runtime._execute_scaled_session(
        scheduled,
        {
            "canonical_title": "Root",
            "lead_text": "Lead",
            "article_text": "Lead",
            "content_hash": "a" * 64,
        },
        {"2026-06": set(), "2026-07": set()},
    )

    assert completed.babel.babelId == babel_id
    assert completed.root_request_id is None
    assert persisted_rolls[0][:2] == (run_id, scheduled.traversal_session_id)
    assert len(persisted_rolls[0][2]) == 1
    assert persisted_rolls[0][2][0].outcome == "start_skipped"


def test_scaled_root_batch_hashes_activates_and_rebuilds_once(monkeypatch) -> None:
    run_id = uuid4()
    creators = [uuid4(), uuid4()]
    schedule = deterministic_schedule(
        run_id,
        [
            ScheduledWork(
                creator_id=creator,
                creator_event_number=0,
                period="2026-06",
                source_article_key=f"enwiki:{index + 1}",
                root_babel_id=uuid4(),
            )
            for index, creator in enumerate(creators)
        ],
    )
    articles = [
        {"content_hash": character * 64}
        for character in ("a", "b")
    ]
    completions = [
        worker_module._CompletedScaledRoot(
            scheduled=row,
            babel=CreatedBabel(
                babelId=row.root_babel_id,
                runId=run_id,
                creatorId=row.creator_id,
                sourceArticleKey=row.source_article_key,
                title=f"Root {row.schedule_index}",
                text="Lead",
                createdAtNs=row.schedule_index + 1,
            ),
            article=articles[row.schedule_index],
            root_request_id=None,
        )
        for row in schedule
    ]
    inserted, activated, applied, finalized, hashed = [], [], [], [], []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runId=run_id)
    runtime.starting_model = SimpleNamespace(
        modelId=uuid4(),
        embeddingSpace=SimpleNamespace(embeddingSpaceId=uuid4()),
    )
    runtime._serving_version = 0
    runtime._simulation_lock = threading.RLock()
    runtime._created = []
    runtime._records = []
    runtime._babel_by_id = {}
    runtime._content_hashes = {}
    runtime._histories = {}
    runtime._serving_vectors = {}
    runtime._frozen_vectors = {
        row.root_babel_id: np.eye(100, dtype=np.float32)[index]
        for index, row in enumerate(schedule)
    }
    runtime.database = SimpleNamespace(
        finalize_babel=lambda *values: finalized.append(values),
        insert_vectors=lambda records: inserted.append(tuple(records)),
        activate_embedding_state=lambda **values: activated.append(values),
        update_metrics=lambda *_args, **_values: None,
    )
    runtime.serving = SimpleNamespace(
        apply_sync=lambda **values: applied.append(values)
    )
    runtime.index = object()
    monkeypatch.setattr(
        worker_module,
        "_snapshot_sha",
        lambda records: hashed.append(tuple(records)) or "c" * 64,
    )

    runtime._publish_scaled_root_batch(tuple(reversed(completions)))

    assert [babel.babelId for babel in runtime._created] == [
        row.root_babel_id for row in schedule
    ]
    assert len(finalized) == 2
    assert len(inserted) == len(activated) == len(applied) == len(hashed) == 1
    assert len(inserted[0]) == 2


def test_interleaved_wave_hides_completed_roots_until_boundary() -> None:
    run_id = uuid4()
    plan = [
        ("2026-06", uuid4(), {
            "article_key": f"enwiki:{index + 1}",
            "canonical_title": f"Root {index}",
            "lead_text": "Lead",
            "article_text": "Lead",
            "content_hash": "a" * 64,
        }, uuid4())
        for index in range(4)
    ]
    visible: set[UUID] = set()
    observed: dict[int, set[UUID]] = {}
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(
        runId=run_id,
        concurrentUsers=2,
        interleaveCreationAndRecommendations=True,
    )
    runtime.database = SimpleNamespace(
        load_work_schedule=lambda _run_id: (),
        persist_work_schedule=lambda _rows: None,
    )

    def execute(row, article, _hidden, **_kwargs):
        observed[row.schedule_index] = set(visible)
        return worker_module._CompletedScaledRoot(
            scheduled=row,
            babel=CreatedBabel(
                babelId=row.root_babel_id,
                runId=run_id,
                creatorId=row.creator_id,
                sourceArticleKey=row.source_article_key,
                title=article["canonical_title"],
                text="Lead",
                createdAtNs=1,
            ),
            article=article,
            root_request_id=None,
        )

    def publish(completed):
        visible.update(value.babel.babelId for value in completed)

    runtime._execute_scaled_session = execute
    runtime._publish_scaled_root_batch = publish

    runtime._simulate_scaled(plan, {"2026-06": set(), "2026-07": set()})

    assert observed[0] == observed[1] == set()
    first_wave = {plan[0][3], plan[1][3]}
    assert observed[2] == observed[3] == first_wave


def test_scaled_decision_draw_identity_includes_session_source_and_depth() -> None:
    run_id = uuid4()
    creator_id = uuid4()
    first = deterministic_schedule(
        run_id,
        [ScheduledWork(
            creator_id=creator_id,
            creator_event_number=0,
            period="2026-06",
            source_article_key="enwiki:1",
            root_babel_id=uuid4(),
        )],
    )[0]
    second = first.__class__(
        **{
            field: getattr(first, field)
            for field in first.__dataclass_fields__
            if field != "traversal_session_id"
        },
        traversal_session_id=uuid4(),
    )
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runSeed=7, runId=run_id)
    candidate = SimpleNamespace(
        babelId=uuid4(), sourceArticleKey="enwiki:2", rank=1
    )
    source = WalkNode(first.root_babel_id, first.source_article_key)

    baseline = runtime._scaled_decision_draw("action", first, source, 0, candidate)

    assert baseline != runtime._scaled_decision_draw(
        "action", second, source, 0, candidate
    )
    assert baseline != runtime._scaled_decision_draw(
        "action", first, WalkNode(uuid4(), "enwiki:3"), 0, candidate
    )
    assert baseline != runtime._scaled_decision_draw(
        "action", first, source, 1, candidate
    )


def test_scaled_feedback_keeps_training_lag_loss_and_sync_path() -> None:
    run_id = uuid4()
    metrics = []
    activities = []
    synchronized = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runId=run_id, syncEverySteps=10)
    runtime._simulation_lock = threading.RLock()
    runtime._feedback_count = 0
    runtime._kafka_offset = 0
    runtime._scaled_started = __import__("time").monotonic() - 1
    runtime._last_logged_training_step = 0
    runtime._last_sync_version = 0
    runtime.source_vector_resolver = SimpleNamespace(
        telemetry=lambda: SimpleNamespace(
            qwen_encode=1, cache_hit=2, pgvector_load=3, evictions=4
        )
    )
    runtime.trainer = SimpleNamespace(
        metrics={
            "optimizerSteps": 10,
            "processedEvents": 0,
            "rollingRankLoss": 0.25,
        },
        last_step_time_ms=3.0,
    )
    runtime.database = SimpleNamespace(
        update_metrics=lambda _run_id, **values: metrics.append(values),
        append_activity=lambda activity: activities.append(activity),
    )
    runtime._persist_and_activate = lambda *, synchronize: synchronized.append(synchronize)

    runtime._after_scaled_feedback(SimpleNamespace(offset=7))

    assert metrics[0]["trainer_steps"] == 10
    assert metrics[0]["rolling_rank_loss"] == 0.25
    assert metrics[0]["kafka_lag"] == 1
    assert metrics[0]["event_rate"] > 0
    assert {row.event for row in activities} == {
        "online_training_progress", "feedback_acknowledged"
    }
    assert synchronized == [True]


def test_disabled_interleave_finishes_all_creation_work_before_any_walk() -> None:
    run_id = uuid4()
    creators = [uuid4(), uuid4()]
    plan = [
        ("2026-06", creator, {
            "article_key": f"enwiki:{index + 1}",
            "canonical_title": f"Root {index}",
            "lead_text": "Lead",
            "article_text": "Lead",
            "content_hash": "a" * 64,
        }, uuid4())
        for index, creator in enumerate(creators)
    ]
    operations = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(
        runId=run_id,
        concurrentUsers=2,
        interleaveCreationAndRecommendations=False,
    )
    runtime.database = SimpleNamespace(
        load_work_schedule=lambda _run_id: (),
        persist_work_schedule=lambda _rows: None,
        stage_babel=lambda **values: operations.append(("stage", values["babel"].babelId)),
    )
    runtime._publish_scaled_root_batch = lambda completed: operations.append(
        ("publish_batch", tuple(value.babel.babelId for value in completed))
    )
    runtime._execute_scaled_session = lambda row, _article, _hidden, **_kwargs: operations.append(("walk", row.root_babel_id))

    runtime._simulate_scaled(plan, {"2026-06": set(), "2026-07": set()})

    first_walk = next(index for index, row in enumerate(operations) if row[0] == "walk")
    assert all(row[0] != "walk" for row in operations[:first_walk])
    assert sum(row[0] == "publish_batch" for row in operations[:first_walk]) == 1
    assert operations[first_walk - 1][1] == tuple(row[3] for row in plan)


def test_v2_activity_identifies_actual_walk_source_and_acting_creator() -> None:
    run_id = uuid4()
    acting_creator = uuid4()
    source_owner = uuid4()
    source_id = uuid4()
    captured = []
    runtime = object.__new__(FridayDemoRuntime)
    runtime.config = SimpleNamespace(runId=run_id)
    runtime.database = SimpleNamespace(
        append_activity=lambda activity: captured.append(activity)
    )
    source = CreatedBabel(
        babelId=source_id,
        runId=run_id,
        creatorId=source_owner,
        sourceArticleKey="enwiki:1",
        title="Accepted source",
        text="Lead",
        createdAtNs=1,
    )
    event = FeedbackEventV2(
        schemaVersion=2,
        eventId=uuid4(),
        requestId=uuid4(),
        runId=run_id,
        creatorId=acting_creator,
        sourceBabelId=source_id,
        sourceArticleKey=source.sourceArticleKey,
        traversalSessionId=uuid4(),
        parentRequestId=uuid4(),
        traversalDepth=1,
        modelId=uuid4(),
        modelVersion=0,
        embeddingSpaceId=uuid4(),
        retrievalBackend="pgvector",
        sourceVectorOrigin="cache_hit",
        candidateActions=[],
        occurredAtNs=1,
    )
    response = SimpleNamespace(
        timingsNs={
            "queue": 0, "encode": 1, "context": 1, "ann": 1,
            "filtering": 1, "serialization": 1, "serverTotal": 5,
        },
        candidates=[],
        modelId=event.modelId,
        modelVersion=0,
        sourceVectorOrigin="cache_hit",
    )

    runtime._record_recommendation(source, response, event, 8)

    activity = captured[0]
    assert activity.details.creatorId == acting_creator
    assert activity.details.newBabelId == source_id
    assert activity.details.newBabelTitle == "Accepted source"
    assert activity.schemaVersion == 2
    assert activity.details.requestId == event.requestId
    assert activity.details.traversalSessionId == event.traversalSessionId
    assert activity.details.sourceVectorOrigin == "cache_hit"
    assert activity.metrics["traversalDepth"] == 1
