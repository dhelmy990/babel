"""Dashboard-driven population and live-condition performance orchestration."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import signal
import stat
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
    evidence_scope: str = "formal"
    source_trial_id: UUID | None = None
    source_workload_path: str | None = None
    source_workload_identity: tuple[str, ...] | None = None
    population_vector_count: int = 0
    population_vector_sha256: str | None = None
    population_model_repository: str | None = None
    population_model_revision: str | None = None
    population_model_sha256: str | None = None
    population_dataset_repository: str | None = None
    population_dataset_revision: str | None = None
    population_dataset_sha256: str | None = None
    replay_request_limit: int | None = None

    @property
    def condition_count(self) -> int:
        return len(self.conditions)

    def validate_formal_defaults(self) -> None:
        if (
            self.evidence_scope != "formal"
            or self.creator_count not in {50, 100, 500}
            or self.target_created_babels != 10_000
            or self.concurrent_users != self.creator_count
            or self.recommendation_start_probability != 0.4
            or self.continuation_probability != 0.4
            or self.maximum_traversal_depth != 2
            or self.maximum_requests_per_traversal != 10
            or not self.interleave_creation_and_recommendations
        ):
            raise ValueError("saved trial does not match the formal cohort contract")
        topologies = (
            ("same_process", "same_host_split", "same_host_isolated")
            if self.creator_count == 50
            else ("same_process", "same_host_split")
        )
        expected = {
            (topology, training, activation)
            for topology in topologies
            for training, activation in ((False, False), (True, False), (True, True))
        }
        actual = {
            (row.topology, row.training_enabled, row.activation_enabled)
            for row in self.conditions
        }
        if len(self.conditions) != len(expected) or actual != expected:
            raise ValueError("saved trial does not contain its exact condition matrix")

    def validate_runnable_contract(self) -> None:
        """Accept formal matrices or the explicitly non-formal split-service trio."""
        if self.evidence_scope == "formal":
            self.validate_formal_defaults()
            return
        if self.evidence_scope not in {
            "representative_same_process_vs_split",
            "representative_split_smoke",
        }:
            raise ValueError("saved trial has an unsupported evidence scope")
        topologies = (
            ("same_process", "same_host_split")
            if self.evidence_scope == "representative_same_process_vs_split"
            else ("same_host_split",)
        )
        expected = {
            (topology, training, activation)
            for topology in topologies
            for training, activation in ((False, False), (True, False), (True, True))
        }
        actual = {
            (row.topology, row.training_enabled, row.activation_enabled)
            for row in self.conditions
        }
        if (
            self.creator_count != 50
            or self.target_created_babels != 10_000
            or self.concurrent_users != 50
            or self.recommendation_start_probability != 0.4
            or self.continuation_probability != 0.4
            or self.maximum_traversal_depth != 2
            or self.maximum_requests_per_traversal != 10
            or not self.interleave_creation_and_recommendations
            or self.source_trial_id is None
            or self.source_workload_path is None
            or self.source_workload_identity is None
            or self.replay_request_limit is None
            or self.replay_request_limit <= 0
            or len(self.conditions) != len(expected)
            or actual != expected
        ):
            raise ValueError(
                "saved representative trial does not contain the exact same-host split trio"
            )


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


def validate_condition3_gate_evidence(
    raw_evidence: dict[str, Any],
    *,
    request_count: int,
    p95_ms: float,
    expected_warmup_count: int,
    expected_request_count: int,
    latency_safety_threshold_ms: float,
) -> dict[str, Any]:
    """Accept only a clean same-process training-and-activation receipt."""
    identity = raw_evidence.get("conditionIdentity")
    if identity != {
        "topology": "same_process",
        "trainingEnabled": True,
        "activationEnabled": True,
        "retrievalBackend": "pgvector",
    }:
        raise ValueError("Condition 3 identity differs from the approved gate")
    measurements = raw_evidence.get("measurements")
    if (
        not isinstance(measurements, list)
        or not measurements
        or any(
            not isinstance(row, dict)
            or row.get("outcome") != "success"
            or type(row.get("scheduleIndex")) is not int
            or type(row.get("isWarmup")) is not bool
            or type(row.get("clientTotalNs")) is not int
            or row["clientTotalNs"] < 0
            for row in measurements
        )
    ):
        raise ValueError("Condition 3 contains unsuccessful measurements")
    if (
        type(request_count) is not int
        or request_count != expected_request_count
        or type(raw_evidence.get("warmupCount")) is not int
        or raw_evidence["warmupCount"] != expected_warmup_count
        or type(raw_evidence.get("selectedRequestCount")) is not int
        or raw_evidence["selectedRequestCount"] != len(measurements)
        or len(measurements) != expected_warmup_count + expected_request_count
    ):
        raise ValueError("Condition 3 measured request count differs")
    warmup = [row for row in measurements if row["isWarmup"]]
    measured = [row for row in measurements if not row["isWarmup"]]
    if len(warmup) != expected_warmup_count or len(measured) != request_count:
        raise ValueError("Condition 3 measured request count differs")
    if any(
        row["scheduleIndex"] != index
        or row["isWarmup"] != (index < expected_warmup_count)
        for index, row in enumerate(measurements)
    ):
        raise ValueError("Condition 3 warmup schedule differs")
    ordered_ns = sorted(row["clientTotalNs"] for row in measured)
    raw_p95_ms = ordered_ns[
        max(0, math.ceil(0.95 * len(ordered_ns)) - 1)
    ] / 1_000_000.0
    if not math.isfinite(p95_ms) or p95_ms != raw_p95_ms:
        raise ValueError("Condition 3 p95 differs from raw measurements")
    if (
        not math.isfinite(latency_safety_threshold_ms)
        or latency_safety_threshold_ms != 5_000.0
        or p95_ms > latency_safety_threshold_ms
    ):
        raise ValueError("Condition 3 exceeds the approved 5,000 ms safety threshold")
    placement = raw_evidence.get("placement")
    processes = placement.get("processes") if isinstance(placement, dict) else None
    if (
        not isinstance(placement, dict)
        or placement.get("requestedTopology") != "same_process"
        or placement.get("actualTopology") != "same_process"
        or not isinstance(processes, list)
        or len(processes) != 2
        or {row.get("role") for row in processes if isinstance(row, dict)}
        != {"serving", "trainer"}
        or len({row.get("pid") for row in processes if isinstance(row, dict)}) != 1
    ):
        raise ValueError("Condition 3 placement or role evidence differs")
    activation = raw_evidence.get("observedActivationTargets")
    final_serving = raw_evidence.get("finalServingIdentity")
    if (
        not isinstance(activation, list)
        or not activation
        or not isinstance(final_serving, dict)
        or type(final_serving.get("modelVersion")) is not int
        or int(final_serving["modelVersion"]) <= 0
    ):
        raise ValueError("Condition 3 activation evidence is incomplete")
    feedback = raw_evidence.get("feedbackKafka")
    final = feedback.get("finalTrainerState") if isinstance(feedback, dict) else None
    records = feedback.get("records") if isinstance(feedback, dict) else None
    if (
        not isinstance(feedback, dict)
        or type(feedback.get("recordCount")) is not int
        or int(feedback["recordCount"]) <= 0
        or not isinstance(records, list)
        or not isinstance(feedback.get("offsetRanges"), list)
        or not feedback["offsetRanges"]
        or not isinstance(final, dict)
        or final.get("available") is not True
    ):
        raise ValueError("Condition 3 Kafka offset evidence is incomplete")
    if feedback["recordCount"] != len(measurements) or len(records) != len(
        measurements
    ):
        raise ValueError("Condition 3 published feedback count differs")
    try:
        measurement_request_ids = [
            UUID(str(row["requestId"])) for row in measurements
        ]
        record_request_ids = [UUID(str(row["requestId"])) for row in records]
        record_event_ids = [UUID(str(row["eventId"])) for row in records]
        record_offsets = {
            (str(row["topic"]), int(row["partition"]), int(row["offset"]))
            for row in records
        }
        ranges = {
            (str(row["topic"]), int(row["partition"])): (
                int(row["startInclusive"]), int(row["endExclusive"])
            )
            for row in feedback["offsetRanges"]
        }
        next_offsets = {
            (str(row["topic"]), int(row["partition"])): int(row["nextOffset"])
            for row in final.get("nextOffsets", [])
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Condition 3 Kafka offset coverage is malformed") from error
    if (
        len(set(measurement_request_ids)) != len(measurement_request_ids)
        or len(set(record_request_ids)) != len(record_request_ids)
        or len(set(record_event_ids)) != len(record_event_ids)
        or len(record_offsets) != len(records)
        or set(record_request_ids) != set(measurement_request_ids)
    ):
        raise ValueError("Condition 3 Kafka request identity coverage is incomplete")
    expected_offsets = {
        (topic, partition, offset)
        for (topic, partition), (start, end) in ranges.items()
        for offset in range(start, end)
    }
    if (
        len(ranges) != len(feedback["offsetRanges"])
        or sum(end - start for start, end in ranges.values())
        != feedback["recordCount"]
        or any(start < 0 or end <= start for start, end in ranges.values())
        or record_offsets != expected_offsets
        or any(next_offsets.get(key, -1) < end for key, (_start, end) in ranges.items())
    ):
        raise ValueError("Condition 3 Kafka offset coverage is incomplete")
    if type(final.get("kafkaLag")) is not int or final["kafkaLag"] != 0:
        raise ValueError("Condition 3 requires exactly zero final Kafka lag")
    if (
        final.get("offsetsCoverPublishedRanges") is not True
        or not isinstance(final.get("nextOffsets"), list)
        or not final["nextOffsets"]
        or not isinstance(final.get("checkpointManifestSha256"), str)
        or len(final["checkpointManifestSha256"]) != 64
    ):
        raise ValueError("Condition 3 Kafka offset coverage is incomplete")
    return raw_evidence


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
        clone: Callable[..., FrozenPopulationManifestV1] | None = None,
    ) -> None:
        if not isinstance(model, ModelManifestV2):
            raise TypeError("formal population requires a real Qwen V2 model")
        if build is None:
            from ..model.frozen_population import build_frozen_population

            build = build_frozen_population
        if clone is None:
            from ..model.frozen_population import freeze_cloned_population

            clone = freeze_cloned_population
        self.database = database
        self.bundle = bundle
        self.model = model
        self.encoder = encoder
        self.output_root = Path(output_root)
        self.build = build
        self.clone = clone

    @staticmethod
    def _same_immutable_artifact(
        left: ModelManifestV2, right: ModelManifestV2
    ) -> bool:
        ignored = {"modelId", "label", "parentModelId", "producingRunId"}
        left_document = left.model_dump(mode="json")
        right_document = right.model_dump(mode="json")
        return all(
            left_document[name] == right_document[name]
            for name in left_document.keys() - ignored
        )

    def _selected_child(self, model_id: UUID):
        descriptor, descriptor_path = self.database.load_real_child_artifact(model_id)
        selected = descriptor.childManifest
        if selected.modelId != model_id:
            raise ValueError("selected child descriptor resolves to another model")
        seen: set[UUID] = set()
        current = selected
        while current.modelId != self.model.modelId:
            if current.modelId in seen or not self._same_immutable_artifact(
                self.model, current
            ):
                raise ValueError("selected child is outside the accepted model lineage")
            seen.add(current.modelId)
            parent_id = current.parentModelId
            if parent_id is None:
                raise ValueError("selected child is outside the accepted model lineage")
            if parent_id == self.model.modelId:
                break
            parent_descriptor, _parent_path = self.database.load_real_child_artifact(
                parent_id
            )
            current = parent_descriptor.childManifest
            if current.modelId != parent_id:
                raise ValueError("selected child lineage descriptor differs")
        root = descriptor_path.parent.resolve()
        for relative, expected in descriptor.files.items():
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError("selected child state escapes its artifact") from error
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("selected child state checksum differs")
        return descriptor

    def __call__(
        self,
        trial: PerformanceExperiment,
        run_id: UUID,
        progress: Callable[..., None],
        stop_requested: Callable[[], bool],
    ) -> tuple[FrozenPopulationManifestV1, Path]:
        trial.validate_formal_defaults()
        if (
            trial.model_repository != self.model.encoderRepo
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
            creatorCount=trial.creator_count,
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
            concurrentUsers=trial.concurrent_users,
            recommendationStartProbability=0.4,
            continuationProbability=0.4,
            maximumTraversalDepth=2,
            maximumRequestsPerTraversal=10,
            interleaveCreationAndRecommendations=True,
        )
        started = time.monotonic()

        def report(batch: Any) -> None:
            elapsed = max(1e-9, time.monotonic() - started)
            progress(
                created_babels=10_000,
                indexed_babels=int(batch.committed_count),
                recent_rate=float(batch.committed_count) / elapsed,
            )

        if trial.starting_model_id == self.model.modelId:
            identity = PopulationIdentity.from_real_model(
                run_id=run_id,
                dataset_revision=config.datasetRevision,
                model=self.model,
                model_version=0,
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
        else:
            descriptor = self._selected_child(trial.starting_model_id)
            child = descriptor.childManifest
            source_run_id = child.producingRunId
            if source_run_id is None:
                raise ValueError("selected child does not identify its producing run")
            source_config = self.database.load_run(source_run_id).config
            if (
                source_config.datasetRepo != config.datasetRepo
                or source_config.datasetConfig != config.datasetConfig
                or source_config.datasetRevision != config.datasetRevision
                or source_config.creatorCount != config.creatorCount
                or source_config.perMonthEventBudget != config.perMonthEventBudget
                or source_config.targetCreatedBabels != config.targetCreatedBabels
            ):
                raise ValueError(
                    "selected child producing run is incompatible with this trial"
                )
            active = self.database.load_active_embedding_state(source_run_id)
            if (
                active.run_id != source_run_id
                or active.model_id != child.modelId
                or active.model_version != descriptor.modelVersion
                or active.embedding_space_id != child.embeddingSpace.embeddingSpaceId
                or active.pgvector_snapshot_sha256 != descriptor.vectorSnapshotSha256
                or active.backend_snapshot_sha256 != descriptor.vectorSnapshotSha256
            ):
                raise ValueError(
                    "selected child is not the producing run's active snapshot"
                )
            source_identity = PopulationIdentity.from_real_model(
                run_id=source_run_id,
                dataset_revision=config.datasetRevision,
                model=child,
                model_version=descriptor.modelVersion,
            )
            result = self.clone(
                database=self.database,
                config=config,
                bundle=self.bundle,
                model=child,
                model_version=descriptor.modelVersion,
                source_identity=source_identity,
                expected_snapshot_sha256=descriptor.vectorSnapshotSha256,
                output_root=self.output_root,
                experiment_id=str(trial.id),
            )
            progress(created_babels=10_000, indexed_babels=10_000, recent_rate=0.0)
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
        request_count = document["requestCount"]
        p95_ms = document["p95Ms"]
        if type(request_count) is not int:
            raise ValueError("live condition requestCount must be an integer")
        if (
            isinstance(p95_ms, bool)
            or not isinstance(p95_ms, (int, float))
            or not math.isfinite(float(p95_ms))
        ):
            raise ValueError("live condition p95Ms must be a finite number")
        evidence = LiveConditionEvidence(
            condition_id=UUID(str(document["conditionId"])),
            run_id=UUID(str(document["runId"])),
            request_count=request_count,
            p95_ms=float(p95_ms),
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


def _validate_population_cohort(
    trial: PerformanceExperiment, manifest: FrozenPopulationManifestV1
) -> None:
    expected_experiment_id = (
        trial.source_trial_id
        if trial.evidence_scope.startswith("representative_")
        else trial.id
    )
    if expected_experiment_id is None or manifest.experimentId != str(
        expected_experiment_id
    ):
        raise ValueError("frozen population belongs to another trial")
    if manifest.creatorCount != trial.creator_count:
        raise ValueError("frozen population creator count differs from cohort")


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
        allow_population_build: bool | None = None,
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
        if allow_population_build is None:
            configured = os.environ.get("BABEL_ONLINE_ALLOW_POPULATION_BUILD", "true")
            if configured not in {"true", "false"}:
                raise ValueError(
                    "BABEL_ONLINE_ALLOW_POPULATION_BUILD must be true or false"
                )
            allow_population_build = configured == "true"
        self.allow_population_build = allow_population_build
        self._lock = threading.RLock()
        self._experiment_id: UUID | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._phase = "idle"
        self._failure: str | None = None
        self._condition3_preparation_binding: tuple[UUID, UUID] | None = None

    def _validate_imported_ready_population(
        self, trial: PerformanceExperiment
    ) -> tuple[FrozenPopulationManifestV1, Path]:
        if (
            not trial.population_ready
            or trial.population_run_id is None
            or trial.population_bundle_path is None
            or trial.population_manifest_sha256 is None
        ):
            raise RuntimeError("validated imported-ready population binding is absent")
        directory = Path(trial.population_bundle_path)
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeError("imported-ready population directory is unavailable")
        manifest_path = directory / "manifest.json"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(manifest_path, flags)
        except OSError as error:
            raise RuntimeError(
                "imported-ready population manifest is unavailable"
            ) from error
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise RuntimeError(
                    "imported-ready population manifest is not a regular file"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            manifest_bytes = b"".join(chunks)
        finally:
            os.close(descriptor)
        if (
            hashlib.sha256(manifest_bytes).hexdigest()
            != trial.population_manifest_sha256
        ):
            raise RuntimeError("imported-ready population manifest checksum differs")
        manifest = self.population_loader(directory)
        _validate_population_cohort(trial, manifest)
        if manifest.sourcePopulationRunId != trial.population_run_id:
            raise RuntimeError("imported-ready population run binding differs")
        return manifest, directory

    def _record_imported_population_failure(
        self, experiment_id: UUID, error: BaseException
    ) -> None:
        self.database.transition_performance(
            experiment_id, "failed", failure=str(error)[:1000]
        )
        with self._lock:
            self._experiment_id = experiment_id
            self._phase = "failed"
            self._failure = str(error)

    def _launch(
        self,
        experiment_id: UUID,
        operation: Callable[[], None],
        *,
        handoff_from_phase: str | None = None,
        handoff_start_phase: str | None = None,
    ) -> None:
        prior_thread: threading.Thread | None = None

        def start_locked() -> None:
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

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if (
                    self._experiment_id == experiment_id
                    and handoff_from_phase is not None
                    and self._phase == handoff_from_phase
                ):
                    prior_thread = self._thread
                elif self._experiment_id == experiment_id:
                    return
                else:
                    raise RuntimeError("another performance experiment is active")
            else:
                start_locked()
                return

        if prior_thread is None:
            return
        prior_thread.join()
        with self._lock:
            if (
                self._thread is not prior_thread
                or self._experiment_id != experiment_id
                or self._phase != handoff_from_phase
            ):
                if self._experiment_id == experiment_id:
                    return
                raise RuntimeError("another performance experiment is active")
            self._phase = handoff_start_phase or f"{handoff_from_phase}_handoff"
            start_locked()

    def start(self, experiment_id: UUID) -> None:
        trial = self.database.load_performance_experiment(experiment_id)
        trial.validate_runnable_contract()
        if trial.population_ready:
            if not self.allow_population_build:
                try:
                    self._validate_imported_ready_population(trial)
                except BaseException as error:
                    self._record_imported_population_failure(experiment_id, error)
                    raise
            with self._lock:
                self._experiment_id = experiment_id
                self._phase = "waiting_for_approval"
            return
        if not self.allow_population_build:
            error = RuntimeError(
                "validated imported-ready population binding is required when "
                "population build is disabled"
            )
            self._record_imported_population_failure(experiment_id, error)
            raise error
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
                    condition_count=trial.condition_count,
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
            _validate_population_cohort(trial, manifest)
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
                condition_count=trial.condition_count,
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
        trial.validate_runnable_contract()
        if trial.status == "completed" and trial.operator_approved:
            if not self.allow_population_build:
                try:
                    self._validate_imported_ready_population(trial)
                except BaseException as error:
                    self._record_imported_population_failure(experiment_id, error)
                    raise
            return
        if not (
            trial.operator_approved
            and trial.population_ready
            and trial.status in {"approved", "running"}
            and trial.population_run_id is not None
            and trial.population_bundle_path is not None
        ):
            raise RuntimeError("durable operator approval is required")
        imported_population: tuple[FrozenPopulationManifestV1, Path] | None = None
        if not self.allow_population_build:
            try:
                imported_population = self._validate_imported_ready_population(trial)
            except BaseException as error:
                self._record_imported_population_failure(experiment_id, error)
                raise
        with self._lock:
            self._phase = "matrix"

        def matrix() -> None:
            current = self.database.load_performance_experiment(experiment_id)
            if current.status == "completed" and current.operator_approved:
                with self._lock:
                    self._phase = "completed"
                return
            capture_started = time.monotonic()
            if imported_population is None:
                population_dir = Path(trial.population_bundle_path or "")
                manifest = self.population_loader(population_dir)
                _validate_population_cohort(trial, manifest)
            else:
                manifest, population_dir = imported_population
            self.database.transition_performance(experiment_id, "running")
            self.database.append_performance_progress(
                experiment_id,
                phase="reference_workload",
                condition_index=None,
                condition_count=trial.condition_count,
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
            with (workload.path / "requests.template.jsonl").open(
                "r", encoding="utf-8"
            ) as source_requests:
                request_count = sum(bool(line.strip()) for line in source_requests)
            if trial.replay_request_limit is not None:
                request_count = min(request_count, trial.replay_request_limit)
            elapsed = max(1e-9, time.monotonic() - capture_started)
            self.database.append_performance_progress(
                experiment_id,
                phase="reference_workload_ready",
                condition_index=None,
                condition_count=trial.condition_count,
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
                    condition_count=trial.condition_count,
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

    def run_condition3_gate(self, experiment_id: UUID) -> None:
        """Run only a fresh representative rerun's exact Condition 3.

        This deliberately leaves the gate trial interrupted after preserving its
        evidence.  The formal nine-condition trial is a separate operator action.
        """
        trial = self.database.load_performance_experiment(experiment_id)
        trial.validate_runnable_contract()
        if (
            trial.evidence_scope != "representative_same_process_vs_split"
            or trial.status != "population_ready"
            or trial.operator_approved
            or trial.warmup_seconds != 30
            or trial.duration_seconds != 120
            or trial.target_rps != 5.0
            or trial.concurrent_users != 50
            or trial.training_micro_batch_size != 8
            or trial.sync_every_steps != 10
        ):
            raise ValueError(
                "Condition 3 gate requires a fresh unapproved 30s/120s/5RPS "
                "frozen-population rerun"
            )
        selected = [
            row
            for row in trial.conditions
            if row.condition_index == 3
            and row.topology == "same_process"
            and row.training_enabled
            and row.activation_enabled
            and row.run_id is None
            and row.status == "pending"
        ]
        if len(selected) != 1:
            raise ValueError("Condition 3 gate binding is missing, dirty, or ambiguous")
        manifest, population_dir = self._validate_imported_ready_population(trial)
        condition = selected[0]
        run_id = uuid5(experiment_id, "condition:3")

        def gate() -> None:
            workload = self.workload_freezer(
                trial, manifest, population_dir, self._stop.is_set
            )
            run_created = False
            try:
                self.database.create_condition_run(trial, condition, run_id)
                run_created = True
                self.database.clone_performance_population(trial, condition, run_id)
                self.database.bind_performance_condition(
                    str(experiment_id), str(condition.id), run_id
                )
                self.database.transition_performance(experiment_id, "running")
                self.database.transition_performance_condition(
                    experiment_id, condition.id, "running"
                )
                evidence = self.condition_runner(
                    trial, condition, run_id, workload, self._stop.is_set
                )
                if (
                    evidence.condition_id != condition.id
                    or evidence.run_id != run_id
                ):
                    raise ValueError("Condition 3 gate returned drifted execution identity")
                expected_warmup_count = round(
                    trial.warmup_seconds * trial.target_rps
                )
                expected_request_count = math.ceil(
                    trial.duration_seconds * trial.target_rps
                )
                validate_condition3_gate_evidence(
                    evidence.raw_evidence,
                    request_count=evidence.request_count,
                    p95_ms=evidence.p95_ms,
                    expected_warmup_count=expected_warmup_count,
                    expected_request_count=expected_request_count,
                    latency_safety_threshold_ms=5_000.0,
                )
                self.database.transition(run_id, "completed")
                self.database.transition_performance_condition(
                    experiment_id, condition.id, "completed"
                )
            except BaseException as error:
                if run_created:
                    try:
                        self.database.transition(
                            run_id, "failed", failure=str(error)[:1000]
                        )
                    except Exception:
                        pass
                try:
                    self.database.transition_performance_condition(
                        experiment_id, condition.id, "failed"
                    )
                except Exception:
                    pass
                raise
            receipt = {
                "schemaVersion": 1,
                "status": "passed",
                "condition": "same_process.training_and_activation.pgvector",
                "experimentId": str(experiment_id),
                "conditionId": str(condition.id),
                "runId": str(run_id),
                "requestCount": evidence.request_count,
                "p95Ms": evidence.p95_ms,
                "latencySafetyThresholdMs": 5_000.0,
                "cleanupVerified": True,
                "activationVerified": True,
                "offsetCoverageVerified": True,
                "finalKafkaLag": 0,
                "formalPerformanceClaim": False,
                "autoContinued": False,
            }
            gate_path = self.output_root / str(experiment_id) / "condition-3-gate.json"
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = gate_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            temporary.replace(gate_path)
            self.database.append_performance_progress(
                experiment_id,
                phase="condition3_gate_passed",
                condition_index=3,
                condition_count=trial.condition_count,
                seeded_articles=10_000,
                created_babels=10_000,
                indexed_babels=10_000,
                requested=evidence.request_count,
                completed=evidence.request_count,
                elapsed_seconds=0.0,
                recent_rate=0.0,
                draining=False,
                telemetry=receipt,
            )
            # Existing schemas intentionally have no gate-passed terminal state.
            # "interrupted" makes the six-condition rerun non-publishable while
            # preserving the successful bounded receipt.
            self.database.transition_performance(experiment_id, "interrupted")
            with self._lock:
                self._phase = "condition3_gate_passed"

        self._launch(
            experiment_id,
            gate,
            handoff_from_phase="condition3_gate_ready",
            handoff_start_phase="condition3_gate_starting",
        )

    def prepare_condition3_gate(
        self, *, source_trial_id: UUID, gate_trial_id: UUID
    ) -> None:
        """Freeze the source workload and provision a separate bounded gate trial."""
        from .performance_rerun import create_representative_rerun

        if source_trial_id == gate_trial_id:
            raise ValueError("Condition 3 gate trial must use a fresh identity")
        with self._lock:
            binding = self._condition3_preparation_binding
            if binding is not None and binding[1] == gate_trial_id:
                if binding[0] != source_trial_id:
                    raise ValueError("Condition 3 gate source trial differs")
                if self._phase == "condition3_gate_ready":
                    return
        try:
            existing_gate = self.database.load_performance_experiment(gate_trial_id)
        except KeyError:
            existing_gate = None
        if existing_gate is not None:
            existing_gate.validate_runnable_contract()
            if (
                existing_gate.status != "population_ready"
                or existing_gate.operator_approved
                or existing_gate.evidence_scope
                != "representative_same_process_vs_split"
                or existing_gate.source_trial_id != source_trial_id
                or existing_gate.warmup_seconds != 30
                or existing_gate.duration_seconds != 120
                or existing_gate.target_rps != 5.0
                or existing_gate.training_micro_batch_size != 8
                or existing_gate.sync_every_steps != 10
            ):
                raise ValueError("existing Condition 3 gate binding differs")
            with self._lock:
                if (
                    self._thread is not None
                    and self._thread.is_alive()
                    and self._experiment_id != gate_trial_id
                ):
                    raise RuntimeError("another performance experiment is active")
                binding = self._condition3_preparation_binding
                if (
                    binding is not None
                    and binding[1] == gate_trial_id
                    and binding[0] != source_trial_id
                ):
                    raise ValueError("Condition 3 gate source trial differs")
                self._condition3_preparation_binding = (
                    source_trial_id,
                    gate_trial_id,
                )
                self._experiment_id = gate_trial_id
                self._phase = "condition3_gate_ready"
                self._failure = None
            return
        source = self.database.load_performance_experiment(source_trial_id)
        source.validate_formal_defaults()
        if (
            source.status != "population_ready"
            or source.operator_approved
            or source.warmup_seconds != 30
            or source.duration_seconds != 120
            or source.target_rps != 5.0
            or source.training_micro_batch_size != 8
            or source.sync_every_steps != 10
        ):
            raise ValueError("Condition 3 source trial differs from the formal gate")
        manifest, population_dir = self._validate_imported_ready_population(source)

        with self._lock:
            binding = self._condition3_preparation_binding
            if binding is not None and binding[1] == gate_trial_id:
                if binding[0] != source_trial_id:
                    raise ValueError("Condition 3 gate source trial differs")
            elif (
                self._thread is not None
                and self._thread.is_alive()
                and self._experiment_id != gate_trial_id
            ):
                raise RuntimeError("another performance experiment is active")
            else:
                self._condition3_preparation_binding = (
                    source_trial_id,
                    gate_trial_id,
                )

        def prepare() -> None:
            self.workload_freezer(
                source, manifest, population_dir, self._stop.is_set
            )
            create_representative_rerun(
                database=self.database,
                source_trial_id=source_trial_id,
                rerun_id=gate_trial_id,
                state_root=self.output_root,
                warmup_seconds=30,
                duration_seconds=120,
                target_rps=5.0,
            )
            with self._lock:
                self._experiment_id = gate_trial_id
                self._phase = "condition3_gate_ready"

        self._launch(gate_trial_id, prepare)

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

    def prepare_representative_rerun(
        self,
        *,
        source_trial_id: UUID,
        rerun_id: UUID,
        matrix: str,
        warmup_seconds: int,
        duration_seconds: int,
        target_rps: float,
    ) -> None:
        """Stage verified frozen inputs; execution still waits on dashboard approval."""
        from .performance_rerun import (
            REPRESENTATIVE_SCOPE,
            SPLIT_SMOKE_SCOPE,
            create_representative_rerun,
        )

        if matrix not in {"2x3", "split-smoke"}:
            raise ValueError("representative rerun matrix is unsupported")
        create_representative_rerun(
            database=self.database,
            source_trial_id=source_trial_id,
            rerun_id=rerun_id,
            state_root=self.output_root,
            evidence_scope=(
                REPRESENTATIVE_SCOPE if matrix == "2x3" else SPLIT_SMOKE_SCOPE
            ),
            warmup_seconds=warmup_seconds,
            duration_seconds=duration_seconds,
            target_rps=target_rps,
        )
        self.start(rerun_id)

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
        "/v1/performance/{source_trial_id}/prepare-rerun/{rerun_id}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def prepare_rerun(
        source_trial_id: UUID,
        rerun_id: UUID,
        matrix: str = "2x3",
        warmup_seconds: int = 5,
        duration_seconds: int = 25,
        target_rps: float = 5.0,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(
            lambda: manager.prepare_representative_rerun(
                source_trial_id=source_trial_id,
                rerun_id=rerun_id,
                matrix=matrix,
                warmup_seconds=warmup_seconds,
                duration_seconds=duration_seconds,
                target_rps=target_rps,
            )
        )

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

    @app.post(
        "/v1/performance/{source_trial_id}/prepare-condition-3-gate/{gate_trial_id}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def prepare_condition3_gate(
        source_trial_id: UUID,
        gate_trial_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(
            lambda: manager.prepare_condition3_gate(
                source_trial_id=source_trial_id, gate_trial_id=gate_trial_id
            )
        )

    @app.post(
        "/v1/performance/{experiment_id}/condition-3-gate",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def condition3_gate(
        experiment_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        return invoke(lambda: manager.run_condition3_gate(experiment_id))

    @app.get("/v1/performance/status")
    def worker_status(
        x_babel_worker_token: str | None = Header(default=None),
    ) -> dict[str, str | None]:
        authorize(x_babel_worker_token)
        return manager.status

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
    "validate_condition3_gate_evidence",
]
