"""Concrete real-service execution for one saved performance condition."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid5

from .performance_worker import PerformanceCondition
from .topology import ResourceRequest, ServiceCommand, Topology


@dataclass(frozen=True, slots=True)
class LiveConditionPlan:
    run_id: UUID
    topology: Topology
    commands: dict[str, ServiceCommand]
    resources: dict[str, ResourceRequest]
    state_root: Path
    serving_port: int


def build_live_condition_plan(
    condition: PerformanceCondition,
    *,
    run_id: UUID,
    state_root: str | Path,
    serving_port: int,
    python_executable: str,
    cpu_count: int,
) -> LiveConditionPlan:
    """Close a saved condition into real serving/trainer process commands."""
    topology = Topology.parse(condition.topology)
    root = Path(state_root)
    scripts = Path(python_executable).parent
    serving = ServiceCommand(
        role="serving",
        argv=(
            str(scripts / "babel-recommendation-server"),
            "--run-id",
            str(run_id),
            "--port",
            str(serving_port),
        ),
        version="babel-online:real-qwen-serving-v1",
    )
    if condition.training_enabled:
        trainer_argv = (
            str(scripts / "babel-online-trainer"),
            "--run-id",
            str(run_id),
            "--activation-enabled",
            str(condition.activation_enabled).lower(),
        )
        trainer_version = "babel-online:real-kafka-trainer-v1"
    else:
        trainer_argv = (
            python_executable,
            "-c",
            "import time; time.sleep(86400)",
        )
        trainer_version = "babel-online:idle-serving-only-control-v1"
    trainer = ServiceCommand(
        role="trainer",
        argv=trainer_argv,
        version=trainer_version,
        environment=(
            {"BABEL_TRAINER_READY_PATH": str(root / "trainer-ready.json")}
            if condition.training_enabled
            else {}
        ),
    )
    resources = {"serving": ResourceRequest(), "trainer": ResourceRequest()}
    if topology is Topology.SAME_HOST_ISOLATED:
        try:
            available_cpus = tuple(sorted(os.sched_getaffinity(0)))
        except (AttributeError, OSError):
            available_cpus = tuple(range(cpu_count))
        if len(available_cpus) < 2:
            raise RuntimeError("isolated topology requires at least two logical CPUs")
        midpoint = max(1, len(available_cpus) // 2)
        resources = {
            "serving": ResourceRequest(cpuAffinity=available_cpus[:midpoint]),
            "trainer": ResourceRequest(cpuAffinity=available_cpus[midpoint:]),
        }
    return LiveConditionPlan(
        run_id=run_id,
        topology=topology,
        commands={"serving": serving, "trainer": trainer},
        resources=resources,
        state_root=root,
        serving_port=serving_port,
    )


class LatencyTraceSink:
    """Collect client-boundary latency while accepting the workload trace protocol."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.feedback: list[Any] = []
        self.rolls: list[tuple[UUID, tuple[Any, ...]]] = []
        self._latencies_ns: list[int] = []

    def record_request(self, request: Any) -> None:
        self.requests.append(request)

    def record_feedback(self, event: Any) -> None:
        self.feedback.append(event)

    def record_rolls(self, traversal_session_id: UUID, rolls: Any) -> None:
        self.rolls.append((traversal_session_id, tuple(rolls)))

    def record_response(
        self, request: Any, response: Any, client_total_ns: int
    ) -> None:
        if (
            response.requestId != request.requestId
            or response.runId != request.runId
        ):
            raise ValueError("recommendation response identity differs")
        if client_total_ns <= 0:
            raise ValueError("recommendation client latency must be positive")
        self._latencies_ns.append(int(client_total_ns))

    @property
    def request_count(self) -> int:
        return len(self._latencies_ns)

    @property
    def p95_ms(self) -> float:
        if not self._latencies_ns:
            raise RuntimeError("condition did not complete any recommendation requests")
        ordered = sorted(self._latencies_ns)
        index = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index] / 1_000_000.0


class VerifiedLiveIdentityLedger:
    """Accept activation responses only after PostgreSQL verifies their lineage/state."""

    def __init__(
        self,
        *,
        database: Any,
        run_id: UUID,
        starting_model_id: UUID,
        embedding_space_id: UUID,
        initial_state: Any,
    ) -> None:
        self.database = database
        self.run_id = run_id
        self.starting_model_id = starting_model_id
        self.embedding_space_id = embedding_space_id
        self._observed: list[tuple[str, int, str, str]] = []
        if (
            initial_state.run_id != run_id
            or initial_state.embedding_space_id != embedding_space_id
        ):
            raise ValueError("initial serving state differs from the condition identity")
        initial = (
            initial_state.model_id,
            int(initial_state.model_version),
            str(initial_state.pgvector_snapshot_sha256),
            str(initial_state.backend_snapshot_sha256),
        )
        if not self._database_verifies(initial):
            raise ValueError("initial serving state is outside the verified model lineage")
        self._verified = {initial}

    def _database_verifies(self, value: tuple[UUID, int, str, str]) -> bool:
        model_id, model_version, pgvector_sha256, backend_sha256 = value
        return bool(
            self.database.verify_live_serving_identity(
                run_id=self.run_id,
                starting_model_id=self.starting_model_id,
                model_id=model_id,
                model_version=model_version,
                embedding_space_id=self.embedding_space_id,
                pgvector_sha256=pgvector_sha256,
                backend_sha256=backend_sha256,
            )
        )

    def validate(self, response: Any) -> None:
        if response.embeddingSpaceId != self.embedding_space_id:
            raise ValueError("live serving embedding space differs")
        identity = (
            response.modelId,
            int(response.modelVersion),
            str(response.pgvectorSnapshotSha256),
            str(response.backendSnapshotSha256),
        )
        if identity not in self._verified and not self._database_verifies(identity):
            raise ValueError("live serving state is outside the verified model lineage")
        self._verified.add(identity)
        value = (
            str(response.modelId),
            int(response.modelVersion),
            str(response.pgvectorSnapshotSha256),
            str(response.backendSnapshotSha256),
        )
        if value not in self._observed:
            self._observed.append(value)

    @property
    def observed(self) -> tuple[tuple[str, int, str, str], ...]:
        return tuple(self._observed)


def _write_candidate_universe(database: Any, run_id: UUID, path: Path) -> None:
    rows = database.created_babels(run_id)
    if len(rows) != 10_000:
        raise RuntimeError("formal condition does not have exactly 10,000 created Babels")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(
        (
            json.dumps(
                {
                    "babelId": str(row.babelId),
                    "runId": str(run_id),
                    "creatorId": str(row.creatorId),
                    "sourceArticleKey": row.sourceArticleKey,
                    "createdBySyntheticCreator": True,
                    "createdInRun": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    path.write_bytes(payload)


class _SameProcessHost:
    """Host real Qwen serving and the Kafka trainer as threads in this process."""

    def __init__(
        self,
        *,
        database: Any,
        run_id: UUID,
        condition: PerformanceCondition,
        serving_port: int,
        kafka_bootstrap_servers: str,
    ) -> None:
        from ..feedback.kafka import KafkaFeedbackConsumer
        from ..model.pgvector_index import PgvectorCandidateIndex
        from ..model.source_vector_cache import SourceVectorResolver
        from ..serving import ServingState, create_app
        from ..training.checkpoint import CheckpointIdentity
        from ..training.consumer import OnlineTrainer
        from ..training.working import NumpyWorkingModel
        from .services import (
            TrainerRole,
            _load_records,
            _load_role_context,
            run_periodic_training,
            scope_split_consumer,
        )
        from .worker import _UvicornThread

        (
            _loaded_database,
            self.config,
            loaded,
            _selected_artifact,
            self.registry,
            self.active,
            self.records,
            encoder,
        ) = _load_role_context(run_id, load_encoder=True)
        self.database = database
        self.run_id = run_id
        self.condition = condition
        self._load_records = _load_records
        self._PgvectorCandidateIndex = PgvectorCandidateIndex
        self.serving = ServingState(
            registry=self.registry,
            selected_model_id=loaded.manifest.modelId,
            materialized_state=self.active,
            candidate_index=PgvectorCandidateIndex(database.query_candidates),
            vector_records=self.records,
            qwen_encoder=encoder,
            scale_run=True,
        )
        resolver = SourceVectorResolver(
            encoder,
            load_active=database.load_active_source_vector,
            capacity=max(512, min(10_000, self.config.creatorCount * 10)),
        )
        self.server = _UvicornThread(
            create_app(self.serving, source_vector_resolver=resolver),
            host="127.0.0.1",
            port=serving_port,
        )
        self._trainer_stop = threading.Event()
        self._trainer_thread: threading.Thread | None = None
        self._trainer_error: BaseException | None = None
        self._last_published = self.active.model_version
        self.consumer = None
        self.trainer = None
        self.role = None
        if condition.training_enabled:
            frozen = {
                record.babel.babelId: __import__("numpy").asarray(
                    record.vector, dtype="<f4"
                )
                for record in self.records
            }
            numpy = __import__("numpy")
            model = NumpyWorkingModel(
                frozen,
                query_vector=numpy.mean(numpy.stack(list(frozen.values())), axis=0),
                learning_rate=0.05,
            )
            raw_consumer = KafkaFeedbackConsumer(
                kafka_bootstrap_servers,
                group_id=f"{self.config.kafkaGroup}.{run_id}",
            )
            self.consumer = scope_split_consumer(raw_consumer, run_id=run_id)
            self.trainer = OnlineTrainer(
                model=model,
                consumer=self.consumer,
                checkpoint_root=Path(self.config.stateRoot)
                / str(run_id)
                / "checkpoints",
                identity=CheckpointIdentity(
                    run_id=run_id,
                    model_id=loaded.manifest.modelId,
                    embedding_space_id=loaded.manifest.embeddingSpace.embeddingSpaceId,
                ),
            )
            self.trainer.training_version = self.active.model_version
            self.role = TrainerRole(
                trainer=self.trainer,
                parent=loaded.manifest,
                registry=self.registry,
                database=database,
                run_id=run_id,
                state_root=self.config.stateRoot,
                base_records=self.records,
            )
            self._run_periodic_training = run_periodic_training

    def _publish_and_activate(self) -> Any:
        from ..model.population import PopulationIdentity

        if self.role is None:
            raise RuntimeError("trainer role is unavailable")
        published = self.role.publish_update()
        child = published.child.descriptor.childManifest
        version = published.child.descriptor.modelVersion
        identity = PopulationIdentity.from_real_model(
            run_id=self.run_id,
            dataset_revision=self.config.datasetRevision,
            model=child,
            model_version=version,
        )
        records = self._load_records(self.database, identity)
        state_type = type(self.active)
        state = state_type(
            run_id=self.run_id,
            model_id=child.modelId,
            model_version=version,
            embedding_space_id=child.embeddingSpace.embeddingSpaceId,
            pgvector_snapshot_sha256=published.vector_snapshot_sha256,
            backend_snapshot_sha256=published.vector_snapshot_sha256,
        )

        def commit() -> None:
            self.database.activate_embedding_state(
                run_id=self.run_id,
                model_id=child.modelId,
                model_version=version,
                embedding_space_id=child.embeddingSpace.embeddingSpaceId,
                pgvector_sha256=published.vector_snapshot_sha256,
                backend_sha256=published.vector_snapshot_sha256,
            )

        self.serving.apply_sync(
            selected_model_id=child.modelId,
            materialized_state=state,
            candidate_index=self._PgvectorCandidateIndex(
                self.database.query_candidates
            ),
            vector_records=records,
            activation_commit=commit,
        )
        try:
            published.activation_request_path.unlink()
        except FileNotFoundError:
            pass
        self._last_published = version
        return published

    def start(self) -> None:
        self.server.start()
        if self.trainer is None:
            return

        def train() -> None:
            try:
                self._last_published = self._run_periodic_training(
                    self.trainer,
                    stop_requested=self._trainer_stop.is_set,
                    checkpoint_every_events=self.config.checkpointEveryEvents,
                    sync_every_steps=self.config.syncEverySteps,
                    activation_enabled=self.condition.activation_enabled,
                    publish_update=self._publish_and_activate,
                    initial_published_version=self.active.model_version,
                )
            except BaseException as error:
                self._trainer_error = error

        self._trainer_thread = threading.Thread(
            target=train, daemon=True, name=f"same-process-trainer-{self.run_id}"
        )
        self._trainer_thread.start()

    def stop(self) -> None:
        try:
            if self.trainer is not None and self.consumer is not None:
                self._trainer_stop.set()
                if self._trainer_thread is not None:
                    self._trainer_thread.join(timeout=30)
                    if self._trainer_thread.is_alive():
                        raise RuntimeError("same-process trainer did not stop")
                if self._trainer_error is not None:
                    raise RuntimeError("same-process trainer failed") from self._trainer_error
                self.trainer.drain_to(self.consumer.high_watermarks())
                self.trainer.checkpoint_and_commit()
                if (
                    self.condition.activation_enabled
                    and self.trainer.training_version > self._last_published
                ):
                    self._publish_and_activate()
                self.consumer.close()
        finally:
            self.server.stop()

    @property
    def services(self) -> dict[str, int]:
        values = {"serving": os.getpid()}
        if self.condition.training_enabled:
            values["trainer"] = os.getpid()
        return values

    @property
    def placement(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "requestedTopology": "same_process",
            "actualTopology": "same_process",
            "processes": [
                {"role": role, "pid": pid} for role, pid in self.services.items()
            ],
        }


class _SplitProcessHost:
    """Own one real serving/trainer process pair for a replay condition."""

    def __init__(
        self,
        plan: LiveConditionPlan,
        *,
        activation_dir: str | Path | None = None,
        starting_model_version: int = 0,
    ) -> None:
        from .topology import TopologySupervisor

        self.plan = plan
        self._supervisor = TopologySupervisor(state_root=plan.state_root)
        self.running = None
        self.activation_dir = None if activation_dir is None else Path(activation_dir)
        self.starting_model_version = int(starting_model_version)

    def _probe(self) -> int:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.plan.serving_port}/health", timeout=2
        ) as response:
            return int(response.status)

    def start(self) -> None:
        ready_path_value = self.plan.commands["trainer"].environment.get(
            "BABEL_TRAINER_READY_PATH"
        )
        ready_path = Path(ready_path_value) if ready_path_value is not None else None
        if ready_path is not None:
            ready_path.unlink(missing_ok=True)
        launched_at_ns = time.time_ns()
        self.running = self._supervisor.launch(
            topology=self.plan.topology,
            commands=self.plan.commands,
            resources=self.plan.resources,
            serving_probe=self._probe,
        )
        deadline = time.monotonic() + 180
        while True:
            if not self.running.process_alive("trainer"):
                raise RuntimeError("online trainer exited during startup")
            try:
                if self.running.serving_status() == 200:
                    if ready_path is None or self._valid_trainer_ready(
                        ready_path, launched_at_ns
                    ):
                        return
            except Exception:
                if not self.running.process_alive("serving"):
                    raise RuntimeError("recommendation serving exited during startup")
            if time.monotonic() >= deadline:
                raise TimeoutError("recommendation serving did not become healthy")
            time.sleep(0.25)

    def _valid_trainer_ready(self, path: Path, launched_at_ns: int) -> bool:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            return (
                document["schemaVersion"] == 1
                and UUID(str(document["runId"])) == self.plan.run_id
                and bool(str(document["consumerGroup"]).strip())
                and int(document["readyAtNs"]) >= launched_at_ns
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def stop(self, *, wait_for_activation: bool) -> None:
        if self.running is None:
            return
        try:
            self.running.graceful_stop_trainer(timeout_seconds=60)
            if wait_for_activation:
                if self.activation_dir is None:
                    raise RuntimeError("activation directory is required for full mode")
                deadline = time.monotonic() + 30
                while not self._has_new_activation_receipt():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("serving did not acknowledge model activation")
                    time.sleep(0.25)
        finally:
            self.running.stop_serving()

    def _has_new_activation_receipt(self) -> bool:
        if self.activation_dir is None:
            return False
        for path in self.activation_dir.glob("receipt-v*.json"):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                version = int(document["modelVersion"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if version > self.starting_model_version:
                return True
        return False

    @property
    def services(self) -> dict[str, int]:
        if self.running is None:
            return {}
        return {
            row.role: row.pid
            for row in self.running.manifest.processes
            if row.role in {"serving", "trainer"}
        }

    @property
    def placement(self) -> dict[str, Any]:
        if self.running is None:
            raise RuntimeError("topology has not started")
        return self.running.manifest.as_document()


class _OrderedFeedbackPublisher:
    """Publish completed concurrent requests in the frozen schedule order."""

    def __init__(
        self,
        feedback_by_request: dict[UUID, Any],
        request_order: dict[UUID, int],
        producer: Any,
        database: Any,
    ):
        self.feedback_by_request = feedback_by_request
        self.request_order = request_order
        self.producer = producer
        self.database = database
        self.published: set[UUID] = set()
        self._records: list[Any] = []
        self._pending: dict[int, Any] = {}
        self._next = 0
        self._expected: int | None = None
        self._aborted = False
        self._error: BaseException | None = None
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def callback(self, replay_row: Any, _response: Any, _measurement: Any) -> None:
        request_id = replay_row.request.requestId
        try:
            event = self.feedback_by_request[request_id]
        except KeyError as error:
            raise RuntimeError("frozen request has no paired feedback") from error
        if event.creatorId != replay_row.request.creatorId:
            raise ValueError("frozen feedback creator differs from replay request")
        index = self.request_order[request_id]
        with self._condition:
            if request_id in self.published or index in self._pending:
                raise RuntimeError("frozen feedback was requested more than once")
            self._pending[index] = event
            self._condition.notify_all()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._aborted
                        or self._next in self._pending
                        or self._expected == self._next
                    )
                    if self._aborted:
                        return
                    if self._expected == self._next:
                        return
                    event = self._pending.pop(self._next)
                    self._next += 1
                record = self.producer.publish(key=str(event.creatorId), event=event)
                if (
                    record.key != str(event.creatorId)
                    or record.event.requestId != event.requestId
                    or record.offset < 0
                    or record.partition < 0
                ):
                    raise ValueError("feedback acknowledgement differs from published event")
                self.database.persist_feedback_edges(event)
                self._records.append(record)
                self.published.add(event.requestId)
        except BaseException as error:
            self._error = error

    def finish(self, expected: int) -> None:
        with self._condition:
            self._expected = expected
            self._condition.notify_all()
        self._thread.join(timeout=60)
        if self._thread.is_alive():
            raise TimeoutError("ordered feedback publisher did not drain")
        if self._error is not None:
            raise RuntimeError("ordered feedback publication failed") from self._error
        if len(self.published) != expected:
            raise RuntimeError("ordered feedback publication was incomplete")
        expected_requests = tuple(
            request_id
            for request_id, _index in sorted(
                self.request_order.items(), key=lambda item: item[1]
            )
        )
        actual_requests = tuple(record.event.requestId for record in self._records)
        if len(self._records) != expected or actual_requests != expected_requests:
            raise RuntimeError("feedback acknowledgement order or count differs")
        offsets = [
            (record.topic, int(record.partition), int(record.offset))
            for record in self._records
        ]
        if len(set(offsets)) != len(offsets):
            raise RuntimeError("feedback acknowledgements contain duplicate offsets")

    def abort(self) -> None:
        with self._condition:
            self._aborted = True
            self._condition.notify_all()
        if self._thread.ident is None:
            return
        self._thread.join(timeout=5)

    @property
    def records(self) -> tuple[Any, ...]:
        return tuple(self._records)

    def offset_range_documents(self) -> list[dict[str, int | str]]:
        ranges: list[dict[str, int | str]] = []
        by_partition: dict[tuple[str, int], list[int]] = {}
        for record in self._records:
            by_partition.setdefault(
                (str(record.topic), int(record.partition)), []
            ).append(int(record.offset))
        for (topic, partition), offsets in sorted(by_partition.items()):
            ordered = sorted(offsets)
            start = previous = ordered[0]
            for offset in ordered[1:]:
                if offset == previous + 1:
                    previous = offset
                    continue
                ranges.append(
                    {
                        "topic": topic,
                        "partition": partition,
                        "startInclusive": start,
                        "endExclusive": previous + 1,
                    }
                )
                start = previous = offset
            ranges.append(
                {
                    "topic": topic,
                    "partition": partition,
                    "startInclusive": start,
                    "endExclusive": previous + 1,
                }
            )
        return ranges


@contextmanager
def _host_lifecycle(host: Any, stop: Callable[[Any], None]):
    """Always stop a host, including when startup only partially succeeds."""
    try:
        host.start()
        yield host
    finally:
        stop(host)


def _feedback_kafka_evidence(
    publisher: _OrderedFeedbackPublisher,
    *,
    database: Any,
    run_id: UUID,
    config: Any,
) -> dict[str, Any]:
    records = [
        {
            "topic": str(record.topic),
            "partition": int(record.partition),
            "offset": int(record.offset),
            "key": str(record.key),
            "eventId": str(record.event.eventId),
            "requestId": str(record.event.requestId),
        }
        for record in publisher.records
    ]
    final: dict[str, Any] = {"available": False}
    try:
        health = dict(database.performance_runtime_health(run_id))
    except (AttributeError, KeyError, RuntimeError):
        health = {}
    if "kafka_lag" in health:
        final = {
            "available": True,
            "kafkaLag": int(health["kafka_lag"]),
            "trainerVersion": int(health.get("trainer_version") or 0),
            "servingVersion": int(health.get("serving_version") or 0),
        }
    state_root = getattr(config, "stateRoot", None)
    if state_root:
        from ..training.checkpoint import load_latest_checkpoint

        checkpoint = load_latest_checkpoint(
            Path(state_root) / str(run_id) / "checkpoints"
        )
        if checkpoint is not None:
            offsets = [
                {
                    "topic": partition.topic,
                    "partition": partition.partition,
                    "nextOffset": int(offset),
                }
                for partition, offset in sorted(checkpoint.next_offsets.items())
            ]
            next_by_partition = {
                (row["topic"], row["partition"]): row["nextOffset"]
                for row in offsets
            }
            ranges = publisher.offset_range_documents()
            final.update(
                available=True,
                nextOffsets=offsets,
                offsetsCoverPublishedRanges=all(
                    next_by_partition.get((row["topic"], row["partition"]), -1)
                    >= row["endExclusive"]
                    for row in ranges
                ),
                checkpointManifestSha256=checkpoint.manifest_sha256,
            )
    return {
        "recordCount": len(records),
        "records": records,
        "offsetRanges": publisher.offset_range_documents(),
        "finalTrainerState": final,
    }


class RealWorkloadFreezer:
    """Capture one bounded real-Qwen reference workload before paired replay."""

    def __init__(
        self,
        *,
        database: Any,
        bundle: Any,
        output_root: str | Path,
        kafka_bootstrap_servers: str = "127.0.0.1:29092",
        serving_port: int = 8791,
        python_executable: str | None = None,
        host_factory: Callable[[LiveConditionPlan], Any] | None = None,
        coordinator_factory: Callable[..., Any] | None = None,
        producer_factory: Callable[[str], Any] | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.database = database
        self.bundle = bundle
        self.output_root = Path(output_root)
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.serving_port = serving_port
        self.python_executable = python_executable
        self.host_factory = host_factory
        self.coordinator_factory = coordinator_factory
        self.producer_factory = producer_factory
        self.client_factory = client_factory

    def __call__(
        self,
        trial: Any,
        _manifest: Any,
        _population_dir: Path,
        stop_requested: Callable[[], bool],
    ) -> Any:
        import sys

        from babel_benchmark.workload import (
            WorkloadTraceCollector,
            freeze_workload,
            load_frozen_workload,
        )
        from ..feedback.kafka import KafkaFeedbackProducer
        from ..simulation.client import RecommendationClient
        from ..simulation.scheduler import ScheduledWork, deterministic_schedule
        from .coordinator import StandaloneCoordinator
        from .performance_worker import FrozenWorkload

        output = self.output_root / str(trial.id) / "workload"
        if (output / "manifest.json").is_file():
            existing = load_frozen_workload(output)
            return FrozenWorkload(existing.path, existing.identity)

        reference_run_id = uuid5(trial.id, "reference-workload")
        reference = PerformanceCondition(
            id=uuid5(trial.id, "reference-condition"),
            condition_index=1,
            topology="same_host_split",
            training_enabled=False,
            activation_enabled=False,
            run_id=reference_run_id,
            status="running",
        )
        self.database.create_condition_run(trial, reference, reference_run_id)
        self.database.clone_performance_population(
            trial, reference, reference_run_id
        )
        persisted = self.database.load_run(reference_run_id)
        all_schedule = self.database.load_work_schedule(reference_run_id)
        request_slots = max(
            1,
            math.ceil((trial.warmup_seconds + trial.duration_seconds) * trial.target_rps),
        )
        root_limit = min(
            len(all_schedule),
            max(50, math.ceil(request_slots / trial.recommendation_start_probability)),
        )
        by_creator: dict[UUID, list[Any]] = {}
        for row in all_schedule:
            by_creator.setdefault(row.creator_id, []).append(row)
        selected: list[Any] = []
        position = 0
        creator_ids = sorted(by_creator, key=str)
        while len(selected) < root_limit:
            progressed = False
            for creator_id in creator_ids:
                rows = by_creator[creator_id]
                if position < len(rows):
                    selected.append(rows[position])
                    progressed = True
                    if len(selected) == root_limit:
                        break
            if not progressed:
                break
            position += 1
        schedule = deterministic_schedule(
            reference_run_id,
            [
                ScheduledWork(
                    creator_id=row.creator_id,
                    creator_event_number=index,
                    period=row.period,
                    source_article_key=row.source_article_key,
                    root_babel_id=row.root_babel_id,
                )
                for creator_id in creator_ids
                for index, row in enumerate(
                    value for value in selected if value.creator_id == creator_id
                )
            ],
        )
        # Re-sort creator-local rows into round-robin order while retaining their
        # newly contiguous event numbers.
        grouped = {creator_id: [] for creator_id in creator_ids}
        for row in schedule:
            grouped[row.creator_id].append(row)
        work = []
        for event_number in range(position + 1):
            for creator_id in creator_ids:
                rows = grouped[creator_id]
                if event_number < len(rows):
                    row = rows[event_number]
                    work.append(
                        ScheduledWork(
                            creator_id=creator_id,
                            creator_event_number=event_number,
                            period=row.period,
                            source_article_key=row.source_article_key,
                            root_babel_id=row.root_babel_id,
                        )
                    )
        schedule = deterministic_schedule(reference_run_id, work)
        hidden: dict[str, set[tuple[str, str]]] = {}
        for period in persisted.config.environmentSequence:
            rows = self.bundle.configs[f"simulator_{period.replace('-', '_')}_hidden"]
            edges = set()
            for row in rows:
                if row.get("record_type") == "pagelink":
                    payload = json.loads(row["payload_json"])
                    edges.add(
                        (payload["source_article_key"], payload["target_article_key"])
                    )
            hidden[period] = edges
        babels = {
            row.babelId: row for row in self.database.created_babels(reference_run_id)
        }
        collector = WorkloadTraceCollector(schedule=schedule, target_rps=trial.target_rps)
        plan = build_live_condition_plan(
            reference,
            run_id=reference_run_id,
            state_root=self.output_root / str(trial.id) / "reference-topology",
            serving_port=self.serving_port,
            python_executable=self.python_executable or sys.executable,
            cpu_count=os.cpu_count() or 1,
        )
        host = (
            self.host_factory(plan)
            if self.host_factory is not None
            else _SplitProcessHost(plan)
        )
        class StopView:
            def is_set(self) -> bool:
                return bool(stop_requested())

        producer = None
        try:
            host.start()
            coordinator_type = self.coordinator_factory or StandaloneCoordinator
            producer = (
                self.producer_factory(self.kafka_bootstrap_servers)
                if self.producer_factory is not None
                else KafkaFeedbackProducer(self.kafka_bootstrap_servers)
            )
            endpoint = f"http://127.0.0.1:{self.serving_port}"
            coordinator = coordinator_type(
                config=persisted.config,
                database=self.database,
                schedule=schedule,
                babels=babels,
                hidden_edges=hidden,
                producer=producer,
                client_factory=lambda: (
                    self.client_factory(endpoint)
                    if self.client_factory is not None
                    else RecommendationClient(endpoint)
                ),
                stop_event=StopView(),
                trace_sink=collector,
            )
            coordinator.run()
            if stop_requested():
                raise InterruptedError(
                    "reference capture stopped at a complete traversal boundary"
                )
            bundle = freeze_workload(collector, output)
            self.database.transition(reference_run_id, "completed")
        except InterruptedError:
            self.database.transition(reference_run_id, "interrupted")
            raise
        except BaseException as error:
            self.database.transition(reference_run_id, "failed", failure=str(error)[:1000])
            raise
        finally:
            try:
                if producer is not None and hasattr(producer, "close"):
                    producer.close()
            finally:
                host.stop(wait_for_activation=False)
        return FrozenWorkload(bundle.path, bundle.identity)


def execute_live_condition(
    *,
    database: Any,
    trial: Any,
    condition: PerformanceCondition,
    run_id: UUID,
    frozen_workload_path: str | Path,
    evidence_path: str | Path,
    serving_port: int = 8791,
    kafka_bootstrap_servers: str = "127.0.0.1:29092",
    python_executable: str | None = None,
    host_factory: Callable[..., Any] | None = None,
    transport_factory: Callable[..., Any] | None = None,
    producer_factory: Callable[..., Any] | None = None,
    concurrent_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute one formal condition through real HTTP, Kafka and model roles.

    Factory seams exist solely so the orchestration contract can be tested without
    starting Qwen or Kafka.  The CLI calls this function without overriding them.
    """
    import sys

    from babel_benchmark.contracts import (
        BenchmarkManifestV2,
        ConditionIdentityV2,
        ConditionSpecV2,
    )
    from babel_benchmark.replay import CandidateUniverse, ReplayCorpus
    from babel_benchmark.resources import (
        PeriodicResourceCollector,
        default_resource_sampler,
    )
    from babel_benchmark.runner import AsyncHttpxTransport, run_concurrent_condition
    from babel_benchmark.workload import (
        load_frozen_workload,
        load_workload_documents,
        materialize_condition_workload,
    )
    from ..contracts import FeedbackEventV2
    from ..feedback.kafka import KafkaFeedbackProducer

    condition_directory = Path(evidence_path).parent
    condition_directory.mkdir(parents=True, exist_ok=True)
    frozen = load_frozen_workload(Path(frozen_workload_path))
    rebound_path = condition_directory / "workload"
    if rebound_path.exists():
        documents = load_workload_documents(rebound_path)
    else:
        materialize_condition_workload(
            frozen, run_id=run_id, output_path=rebound_path
        )
        documents = load_workload_documents(rebound_path)
    request_path = rebound_path / "requests.template.jsonl"
    replay = ReplayCorpus.from_jsonl(request_path)
    candidate_path = condition_directory / "candidate-universe.jsonl"
    _write_candidate_universe(database, run_id, candidate_path)
    universe = CandidateUniverse.from_jsonl(candidate_path)
    active = database.load_active_embedding_state(run_id)
    persisted = database.load_run(run_id)
    config = persisted.config
    if config.startingModelId != trial.starting_model_id:
        raise ValueError("condition run starting model differs from saved trial")
    feedback_events = tuple(
        FeedbackEventV2.model_validate(row)
        for row in documents["feedback.template.jsonl"]
    )
    feedback_by_request = {row.requestId: row for row in feedback_events}
    request_ids = {row.request.requestId for row in replay.rows}
    if len(feedback_by_request) != len(feedback_events) or set(feedback_by_request) != request_ids:
        raise ValueError("frozen requests and feedback are not one-to-one")

    identity = ConditionIdentityV2(
        topology=condition.topology,
        trainingEnabled=condition.training_enabled,
        activationEnabled=condition.activation_enabled,
        retrievalBackend="pgvector",
    )
    spec = ConditionSpecV2(
        identity=identity,
        requestCorpusSha256=replay.sha256,
        scheduleOffsetsNs=tuple(row.scheduleOffsetNs for row in replay.rows),
        expectedModelId=active.model_id,
        expectedModelVersion=active.model_version,
        expectedEmbeddingSpaceId=active.embedding_space_id,
        expectedDatasetSnapshotSha256=universe.sha256,
        expectedPgvectorSnapshotSha256=active.pgvector_snapshot_sha256,
        expectedBackendSnapshotSha256=active.backend_snapshot_sha256,
        activationValidation=(
            "verified_live_ledger" if condition.activation_enabled else "pinned_targets"
        ),
    )
    warmup_count = min(
        max(0, round(float(getattr(trial, "warmup_seconds", 0)) * trial.target_rps)),
        len(replay.rows) - 1,
    )
    manifest = BenchmarkManifestV2(
        schemaVersion=2,
        benchmarkRunId=run_id,
        endpoint=f"http://127.0.0.1:{serving_port}",
        requestPath="/api/v2/recommendations",
        requestCorpusPath=str(request_path),
        requestCorpusSha256=replay.sha256,
        candidateUniversePath=str(candidate_path),
        candidateUniverseSha256=universe.sha256,
        scheduleOffsetsNs=spec.scheduleOffsetsNs,
        warmupCount=warmup_count,
        timeoutSeconds=30.0,
        scheduleMode="open_loop",
        maxInFlight=trial.concurrent_users,
        conditions=(spec,),
    )

    endpoint = f"http://127.0.0.1:{serving_port}"
    if host_factory is not None:
        host = host_factory(condition=condition, run_id=run_id, serving_port=serving_port)
    elif condition.topology == "same_process":
        host = _SameProcessHost(
            database=database,
            run_id=run_id,
            condition=condition,
            serving_port=serving_port,
            kafka_bootstrap_servers=kafka_bootstrap_servers,
        )
    else:
        plan = build_live_condition_plan(
            condition,
            run_id=run_id,
            state_root=condition_directory / "topology",
            serving_port=serving_port,
            python_executable=python_executable or sys.executable,
            cpu_count=os.cpu_count() or 1,
        )
        host = _SplitProcessHost(
            plan,
            activation_dir=Path(config.stateRoot) / str(run_id) / "activations",
            starting_model_version=active.model_version,
        )
    producer = (
        producer_factory(kafka_bootstrap_servers)
        if producer_factory is not None
        else KafkaFeedbackProducer(kafka_bootstrap_servers)
    )
    transport = (
        transport_factory(endpoint, trial.concurrent_users)
        if transport_factory is not None
        else AsyncHttpxTransport(endpoint, max_connections=trial.concurrent_users)
    )
    runner = concurrent_runner or run_concurrent_condition
    ledger = (
        VerifiedLiveIdentityLedger(
            database=database,
            run_id=run_id,
            starting_model_id=trial.starting_model_id,
            embedding_space_id=active.embedding_space_id,
            initial_state=active,
        )
        if condition.activation_enabled
        else None
    )
    feedback_publisher = _OrderedFeedbackPublisher(
        feedback_by_request,
        {row.request.requestId: index for index, row in enumerate(replay.rows)},
        producer,
        database,
    )
    last_health = {"time": time.monotonic(), "steps": 0}

    def health() -> dict[str, int | float | None]:
        values = dict(database.performance_runtime_health(run_id))
        now = time.monotonic()
        steps = int(values.get("trainer_version") or 0)
        values["training_step_rate"] = max(0, steps - last_health["steps"]) / max(
            1e-9, now - last_health["time"]
        )
        last_health.update(time=now, steps=steps)
        return values

    resource_collector = PeriodicResourceCollector(
        default_resource_sampler(run_id, identity.stable_key),
        services=host.services,
        interval_seconds=1.0,
        health_provider=health,
    )
    result = None
    try:
        with _host_lifecycle(
            host,
            lambda value: (
                value.stop(wait_for_activation=condition.activation_enabled)
                if isinstance(value, _SplitProcessHost)
                else value.stop()
            ),
        ):
            feedback_publisher.start()
            result = asyncio.run(
                runner(
                    manifest,
                    spec,
                    replay,
                    universe,
                    transport=transport,
                    schedule_mode="open_loop",
                    max_in_flight=trial.concurrent_users,
                    resource_collector=resource_collector,
                    live_identity_validator=None if ledger is None else ledger.validate,
                    success_callback=feedback_publisher.callback,
                )
            )
            if any(row.outcome != "success" for row in result.measurements):
                feedback_publisher.abort()
            else:
                feedback_publisher.finish(len(replay.rows))
    except BaseException:
        feedback_publisher.abort()
        raise
    finally:
        producer.close()
    final_active = database.load_active_embedding_state(run_id)
    if condition.activation_enabled:
        if (
            final_active.model_id == active.model_id
            or final_active.model_version <= active.model_version
            or not database.verify_live_serving_identity(
                run_id=run_id,
                starting_model_id=trial.starting_model_id,
                model_id=final_active.model_id,
                model_version=final_active.model_version,
                embedding_space_id=final_active.embedding_space_id,
                pgvector_sha256=final_active.pgvector_snapshot_sha256,
                backend_sha256=final_active.backend_snapshot_sha256,
            )
        ):
            raise RuntimeError("training-and-activation did not activate a verified child")
    elif final_active != active:
        raise RuntimeError("non-activation condition changed the serving model")
    if result is None:
        raise RuntimeError("condition replay did not produce evidence")
    measured = [
        row for row in result.measurements if not row.isWarmup and row.outcome == "success"
    ]
    failures = [row for row in result.measurements if row.outcome != "success"]
    if failures or not measured:
        raise RuntimeError(
            f"formal condition had {len(failures)} failed requests and {len(measured)} successes"
        )
    if len(feedback_publisher.published) != len(result.measurements):
        raise RuntimeError("not every successful request published its frozen feedback")
    ordered_ns = sorted(row.clientTotalNs for row in measured)
    p95_ns = ordered_ns[max(0, math.ceil(0.95 * len(ordered_ns)) - 1)]
    raw = {
        "conditionIdentity": identity.model_dump(mode="json"),
        "workloadIdentity": list(frozen.identity),
        "warmupCount": warmup_count,
        "measurements": [row.model_dump(mode="json") for row in result.measurements],
        "resources": [row.model_dump(mode="json") for row in result.resources],
        "placement": host.placement,
        "feedbackKafka": _feedback_kafka_evidence(
            feedback_publisher,
            database=database,
            run_id=run_id,
            config=config,
        ),
        "observedActivationTargets": [] if ledger is None else list(ledger.observed),
        "finalServingIdentity": {
            "modelId": str(final_active.model_id),
            "modelVersion": final_active.model_version,
            "embeddingSpaceId": str(final_active.embedding_space_id),
            "pgvectorSnapshotSha256": final_active.pgvector_snapshot_sha256,
            "backendSnapshotSha256": final_active.backend_snapshot_sha256,
        },
    }
    document = {
        "conditionId": str(condition.id),
        "runId": str(run_id),
        "requestCount": len(measured),
        "p95Ms": p95_ns / 1_000_000.0,
        "rawEvidence": raw,
    }
    Path(evidence_path).write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return document


__all__ = [
    "LatencyTraceSink",
    "LiveConditionPlan",
    "RealWorkloadFreezer",
    "VerifiedLiveIdentityLedger",
    "build_live_condition_plan",
    "execute_live_condition",
]
