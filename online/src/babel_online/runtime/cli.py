"""CLI for the loopback online worker; run launch remains dashboard-only."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import signal
import sys
import time
from pathlib import Path
import urllib.request
from uuid import UUID

from ..model.artifact import (
    LoadedRealArtifact,
    build_real_original_manifest,
)
from ..model.distilled_artifact import (
    REAL_ARTIFACT_ID,
    REAL_ARTIFACT_REVISION,
    REAL_MODEL_REPO,
    DistilledArtifactV1,
)
from ..model.qwen_encoder import Qwen100Encoder
from ..feedback import KafkaFeedbackConsumer
from .control import create_control_app
from .database import RuntimeDatabase, load_configured_model_artifact
from .dataset_bundle import (
    SCALE_DATASET_CONFIG,
    acquire_pinned_bundle,
    load_demo_dataset_bundle,
    load_scale_dataset_bundle,
)
from .performance_export import (
    export_completed_performance_trial,
    write_trial_bundle_inputs,
)
from .worker import FridayDemoRuntime, WorkerManager
from .coordinator import coordinator_from_environment
from .supervisor import PerRunTopologyManager, build_service_commands
from .topology import (
    PlacementManifestV1,
    ResourceRequest,
    ServiceCommand,
    Topology,
    TopologySupervisor,
)


REAL_ONLINE_MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
REAL_EMBEDDING_SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _load_real_launch(
    database: RuntimeDatabase,
    *,
    token: str,
    artifact_cache_dir: str | Path,
    model_cache_dir: str | Path,
    device: str,
) -> tuple[LoadedRealArtifact, Qwen100Encoder]:
    """Load one accepted private artifact and one reusable encoder instance."""
    artifact = DistilledArtifactV1.load(
        repo_id=REAL_MODEL_REPO,
        revision=REAL_ARTIFACT_REVISION,
        artifact_id=REAL_ARTIFACT_ID,
        token=token,
        cache_dir=artifact_cache_dir,
    )
    manifest = build_real_original_manifest(
        artifact,
        model_id=REAL_ONLINE_MODEL_ID,
        embedding_space_id=REAL_EMBEDDING_SPACE_ID,
    )
    database.bootstrap_real_model(
        manifest,
        artifact_manifest_path=artifact.path_for("artifact_manifest.json"),
    )
    encoder = Qwen100Encoder.from_artifact(
        artifact,
        token=token,
        device=device,
        model_cache_dir=model_cache_dir,
    )
    return LoadedRealArtifact(manifest, artifact), encoder


def _load_real_lineage(
    database: RuntimeDatabase,
    original: LoadedRealArtifact,
    selected_model_id: UUID,
) -> list[LoadedRealArtifact]:
    if selected_model_id == original.manifest.modelId:
        return [original]
    reverse = []
    current = selected_model_id
    while current != original.manifest.modelId:
        descriptor, descriptor_path = database.load_real_child_artifact(current)
        reverse.append(
            LoadedRealArtifact(
                descriptor.childManifest,
                original.distilled_artifact,
                descriptor_path.parent / descriptor.onlineStatePath,
            )
        )
        current = descriptor.childManifest.parentModelId
    return [original, *reversed(reverse)]


def record_same_process_placement(
    *,
    state_root: str | Path,
    serving_version: str,
    trainer_version: str,
) -> PlacementManifestV1:
    """Persist truthful monolith placement before accepting dashboard runs."""
    activated_at_ns = time.time_ns()
    running = TopologySupervisor(state_root=state_root).launch(
        topology=Topology.SAME_PROCESS,
        commands={
            "serving": ServiceCommand(
                role="serving",
                argv=("in-process-recommendation-serving",),
                version=serving_version,
            ),
            "trainer": ServiceCommand(
                role="trainer",
                argv=("in-process-online-training",),
                version=trainer_version,
            ),
        },
        serving_probe=lambda: 200,
        published_at_ns=activated_at_ns,
        activated_at_ns=activated_at_ns,
        model_version=0,
    )
    return running.manifest


def _service_version(environment_name: str, role: str) -> str:
    configured = os.environ.get(environment_name, "").strip()
    if configured and configured.casefold() != "unknown":
        return configured
    try:
        package = importlib.metadata.version("babel-online")
    except importlib.metadata.PackageNotFoundError:
        package = "source-tree"
    return f"{role}:babel-online/{package}"


def _serve() -> None:
    import uvicorn

    database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    model_mode = os.environ.get("BABEL_ONLINE_MODEL_MODE", "fixture")
    dataset_config = os.environ.get("BABEL_ONLINE_DATASET_CONFIG", "demo_crosswalk")
    if (model_mode == "real_qwen") != (dataset_config == SCALE_DATASET_CONFIG):
        raise SystemExit(
            "real_qwen requires crosswalk_2026_06_07; fixture requires demo_crosswalk"
        )
    real_launch: LoadedRealArtifact | None = None
    qwen_encoder: Qwen100Encoder | None = None
    if model_mode == "fixture":
        configured_artifact = load_configured_model_artifact(
            _required("BABEL_ONLINE_MODEL_ARTIFACT")
        )
        database.bootstrap_model(configured_artifact)
    elif model_mode == "real_qwen":
        token = _required("HF_TOKEN")
        real_launch, qwen_encoder = _load_real_launch(
            database,
            token=token,
            artifact_cache_dir=os.environ.get(
                "BABEL_ONLINE_MODEL_ARTIFACT_CACHE",
                "state/online/cache/model-artifact",
            ),
            model_cache_dir=os.environ.get(
                "BABEL_ONLINE_QWEN_CACHE",
                "state/online/cache/qwen-base",
            ),
            device=os.environ.get("BABEL_ONLINE_QWEN_DEVICE", "cpu"),
        )
    else:
        raise SystemExit("BABEL_ONLINE_MODEL_MODE must be fixture or real_qwen")
    revision = _required("BABEL_ONLINE_DATASET_REVISION")
    dataset_repo = _required("BABEL_ONLINE_DATASET_REPOSITORY")
    dataset_root = acquire_pinned_bundle(
        repo_id=dataset_repo,
        revision=revision,
        token=_required("HF_TOKEN"),
        cache_dir=os.environ.get(
            "BABEL_ONLINE_HF_CACHE",
            "state/online/cache/dataset",
        ),
    )
    loader = (
        load_scale_dataset_bundle
        if dataset_config == SCALE_DATASET_CONFIG
        else load_demo_dataset_bundle
    )
    bundle = loader(
        dataset_root,
        dataset_repository=dataset_repo,
        dataset_config=dataset_config,
        dataset_revision=revision,
    )
    kafka = os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    recommendation_port = int(os.environ.get("BABEL_RECOMMENDATION_PORT", "8791"))

    def runtime_factory(config, stop_event):
        if real_launch is not None:
            lineage = _load_real_lineage(
                database, real_launch, config.startingModelId
            )
        else:
            lineage = database.load_model_lineage(config.startingModelId)
        return FridayDemoRuntime(
            config=config,
            database=database,
            bundle=bundle,
            model_lineage=lineage,
            kafka_bootstrap_servers=kafka,
            recommendation_port=recommendation_port,
            stop_event=stop_event,
            qwen_encoder=qwen_encoder,
        )

    placement_root = Path(
        os.environ.get("BABEL_ONLINE_STATE_ROOT", "state/online/topology")
    )
    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle,
        runtime_factory=runtime_factory,
        placement_factory=lambda run_id: record_same_process_placement(
            state_root=placement_root / str(run_id),
            serving_version=_service_version(
                "BABEL_ONLINE_SERVING_VERSION", "serving"
            ),
            trainer_version=_service_version(
                "BABEL_ONLINE_TRAINER_VERSION", "trainer"
            ),
        ),
    )
    app = create_control_app(manager, token=_required("BABEL_ONLINE_WORKER_TOKEN"))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("BABEL_ONLINE_WORKER_PORT", "8790")),
        log_level="info",
    )


def _cpu_affinity(name: str) -> tuple[int, ...]:
    raw = os.environ.get(name, "").strip()
    return tuple(int(value) for value in raw.split(",") if value.strip())


def _memory_limit(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else None


def _gpu_devices(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    )


def _supervise() -> None:
    """Launch the default genuine same-host split from explicit role commands."""
    import uvicorn
    from .database import RuntimeDatabase

    topology = Topology.parse(os.environ.get("BABEL_RUNTIME_TOPOLOGY"))
    if topology is Topology.SAME_PROCESS:
        _serve()
        return
    commands = build_service_commands(
        serving_command=os.environ.get(
            "BABEL_ONLINE_SERVING_COMMAND",
            "babel-recommendation-server --run-id {run_id}",
        ),
        trainer_command=os.environ.get(
            "BABEL_ONLINE_TRAINER_COMMAND",
            "babel-online-trainer --run-id {run_id} --activation-enabled true",
        ),
        serving_version=_service_version("BABEL_ONLINE_SERVING_VERSION", "serving"),
        trainer_version=_service_version("BABEL_ONLINE_TRAINER_VERSION", "trainer"),
    )
    resources = {
        "serving": ResourceRequest(
            cpuAffinity=_cpu_affinity("BABEL_SERVING_CPU_AFFINITY"),
            memoryLimitBytes=_memory_limit("BABEL_SERVING_MEMORY_LIMIT_BYTES"),
            gpuDevices=_gpu_devices("BABEL_SERVING_GPU_DEVICES"),
        ),
        "trainer": ResourceRequest(
            cpuAffinity=_cpu_affinity("BABEL_TRAINER_CPU_AFFINITY"),
            memoryLimitBytes=_memory_limit("BABEL_TRAINER_MEMORY_LIMIT_BYTES"),
            gpuDevices=_gpu_devices("BABEL_TRAINER_GPU_DEVICES"),
        ),
    }
    serving_health = os.environ.get(
        "BABEL_ONLINE_SERVING_HEALTH_URL", "http://127.0.0.1:8791/health"
    )

    def serving_probe() -> int:
        with urllib.request.urlopen(serving_health, timeout=2) as response:
            return int(response.status)

    topology_database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    manager = PerRunTopologyManager(
        topology=topology,
        commands=commands,
        resources=resources,
        state_root=os.environ.get("BABEL_ONLINE_STATE_ROOT", "state/online/topology"),
        serving_probe=serving_probe,
        coordinator_factory=coordinator_from_environment,
        starting_reporter=lambda run_id: topology_database.transition(
            run_id, "starting"
        ),
        running_reporter=lambda run_id: topology_database.transition(run_id, "running"),
        stopped_reporter=lambda run_id: topology_database.transition(run_id, "completed"),
        failure_reporter=lambda run_id, error: topology_database.transition(
            run_id, "failed", failure=str(error)
        ),
        activation_timeout_seconds=float(
            os.environ.get("BABEL_ONLINE_ACTIVATION_TIMEOUT_SECONDS", "60")
        ),
    )
    app = create_control_app(manager, token=_required("BABEL_ONLINE_WORKER_TOKEN"))
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("BABEL_ONLINE_WORKER_PORT", "8790")),
            log_level="info",
        )
    finally:
        if manager.active_run_id is not None:
            manager.request_stop(manager.active_run_id)


def _boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("value must be true or false")
    return normalized == "true"


def _performance_condition(argv: list[str]) -> None:
    from .performance_condition import execute_live_condition

    parser = argparse.ArgumentParser(prog="babel-online performance-condition")
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument("--condition-id", required=True, type=UUID)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--training-enabled", required=True, type=_boolean)
    parser.add_argument("--activation-enabled", required=True, type=_boolean)
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--duration-seconds", required=True, type=int)
    parser.add_argument("--target-rps", required=True, type=float)
    parser.add_argument("--evidence", required=True, type=Path)
    arguments = parser.parse_args(argv)
    database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    trial = database.load_performance_experiment(arguments.experiment_id)
    try:
        condition = next(
            row for row in trial.conditions if row.id == arguments.condition_id
        )
    except StopIteration as error:
        raise SystemExit("condition is outside the saved performance trial") from error
    expected = (
        condition.run_id,
        condition.topology,
        condition.training_enabled,
        condition.activation_enabled,
        trial.duration_seconds,
        trial.target_rps,
    )
    actual = (
        arguments.run_id,
        arguments.topology,
        arguments.training_enabled,
        arguments.activation_enabled,
        arguments.duration_seconds,
        arguments.target_rps,
    )
    if expected != actual:
        raise SystemExit("condition command differs from its durable dashboard binding")
    def interrupt(_signum, _frame) -> None:
        raise InterruptedError("performance condition received a graceful-stop signal")

    prior = {
        selected: signal.getsignal(selected)
        for selected in (signal.SIGTERM, signal.SIGINT)
    }
    for selected in prior:
        signal.signal(selected, interrupt)
    try:
        execute_live_condition(
            database=database,
            trial=trial,
            condition=condition,
            run_id=arguments.run_id,
            frozen_workload_path=arguments.workload,
            evidence_path=arguments.evidence,
            serving_port=int(os.environ.get("BABEL_RECOMMENDATION_PORT", "8791")),
            kafka_bootstrap_servers=os.environ.get(
                "BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092"
            ),
        )
    finally:
        for selected, handler in prior.items():
            signal.signal(selected, handler)


def _performance_worker() -> None:
    import uvicorn

    from .performance_condition import RealWorkloadFreezer
    from .performance_worker import (
        PerformanceConditionCommandRunner,
        PerformanceJobManager,
        RealPopulationBuilder,
        create_performance_control_app,
    )

    database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    token = _required("HF_TOKEN")
    real, encoder = _load_real_launch(
        database,
        token=token,
        artifact_cache_dir=os.environ.get(
            "BABEL_ONLINE_MODEL_ARTIFACT_CACHE",
            "state/online/cache/model-artifact",
        ),
        model_cache_dir=os.environ.get(
            "BABEL_ONLINE_QWEN_CACHE", "state/online/cache/qwen-base"
        ),
        device=os.environ.get("BABEL_ONLINE_QWEN_DEVICE", "cpu"),
    )
    dataset_repo = _required("BABEL_ONLINE_DATASET_REPOSITORY")
    dataset_revision = _required("BABEL_ONLINE_DATASET_REVISION")
    dataset_root = acquire_pinned_bundle(
        repo_id=dataset_repo,
        revision=dataset_revision,
        token=token,
        cache_dir=os.environ.get(
            "BABEL_ONLINE_HF_CACHE", "state/online/cache/dataset"
        ),
    )
    bundle = load_scale_dataset_bundle(
        dataset_root,
        dataset_repository=dataset_repo,
        dataset_config=SCALE_DATASET_CONFIG,
        dataset_revision=dataset_revision,
    )
    output_root = Path(
        os.environ.get("BABEL_PERFORMANCE_STATE_ROOT", "state/performance")
    )
    kafka = os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    serving_port = int(os.environ.get("BABEL_RECOMMENDATION_PORT", "8791"))
    manager = PerformanceJobManager(
        database=database,
        output_root=output_root,
        population_builder=RealPopulationBuilder(
            database=database,
            bundle=bundle,
            model=real.manifest,
            encoder=encoder,
            output_root=output_root,
        ),
        workload_freezer=RealWorkloadFreezer(
            database=database,
            bundle=bundle,
            output_root=output_root,
            kafka_bootstrap_servers=kafka,
            serving_port=serving_port,
        ),
        condition_runner=PerformanceConditionCommandRunner(
            output_root=output_root,
            executable=os.environ.get("BABEL_ONLINE_EXECUTABLE", "babel-online"),
        ),
    )
    app = create_performance_control_app(
        manager, token=_required("BABEL_PERFORMANCE_WORKER_TOKEN")
    )
    uvicorn.run(app, host="127.0.0.1", port=8792, log_level="info")


def _performance_command(argv: list[str]) -> None:
    """Retry one dashboard worker control action from the operator shell."""
    parser = argparse.ArgumentParser(prog="babel-online performance-command")
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument(
        "--action",
        required=True,
        choices=("start", "graceful-stop", "approve-next-scale"),
    )
    arguments = parser.parse_args(argv)
    request = urllib.request.Request(
        "http://127.0.0.1:8792/v1/performance/"
        f"{arguments.experiment_id}/{arguments.action}",
        data=b"",
        method="POST",
        headers={
            "X-Babel-Worker-Token": _required("BABEL_PERFORMANCE_WORKER_TOKEN")
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if int(response.status) != 202:
            raise RuntimeError(
                f"performance worker rejected operator action: {response.status}"
            )


def _performance_export(argv: list[str]) -> None:
    """Export one completed formal matrix from exact acknowledged Kafka ranges."""
    parser = argparse.ArgumentParser(prog="babel-online performance-export")
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--kafka-group")
    parser.add_argument(
        "--selected-condition-index", type=int, choices=(3, 6, 9), default=6
    )
    parser.add_argument("--bundle-inputs", type=Path)
    arguments = parser.parse_args(argv)
    database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    group_id = arguments.kafka_group or (
        f"babel-performance-export-{arguments.experiment_id}"
    )
    consumer = KafkaFeedbackConsumer(
        os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092"),
        group_id=group_id,
    )
    try:
        result = export_completed_performance_trial(
            database=database,
            experiment_id=arguments.experiment_id,
            evidence_root=arguments.evidence_root,
            output_root=arguments.output_root,
            feedback_source=consumer,
        )
    finally:
        consumer.close()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    receipt = {
        "experimentId": str(arguments.experiment_id),
        "feedbackRecords": int(manifest["records"]),
        "canonicalEdges": int(manifest["canonicalEdges"]),
        "feedbackParquet": str(result.parquet_path.resolve()),
        "edgesParquet": str(result.edge_parquet_path.resolve()),
        "feedbackExportManifest": str(result.manifest_path.resolve()),
    }
    if arguments.bundle_inputs is not None:
        generated = write_trial_bundle_inputs(
            database=database,
            experiment_id=arguments.experiment_id,
            evidence_root=arguments.evidence_root,
            feedback_parquet=result.parquet_path,
            edges_parquet=result.edge_parquet_path,
            feedback_export_manifest=result.manifest_path,
            selected_condition_index=arguments.selected_condition_index,
            output_path=arguments.bundle_inputs,
        )
        receipt.update(
            bundleInputs=str(generated.resolve()),
            selectedConditionIndex=arguments.selected_condition_index,
        )
    print(json.dumps(receipt, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    command = values.pop(0) if values else "supervise"
    if command == "serve":
        if values:
            raise SystemExit("serve does not accept positional arguments")
        _serve()
    elif command == "supervise":
        if values:
            raise SystemExit("supervise does not accept positional arguments")
        _supervise()
    elif command == "performance-worker":
        if values:
            raise SystemExit("performance-worker does not accept positional arguments")
        _performance_worker()
    elif command == "performance-condition":
        _performance_condition(values)
    elif command == "performance-command":
        _performance_command(values)
    elif command == "performance-export":
        _performance_export(values)
    else:
        raise SystemExit(f"unsupported babel-online command: {command}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
