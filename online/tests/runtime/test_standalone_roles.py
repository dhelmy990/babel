from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from babel_online.contracts import RunConfigV1, RunConfigV2
from babel_online.model.registry import ModelRegistry
from babel_online.feedback.bus import TopicPartition
from babel_online.model.state_distributor import ModelStateDistributor
from babel_online.observable import CreatedBabel, VectorRecord
from babel_online.runtime.services import (
    _activate_prepared_snapshot,
    ServingRole,
    TrainerRole,
    serving_main,
    trainer_main,
    trainer_runtime_metrics,
    resolve_role_state_root,
    scope_split_consumer,
    active_model_descends_from,
    publish_final_update,
    require_scaled_trainer_config,
    resolve_trainer_activation,
    run_periodic_training,
    load_selected_working_model,
)
from babel_online.training.torch_working import TorchOnlineRecommender
import pytest


def test_split_activation_passes_the_prebuilt_snapshot_without_rebuilding() -> None:
    prepared_snapshot = object()
    activated = []
    commits = []

    class State:
        def activate_prepared(self, replacement, *, activation_commit):
            activation_commit()
            activated.append(replacement)

    _activate_prepared_snapshot(
        state=State(),
        prepared_snapshot=prepared_snapshot,
        activation_commit=lambda: commits.append("committed"),
    )

    assert activated == [prepared_snapshot]
    assert commits == ["committed"]


def test_standalone_trainer_publishes_file_handoff_and_serving_explicitly_activates(
    tmp_path, real_model_manifest
) -> None:
    run_id = uuid4()
    babel = CreatedBabel(
        babelId=uuid4(),
        runId=run_id,
        creatorId=uuid4(),
        sourceArticleKey="enwiki:42",
        title="Forty two",
        text="A useful article.",
        createdAtNs=1,
    )
    vector = np.eye(100, dtype=np.float32)[0]
    base = VectorRecord(
        babel=babel,
        catalogContentHash="c" * 64,
        embeddingSpaceId=real_model_manifest.embeddingSpace.embeddingSpaceId,
        servingModelId=real_model_manifest.modelId,
        materializedModelVersion=0,
        vector=tuple(float(value) for value in vector),
    )
    trainer = SimpleNamespace(
        processed_events=3,
        capture_sync_state=lambda: SimpleNamespace(
            version=2,
            materialized_vectors={babel.babelId: vector},
            model_state={"transferState": {"queryVector": vector.tolist()}},
        ),
    )
    trainer_registry = ModelRegistry()
    trainer_registry.register_real_original(real_model_manifest)
    inserted = []
    registered = []
    publication_order = []
    database = SimpleNamespace(
        insert_vectors=lambda records: (
            publication_order.append("vectors"), inserted.extend(records)
        ),
        register_real_child=lambda descriptor, path: (
            publication_order.append("child"), registered.append((descriptor, path))
        ),
    )
    published = TrainerRole(
        trainer=trainer,
        parent=real_model_manifest,
        registry=trainer_registry,
        database=database,
        run_id=run_id,
        state_root=tmp_path,
        base_records=[base],
    ).publish_update()

    assert published.activation_request_path.is_file()
    request = json.loads(published.activation_request_path.read_text())
    assert request["runId"] == str(run_id)
    assert published.activation_request_path.parent.name == "activations"
    assert published.activation_request_path.name == "request-v00000002.json"
    assert request["descriptorPath"] == str(published.child.descriptor_path)
    assert all(record.servingModelId == published.child.descriptor.childManifest.modelId for record in inserted)
    assert registered[0][0].modelVersion == 2
    assert publication_order == ["child", "vectors"]

    serving_registry = ModelRegistry()
    serving_registry.register_real_original(real_model_manifest)
    selected = {"state": "original"}
    distributor = ModelStateDistributor(
        registry=serving_registry,
        current_state=lambda: selected["state"],
        activate_state=lambda state: selected.__setitem__("state", state),
    )
    role = ServingRole(
        distributor=distributor,
        activation_request_path=published.activation_request_path,
        expected_run_id=run_id,
        prepare=lambda descriptor, _root: str(descriptor.childManifest.modelId),
        probe=lambda _prepared, _probe: True,
    )

    mismatched = dict(request)
    mismatched["modelId"] = str(uuid4())
    published.activation_request_path.write_text(json.dumps(mismatched))
    assert role.poll_activation() is None
    assert selected["state"] == "original"
    published.activation_request_path.write_text(json.dumps(request))
    receipt = role.poll_activation()

    assert receipt is not None
    assert receipt.modelVersion == 2
    assert selected["state"] == str(published.child.descriptor.childManifest.modelId)
    assert not published.activation_request_path.exists()
    receipt_path = published.activation_request_path.with_name(
        "receipt-v00000002.json"
    )
    receipt_document = json.loads(receipt_path.read_text())
    assert receipt_document["modelId"] == str(receipt.modelId)
    assert receipt_document["modelVersion"] == 2
    assert receipt_document["activatedAtNs"] >= receipt_document["publishedAtNs"]
    assert receipt_document["preparedAtNs"] >= receipt_document["publishedAtNs"]
    assert receipt_document["stageDurationNs"] >= 0
    assert receipt_document["switchDurationNs"] >= 0


def test_serving_role_stays_on_last_valid_state_when_no_trainer_update(tmp_path) -> None:
    selected = {"state": "last-valid"}
    role = ServingRole(
        distributor=SimpleNamespace(),
        activation_request_path=tmp_path / "activation-request.json",
        expected_run_id=uuid4(),
        prepare=lambda *_: None,
        probe=lambda *_: False,
    )

    assert role.poll_activation() is None
    assert selected["state"] == "last-valid"


def test_serving_rejects_invalid_activation_request_without_losing_availability(
    tmp_path,
) -> None:
    run_id = uuid4()
    request = tmp_path / "request-v00000001.json"
    request.write_text('{"schemaVersion":1,"runId":"wrong"}\n')
    role = ServingRole(
        distributor=SimpleNamespace(),
        activation_request_path=request,
        expected_run_id=run_id,
        prepare=lambda *_: None,
        probe=lambda *_: False,
    )

    assert role.poll_activation() is None
    assert role.last_rejection is not None
    assert request.exists()


@pytest.mark.parametrize("entrypoint", [serving_main, trainer_main])
def test_role_entrypoints_are_independently_invokable(entrypoint, capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        entrypoint(["--help"])
    assert stopped.value.code == 0
    assert "--run-id" in capsys.readouterr().out


def test_trainer_condition_requires_an_explicit_valid_activation_setting() -> None:
    assert resolve_trainer_activation("true", {}) is True
    assert resolve_trainer_activation("false", {}) is False
    assert (
        resolve_trainer_activation(None, {"BABEL_ONLINE_ACTIVATION_ENABLED": "true"})
        is True
    )
    assert (
        resolve_trainer_activation("false", {"BABEL_ONLINE_ACTIVATION_ENABLED": "true"})
        is False
    )
    with pytest.raises(ValueError, match="activation-enabled"):
        resolve_trainer_activation(None, {})
    with pytest.raises(ValueError, match="true or false"):
        resolve_trainer_activation(
            None, {"BABEL_ONLINE_ACTIVATION_ENABLED": "sometimes"}
        )


def test_split_trainer_accepts_only_scaled_v2_run_configs() -> None:
    scaled = RunConfigV2.model_construct(schemaVersion=2)
    assert require_scaled_trainer_config(scaled) is scaled
    with pytest.raises(TypeError, match="RunConfigV2"):
        require_scaled_trainer_config(RunConfigV1.model_construct(schemaVersion=1))


def test_trainer_publishes_periodic_immutable_updates_before_shutdown() -> None:
    stop = SimpleNamespace(value=False, is_set=lambda: stop.value)
    published, checkpoints, progress = [], [], []

    class Trainer:
        training_version = 0
        processed_events = 0

        def process_available(self, **_kwargs):
            self.training_version += 1
            self.processed_events += 1
            if self.training_version == 5:
                stop.value = True
            return 1

        def checkpoint_and_commit(self):
            checkpoints.append(self.training_version)

    trainer = Trainer()
    run_periodic_training(
        trainer,
        stop_requested=stop.is_set,
        checkpoint_every_events=2,
        sync_every_steps=2,
        activation_enabled=True,
        publish_update=lambda: published.append(trainer.training_version),
        initial_published_version=0,
        report_metrics=lambda: progress.append(trainer.training_version),
    )

    assert published == [2, 4]
    assert checkpoints == [2, 4]
    assert progress == [1, 2, 3, 4, 5]


def test_training_without_activation_consumes_checkpoints_and_never_publishes() -> None:
    stop = SimpleNamespace(value=False, is_set=lambda: stop.value)
    published, checkpoints, progress = [], [], []

    class Trainer:
        training_version = 0
        processed_events = 0

        def process_available(self, **_kwargs):
            self.training_version += 1
            self.processed_events += 1
            if self.training_version == 5:
                stop.value = True
            return 1

        def checkpoint_and_commit(self):
            checkpoints.append(self.training_version)

    trainer = Trainer()
    last_published = run_periodic_training(
        trainer,
        stop_requested=stop.is_set,
        checkpoint_every_events=2,
        sync_every_steps=2,
        activation_enabled=False,
        publish_update=lambda: published.append(trainer.training_version),
        initial_published_version=0,
        report_metrics=lambda: progress.append(trainer.training_version),
    )

    assert trainer.training_version == 5
    assert published == []
    assert checkpoints == [2, 4]
    assert progress == [1, 2, 3, 4, 5]
    assert last_published == 0


def test_training_without_activation_never_publishes_the_final_update() -> None:
    published = []
    trainer = SimpleNamespace(training_version=5)
    role = SimpleNamespace(publish_update=lambda: published.append(5))

    assert (
        publish_final_update(trainer, role, last_published=0, activation_enabled=False)
        == 0
    )
    assert published == []
    assert (
        publish_final_update(trainer, role, last_published=0, activation_enabled=True)
        == 5
    )
    assert published == [5]


def test_split_trainer_reports_steps_loss_and_current_kafka_lag() -> None:
    partition = TopicPartition("babel.feedback.v1", 0)
    trainer = SimpleNamespace(
        metrics={"optimizerSteps": 12, "rollingRankLoss": 0.25},
        next_offsets={partition: 7},
        consumer=SimpleNamespace(high_watermarks=lambda: {partition: 10}),
    )

    assert trainer_runtime_metrics(trainer) == {
        "trainer_steps": 12,
        "rolling_rank_loss": 0.25,
        "kafka_lag": 3,
    }


def test_split_roles_share_supervisor_state_root_when_config_differs(tmp_path) -> None:
    configured = tmp_path / "run-config-state"
    supervised = tmp_path / "supervisor-state"

    assert resolve_role_state_root(
        SimpleNamespace(stateRoot=str(configured)),
        {"BABEL_ONLINE_STATE_ROOT": str(supervised)},
    ) == supervised
    assert resolve_role_state_root(
        SimpleNamespace(stateRoot=str(configured)), {}
    ) == configured


def test_split_consumer_starts_at_run_watermark_and_rejects_other_runs() -> None:
    run_id = uuid4()
    partition = TopicPartition("babel.feedback.v1", 0)
    seeks = []

    class RawConsumer:
        def high_watermarks(self):
            return {partition: 12}

        def seek(self, offsets):
            seeks.append(offsets)

        def poll(self, _timeout=0.0):
            return SimpleNamespace(event=SimpleNamespace(runId=uuid4()))

    scoped = scope_split_consumer(RawConsumer(), run_id=run_id)

    assert seeks == [{partition: 12}]
    with pytest.raises(RuntimeError, match="cross-run"):
        scoped.poll()


def test_role_restart_accepts_active_child_of_configured_starting_model(
    real_model_manifest,
) -> None:
    child_document = real_model_manifest.model_dump(mode="json")
    child_document.update(
        modelId=uuid4(), parentModelId=real_model_manifest.modelId,
        producingRunId=uuid4(), label="active child",
    )
    child = type(real_model_manifest).model_validate(child_document)
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    registry.register_child(child)

    assert active_model_descends_from(
        registry,
        active_model_id=child.modelId,
        starting_model_id=real_model_manifest.modelId,
    )
    assert active_model_descends_from(
        registry, active_model_id=child.modelId, starting_model_id=child.modelId
    )
    assert not active_model_descends_from(
        registry, active_model_id=real_model_manifest.modelId,
        starting_model_id=child.modelId,
    )


def test_same_process_and_split_training_load_identical_selected_child_state(
    tmp_path,
) -> None:
    run_id = uuid4()
    babel_ids = (uuid4(), uuid4())
    records = []
    for index, babel_id in enumerate(babel_ids):
        vector = np.zeros(100, dtype=np.float32)
        vector[index] = 1.0
        records.append(
            VectorRecord(
                babel=CreatedBabel(
                    babelId=babel_id,
                    runId=run_id,
                    creatorId=uuid4(),
                    sourceArticleKey=f"enwiki:{index + 1}",
                    title=f"Babel {index}",
                    text="Lead",
                    createdAtNs=index + 1,
                ),
                catalogContentHash=str(index + 1) * 64,
                embeddingSpaceId=uuid4(),
                servingModelId=uuid4(),
                materializedModelVersion=3,
                vector=tuple(float(value) for value in vector),
            )
        )
    frozen = {
        record.babel.babelId: np.asarray(record.vector, dtype="<f4")
        for record in records
    }
    selected_model = TorchOnlineRecommender(frozen, learning_rate=0.017)
    state = selected_model.state_dict()
    descriptor_path = tmp_path / "model" / "state-descriptor.json"
    descriptor_path.parent.mkdir()
    (descriptor_path.parent / "online-state.json").write_text(json.dumps(state))
    selected = (SimpleNamespace(onlineStatePath="online-state.json"), descriptor_path)

    same_process = load_selected_working_model(records, selected)
    split_process = load_selected_working_model(records, selected)

    assert same_process.state_dict() == split_process.state_dict()
    assert same_process.frozen_bytes() == split_process.frozen_bytes()
    assert same_process.learning_rate == state["learningRate"]
    assert same_process.transfer_state_dict() == state["transferState"]
    for record in records:
        assert np.allclose(
            same_process.materialized_vector(record.babel.babelId),
            np.asarray(record.vector, dtype="<f4"),
        )
        assert np.allclose(same_process.residual(record.babel.babelId), np.zeros(100))


def test_original_training_starts_without_child_state() -> None:
    run_id = uuid4()
    vector = np.eye(100, dtype=np.float32)[0]
    record = VectorRecord(
        babel=CreatedBabel(
            babelId=uuid4(),
            runId=run_id,
            creatorId=uuid4(),
            sourceArticleKey="enwiki:1",
            title="Original",
            text="Lead",
            createdAtNs=1,
        ),
        catalogContentHash="a" * 64,
        embeddingSpaceId=uuid4(),
        servingModelId=uuid4(),
        materializedModelVersion=0,
        vector=tuple(float(value) for value in vector),
    )

    model = load_selected_working_model([record], (None, None))

    assert isinstance(model, TorchOnlineRecommender)
    assert model.learning_rate == 0.05
    assert np.allclose(model.residual(record.babel.babelId), np.zeros(100))


def test_periodic_training_polls_the_configured_micro_batch() -> None:
    calls = []

    class Trainer:
        micro_batch_size = 8
        training_version = 0
        processed_events = 0

        def process_available(self, **kwargs):
            calls.append(kwargs["max_records"])
            return 0

    run_periodic_training(
        Trainer(),
        stop_requested=lambda: len(calls) == 1,
        checkpoint_every_events=100,
        sync_every_steps=10,
        activation_enabled=False,
        publish_update=lambda: None,
        initial_published_version=0,
    )

    assert calls == [8]
