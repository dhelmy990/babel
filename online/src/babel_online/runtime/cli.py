"""CLI for the loopback online worker; run launch remains dashboard-only."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
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
from .control import create_control_app
from .database import RuntimeDatabase, load_configured_model_artifact
from .dataset_bundle import (
    SCALE_DATASET_CONFIG,
    acquire_pinned_bundle,
    load_demo_dataset_bundle,
    load_scale_dataset_bundle,
)
from .worker import FridayDemoRuntime, WorkerManager


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
            if config.startingModelId != real_launch.manifest.modelId:
                raise RuntimeError(
                    "scale launch selected a model other than the loaded Qwen original"
                )
            lineage = [real_launch]
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

    manager = WorkerManager(
        database=database,
        dataset_bundle=bundle,
        runtime_factory=runtime_factory,
    )
    app = create_control_app(manager, token=_required("BABEL_ONLINE_WORKER_TOKEN"))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("BABEL_ONLINE_WORKER_PORT", "8790")),
        log_level="info",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="babel-online")
    parser.add_argument("command", choices=["serve"])
    arguments = parser.parse_args(argv)
    if arguments.command == "serve":
        _serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
