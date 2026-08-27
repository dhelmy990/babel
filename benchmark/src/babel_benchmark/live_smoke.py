"""Executable, fixture-scale live matrix kept separate from formal population."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import httpx
import numpy as np

from .matrix import SmokeCondition, SmokeConditionResult, TinySmokePlan, tiny_smoke_plan


@dataclass(frozen=True, slots=True)
class ConditionScope:
    state_root: Path
    port: int
    consumer_group: str


@dataclass(frozen=True, slots=True)
class LiveSmokeSettings:
    fixture_root: Path
    state_root: Path
    kafka_bootstrap_servers: str = "127.0.0.1:29092"
    request_count: int = 2
    timeout_seconds: float = 180.0
    port_base: int = 18_890

    def __post_init__(self) -> None:
        if not 1 <= self.request_count <= 20:
            raise ValueError("live smoke request count must be between 1 and 20")
        if not 0 < self.timeout_seconds <= 180:
            raise ValueError("live smoke timeout must be at most 180 seconds")
        if self.port_base < 18_000 or self.port_base + 8 > 65_535:
            raise ValueError("live smoke requires an alternate nine-port range")
        if not self.kafka_bootstrap_servers:
            raise ValueError("Kafka bootstrap servers are required")

    def scope(self, condition: SmokeCondition) -> ConditionScope:
        plan = tiny_smoke_plan(timeout_seconds=self.timeout_seconds)
        index = next(
            number
            for number, row in enumerate(plan.conditions)
            if row.condition_id == condition.condition_id
        )
        slug = condition.condition_id.replace(".", "-")
        namespace = hashlib.sha256(str(self.state_root).encode("utf-8")).hexdigest()[:12]
        return ConditionScope(
            state_root=self.state_root / slug,
            port=self.port_base + index,
            consumer_group=f"babel.live-smoke.{namespace}.{slug}",
        )


def build_live_smoke_plan(settings: LiveSmokeSettings) -> TinySmokePlan:
    base = tiny_smoke_plan(timeout_seconds=settings.timeout_seconds)
    return replace(
        base,
        conditions=tuple(
            replace(row, request_limit=settings.request_count) for row in base.conditions
        ),
    )


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    partial.replace(path)


def _append_evidence(path: Path, *, kind: str, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "kind": kind,
                    "formalPerformanceClaim": False,
                    **values,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


def _record_activation(
    path: Path, *, health: dict[str, object], activation: dict[str, object]
) -> None:
    if int(activation["modelVersion"]) != int(health["modelVersion"]):
        raise RuntimeError("activation receipt and serving health versions differ")
    _append_evidence(path, kind="activation", **activation)


def _fixture_state(fixture_root: Path):
    from babel_online.contracts import ModelManifestV1, RecommendationRequestV1
    from babel_online.model import InMemoryCreatedBabelIndex, MaterializedServingState
    from babel_online.model.item_tower import ItemTower
    from babel_online.model.registry import ModelRegistry
    from babel_online.observable import CreatedBabel, VectorRecord
    from babel_online.serving import ServingState, create_app

    model = ModelManifestV1.model_validate_json(
        (fixture_root / "original-model.json").read_text(encoding="utf-8")
    )
    babels = [
        CreatedBabel.model_validate_json(line)
        for line in (fixture_root / "observable/created-babels.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    tower = ItemTower(model.embeddingSpace)
    records = [
        VectorRecord(
            babel=babel,
            catalogContentHash=f"{index + 1:x}" * 64,
            embeddingSpaceId=model.embeddingSpace.embeddingSpaceId,
            servingModelId=model.modelId,
            materializedModelVersion=0,
            vector=tuple(float(value) for value in tower.encode_article(babel.title, babel.text)),
        )
        for index, babel in enumerate(babels)
    ]
    run_id = babels[0].runId
    materialized = MaterializedServingState(
        run_id=run_id,
        model_id=model.modelId,
        model_version=0,
        embedding_space_id=model.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    registry = ModelRegistry()
    registry.register_original(model)
    index = InMemoryCreatedBabelIndex(records)
    state = ServingState(
        registry=registry,
        selected_model_id=model.modelId,
        materialized_state=materialized,
        candidate_index=index,
        vector_records=records,
    )
    request = RecommendationRequestV1.model_validate_json(
        (fixture_root / "observable/request.json").read_text(encoding="utf-8")
    )
    return create_app(state), state, index, model, records, request


def _vector_state_sha(vectors: dict[UUID, np.ndarray]) -> str:
    payload = b"".join(
        item_id.bytes + np.asarray(vectors[item_id], dtype="<f4").tobytes()
        for item_id in sorted(vectors, key=lambda value: value.hex)
    )
    return hashlib.sha256(payload).hexdigest()


def _materialize_trainer_records(records, vectors, *, version: int):
    from babel_online.observable import VectorRecord

    expected = {row.babel.babelId for row in records}
    if set(vectors) != expected:
        raise ValueError("trainer vector identities differ from serving population")
    before = {
        row.babel.babelId: np.asarray(row.vector, dtype="<f4") for row in records
    }
    checked = {
        item_id: np.asarray(vectors[item_id], dtype="<f4").reshape(-1)
        for item_id in expected
    }
    if any(value.shape != (100,) or not np.isfinite(value).all() for value in checked.values()):
        raise ValueError("trainer activation vectors must be finite 100d values")
    changed = sum(not np.array_equal(before[item_id], checked[item_id]) for item_id in expected)
    if changed <= 0:
        raise RuntimeError("trainer activation did not change any serving vector")
    updated = [
        VectorRecord(
            babel=row.babel,
            catalogContentHash=row.catalogContentHash,
            embeddingSpaceId=row.embeddingSpaceId,
            servingModelId=row.servingModelId,
            materializedModelVersion=version,
            vector=tuple(float(value) for value in checked[row.babel.babelId]),
        )
        for row in records
    ]
    return updated, {
        "beforeVectorStateSha256": _vector_state_sha(before),
        "afterVectorStateSha256": _vector_state_sha(checked),
        "changedVectorCount": changed,
    }


class FixtureLiveSmoke:
    def __init__(self, settings: LiveSmokeSettings) -> None:
        self.settings = settings

    def execute(
        self,
        condition: SmokeCondition,
        request_limit: int,
        timeout_seconds: float,
        cancel: Any,
    ) -> SmokeConditionResult:
        if request_limit > self.settings.request_count:
            raise ValueError("condition escaped configured live-smoke request bound")
        if condition.topology != "same_process":
            return self._execute_split(condition, request_limit, timeout_seconds, cancel)
        return self._execute_same_process(condition, request_limit, timeout_seconds, cancel)

    def _execute_same_process(
        self,
        condition: SmokeCondition,
        request_limit: int,
        timeout_seconds: float,
        cancel: Any,
    ) -> SmokeConditionResult:
        from babel_online.runtime.worker import _UvicornThread

        scope = self.settings.scope(condition)
        scope.state_root.mkdir(parents=True, exist_ok=True)
        evidence_path = scope.state_root / "raw-evidence.jsonl"
        evidence_path.unlink(missing_ok=True)
        app, state, index, model, records, request = _fixture_state(
            self.settings.fixture_root
        )
        bus, producer, consumer = self._transport(scope)
        trainer = self._trainer(consumer, records, scope)
        server = _UvicornThread(app, host="127.0.0.1", port=scope.port)
        trainer_enabled = condition.load_mode != "serving_only"
        activation_enabled = condition.load_mode == "training_and_activation"
        deadline = time.monotonic() + min(timeout_seconds, self.settings.timeout_seconds)
        edges = 0
        trainer_stop = threading.Event()
        trainer_errors: list[BaseException] = []

        def train() -> None:
            try:
                trainer.run_until_stopped(
                    stop_requested=trainer_stop.is_set,
                    checkpoint_every_events=1,
                    poll_timeout_seconds=0.01,
                )
            except BaseException as error:
                trainer_errors.append(error)
                trainer_stop.set()

        thread = threading.Thread(target=train, daemon=True)
        server.start()
        if trainer_enabled:
            thread.start()
        startup_verified = self._health(scope.port)["status"] == "ok"
        _append_evidence(
            evidence_path,
            kind="topology",
            requestedTopology=condition.topology,
            actualTopology="same_process",
            servingPid=None,
            trainerPid=None,
        )
        try:
            edges, latencies_ns = self._issue_requests(
                condition,
                request_limit,
                request,
                producer,
                scope.port,
                evidence_path,
                deadline,
                cancel,
            )
            if trainer_enabled:
                self._wait_for_training(trainer, request_limit, deadline, trainer_errors)
                checkpoint = trainer.checkpoint_and_commit()
                _append_evidence(
                    evidence_path,
                    kind="training",
                    processedEvents=trainer.processed_events,
                    optimizerSteps=trainer.global_step,
                    checkpointPath=str(checkpoint),
                )
                if activation_enabled:
                    captured = trainer.capture_sync_state()
                    version = captured.version
                    updated, activation = _materialize_trainer_records(
                        records, captured.materialized_vectors, version=version
                    )
                    from babel_online.model import InMemoryCreatedBabelIndex

                    updated_index = InMemoryCreatedBabelIndex(updated)
                    materialized = replace(
                        state.snapshot().materialized_state,
                        model_version=version,
                        pgvector_snapshot_sha256=activation[
                            "afterVectorStateSha256"
                        ],
                        backend_snapshot_sha256=activation[
                            "afterVectorStateSha256"
                        ],
                    )
                    state.apply_sync(
                        selected_model_id=model.modelId,
                        materialized_state=materialized,
                        candidate_index=updated_index,
                        vector_records=updated,
                    )
                    _append_evidence(
                        evidence_path,
                        kind="activation",
                        modelVersion=version,
                        **activation,
                    )
            else:
                _append_evidence(
                    evidence_path,
                    kind="training",
                    processedEvents=0,
                    optimizerSteps=0,
                    disabled=True,
                )
            trainer_stop.set()
            if thread.is_alive():
                thread.join(timeout=2)
            serving_after_trainer_stop = self._health(scope.port)["status"] == "ok"
            if not serving_after_trainer_stop:
                raise RuntimeError("serving failed after trainer shutdown")
        finally:
            trainer_stop.set()
            if thread.is_alive():
                thread.join(timeout=2)
            server.stop()
            producer.close()
            consumer.close()
            if bus is not None:
                bus.close()
        cleanup_verified = not server.thread.is_alive() and not thread.is_alive()
        return SmokeConditionResult(
            condition_id=condition.condition_id,
            request_count=request_limit,
            client_p95_ms=self._p95_ms(latencies_ns),
            startup_verified=startup_verified,
            cleanup_verified=cleanup_verified,
            edges_observed=edges,
            progress_observed=request_limit > 0,
            raw_results_path=str(evidence_path),
            trainer_failure_status="not_applicable",
        )

    def _execute_split(self, condition, request_limit, timeout_seconds, cancel):
        # The independently placed role implementation is intentionally below;
        # keeping the dispatch explicit prevents a split condition being
        # mislabeled as same-process evidence.
        return self._execute_independent_roles(
            condition, request_limit, timeout_seconds, cancel
        )

    def _transport(self, scope: ConditionScope):
        if self.settings.kafka_bootstrap_servers == "memory://":
            from babel_online.feedback import InMemoryFeedbackBus

            bus = InMemoryFeedbackBus()
            return bus, bus, bus.consumer(group_id=scope.consumer_group)
        from babel_online.feedback import KafkaFeedbackConsumer, KafkaFeedbackProducer

        consumer = KafkaFeedbackConsumer(
            self.settings.kafka_bootstrap_servers, group_id=scope.consumer_group
        )
        consumer.position()
        consumer.seek(consumer.high_watermarks())
        return (
            None,
            KafkaFeedbackProducer(self.settings.kafka_bootstrap_servers),
            consumer,
        )

    @staticmethod
    def _trainer(consumer, records, scope):
        from babel_online.training import NumpyWorkingModel, OnlineTrainer

        vectors = {
            row.babel.babelId: np.asarray(row.vector, dtype=np.float32) for row in records
        }
        return OnlineTrainer(
            model=NumpyWorkingModel(
                vectors, query_vector=next(iter(vectors.values())), learning_rate=0.05
            ),
            consumer=consumer,
            checkpoint_root=scope.state_root / "checkpoints",
        )

    @staticmethod
    def _health(port: int) -> dict[str, object]:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _wait_for_training(trainer, count, deadline, errors) -> None:
        while trainer.processed_events < count:
            if errors:
                raise RuntimeError("live-smoke trainer failed") from errors[0]
            if time.monotonic() >= deadline:
                raise TimeoutError("live-smoke trainer did not consume feedback")
            time.sleep(0.01)

    @staticmethod
    def _issue_requests(
        condition,
        request_limit,
        template,
        producer,
        port,
        evidence_path,
        deadline,
        cancel,
    ) -> tuple[int, list[int]]:
        from babel_online.contracts import CandidateActionV1, FeedbackEventV1
        from babel_online.simulation.client import RecommendationClient

        client = RecommendationClient(f"http://127.0.0.1:{port}")
        edges = 0
        latencies_ns: list[int] = []
        try:
            for sequence in range(request_limit):
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("live-smoke condition was cancelled")
                request_id = uuid5(
                    template.runId,
                    f"live-smoke:{condition.condition_id}:{sequence}",
                )
                request = template.model_copy(update={"requestId": request_id})
                started = time.perf_counter_ns()
                response = client.recommend(request)
                client_ns = time.perf_counter_ns() - started
                latencies_ns.append(client_ns)
                actions = [
                    CandidateActionV1(
                        babelId=row.babelId,
                        sourceArticleKey=row.sourceArticleKey,
                        rank=row.rank,
                        modelScore=row.modelScore,
                        action="include" if row.rank == 1 else "exclude",
                    )
                    for row in response.candidates
                ]
                edges += sum(row.action == "include" for row in actions)
                event = FeedbackEventV1(
                    schemaVersion=1,
                    eventId=uuid5(
                        template.runId,
                        f"live-smoke-feedback:{condition.condition_id}:{sequence}",
                    ),
                    requestId=request_id,
                    runId=template.runId,
                    creatorId=template.creatorId,
                    newBabelId=template.newBabelId,
                    newSourceArticleKey=template.newSourceArticleKey,
                    modelId=response.modelId,
                    modelVersion=response.modelVersion,
                    embeddingSpaceId=response.embeddingSpaceId,
                    retrievalBackend=response.retrievalBackend,
                    candidateActions=actions,
                    occurredAtNs=time.time_ns(),
                )
                record = producer.publish(key=str(template.creatorId), event=event)
                _append_evidence(
                    evidence_path,
                    kind="recommendation",
                    requestId=str(request_id),
                    clientTotalNs=client_ns,
                    serverTotalNs=response.timingsNs["serverTotal"],
                    modelVersion=response.modelVersion,
                )
                _append_evidence(
                    evidence_path,
                    kind="feedback",
                    eventId=str(event.eventId),
                    kafkaOffset=record.offset,
                    acceptedEdges=sum(row.action == "include" for row in actions),
                )
        finally:
            client.close()
        return edges, latencies_ns

    @staticmethod
    def _p95_ms(values: list[int]) -> float:
        if not values:
            raise RuntimeError("live smoke did not measure client latency")
        ordered = sorted(values)
        index = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index] / 1_000_000.0

    def _execute_independent_roles(self, condition, request_limit, timeout_seconds, cancel):
        if self.settings.kafka_bootstrap_servers == "memory://":
            raise RuntimeError("split live smoke requires real Kafka")

        from babel_online.feedback import KafkaFeedbackProducer
        from babel_online.runtime.topology import (
            ResourceRequest,
            ServiceCommand,
            TopologySupervisor,
        )

        scope = self.settings.scope(condition)
        scope.state_root.mkdir(parents=True, exist_ok=True)
        evidence_path = scope.state_root / "raw-evidence.jsonl"
        evidence_path.unlink(missing_ok=True)
        ready_path = scope.state_root / "trainer-ready.json"
        status_path = scope.state_root / "trainer-status.json"
        activation_dir = scope.state_root / "activation"
        for path in (ready_path, status_path):
            path.unlink(missing_ok=True)
        activation_dir.mkdir(parents=True, exist_ok=True)
        for path in activation_dir.glob("*.json"):
            path.unlink()

        common = (
            "--fixture-root",
            str(self.settings.fixture_root),
            "--state-root",
            str(scope.state_root),
            "--kafka-bootstrap",
            self.settings.kafka_bootstrap_servers,
            "--consumer-group",
            scope.consumer_group,
            "--load-mode",
            condition.load_mode,
        )
        commands = {
            "serving": ServiceCommand(
                role="serving",
                argv=(
                    sys.executable,
                    "-m",
                    "babel_benchmark.live_smoke",
                    "_serve",
                    *common,
                    "--port",
                    str(scope.port),
                ),
                version="babel-live-smoke-serving:0.1.0",
            ),
            "trainer": ServiceCommand(
                role="trainer",
                argv=(
                    sys.executable,
                    "-m",
                    "babel_benchmark.live_smoke",
                    "_train",
                    *common,
                ),
                version="babel-live-smoke-trainer:0.1.0",
            ),
        }
        resources = None
        if condition.topology == "same_host_isolated":
            available = sorted(os.sched_getaffinity(0))
            if len(available) < 2:
                raise RuntimeError("isolated live smoke requires two available CPUs")
            serving_cpu = available[0]
            trainer_cpu = available[1]
            resources = {
                "serving": ResourceRequest(cpuAffinity=(serving_cpu,)),
                "trainer": ResourceRequest(cpuAffinity=(trainer_cpu,)),
            }
        supervisor = TopologySupervisor(state_root=scope.state_root / "placement")
        running = supervisor.launch(
            topology=condition.topology,
            commands=commands,
            resources=resources,
            serving_probe=lambda: httpx.get(
                f"http://127.0.0.1:{scope.port}/health", timeout=2
            ).status_code,
        )
        deadline = time.monotonic() + min(timeout_seconds, self.settings.timeout_seconds)
        producer = KafkaFeedbackProducer(self.settings.kafka_bootstrap_servers)
        startup_verified = False
        trainer_failure_available = False
        edges = 0
        try:
            self._wait_for_role_start(scope.port, ready_path, deadline, cancel)
            startup_verified = running.serving_status() == 200
            _append_evidence(
                evidence_path,
                kind="topology",
                requestedTopology=condition.topology,
                actualTopology=running.manifest.actualTopology,
                servingPid=running.manifest.process("serving").pid,
                trainerPid=running.manifest.process("trainer").pid,
                placementManifest=str(running.manifest.path),
            )
            _app, _state, _index, _model, _records, request = _fixture_state(
                self.settings.fixture_root
            )
            edges, latencies_ns = self._issue_requests(
                condition,
                request_limit,
                request,
                producer,
                scope.port,
                evidence_path,
                deadline,
                cancel,
            )
            if condition.load_mode != "serving_only":
                status = self._wait_for_status(status_path, request_limit, deadline, cancel)
                _append_evidence(evidence_path, kind="training", **status)
                if condition.load_mode == "training_and_activation":
                    health, activation = self._wait_for_activation(
                        scope.port, activation_dir, deadline, cancel
                    )
                    _record_activation(
                        evidence_path, health=health, activation=activation
                    )
            else:
                _append_evidence(
                    evidence_path,
                    kind="training",
                    processedEvents=0,
                    optimizerSteps=0,
                    disabled=True,
                )
            if condition.load_mode != "serving_only":
                if not running.process_alive("trainer"):
                    raise RuntimeError("trainer was not alive before failure injection")
                running.kill_trainer()
                if running.serving_status() != 200:
                    raise RuntimeError("serving failed after trainer kill")
                trainer_failure_status = "verified"
            else:
                trainer_failure_status = "not_applicable"
        finally:
            producer.close()
            running.stop()
        cleanup_verified = not running.process_alive("serving") and not running.process_alive(
            "trainer"
        )
        return SmokeConditionResult(
            condition_id=condition.condition_id,
            request_count=request_limit,
            client_p95_ms=self._p95_ms(latencies_ns),
            startup_verified=startup_verified,
            cleanup_verified=cleanup_verified,
            edges_observed=edges,
            progress_observed=request_limit > 0,
            raw_results_path=str(evidence_path),
            trainer_failure_status=trainer_failure_status,
        )

    @staticmethod
    def _wait_for_role_start(port, ready_path, deadline, cancel) -> None:
        while True:
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("live-smoke roles did not become ready")
            try:
                healthy = httpx.get(
                    f"http://127.0.0.1:{port}/health", timeout=0.25
                ).status_code == 200
            except httpx.HTTPError:
                healthy = False
            if healthy and ready_path.is_file():
                return
            time.sleep(0.02)

    @staticmethod
    def _wait_for_status(path, count, deadline, cancel):
        while True:
            if path.is_file():
                try:
                    status = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    status = {}
                if int(status.get("processedEvents", 0)) >= count:
                    return status
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("split trainer did not consume smoke feedback")
            time.sleep(0.02)

    @staticmethod
    def _wait_for_activation(port, activation_dir, deadline, cancel):
        while True:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.25)
                if response.status_code == 200 and response.json()["modelVersion"] > 0:
                    receipts = sorted(activation_dir.glob("receipt-v*.json"))
                    if receipts:
                        activation = json.loads(receipts[-1].read_text(encoding="utf-8"))
                        if (
                            activation.get("changedVectorCount", 0) > 0
                            and activation.get("beforeVectorStateSha256")
                            != activation.get("afterVectorStateSha256")
                        ):
                            return response.json(), activation
            except httpx.HTTPError:
                pass
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("split serving role did not activate trainer output")
            time.sleep(0.02)


def _free_port_base() -> int:
    for base in range(18_890, 19_890, 9):
        reservations = []
        try:
            for port in range(base, base + 9):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port))
                reservations.append(sock)
            return base
        except OSError:
            pass
        finally:
            for sock in reservations:
                sock.close()
    raise RuntimeError("no alternate nine-port live-smoke range is available")


def _role_parser(role: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"babel-live-smoke {role}")
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--kafka-bootstrap", required=True)
    parser.add_argument("--consumer-group", required=True)
    parser.add_argument(
        "--load-mode",
        required=True,
        choices=("serving_only", "training_no_activation", "training_and_activation"),
    )
    if role == "_serve":
        parser.add_argument("--port", required=True, type=int)
    return parser


def _serve_role(args: argparse.Namespace) -> int:
    import uvicorn

    app, state, index, model, records, _request = _fixture_state(args.fixture_root)
    stop = threading.Event()

    def watch_activation() -> None:
        current = 0
        activation_dir = args.state_root / "activation"
        while not stop.wait(0.02):
            updates = sorted(activation_dir.glob("update-v*.json"))
            if not updates:
                continue
            try:
                document = json.loads(updates[-1].read_text(encoding="utf-8"))
                version = int(document["modelVersion"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if version <= current:
                continue
            vectors = {
                UUID(item_id): np.asarray(vector, dtype="<f4")
                for item_id, vector in document["vectors"].items()
            }
            updated, activation = _materialize_trainer_records(
                records, vectors, version=version
            )
            if activation["afterVectorStateSha256"] != document["vectorStateSha256"]:
                raise RuntimeError("trainer activation vector checksum differs")
            from babel_online.model import InMemoryCreatedBabelIndex

            state.apply_sync(
                selected_model_id=model.modelId,
                materialized_state=replace(
                    state.snapshot().materialized_state,
                    model_version=version,
                    pgvector_snapshot_sha256=activation[
                        "afterVectorStateSha256"
                    ],
                    backend_snapshot_sha256=activation[
                        "afterVectorStateSha256"
                    ],
                ),
                candidate_index=InMemoryCreatedBabelIndex(updated),
                vector_records=updated,
            )
            _write_json(
                activation_dir / f"receipt-v{version:08d}.json",
                {"schemaVersion": 2, "modelVersion": version, **activation},
            )
            current = version

    watcher = threading.Thread(target=watch_activation, daemon=True)
    watcher.start()
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )
    finally:
        stop.set()
        watcher.join(timeout=1)
    return 0


def _train_role(args: argparse.Namespace) -> int:
    from babel_online.feedback import KafkaFeedbackConsumer

    _app, _state, _index, model, records, request = _fixture_state(args.fixture_root)
    consumer = KafkaFeedbackConsumer(
        args.kafka_bootstrap, group_id=args.consumer_group
    )
    # Force assignment before declaring readiness so no first feedback can be
    # lost behind a late auto.offset.reset decision.
    consumer.position()
    consumer.seek(consumer.high_watermarks())
    trainer = FixtureLiveSmoke._trainer(
        consumer,
        records,
        ConditionScope(args.state_root, 0, args.consumer_group),
    )
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    _write_json(
        args.state_root / "trainer-ready.json",
        {"ready": True, "pid": os.getpid(), "formalPerformanceClaim": False},
    )
    try:
        if args.load_mode == "serving_only":
            while not stop.wait(0.05):
                pass
            return 0
        while not stop.is_set():
            processed = trainer.process_available(
                max_records=1, poll_timeout_seconds=0.05
            )
            if not processed:
                continue
            checkpoint = trainer.checkpoint_and_commit()
            status = {
                "processedEvents": trainer.processed_events,
                "optimizerSteps": trainer.global_step,
                "modelVersion": trainer.training_version,
                "checkpointPath": str(checkpoint),
                "pid": os.getpid(),
            }
            _write_json(args.state_root / "trainer-status.json", status)
            if args.load_mode == "training_and_activation":
                captured = trainer.capture_sync_state()
                vectors = {
                    str(item_id): [float(value) for value in vector]
                    for item_id, vector in captured.materialized_vectors.items()
                }
                _write_json(
                    args.state_root
                    / "activation"
                    / f"update-v{trainer.training_version:08d}.json",
                    {
                        "schemaVersion": 2,
                        "modelVersion": trainer.training_version,
                        "vectorStateSha256": _vector_state_sha(
                            captured.materialized_vectors
                        ),
                        "vectors": vectors,
                    },
                )
    finally:
        consumer.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"_serve", "_train"}:
        role = argv.pop(0)
        args = _role_parser(role).parse_args(argv)
        return _serve_role(args) if role == "_serve" else _train_role(args)
    parser = argparse.ArgumentParser(prog="babel-live-smoke")
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--kafka-bootstrap", default="127.0.0.1:29092")
    parser.add_argument("--requests-per-condition", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args(argv)
    from .matrix import run_tiny_smoke

    settings = LiveSmokeSettings(
        fixture_root=args.fixture_root,
        state_root=args.state_root,
        kafka_bootstrap_servers=args.kafka_bootstrap,
        request_count=args.requests_per_condition,
        timeout_seconds=args.timeout_seconds,
        port_base=_free_port_base(),
    )
    staged_receipt = args.receipt.with_suffix(args.receipt.suffix + ".running")
    args.receipt.unlink(missing_ok=True)
    staged_receipt.unlink(missing_ok=True)
    try:
        run_tiny_smoke(
            build_live_smoke_plan(settings),
            FixtureLiveSmoke(settings).execute,
            receipt_path=staged_receipt,
        )
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_receipt, args.receipt)
    except BaseException:
        staged_receipt.unlink(missing_ok=True)
        args.receipt.unlink(missing_ok=True)
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ConditionScope",
    "FixtureLiveSmoke",
    "LiveSmokeSettings",
    "build_live_smoke_plan",
    "main",
]
