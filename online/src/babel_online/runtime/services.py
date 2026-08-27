"""Standalone serving/trainer role boundaries for same-host split runs."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import signal
import struct
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid5

import numpy as np

from ..contracts import (
    ActivityLogV1,
    ModelManifestV2,
    RunConfigV2,
    SynchronizationActivityV1,
    canonical_pgvector_snapshot_sha256,
)
from ..model.registry import ModelRegistry
from ..model.state_distributor import (
    ActivationReceipt,
    ExportedRealQwenChild,
    KnownVectorProbeV1,
    ModelStateDistributor,
    RealQwenChildStateV1,
    export_real_qwen_child,
    semantic_vector_sha256,
)
from ..observable import VectorRecord


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _snapshot_sha(records: list[VectorRecord]) -> str:
    return canonical_pgvector_snapshot_sha256(
        {
            "babelId": record.babel.babelId,
            "creatorId": record.babel.creatorId,
            "sourceArticleKey": record.babel.sourceArticleKey,
            "catalogContentHash": record.catalogContentHash,
            "embeddingSpaceId": record.embeddingSpaceId,
            "servingModelId": record.servingModelId,
            "materializedModelVersion": record.materializedModelVersion,
            "vectorSha256": hashlib.sha256(
                struct.pack("<100f", *record.vector)
            ).hexdigest(),
        }
        for record in records
    )


def _atomic_request(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_bytes(_canonical_json(document))
    os.replace(partial, path)


@dataclass(frozen=True, slots=True)
class PublishedUpdate:
    child: ExportedRealQwenChild
    activation_request_path: Path
    vector_snapshot_sha256: str


class TrainerRole:
    """Own online updates and publish state only through immutable files."""

    def __init__(
        self,
        *,
        trainer: Any,
        parent: ModelManifestV2,
        registry: ModelRegistry,
        database: Any,
        run_id: UUID,
        state_root: str | Path,
        base_records: list[VectorRecord],
    ) -> None:
        if not isinstance(parent, ModelManifestV2):
            raise TypeError("standalone trainer requires the real Qwen V2 parent")
        self.trainer = trainer
        self.parent = parent
        self.registry = registry
        self.database = database
        self.run_id = run_id
        self.state_root = Path(state_root)
        self.base_records = list(base_records)

    def publish_update(self) -> PublishedUpdate:
        captured = self.trainer.capture_sync_state()
        version = int(captured.version)
        if version <= 0:
            raise ValueError("trainer has no versioned online update to publish")
        child_id = uuid5(self.run_id, f"real-qwen-online-child-v{version}")
        records = [
            VectorRecord(
                babel=record.babel,
                catalogContentHash=record.catalogContentHash,
                embeddingSpaceId=self.parent.embeddingSpace.embeddingSpaceId,
                servingModelId=child_id,
                materializedModelVersion=version,
                vector=tuple(
                    float(value)
                    for value in captured.materialized_vectors[record.babel.babelId]
                ),
            )
            for record in self.base_records
        ]
        snapshot_sha = _snapshot_sha(records)
        state = _canonical_json(captured.model_state)
        transfer = captured.model_state.get("transferState", {})
        probe_vector = transfer.get("queryVector", [1.0] + [0.0] * 99)
        child = export_real_qwen_child(
            self.state_root / str(self.run_id) / "models",
            parent=self.parent,
            run_id=self.run_id,
            child_model_id=child_id,
            label=f"Post-run real Qwen model v{version}",
            online_state=state,
            processed_feedback_events=int(self.trainer.processed_events),
            model_version=version,
            vector_snapshot_sha256=snapshot_sha,
            probe=KnownVectorProbeV1(
                schemaVersion=1,
                inputVector=probe_vector,
                expectedSemanticSha256=semantic_vector_sha256(probe_vector),
            ),
            registry=self.registry,
        )
        self.database.register_real_child(child.descriptor, child.descriptor_path)
        # The vector rows reference recommender_models. Register the immutable
        # child before inserting its materialized population.
        self.database.insert_vectors(records)
        request_path = (
            self.state_root
            / str(self.run_id)
            / "activations"
            / f"request-v{version:08d}.json"
        )
        _atomic_request(
            request_path,
            {
                "schemaVersion": 1,
                "runId": str(self.run_id),
                "modelId": str(child.descriptor.childManifest.modelId),
                "modelVersion": version,
                "descriptorPath": str(child.descriptor_path),
                "descriptorSha256": child.descriptor_sha256,
                "publishedAtNs": time.time_ns(),
            },
        )
        return PublishedUpdate(child, request_path, snapshot_sha)


class ServingRole:
    """Poll explicit activation requests while retaining the current snapshot."""

    def __init__(
        self,
        *,
        distributor: ModelStateDistributor,
        activation_request_path: str | Path,
        expected_run_id: UUID,
        prepare: Callable[[RealQwenChildStateV1, Path], Any],
        probe: Callable[[Any, KnownVectorProbeV1], bool],
    ) -> None:
        self.distributor = distributor
        self.activation_request_path = Path(activation_request_path)
        self.expected_run_id = expected_run_id
        self.prepare = prepare
        self.probe = probe
        self.last_rejection: str | None = None

    def poll_activation(self) -> ActivationReceipt | None:
        if not self.activation_request_path.is_file():
            return None
        try:
            request = json.loads(
                self.activation_request_path.read_text(encoding="utf-8")
            )
            required = {
                "schemaVersion",
                "runId",
                "modelId",
                "modelVersion",
                "descriptorPath",
                "descriptorSha256",
                "publishedAtNs",
            }
            if set(request) != required or request["schemaVersion"] != 1:
                raise ValueError("activation request contract is invalid")
            if request["runId"] != str(self.expected_run_id):
                raise ValueError("activation request belongs to another run")
            descriptor_path = Path(request["descriptorPath"])
            if hashlib.sha256(descriptor_path.read_bytes()).hexdigest() != request[
                "descriptorSha256"
            ]:
                raise ValueError("activation request descriptor checksum differs")
            requested_descriptor = RealQwenChildStateV1.model_validate_json(
                descriptor_path.read_text(encoding="utf-8")
            )
            requested_child = requested_descriptor.childManifest
            if requested_child.producingRunId != self.expected_run_id:
                raise ValueError("activation descriptor belongs to another run")
            if str(requested_child.modelId) != request["modelId"]:
                raise ValueError("activation descriptor model differs from request")
            if requested_descriptor.modelVersion != int(request["modelVersion"]):
                raise ValueError("activation descriptor version differs from request")
            receipt = self.distributor.activate(
                descriptor_path.parent,
                prepare=self.prepare,
                probe=self.probe,
                published_at_ns=int(request["publishedAtNs"]),
            )
            if str(receipt.modelId) != request["modelId"]:
                raise ValueError("activated model differs from requested model")
            if receipt.modelVersion != int(request["modelVersion"]):
                raise ValueError("activated version differs from requested version")
        except Exception as error:
            # Never delete a rejected request: it remains fault evidence and the
            # current serving snapshot remains active.
            self.last_rejection = str(error)
            return None
        receipt_path = self.activation_request_path.with_name(
            self.activation_request_path.name.replace("request-v", "receipt-v", 1)
        )
        _atomic_request(
            receipt_path,
            {
                "schemaVersion": 1,
                "runId": str(self.expected_run_id),
                "modelId": str(receipt.modelId),
                "modelVersion": receipt.modelVersion,
                "publishedAtNs": int(request["publishedAtNs"]),
                "activatedAtNs": receipt.activatedAtNs,
                "stalenessNs": receipt.stalenessNs,
            },
        )
        self.activation_request_path.unlink()
        self.last_rejection = None
        return receipt


__all__ = ["PublishedUpdate", "ServingRole", "TrainerRole"]


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _load_records(database: Any, identity: Any) -> list[VectorRecord]:
    records: list[VectorRecord] = []
    after: UUID | None = None
    while True:
        batch = database.population_vectors(identity, after_babel_id=after, limit=2_000)
        if not batch:
            break
        records.extend(batch)
        after = batch[-1].babel.babelId
    if not records:
        raise RuntimeError("run has no active synthetic-created Babel population")
    return records


def load_selected_working_model(
    records: list[VectorRecord],
    selected_artifact: tuple[RealQwenChildStateV1 | None, Path | None],
) -> Any:
    """Construct the trainer state shared by monolith and split topologies."""
    from ..training.working import NumpyWorkingModel

    frozen = {
        record.babel.babelId: np.asarray(record.vector, dtype="<f4")
        for record in records
    }
    model = NumpyWorkingModel(
        frozen,
        query_vector=np.mean(np.stack(list(frozen.values())), axis=0),
        learning_rate=0.05,
    )
    descriptor, descriptor_path = selected_artifact
    if (descriptor is None) != (descriptor_path is None):
        raise ValueError("selected child descriptor binding is incomplete")
    if descriptor is not None and descriptor_path is not None:
        state_path = (descriptor_path.parent / descriptor.onlineStatePath).resolve()
        try:
            state_path.relative_to(descriptor_path.parent.resolve())
        except ValueError as error:
            raise ValueError("selected child state escapes its artifact") from error
        state = json.loads(state_path.read_text(encoding="utf-8"))
        model.load_state_dict(state)
    return model


def resolve_role_state_root(
    config: Any, environment: Mapping[str, str] = os.environ
) -> Path:
    """Resolve the one shared trainer/serving/distributor handoff directory."""
    return Path(environment.get("BABEL_ONLINE_STATE_ROOT", config.stateRoot))


def scope_split_consumer(consumer: Any, *, run_id: UUID) -> Any:
    """Start after historical traffic and fail closed on cross-run feedback."""
    from .worker import RunScopedConsumer, isolate_new_run_offsets

    isolate_new_run_offsets(consumer)
    return RunScopedConsumer(consumer, run_id=run_id)


def active_model_descends_from(
    registry: ModelRegistry,
    *,
    active_model_id: UUID,
    starting_model_id: UUID,
) -> bool:
    """Accept restart state only on the configured immutable lineage branch."""
    current = registry.get(active_model_id)
    while True:
        if current.modelId == starting_model_id:
            return True
        if current.parentModelId is None:
            return False
        current = registry.get(current.parentModelId)


def _load_role_context(run_id: UUID, *, load_encoder: bool):
    from ..model.artifact import LoadedRealArtifact, build_real_original_manifest
    from ..model.distilled_artifact import (
        REAL_ARTIFACT_ID,
        REAL_ARTIFACT_REVISION,
        REAL_MODEL_REPO,
        DistilledArtifactV1,
    )
    from ..model.population import PopulationIdentity
    from ..model.qwen_encoder import Qwen100Encoder
    from .cli import REAL_EMBEDDING_SPACE_ID, REAL_ONLINE_MODEL_ID
    from .database import RuntimeDatabase

    database = RuntimeDatabase(_required_environment("BABEL_DATABASE_URL"))
    persisted = database.load_run(run_id)
    token = _required_environment("HF_TOKEN")
    distilled = DistilledArtifactV1.load(
        repo_id=REAL_MODEL_REPO,
        revision=REAL_ARTIFACT_REVISION,
        artifact_id=REAL_ARTIFACT_ID,
        token=token,
        cache_dir=os.environ.get(
            "BABEL_ONLINE_MODEL_ARTIFACT_CACHE", "state/online/cache/model-artifact"
        ),
    )
    original = build_real_original_manifest(
        distilled,
        model_id=REAL_ONLINE_MODEL_ID,
        embedding_space_id=REAL_EMBEDDING_SPACE_ID,
    )
    database.bootstrap_real_model(
        original,
        artifact_manifest_path=distilled.path_for("artifact_manifest.json"),
    )
    registry = ModelRegistry()
    registry.register_real_original(original)
    active = database.load_active_embedding_state(run_id)
    selected = original
    selected_descriptor = None
    selected_descriptor_path = None
    if active.model_id != original.modelId:
        lineage = []
        current = active.model_id
        while current != original.modelId:
            descriptor, descriptor_path = database.load_real_child_artifact(current)
            lineage.append((descriptor, descriptor_path))
            current = descriptor.childManifest.parentModelId
        for descriptor, _descriptor_path in reversed(lineage):
            registry.register_child(descriptor.childManifest)
        selected_descriptor, selected_descriptor_path = lineage[0]
        selected = selected_descriptor.childManifest
    if not active_model_descends_from(
        registry,
        active_model_id=selected.modelId,
        starting_model_id=persisted.config.startingModelId,
    ):
        raise RuntimeError("active population is outside the configured model lineage")
    identity = PopulationIdentity.from_real_model(
        run_id=run_id,
        dataset_revision=persisted.config.datasetRevision,
        model=selected,
        model_version=active.model_version,
    )
    records = _load_records(database, identity)
    encoder = None
    if load_encoder:
        encoder = Qwen100Encoder.from_artifact(
            distilled,
            token=token,
            device=os.environ.get("BABEL_ONLINE_QWEN_DEVICE", "cpu"),
            model_cache_dir=os.environ.get(
                "BABEL_ONLINE_QWEN_CACHE", "state/online/cache/qwen-base"
            ),
        )
    return (
        database,
        persisted.config,
        LoadedRealArtifact(selected, distilled),
        (selected_descriptor, selected_descriptor_path),
        registry,
        active,
        records,
        encoder,
    )


def resolve_trainer_activation(
    cli_value: str | None,
    environment: Mapping[str, str] = os.environ,
) -> bool:
    """Resolve the condition's explicit activation control without a silent default."""
    raw = cli_value
    if raw is None:
        raw = environment.get("BABEL_ONLINE_ACTIVATION_ENABLED")
    if raw is None:
        raise ValueError(
            "--activation-enabled or BABEL_ONLINE_ACTIVATION_ENABLED is required"
        )
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError("activation-enabled must be true or false")
    return normalized == "true"


def require_scaled_trainer_config(config: object) -> RunConfigV2:
    """Keep formal split-condition training on the scaled V2 run contract."""
    if not isinstance(config, RunConfigV2):
        raise TypeError("split condition trainer requires RunConfigV2")
    return config


def run_periodic_training(
    trainer: Any,
    *,
    stop_requested: Callable[[], bool],
    checkpoint_every_events: int,
    sync_every_steps: int,
    activation_enabled: bool,
    publish_update: Callable[[], Any],
    initial_published_version: int,
    report_metrics: Callable[[], None] | None = None,
    report_checkpoint: Callable[[Path], None] | None = None,
) -> int:
    """Consume continuously while publishing bounded immutable update versions."""
    if checkpoint_every_events <= 0 or sync_every_steps <= 0:
        raise ValueError("checkpoint and synchronization intervals must be positive")
    published_version = initial_published_version
    while not stop_requested():
        processed = trainer.process_available(
            max_records=1, poll_timeout_seconds=0.1
        )
        if processed and report_metrics is not None:
            report_metrics()
        if processed and trainer.processed_events % checkpoint_every_events == 0:
            checkpoint = trainer.checkpoint_and_commit()
            if report_checkpoint is not None:
                report_checkpoint(checkpoint)
        if (
            activation_enabled
            and trainer.training_version >= published_version + sync_every_steps
        ):
            publish_update()
            published_version = trainer.training_version
    return published_version


def publish_final_update(
    trainer: Any,
    role: TrainerRole,
    *,
    last_published: int,
    activation_enabled: bool,
) -> int:
    """Publish shutdown state only for the activation-enabled condition."""
    if activation_enabled and trainer.training_version > last_published:
        role.publish_update()
        return trainer.training_version
    return last_published


def trainer_runtime_metrics(trainer: Any) -> dict[str, int | float]:
    """Snapshot independently observable split-trainer health telemetry."""
    metrics = trainer.metrics
    high_watermarks = trainer.consumer.high_watermarks()
    lag = sum(
        max(0, end - trainer.next_offsets.get(partition, 0))
        for partition, end in high_watermarks.items()
    )
    return {
        "trainer_steps": int(metrics["optimizerSteps"]),
        "rolling_rank_loss": float(metrics["rollingRankLoss"]),
        "kafka_lag": lag,
    }


def trainer_main(argv: list[str] | None = None) -> int:
    """Run the Kafka-consuming online trainer as its own OS process."""
    from ..feedback.kafka import KafkaFeedbackConsumer
    from ..training.checkpoint import CheckpointIdentity, load_latest_checkpoint
    from ..training.consumer import OnlineTrainer

    parser = argparse.ArgumentParser(prog="babel-online-trainer")
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--activation-enabled", metavar="{true,false}")
    arguments = parser.parse_args(argv)
    try:
        activation_enabled = resolve_trainer_activation(arguments.activation_enabled)
    except ValueError as error:
        parser.error(str(error))
    (
        database,
        config,
        loaded,
        selected_artifact,
        registry,
        active,
        records,
        _encoder,
    ) = _load_role_context(arguments.run_id, load_encoder=False)
    config = require_scaled_trainer_config(config)
    model = load_selected_working_model(records, selected_artifact)
    raw_consumer = KafkaFeedbackConsumer(
        os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092"),
        group_id=f"{config.kafkaGroup}.{arguments.run_id}",
    )
    consumer = scope_split_consumer(raw_consumer, run_id=arguments.run_id)
    role_state_root = resolve_role_state_root(config)
    trainer = OnlineTrainer(
        model=model,
        consumer=consumer,
        checkpoint_root=role_state_root / str(arguments.run_id) / "checkpoints",
        identity=CheckpointIdentity(
            run_id=arguments.run_id,
            model_id=loaded.manifest.modelId,
            embedding_space_id=loaded.manifest.embeddingSpace.embeddingSpaceId,
        ),
    )
    restored = trainer.restore_latest()
    if restored is None:
        trainer.training_version = active.model_version
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    role = TrainerRole(
        trainer=trainer,
        parent=loaded.manifest,
        registry=registry,
        database=database,
        run_id=arguments.run_id,
        state_root=role_state_root,
        base_records=records,
    )
    ready_path_value = os.environ.get("BABEL_TRAINER_READY_PATH")
    ready_path = Path(ready_path_value) if ready_path_value else None
    if ready_path is not None:
        _atomic_request(
            ready_path,
            {
                "schemaVersion": 1,
                "runId": str(arguments.run_id),
                "consumerGroup": f"{config.kafkaGroup}.{arguments.run_id}",
                "readyAtNs": time.time_ns(),
            },
        )

    def report_metrics() -> None:
        database.update_metrics(arguments.run_id, **trainer_runtime_metrics(trainer))

    def report_checkpoint(path: Path) -> None:
        loaded_checkpoint = load_latest_checkpoint(path.parent)
        if loaded_checkpoint is None:
            raise RuntimeError("published checkpoint cannot be reloaded")
        database.update_metrics(
            arguments.run_id,
            checkpoint_path=str(path),
            checkpoint_sha256=loaded_checkpoint.manifest_sha256,
        )

    try:
        last_published = run_periodic_training(
            trainer,
            stop_requested=stopping.is_set,
            checkpoint_every_events=config.checkpointEveryEvents,
            sync_every_steps=config.syncEverySteps,
            activation_enabled=activation_enabled,
            publish_update=role.publish_update,
            initial_published_version=active.model_version,
            report_metrics=report_metrics,
            report_checkpoint=report_checkpoint,
        )
        end_offsets = consumer.high_watermarks()
        trainer.drain_to(end_offsets)
        final_checkpoint = trainer.checkpoint_and_commit()
        report_metrics()
        report_checkpoint(final_checkpoint)
        publish_final_update(
            trainer,
            role,
            last_published=last_published,
            activation_enabled=activation_enabled,
        )
    finally:
        consumer.close()
        if ready_path is not None:
            ready_path.unlink(missing_ok=True)
    return 0


def serving_main(argv: list[str] | None = None) -> int:
    """Run synchronous Qwen/pgvector recommendation serving independently."""
    import uvicorn

    from ..model.candidate_index import MaterializedServingState
    from ..model.pgvector_index import PgvectorCandidateIndex
    from ..model.population import PopulationIdentity
    from ..model.source_vector_cache import SourceVectorResolver
    from ..serving.app import create_app
    from ..serving.state import ServingState
    from ..training.working import NumpyWorkingModel

    parser = argparse.ArgumentParser(prog="babel-recommendation-server")
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--port", type=int, default=8791)
    arguments = parser.parse_args(argv)
    (
        database,
        config,
        loaded,
        _selected_artifact,
        registry,
        active,
        records,
        encoder,
    ) = _load_role_context(arguments.run_id, load_encoder=True)
    index = PgvectorCandidateIndex(database.query_candidates)
    state = ServingState(
        registry=registry,
        selected_model_id=loaded.manifest.modelId,
        materialized_state=active,
        candidate_index=index,
        vector_records=records,
        qwen_encoder=encoder,
        scale_run=True,
    )
    active_prepared: dict[str, Any] = {
        "value": (loaded.manifest, active, index, records)
    }

    def prepare(descriptor: RealQwenChildStateV1, root: Path):
        child = descriptor.childManifest
        identity = PopulationIdentity.from_real_model(
            run_id=arguments.run_id,
            dataset_revision=config.datasetRevision,
            model=child,
            model_version=descriptor.modelVersion,
        )
        child_records = _load_records(database, identity)
        if _snapshot_sha(child_records) != descriptor.vectorSnapshotSha256:
            raise ValueError("child pgvector snapshot checksum differs")
        child_state = MaterializedServingState(
            run_id=arguments.run_id,
            model_id=child.modelId,
            model_version=descriptor.modelVersion,
            embedding_space_id=child.embeddingSpace.embeddingSpaceId,
            pgvector_snapshot_sha256=descriptor.vectorSnapshotSha256,
            backend_snapshot_sha256=descriptor.vectorSnapshotSha256,
        )
        online_state = json.loads((root / descriptor.onlineStatePath).read_text())
        frozen = {
            record.babel.babelId: np.asarray(record.vector, dtype="<f4")
            for record in records
        }
        probe_model = NumpyWorkingModel(
            frozen,
            query_vector=np.mean(np.stack(list(frozen.values())), axis=0),
            learning_rate=0.05,
        )
        probe_model.load_state_dict(online_state)
        materialized = probe_model.materialized_vectors()
        if any(
            not np.allclose(
                materialized[record.babel.babelId],
                np.asarray(record.vector, dtype="<f4"),
                rtol=1e-6,
                atol=1e-7,
            )
            for record in child_records
        ):
            raise ValueError("online state does not reproduce child vectors")
        return (
            child,
            child_state,
            PgvectorCandidateIndex(database.query_candidates),
            child_records,
            root,
            descriptor,
            probe_model,
        )

    def probe(prepared, known: KnownVectorProbeV1) -> bool:
        descriptor_root = prepared[4]
        state_path = descriptor_root / "online-state.json"
        probe_model = prepared[6]
        actual = probe_model.transfer_state_dict()["queryVector"]
        return (
            state_path.is_file()
            and np.allclose(actual, known.inputVector, rtol=1e-6, atol=1e-7)
            and semantic_vector_sha256(actual) == known.expectedSemanticSha256
            and bool(prepared[3])
        )

    def activate(prepared) -> None:
        child, child_state, child_index, child_records = prepared[:4]
        def commit_pointer() -> None:
            database.activate_embedding_state(
                run_id=arguments.run_id,
                model_id=child.modelId,
                model_version=child_state.model_version,
                embedding_space_id=child_state.embedding_space_id,
                pgvector_sha256=child_state.pgvector_snapshot_sha256,
                backend_sha256=child_state.backend_snapshot_sha256,
            )
            database.update_metrics(
                arguments.run_id,
                serving_synced=True,
                active_model_id=child.modelId,
                active_model_version=child_state.model_version,
            )

        state.apply_sync(
            selected_model_id=child.modelId,
            materialized_state=child_state,
            candidate_index=child_index,
            vector_records=child_records,
            activation_commit=commit_pointer,
        )
        active_prepared["value"] = prepared
        if len(prepared) < 6:
            # Rollback to the original/previous prepared snapshot. It has no
            # child descriptor and must remain selectable without child logs.
            return
        descriptor = prepared[5]
        activated_at_ns = time.time_ns()
        online_state = Path(descriptor.onlineStatePath)
        database.append_activity(
            ActivityLogV1(
                schemaVersion=1,
                runId=arguments.run_id,
                sequence=1,
                occurredAtNs=activated_at_ns,
                level="info",
                component="serving",
                event="immutable_model_activated",
                message=(
                    f"Serving activated immutable model {child.modelId} "
                    f"at version {child_state.model_version}."
                ),
                metrics={
                    "publishedAtNs": descriptor.createdAtNs,
                    "activatedAtNs": activated_at_ns,
                    "stalenessNs": max(0, activated_at_ns - descriptor.createdAtNs),
                },
                details=SynchronizationActivityV1(
                    kind="synchronization",
                    checkpointPath=str(online_state),
                    checkpointSha256=descriptor.files[str(online_state)],
                    synchronizationVersion=child_state.model_version,
                    modelId=child.modelId,
                    modelVersion=child_state.model_version,
                ),
            )
        )

    distributor = ModelStateDistributor(
        registry=registry,
        current_state=lambda: active_prepared["value"],
        activate_state=activate,
        register_persistent=lambda descriptor, root: database.register_real_child(
            descriptor, root / "state-descriptor.json"
        ),
    )
    activation_dir = (
        resolve_role_state_root(config) / str(arguments.run_id) / "activations"
    )
    stopping = threading.Event()

    def watch() -> None:
        attempted: dict[Path, int] = {}
        while not stopping.wait(0.25):
            for request_path in sorted(activation_dir.glob("request-v*.json")):
                modified = request_path.stat().st_mtime_ns
                if attempted.get(request_path) == modified:
                    continue
                attempted[request_path] = modified
                ServingRole(
                    distributor=distributor,
                    activation_request_path=request_path,
                    expected_run_id=arguments.run_id,
                    prepare=prepare,
                    probe=probe,
                ).poll_activation()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    resolver = SourceVectorResolver(
        encoder,
        load_active=database.load_active_source_vector,
        capacity=max(512, min(10_000, config.creatorCount * 10)),
    )
    try:
        uvicorn.run(
            create_app(state, source_vector_resolver=resolver),
            host="127.0.0.1",
            port=arguments.port,
            log_level="info",
        )
    finally:
        stopping.set()
        watcher.join(timeout=2)
    return 0


__all__ = [
    "PublishedUpdate",
    "active_model_descends_from",
    "load_selected_working_model",
    "ServingRole",
    "TrainerRole",
    "publish_final_update",
    "require_scaled_trainer_config",
    "resolve_trainer_activation",
    "run_periodic_training",
    "resolve_role_state_root",
    "scope_split_consumer",
    "serving_main",
    "trainer_runtime_metrics",
    "trainer_main",
]
