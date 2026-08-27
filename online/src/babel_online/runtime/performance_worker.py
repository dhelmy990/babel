"""Dashboard-driven population and live-condition performance orchestration."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid5

from fastapi import FastAPI, Header, HTTPException, Response, status

from ..contracts import ModelManifestV2, RunConfigV2
from ..model.frozen_population import FrozenPopulationManifestV1
from ..model.population import PopulationIdentity, PopulationReceipt


PerformanceStatus = Literal[
    "population_pending",
    "population_ready",
    "approved",
    "running",
    "stop_requested",
    "draining",
    "completed",
    "failed",
    "interrupted",
]


@dataclass(frozen=True, slots=True)
class PerformanceCondition:
    id: UUID
    condition_index: int
    topology: Literal["same_process", "same_host_split", "same_host_isolated"]
    training_enabled: bool
    activation_enabled: bool
    run_id: UUID | None
    status: str

    def __post_init__(self) -> None:
        if not 1 <= self.condition_index <= 9:
            raise ValueError("condition index must be between one and nine")
        if self.activation_enabled and not self.training_enabled:
            raise ValueError("activation requires online training")


@dataclass(frozen=True, slots=True)
class PerformanceExperiment:
    id: UUID
    status: PerformanceStatus
    starting_model_id: UUID
    model_repository: str
    model_revision: str
    dataset_repository: str
    dataset_config: str
    dataset_revision: str
    creator_count: int
    target_created_babels: int
    concurrent_users: int
    recommendation_start_probability: float
    continuation_probability: float
    maximum_traversal_depth: int
    maximum_requests_per_traversal: int
    interleave_creation_and_recommendations: bool
    warmup_seconds: int
    duration_seconds: int
    target_rps: float
    training_micro_batch_size: int
    sync_every_steps: int
    operator_approved: bool
    population_ready: bool
    population_run_id: UUID | None
    population_bundle_path: str | None
    population_manifest_sha256: str | None
    conditions: tuple[PerformanceCondition, ...]

    def validate_formal_defaults(self) -> None:
        if (
            self.creator_count != 50
            or self.target_created_babels != 10_000
            or self.concurrent_users != 50
            or self.recommendation_start_probability != 0.4
            or self.continuation_probability != 0.4
            or self.maximum_traversal_depth != 2
            or self.maximum_requests_per_traversal != 10
            or not self.interleave_creation_and_recommendations
        ):
            raise ValueError("saved trial does not match the formal first-cohort contract")
        expected = {
            (topology, training, activation)
            for topology in (
                "same_process",
                "same_host_split",
                "same_host_isolated",
            )
            for training, activation in ((False, False), (True, False), (True, True))
        }
        actual = {
            (row.topology, row.training_enabled, row.activation_enabled)
            for row in self.conditions
        }
        if len(self.conditions) != 9 or actual != expected:
            raise ValueError("saved trial does not contain the exact 3x3 condition matrix")


@dataclass(frozen=True, slots=True)
class FrozenWorkload:
    path: Path
    identity: tuple[str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class LiveConditionEvidence:
    condition_id: UUID
    run_id: UUID
    request_count: int
    p95_ms: float
    raw_evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if self.request_count <= 0 or self.p95_ms <= 0:
            raise ValueError("live condition requires requests and positive p95")


class PerformanceDatabase(Protocol):
    def load_performance_experiment(self, experiment_id: UUID) -> PerformanceExperiment: ...

    def append_performance_progress(self, experiment_id: UUID, **progress: Any) -> None: ...

    def bind_performance_population(
        self, experiment_id: str, run_id: UUID, manifest_sha256: str, bundle_path: str
    ) -> None: ...

    def mark_performance_population_ready(
        self, experiment_id: UUID, manifest: FrozenPopulationManifestV1
    ) -> None: ...

    def transition_performance(
        self, experiment_id: UUID, status: str, failure: str | None = None
    ) -> None: ...

    def create_condition_run(
        self,
        trial: PerformanceExperiment,
        condition: PerformanceCondition,
        run_id: UUID,
    ) -> UUID: ...

    def clone_performance_population(
        self,
        trial: PerformanceExperiment,
        condition: PerformanceCondition,
        run_id: UUID,
    ) -> None: ...

    def bind_performance_condition(
        self, experiment_id: str, condition_id: str, run_id: UUID
    ) -> None: ...

    def transition_performance_condition(
        self, experiment_id: UUID, condition_id: UUID, status: str
    ) -> None: ...

    def save_performance_condition_result(
        self,
        experiment_id: UUID,
        evidence: LiveConditionEvidence,
        *,
        serving_p95_ms: float,
        training_p95_ms: float,
        full_p95_ms: float,
    ) -> None: ...

    def transition(
        self, run_id: UUID, status: str, *, failure: str | None = None
    ) -> None: ...


PopulationBuilder = Callable[
    [
        PerformanceExperiment,
        UUID,
        Callable[..., None],
        Callable[[], bool],
    ],
    tuple[FrozenPopulationManifestV1, Path],
]
WorkloadFreezer = Callable[
    [PerformanceExperiment, FrozenPopulationManifestV1, Path, Callable[[], bool]],
    FrozenWorkload,
]
ConditionRunner = Callable[
    [
        PerformanceExperiment,
        PerformanceCondition,
        UUID,
        FrozenWorkload,
        Callable[[], bool],
    ],
    LiveConditionEvidence,
]


class RealPopulationBuilder:
    """Bind one saved formal trial to the pinned dataset/model population builder."""

    def __init__(
        self,
        *,
        database: Any,
        bundle: Any,
        model: ModelManifestV2,
        encoder: Any,
        output_root: str | Path,
        build: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(model, ModelManifestV2):
            raise TypeError("formal population requires a real Qwen V2 model")
        if build is None:
            from ..model.frozen_population import build_frozen_population

            build = build_frozen_population
        self.database = database
        self.bundle = bundle
        self.model = model
        self.encoder = encoder
        self.output_root = Path(output_root)
        self.build = build

    def __call__(
        self,
        trial: PerformanceExperiment,
        run_id: UUID,
        progress: Callable[..., None],
        stop_requested: Callable[[], bool],
    ) -> tuple[FrozenPopulationManifestV1, Path]:
        trial.validate_formal_defaults()
        if (
            trial.starting_model_id != self.model.modelId
            or trial.model_repository != self.model.encoderRepo
            or trial.model_revision != self.model.encoderRevision
        ):
            raise ValueError("saved trial model differs from the accepted Qwen artifact")
        bundle_identity = (
            self.bundle.dataset_repository,
            self.bundle.dataset_config,
            self.bundle.dataset_revision,
        )
        if bundle_identity != (
            trial.dataset_repository,
            trial.dataset_config,
            trial.dataset_revision,
        ):
            raise ValueError("saved trial dataset differs from the loaded pinned bundle")
        root = self.output_root / str(trial.id)
        config = RunConfigV2(
            schemaVersion=2,
            runId=run_id,
            datasetRepo=trial.dataset_repository,
            datasetConfig=trial.dataset_config,
            datasetRevision=trial.dataset_revision,
            startingModelId=trial.starting_model_id,
            retrievalBackend="pgvector",
            creatorCount=50,
            embeddingDimension=100,
            environmentSequence=["2026-06", "2026-07"],
            perMonthEventBudget={"2026-06": 5_000, "2026-07": 5_000},
            runSeed=int.from_bytes(
                hashlib.sha256(f"{trial.id}:population".encode("utf-8")).digest()[:8],
                "big",
            )
            & ((1 << 63) - 1),
            recommendationK=10,
            topL=100,
            kafkaTopic="babel.feedback.v1",
            kafkaGroup=f"babel-performance-population-{trial.id}",
            checkpointEveryEvents=100,
            syncEverySteps=trial.sync_every_steps,
            artifactRoot=str(root / "artifacts"),
            stateRoot=str(root / "state"),
            sourceArticlesPerMonth=5_000,
            targetCreatedBabels=10_000,
            concurrentUsers=50,
            recommendationStartProbability=0.4,
            continuationProbability=0.4,
            maximumTraversalDepth=2,
            maximumRequestsPerTraversal=10,
            interleaveCreationAndRecommendations=True,
        )
        identity = PopulationIdentity.from_real_model(
            run_id=run_id,
            dataset_revision=config.datasetRevision,
            model=self.model,
            model_version=0,
        )
        started = time.monotonic()

        def report(batch: Any) -> None:
            elapsed = max(1e-9, time.monotonic() - started)
            progress(
                created_babels=10_000,
                indexed_babels=int(batch.committed_count),
                recent_rate=float(batch.committed_count) / elapsed,
            )

        result = self.build(
            database=self.database,
            config=config,
            bundle=self.bundle,
            model=self.model,
            encoder=self.encoder,
            identity=identity,
            output_root=self.output_root,
            experiment_id=str(trial.id),
            progress_sink=report,
            stop_requested=stop_requested,
        )
        if isinstance(result, PopulationReceipt):
            if not result.complete and stop_requested():
                raise InterruptedError("population build stopped at a committed boundary")
            raise RuntimeError("population build did not produce a frozen manifest")
        if not isinstance(result, FrozenPopulationManifestV1):
            raise TypeError("population builder returned an invalid result")
        return result, root / "population"


class PerformanceConditionCommandRunner:
    """Run the concrete real-service condition command and validate its receipt."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        executable: str = "babel-online",
        execute: Callable[..., Any] | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.executable = executable
        self.execute = execute

    def __call__(
        self,
        trial: PerformanceExperiment,
        condition: PerformanceCondition,
        run_id: UUID,
        workload: FrozenWorkload,
        stop_requested: Callable[[], bool],
    ) -> LiveConditionEvidence:
        directory = (
            self.output_root
            / str(trial.id)
            / "conditions"
            / f"{condition.condition_index:02d}"
        )
        evidence_path = directory / "live-evidence.json"
        directory.mkdir(parents=True, exist_ok=True)
        argv = [
            self.executable,
            "performance-condition",
            "--experiment-id",
            str(trial.id),
            "--condition-id",
            str(condition.id),
            "--run-id",
            str(run_id),
            "--topology",
            condition.topology,
            "--training-enabled",
            str(condition.training_enabled).lower(),
            "--activation-enabled",
            str(condition.activation_enabled).lower(),
            "--workload",
            str(workload.path),
            "--duration-seconds",
            str(trial.duration_seconds),
            "--target-rps",
            str(trial.target_rps),
            "--evidence",
            str(evidence_path),
        ]
        if evidence_path.is_file():
            pass
        elif self.execute is not None:
            completed = self.execute(argv, check=False)
            if int(completed.returncode) != 0:
                raise RuntimeError("live performance condition command failed")
        else:
            process = subprocess.Popen(argv, start_new_session=True)
            while process.poll() is None:
                if stop_requested():
                    process_group = os.getpgid(process.pid)
                    try:
                        os.killpg(process_group, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process_group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=10)
                    raise InterruptedError("live performance condition stopped")
                time.sleep(0.1)
            if process.returncode != 0:
                raise RuntimeError("live performance condition command failed")
        try:
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("live performance condition evidence is unavailable") from error
        if set(document) != {
            "conditionId",
            "runId",
            "requestCount",
            "p95Ms",
            "rawEvidence",
        }:
            raise ValueError("live performance condition evidence contract differs")
        evidence = LiveConditionEvidence(
            condition_id=UUID(str(document["conditionId"])),
            run_id=UUID(str(document["runId"])),
            request_count=int(document["requestCount"]),
            p95_ms=float(document["p95Ms"]),
            raw_evidence=dict(document["rawEvidence"]),
        )
        if evidence.condition_id != condition.id or evidence.run_id != run_id:
            raise ValueError("live condition evidence identity differs")
        return evidence


def _manifest_sha(manifest: FrozenPopulationManifestV1) -> str:
    value = (
        json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


class PerformanceJobManager:
    """One event-driven population/matrix job controlled by the dashboard."""

    def __init__(
        self,
        *,
        database: PerformanceDatabase,
        output_root: str | Path,
        population_builder: PopulationBuilder,
        workload_freezer: WorkloadFreezer,
        condition_runner: ConditionRunner,
        population_loader: Callable[[Path], FrozenPopulationManifestV1] | None = None,
    ) -> None:
        self.database = database
        self.output_root = Path(output_root)
        self.population_builder = population_builder
        self.workload_freezer = workload_freezer
        self.condition_runner = condition_runner
        if population_loader is None:
            from ..model.frozen_population import load_frozen_population

            population_loader = load_frozen_population
        self.population_loader = population_loader
        self._lock = threading.RLock()
        self._experiment_id: UUID | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._phase = "idle"
        self._failure: str | None = None

    def _launch(self, experiment_id: UUID, operation: Callable[[], None]) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._experiment_id == experiment_id:
                    return
                raise RuntimeError("another performance experiment is active")
            self._experiment_id = experiment_id
            self._stop.clear()
            self._failure = None

            def execute() -> None:
                try:
                    operation()
                except InterruptedError:
                    if not self._stop.is_set():
                        raise
                    self.database.transition_performance(
                        experiment_id, "interrupted"
                    )
                    with self._lock:
                        self._phase = "interrupted"
                        self._failure = None
                except BaseException as error:
                    with self._lock:
                        self._phase = "failed"
                        self._failure = str(error)
                    try:
                        self.database.transition_performance(
                            experiment_id, "failed", failure=str(error)[:1000]
                        )
                    except Exception:
                        pass

            self._thread = threading.Thread(
                target=execute,
                daemon=True,
                name=f"babel-performance-{experiment_id}",
            )
            self._thread.start()

    def start(self, experiment_id: UUID) -> None:
        trial = self.database.load_performance_experiment(experiment_id)
        trial.validate_formal_defaults()
        if trial.population_ready:
            with self._lock:
                self._experiment_id = experiment_id
                self._phase = "waiting_for_approval"
            return
        if trial.status != "population_pending":
            raise RuntimeError("trial is not ready to build its population")
        with self._lock:
            self._phase = "population"

        def build() -> None:
            started = time.monotonic()
            run_id = uuid5(experiment_id, "population")

            def progress(**values: Any) -> None:
                elapsed = max(0.0, time.monotonic() - started)
                self.database.append_performance_progress(
                    experiment_id,
                    phase="population",
                    condition_index=None,
                    condition_count=9,
                    seeded_articles=int(values.get("created_babels", 0)),
                    created_babels=int(values.get("created_babels", 0)),
                    indexed_babels=int(values.get("indexed_babels", 0)),
                    requested=0,
                    completed=0,
                    elapsed_seconds=elapsed,
                    recent_rate=float(values.get("recent_rate", 0.0)),
                    draining=False,
                    telemetry={},
                )

            manifest, directory = self.population_builder(
                trial, run_id, progress, self._stop.is_set
            )
            if self._stop.is_set():
                self.database.transition_performance(experiment_id, "interrupted")
                with self._lock:
                    self._phase = "interrupted"
                return
            if manifest.sourcePopulationRunId != run_id:
                raise ValueError("population builder returned a different source run")
            self.database.transition(run_id, "completed")
            manifest_sha = _manifest_sha(manifest)
            self.database.bind_performance_population(
                str(experiment_id), run_id, manifest_sha, str(directory)
            )
            self.database.mark_performance_population_ready(experiment_id, manifest)
            self.database.append_performance_progress(
                experiment_id,
                phase="population_ready",
                condition_index=None,
                condition_count=9,
                seeded_articles=10_000,
                created_babels=10_000,
                indexed_babels=10_000,
                requested=0,
                completed=0,
                elapsed_seconds=max(0.0, time.monotonic() - started),
                recent_rate=0.0,
                draining=False,
                telemetry={"populationManifestSha256": manifest_sha},
            )
            with self._lock:
                self._phase = "waiting_for_approval"

        self._launch(experiment_id, build)

    def approve_next_scale(self, experiment_id: UUID) -> None:
        trial = self.database.load_performance_experiment(experiment_id)
        trial.validate_formal_defaults()
        if trial.status == "completed" and trial.operator_approved:
            return
        if not (
            trial.operator_approved
            and trial.population_ready
            and trial.status in {"approved", "running"}
            and trial.population_run_id is not None
            and trial.population_bundle_path is not None
        ):
            raise RuntimeError("durable operator approval is required")
        with self._lock:
            self._phase = "matrix"

        def matrix() -> None:
            capture_started = time.monotonic()
            population_dir = Path(trial.population_bundle_path or "")
            manifest = self.population_loader(population_dir)
            self.database.transition_performance(experiment_id, "running")
            self.database.append_performance_progress(
                experiment_id,
                phase="reference_workload",
                condition_index=None,
                condition_count=9,
                seeded_articles=10_000,
                created_babels=10_000,
                indexed_babels=10_000,
                requested=max(
                    1,
                    math.ceil(
                        (trial.warmup_seconds + trial.duration_seconds)
                        * trial.target_rps
                    ),
                ),
                completed=0,
                elapsed_seconds=0.0,
                recent_rate=0.0,
                draining=False,
                telemetry={"status": "capturing_reference_workload"},
            )
            workload = self.workload_freezer(
                trial, manifest, population_dir, self._stop.is_set
            )
            request_count = sum(
                1
                for line in (workload.path / "requests.template.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
            elapsed = max(1e-9, time.monotonic() - capture_started)
            self.database.append_performance_progress(
                experiment_id,
                phase="reference_workload_ready",
                condition_index=None,
                condition_count=9,
                seeded_articles=10_000,
                created_babels=10_000,
                indexed_babels=10_000,
                requested=request_count,
                completed=request_count,
                elapsed_seconds=elapsed,
                recent_rate=request_count / elapsed,
                draining=False,
                telemetry={"workloadIdentity": list(workload.identity)},
            )
            completed = 0
            topology_evidence: dict[
                str, list[tuple[PerformanceCondition, LiveConditionEvidence]]
            ] = {}
            for condition in sorted(
                trial.conditions, key=lambda value: value.condition_index
            ):
                if self._stop.is_set():
                    self.database.transition_performance(experiment_id, "interrupted")
                    with self._lock:
                        self._phase = "interrupted"
                    return
                run_id = uuid5(experiment_id, f"condition:{condition.condition_index}")
                self.database.create_condition_run(trial, condition, run_id)
                self.database.clone_performance_population(trial, condition, run_id)
                self.database.bind_performance_condition(
                    str(experiment_id), str(condition.id), run_id
                )
                self.database.transition_performance_condition(
                    experiment_id, condition.id, "running"
                )
                try:
                    evidence = self.condition_runner(
                        trial, condition, run_id, workload, self._stop.is_set
                    )
                except InterruptedError:
                    self.database.transition(run_id, "interrupted")
                    self.database.transition_performance_condition(
                        experiment_id, condition.id, "interrupted"
                    )
                    raise
                except BaseException as error:
                    self.database.transition(
                        run_id, "failed", failure=str(error)[:1000]
                    )
                    self.database.transition_performance_condition(
                        experiment_id, condition.id, "failed"
                    )
                    raise
                if evidence.condition_id != condition.id or evidence.run_id != run_id:
                    raise ValueError("condition runner returned drifted execution identity")
                self.database.transition(run_id, "completed")
                rows = topology_evidence.setdefault(condition.topology, [])
                rows.append((condition, evidence))
                if len(rows) == 3:
                    by_mode = {
                        (row.training_enabled, row.activation_enabled): result
                        for row, result in rows
                    }
                    if set(by_mode) != {
                        (False, False),
                        (True, False),
                        (True, True),
                    }:
                        raise ValueError("topology does not contain the exact load-mode trio")
                    serving_p95 = by_mode[(False, False)].p95_ms
                    training_p95 = by_mode[(True, False)].p95_ms
                    full_p95 = by_mode[(True, True)].p95_ms
                    for _row, result in rows:
                        self.database.save_performance_condition_result(
                            experiment_id,
                            result,
                            serving_p95_ms=serving_p95,
                            training_p95_ms=training_p95,
                            full_p95_ms=full_p95,
                        )
                self.database.transition_performance_condition(
                    experiment_id, condition.id, "completed"
                )
                completed += 1
                self.database.append_performance_progress(
                    experiment_id,
                    phase="matrix",
                    condition_index=condition.condition_index,
                    condition_count=9,
                    seeded_articles=10_000,
                    created_babels=10_000,
                    indexed_babels=10_000,
                    requested=evidence.request_count,
                    completed=evidence.request_count,
                    elapsed_seconds=0.0,
                    recent_rate=0.0,
                    draining=False,
                    telemetry={
                        "completedConditions": completed,
                        "workloadIdentity": list(workload.identity),
                    },
                )
            self.database.transition_performance(experiment_id, "completed")
            with self._lock:
                self._phase = "completed"

        self._launch(experiment_id, matrix)

    def request_stop(self, experiment_id: UUID) -> None:
        trial = self.database.load_performance_experiment(experiment_id)
        with self._lock:
            if self._experiment_id not in {None, experiment_id}:
                raise KeyError(experiment_id)
            self._experiment_id = experiment_id
            self._stop.set()
            active = self._thread is not None and self._thread.is_alive()
            if trial.status not in {"completed", "failed", "interrupted"} and active:
                self._phase = "stopping"
            elif trial.status not in {"completed", "failed", "interrupted"}:
                self.database.transition_performance(experiment_id, "interrupted")
                self._phase = "interrupted"

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise TimeoutError("performance job did not stop before timeout")

    @property
    def status(self) -> dict[str, str | None]:
        with self._lock:
            return {
                "experimentId": (
                    None if self._experiment_id is None else str(self._experiment_id)
                ),
                "phase": self._phase,
                "failure": self._failure,
            }


def _valid_token(token: str) -> bool:
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def create_performance_control_app(
    manager: PerformanceJobManager, *, token: str
) -> FastAPI:
    """Expose exactly the loopback routes used by the C++ dashboard bridge."""
    if not _valid_token(token):
        raise ValueError("worker token must contain exactly 64 lowercase hex digits")
    app = FastAPI(title="Babel performance worker", version="1")

    def authorize(presented: str | None) -> None:
        if presented is None or not hmac.compare_digest(token, presented):
            raise HTTPException(status_code=403, detail="forbidden")

    def invoke(operation: Callable[[], None]) -> Response:
        try:
            operation()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="experiment not found") from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/performance/{experiment_id}/start",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start(
        experiment_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(lambda: manager.start(experiment_id))

    @app.post(
        "/v1/performance/{experiment_id}/graceful-stop",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def stop(
        experiment_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(lambda: manager.request_stop(experiment_id))

    @app.post(
        "/v1/performance/{experiment_id}/approve-next-scale",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def approve(
        experiment_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(lambda: manager.approve_next_scale(experiment_id))

    return app


__all__ = [
    "FrozenWorkload",
    "LiveConditionEvidence",
    "PerformanceCondition",
    "PerformanceConditionCommandRunner",
    "PerformanceExperiment",
    "PerformanceJobManager",
    "RealPopulationBuilder",
    "create_performance_control_app",
]
