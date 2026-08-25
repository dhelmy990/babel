"""CLI for the loopback online worker; run launch remains dashboard-only."""

from __future__ import annotations

import argparse
import os

from .control import create_control_app
from .database import RuntimeDatabase, load_configured_model_artifact
from .dataset_bundle import acquire_pinned_bundle, load_demo_dataset_bundle
from .worker import FridayDemoRuntime, WorkerManager


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _serve() -> None:
    import uvicorn

    database = RuntimeDatabase(_required("BABEL_DATABASE_URL"))
    configured_artifact = load_configured_model_artifact(
        _required("BABEL_ONLINE_MODEL_ARTIFACT")
    )
    database.bootstrap_model(configured_artifact)
    revision = _required("BABEL_ONLINE_DATASET_REVISION")
    dataset_root = acquire_pinned_bundle(
        repo_id=_required("BABEL_ONLINE_DATASET_REPOSITORY"),
        revision=revision,
        token=_required("HF_TOKEN"),
        cache_dir=os.environ.get(
            "BABEL_ONLINE_HF_CACHE",
            "/home/dhelmy990/Data/babel-data/cache/online-dataset",
        ),
    )
    bundle = load_demo_dataset_bundle(
        dataset_root,
        dataset_repository=_required("BABEL_ONLINE_DATASET_REPOSITORY"),
        dataset_config="demo_crosswalk",
        dataset_revision=revision,
    )
    kafka = os.environ.get("BABEL_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:29092")
    recommendation_port = int(os.environ.get("BABEL_RECOMMENDATION_PORT", "8791"))

    def runtime_factory(config, stop_event):
        return FridayDemoRuntime(
            config=config,
            database=database,
            bundle=bundle,
            model_lineage=database.load_model_lineage(config.startingModelId),
            kafka_bootstrap_servers=kafka,
            recommendation_port=recommendation_port,
            stop_event=stop_event,
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
