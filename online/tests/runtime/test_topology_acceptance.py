"""Fixture-scale topology acceptance through real Babel runtime components."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import httpx
import numpy as np
import uvicorn
from fastapi.testclient import TestClient

from babel_online.contracts import (
    DistilledServingArtifactV1,
    FeedbackEventV2,
    ModelManifestV2,
)
from babel_online.feedback import InMemoryFeedbackBus
from babel_online.model.candidate_index import (
    InMemoryCreatedBabelIndex,
    MaterializedServingState,
)
from babel_online.model.qwen_encoder import Qwen100Encoder, format_article_input
from babel_online.model.registry import ModelRegistry
from babel_online.model.source_vector_cache import SourceVectorResolver
from babel_online.observable import CreatedBabel, VectorRecord
from babel_online.runtime.control import create_control_app
from babel_online.runtime.coordinator import StandaloneCoordinator
from babel_online.runtime.topology import semantic_replay_checksum
from babel_online.runtime.worker import _UvicornThread
from babel_online.serving import ServingState, create_app
from babel_online.simulation.client import RecommendationClient
from babel_online.simulation.scheduler import ScheduledWork, deterministic_schedule
from babel_online.training import NumpyWorkingModel, OnlineTrainer


RUN_ID = UUID("00000000-0000-5000-8000-000000000901")
CREATOR = UUID("00000000-0000-5000-8000-000000000902")
OTHER_CREATORS = (
    UUID("00000000-0000-5000-8000-000000000903"),
    UUID("00000000-0000-5000-8000-000000000904"),
)
BABEL_IDS = (
    UUID("00000000-0000-5000-8000-000000000911"),
    UUID("00000000-0000-5000-8000-000000000912"),
    UUID("00000000-0000-5000-8000-000000000913"),
)


class _AcceptedEncoder(Qwen100Encoder):
    """Spawn-safe accepted Qwen boundary; only tensor execution is a fixture."""

    def __init__(self, contract: DistilledServingArtifactV1) -> None:
        self.contract = contract
        self.device = "cpu"
        self.cache_identity = "topology-acceptance-qwen"

    def encode(self, texts):
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            row = np.frombuffer((digest * 4)[:100], dtype=np.uint8).astype(np.float32)
            row -= 127.5
            rows.append(row / np.linalg.norm(row))
        return np.asarray(rows, dtype=np.float32)


def _free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


def _serve_babel(app, port: int) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def _serve_fixture(manifest_document, contract_document, port: int) -> None:
    manifest = ModelManifestV2.model_validate(manifest_document)
    encoder = _AcceptedEncoder(
        DistilledServingArtifactV1.model_validate(contract_document)
    )
    app, _babels, _records = _fixture_app(manifest, encoder)
    _serve_babel(app, port)


def _wait_for_health(endpoint: str) -> dict[str, object]:
    deadline = time.monotonic() + 10.0
    while True:
        try:
            response = httpx.get(f"{endpoint}/health", timeout=0.5)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("real Babel recommendation app did not become healthy")
        time.sleep(0.02)


class _RecordingDatabase:
    def __init__(self) -> None:
        self.feedback = []
        self.activities = []
        self.rolls = []
        self.metrics = []

    def stop_requested(self, _run_id) -> bool:
        return False

    def persist_feedback_edges(self, event) -> None:
        self.feedback.append(event)

    def append_activity(self, activity) -> None:
        self.activities.append(activity)

    def persist_traversal_rolls(self, _run_id, _session_id, rolls) -> None:
        self.rolls.extend(rolls)

    def update_metrics(self, _run_id, **metrics) -> None:
        self.metrics.append(metrics)


def _fixture_app(real_model_manifest, encoder):
    babels = (
        CreatedBabel(
            babelId=BABEL_IDS[index],
            runId=RUN_ID,
            creatorId=(CREATOR, *OTHER_CREATORS)[index],
            sourceArticleKey=f"enwiki:{index + 1}",
            title=("Root", "Candidate A", "Candidate B")[index],
            text=("Root lead", "Candidate A lead", "Candidate B lead")[index],
            createdAtNs=index + 1,
        )
        for index in range(3)
    )
    babels = tuple(babels)
    vectors = encoder.encode(
        [format_article_input(row.title, row.text) for row in babels]
    )
    records = [
        VectorRecord(
            babel=babel,
            catalogContentHash=f"{index + 1:x}" * 64,
            embeddingSpaceId=real_model_manifest.embeddingSpace.embeddingSpaceId,
            servingModelId=real_model_manifest.modelId,
            materializedModelVersion=0,
            vector=tuple(float(value) for value in vectors[index]),
        )
        for index, babel in enumerate(babels)
    ]
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    materialized = MaterializedServingState(
        run_id=RUN_ID,
        model_id=real_model_manifest.modelId,
        model_version=0,
        embedding_space_id=real_model_manifest.embeddingSpace.embeddingSpaceId,
        pgvector_snapshot_sha256="a" * 64,
        backend_snapshot_sha256="b" * 64,
    )
    serving = ServingState(
        registry=registry,
        selected_model_id=real_model_manifest.modelId,
        materialized_state=materialized,
        candidate_index=InMemoryCreatedBabelIndex(records),
        vector_records=records,
        qwen_encoder=encoder,
        scale_run=True,
    )
    active_vectors = {record.babel.babelId: np.asarray(record.vector, dtype=np.float32) for record in records}
    resolver = SourceVectorResolver(
        encoder,
        load_active=lambda key: active_vectors[key.babel_id],
    )
    return create_app(serving, source_vector_resolver=resolver), babels, records


def _run_coordinator(endpoint: str, babels) -> tuple[_RecordingDatabase, InMemoryFeedbackBus]:
    database = _RecordingDatabase()
    bus = InMemoryFeedbackBus()
    schedule = deterministic_schedule(RUN_ID, [ScheduledWork(
        creator_id=CREATOR,
        creator_event_number=0,
        period="2026-06",
        source_article_key=babels[0].sourceArticleKey,
        root_babel_id=babels[0].babelId,
    )])
    config = SimpleNamespace(
        runId=RUN_ID,
        runSeed=7,
        recommendationK=2,
        recommendationStartProbability=1.0,
        continuationProbability=0.0,
        maximumTraversalDepth=2,
        maximumRequestsPerTraversal=3,
        concurrentUsers=1,
    )
    StandaloneCoordinator(
        config=config,
        database=database,
        schedule=schedule,
        babels={row.babelId: row for row in babels},
        hidden_edges={"2026-06": set()},
        producer=bus,
        client_factory=lambda: RecommendationClient(endpoint),
        stop_event=Event(),
        decide=lambda _scheduled, _source, _depth, candidate, _period: (
            "include" if candidate.rank == 1 else "exclude"
        ),
    ).run()
    return database, bus


def _train(bus, records, checkpoint_root: Path) -> dict[str, object]:
    vectors = {
        record.babel.babelId: np.asarray(record.vector, dtype=np.float32)
        for record in records
    }
    trainer = OnlineTrainer(
        model=NumpyWorkingModel(vectors, query_vector=vectors[BABEL_IDS[0]]),
        consumer=bus.consumer(group_id="trainer.acceptance", auto_commit=False),
        checkpoint_root=checkpoint_root,
    )
    assert trainer.process_available() == 1
    state = trainer.model.state_dict()
    return {
        "metrics": trainer.metrics,
        "modelStateSha256": semantic_replay_checksum(state),
    }


def _trainer_child(event_documents, record_documents, checkpoint_root: Path, connection) -> None:
    try:
        bus = InMemoryFeedbackBus()
        for document in event_documents:
            event = FeedbackEventV2.model_validate(document)
            bus.publish(key=str(event.creatorId), event=event)
        records = [VectorRecord.model_validate(document) for document in record_documents]
        connection.send((_train(bus, records, checkpoint_root), os.getpid(), None))
        connection.recv()
    except BaseException as error:  # pragma: no cover - child error transport
        connection.send((None, os.getpid(), repr(error)))
    finally:
        connection.close()


def _semantics(database: _RecordingDatabase, training: dict[str, object]) -> dict[str, object]:
    return {
        "feedback": [
            {
                "eventId": str(event.eventId),
                "requestId": str(event.requestId),
                "sourceBabelId": str(event.sourceBabelId),
                "modelVersion": event.modelVersion,
                "actions": [
                    {
                        "babelId": str(action.babelId),
                        "rank": action.rank,
                        "action": action.action,
                    }
                    for action in event.candidateActions
                ],
            }
            for event in database.feedback
        ],
        "acceptedEdges": sorted(
            (str(event.sourceBabelId), str(action.babelId))
            for event in database.feedback
            for action in event.candidateActions
            if action.action == "include"
        ),
        "training": training,
    }


@dataclass
class _SplitTrace:
    semantics: dict[str, object]
    endpoint: str
    health: dict[str, object]
    serving: multiprocessing.Process
    trainer: multiprocessing.Process
    trainer_control: object

    def close(self) -> None:
        close = getattr(self.trainer_control, "close", None)
        if callable(close):
            close()
        for process in (self.trainer, self.serving):
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def _run_split_trace(
    manifest, encoder_contract, babels, records, tmp_path: Path
) -> _SplitTrace:
    context = multiprocessing.get_context("spawn")
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    serving = context.Process(
        target=_serve_fixture,
        args=(
            manifest.model_dump(mode="json"),
            encoder_contract.model_dump(mode="json"),
            port,
        ),
    )
    serving.start()
    health = _wait_for_health(endpoint)
    database, _bus = _run_coordinator(endpoint, babels)
    parent, child = context.Pipe()
    trainer = context.Process(
        target=_trainer_child,
        args=(
            [event.model_dump(mode="json") for event in database.feedback],
            [record.model_dump(mode="json") for record in records],
            tmp_path / "split-checkpoints",
            child,
        ),
    )
    trainer.start()
    child.close()
    training, trainer_pid, error = parent.recv()
    if error is not None:
        parent.close()
        trainer.join(timeout=5)
        serving.terminate()
        serving.join(timeout=5)
        raise RuntimeError(error)
    assert trainer_pid == trainer.pid
    return _SplitTrace(
        semantics=_semantics(database, training),
        endpoint=endpoint,
        health=health,
        serving=serving,
        trainer=trainer,
        trainer_control=parent,
    )


def test_dashboard_start_runs_real_post_feedback_and_trainer_and_matches_split_replay(
    tmp_path: Path, real_model_manifest, accepted_qwen_factory
) -> None:
    encoder = accepted_qwen_factory()
    app, babels, records = _fixture_app(real_model_manifest, encoder)
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    server = _UvicornThread(app, host="127.0.0.1", port=port)
    result = {}

    class DashboardTopologyManager:
        def start(self, run_id) -> None:
            assert run_id == RUN_ID
            database, bus = _run_coordinator(endpoint, babels)
            result["semantics"] = _semantics(
                database, _train(bus, records, tmp_path / "same-process-checkpoints")
            )

        def request_stop(self, _run_id) -> None:
            return None

    server.start()
    try:
        token = "a" * 64
        with TestClient(
            create_control_app(DashboardTopologyManager(), token=token)
        ) as control:
            response = control.post(
                f"/v1/runs/{RUN_ID}/start",
                headers={"X-Babel-Worker-Token": token},
            )
        assert response.status_code == 202
        same_process = result["semantics"]
        assert same_process["acceptedEdges"]
        assert same_process["training"]["metrics"] == {
            "processedEvents": 1,
            "optimizerSteps": 1,
            "rollingRankLoss": same_process["training"]["metrics"]["rollingRankLoss"],
        }
    finally:
        server.stop()

    # Build a fresh real application before forking; the parent has no live
    # uvicorn thread when the independently placed roles start.
    split_encoder = accepted_qwen_factory()
    _split_app, split_babels, split_records = _fixture_app(
        real_model_manifest, split_encoder
    )
    split = _run_split_trace(
        real_model_manifest,
        split_encoder.contract,
        split_babels,
        split_records,
        tmp_path,
    )
    try:
        assert semantic_replay_checksum(same_process) == semantic_replay_checksum(
            split.semantics
        )
        assert split.serving.pid != split.trainer.pid
        assert split.health["modelVersion"] == 0
        assert split.trainer.is_alive()

        split.trainer.kill()
        split.trainer.join(timeout=5)

        after_kill = httpx.get(f"{split.endpoint}/health", timeout=2)
        assert after_kill.status_code == 200
        assert after_kill.json() == split.health
        assert split.serving.is_alive()
        assert not split.trainer.is_alive()
    finally:
        split.close()
