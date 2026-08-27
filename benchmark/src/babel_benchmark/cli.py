"""CLI for real loopback replays and deterministic reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from .analysis import analyze, render_markdown
from .contracts import (
    ConditionTelemetryV1,
    CreatedBabelV1,
    dump_jsonl,
    load_benchmark_manifest,
    load_jsonl,
    load_request_measurements,
)
from .replay import CandidateUniverse, ReplayCorpus
from .resources import PeriodicResourceCollector, default_resource_sampler
from .runner import (
    AlreadyConfiguredConditionDriver,
    AsyncHttpxTransport,
    HttpxTransport,
    run_concurrent_condition,
    run_condition,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babel-friday-benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser(
        "replay", help="run one externally configured condition"
    )
    replay.add_argument("--manifest", required=True, type=Path)
    replay.add_argument("--requests", required=True, type=Path)
    replay.add_argument("--candidate-universe", required=True, type=Path)
    replay.add_argument("--condition", required=True)
    replay.add_argument("--measurements", required=True, type=Path)

    concurrent = commands.add_parser(
        "concurrent-replay", help="run a bounded closed/open-loop async POST schedule"
    )
    concurrent.add_argument("--manifest", required=True, type=Path)
    concurrent.add_argument("--requests", required=True, type=Path)
    concurrent.add_argument("--candidate-universe", required=True, type=Path)
    concurrent.add_argument("--condition", required=True)
    concurrent.add_argument("--measurements", required=True, type=Path)
    concurrent.add_argument(
        "--schedule-mode", choices=("closed_loop", "open_loop"), default=None
    )
    concurrent.add_argument("--max-in-flight", type=int, default=None)
    concurrent.add_argument("--resources", type=Path)
    concurrent.add_argument("--service-pid", action="append", default=[])
    concurrent.add_argument("--resource-interval-seconds", type=float, default=1.0)
    concurrent.add_argument("--maximum-resource-samples", type=int, default=100_000)

    live = commands.add_parser(
        "live-replay", help="run real Kafka training interference with loopback POSTs"
    )
    live.add_argument("--manifest", required=True, type=Path)
    live.add_argument("--requests", required=True, type=Path)
    live.add_argument("--candidate-universe", required=True, type=Path)
    live.add_argument("--condition", required=True)
    live.add_argument("--measurements", required=True, type=Path)
    live.add_argument("--telemetry", required=True, type=Path)
    live.add_argument("--dsn", required=True)
    live.add_argument("--kafka-bootstrap", required=True)
    live.add_argument("--feedback", required=True, type=Path)
    live.add_argument("--run-id", required=True, type=UUID)
    live.add_argument("--model-version", required=True, type=int)
    live.add_argument("--sync-root", type=Path)
    live.add_argument("--sync-every-steps", type=int, default=50)
    live.add_argument("--publish-limit", type=int, default=4_000)

    report = commands.add_parser("report", help="summarize raw condition JSONL")
    report.add_argument("--measurements", required=True, nargs="+", type=Path)
    report.add_argument("--telemetry", nargs="*", default=[], type=Path)
    report.add_argument("--summary", required=True, type=Path)
    report.add_argument("--markdown", required=True, type=Path)

    trial = commands.add_parser(
        "trial-bundle-build", help="validate and build one formal cohort trial bundle"
    )
    trial.add_argument("--output-root", required=True, type=Path)
    trial.add_argument("--inputs", type=Path)
    trial.add_argument("--trial-id", type=UUID)
    trial.add_argument("--evidence", nargs="+", type=Path)
    trial.add_argument("--population-manifest", type=Path)
    trial.add_argument("--feedback-parquet", type=Path)
    trial.add_argument("--edges-parquet", type=Path)
    trial.add_argument("--feedback-export-manifest", type=Path)
    trial.add_argument("--model-manifest", type=Path)
    trial.add_argument("--model-artifact-root", type=Path)
    trial.add_argument("--selected-child", type=Path)
    trial.add_argument("--model-repository")
    trial.add_argument("--model-revision")
    trial.add_argument("--dataset-repository")
    trial.add_argument("--dataset-revision")

    publish = commands.add_parser(
        "trial-bundle-publish", help="upload and remotely verify one built trial bundle"
    )
    publish.add_argument("--bundle-root", required=True, type=Path)
    publish.add_argument("--repo-id", required=True)
    publish.add_argument("--revision", default="main")
    publish.add_argument("--token-env", default="HF_TOKEN")

    attach = commands.add_parser(
        "trial-bundle-attach",
        help="attach a remotely verified bundle receipt to its saved trial",
    )
    attach.add_argument("--receipt", required=True, type=Path)
    attach.add_argument("--trial-id", required=True, type=UUID)
    attach.add_argument("--base-url", default="http://127.0.0.1:8787")
    attach.add_argument("--nonce-env", default="BABEL_ADMIN_NONCE")

    retrieval = commands.add_parser(
        "retrieval-compare",
        help="compare pgvector and hnswlib over one frozen snapshot (retrieval only)",
    )
    retrieval.add_argument("--population", required=True, type=Path)
    retrieval.add_argument("--formal-pgvector-evidence", required=True, type=Path)
    retrieval.add_argument("--dsn", required=True)
    retrieval.add_argument("--output", required=True, type=Path)
    retrieval.add_argument("--query-count", type=int, default=100)
    retrieval.add_argument("--warmup-passes", type=int, default=1)
    retrieval.add_argument("--measurement-passes", type=int, default=3)
    return parser


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _publication_receipt_json(receipt: Any) -> dict[str, object]:
    document = asdict(receipt)
    document["backendArtifactReceipt"] = {
        "artifactSha256": receipt.artifact_sha256,
        "remoteHfCommitSha": receipt.commit_sha,
        "remoteHfBundlePath": receipt.bundle_path,
    }
    return document


def _attach_backend_artifact_receipt(
    *,
    base_url: str,
    trial_id: UUID,
    receipt: dict[str, object],
    admin_nonce: str,
    transport: Any | None = None,
) -> dict[str, object]:
    normalized = base_url.rstrip("/")
    parsed = urlsplit(normalized)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if normalized != origin or origin not in {
        "http://127.0.0.1:8787",
        "http://localhost:8787",
    }:
        raise ValueError("dashboard artifact endpoint must be loopback port 8787")
    if not admin_nonce:
        raise ValueError("dashboard admin nonce is required")
    required = {
        "artifactSha256",
        "remoteHfCommitSha",
        "remoteHfBundlePath",
    }
    if set(receipt) != required or any(
        not isinstance(receipt[field], str) or not receipt[field]
        for field in required
    ):
        raise ValueError("backend artifact receipt fields differ")
    if receipt["remoteHfBundlePath"] != f"runs/{trial_id}":
        raise ValueError("remote bundle path differs from trial")
    if transport is None:
        import httpx

        transport = httpx
    response = transport.request(
        "POST",
        f"{normalized}/admin/api/v1/performance/{trial_id}/artifact",
        headers={
            "Origin": origin,
            "X-Babel-Admin-Nonce": admin_nonce,
        },
        json=receipt,
        timeout=10.0,
    )
    if int(response.status_code) != 200:
        raise RuntimeError(
            f"dashboard artifact attachment failed: HTTP {response.status_code}"
        )
    document = response.json()
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("trial"), dict)
        or document["trial"].get("experimentId") != str(trial_id)
    ):
        raise RuntimeError("dashboard artifact attachment returned another trial")
    return document


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "retrieval-compare":
        from .retrieval_comparison import run_live_retrieval_comparison

        report = run_live_retrieval_comparison(
            population_path=args.population,
            formal_pgvector_evidence_path=args.formal_pgvector_evidence,
            dsn=args.dsn,
            query_count=args.query_count,
            warmup_passes=args.warmup_passes,
            measurement_passes=args.measurement_passes,
        )
        _write(args.output, json.dumps(report, indent=2, sort_keys=True) + "\n")
        return 0
    if args.command == "trial-bundle-attach":
        try:
            publication = json.loads(args.receipt.read_text(encoding="utf-8"))
            receipt = publication["backendArtifactReceipt"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("saved publication receipt is invalid") from error
        if not isinstance(receipt, dict):
            raise ValueError("saved publication receipt is invalid")
        admin_nonce = os.environ.get(args.nonce_env)
        if not admin_nonce:
            raise ValueError(f"admin nonce environment is unset: {args.nonce_env}")
        attached = _attach_backend_artifact_receipt(
            base_url=args.base_url,
            trial_id=args.trial_id,
            receipt=receipt,
            admin_nonce=admin_nonce,
        )
        print(json.dumps(attached, sort_keys=True))
        return 0
    if args.command == "trial-bundle-build":
        from .trial_bundle import (
            FormalPins,
            build_formal_trial_bundle,
            load_formal_trial_bundle_inputs,
        )

        manual_names = (
            "trial_id",
            "evidence",
            "population_manifest",
            "feedback_parquet",
            "edges_parquet",
            "feedback_export_manifest",
            "model_manifest",
            "model_artifact_root",
            "selected_child",
            "model_repository",
            "model_revision",
            "dataset_repository",
            "dataset_revision",
        )
        if args.inputs is not None:
            if any(getattr(args, name) is not None for name in manual_names):
                raise ValueError("--inputs cannot be combined with manual bundle fields")
            inputs = load_formal_trial_bundle_inputs(args.inputs)
            options = {
                "trial_id": inputs.trial_id,
                "evidence_paths": inputs.evidence_paths,
                "population_manifest_path": inputs.population_manifest,
                "feedback_parquet": inputs.feedback_parquet,
                "edges_parquet": inputs.edges_parquet,
                "feedback_export_manifest_path": inputs.feedback_export_manifest,
                "model_manifest": inputs.model_manifest,
                "model_artifact_root": inputs.model_artifact_root,
                "selected_child_path": inputs.selected_child,
                "pins": inputs.pins,
            }
        else:
            missing = [name for name in manual_names if getattr(args, name) is None]
            if missing:
                raise ValueError(
                    "manual trial bundle fields are incomplete: " + ", ".join(missing)
                )
            options = {
                "trial_id": args.trial_id,
                "evidence_paths": args.evidence,
                "population_manifest_path": args.population_manifest,
                "feedback_parquet": args.feedback_parquet,
                "edges_parquet": args.edges_parquet,
                "feedback_export_manifest_path": args.feedback_export_manifest,
                "model_manifest": args.model_manifest,
                "model_artifact_root": args.model_artifact_root,
                "selected_child_path": args.selected_child,
                "pins": FormalPins(
                    args.model_repository,
                    args.model_revision,
                    args.dataset_repository,
                    args.dataset_revision,
                ),
            }

        bundle = build_formal_trial_bundle(
            args.output_root,
            **options,
        )
        print(
            json.dumps(
                {
                    "runId": str(bundle.run_id),
                    "bundleRoot": str(bundle.root),
                    "manifest": str(bundle.manifest_path),
                    "checksums": str(bundle.checksums_path),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "trial-bundle-publish":
        from huggingface_hub import HfApi

        from .hub import RunBundle, publish_run_bundle

        manifest_path = args.bundle_root / "manifest.json"
        checksums_path = args.bundle_root / "checksums.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_id = UUID(str(manifest["runId"]))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("built trial bundle manifest is invalid") from error
        if args.bundle_root.name != str(run_id) or not checksums_path.is_file():
            raise ValueError("built trial bundle path or checksums differ from its run ID")
        token = os.environ.get(args.token_env)
        if not token:
            raise ValueError(f"publication token environment is unset: {args.token_env}")
        receipt = publish_run_bundle(
            HfApi(),
            RunBundle(args.bundle_root, run_id, manifest_path, checksums_path),
            repo_id=args.repo_id,
            token=token,
            revision=args.revision,
        )
        print(json.dumps(_publication_receipt_json(receipt), sort_keys=True))
        return 0
    if args.command in {"replay", "live-replay", "concurrent-replay"}:
        manifest = load_benchmark_manifest(args.manifest)
        matches = [row for row in manifest.conditions if row.name == args.condition]
        if len(matches) != 1:
            raise ValueError(
                f"condition is not in the frozen manifest: {args.condition}"
            )
        replay = ReplayCorpus.from_jsonl(args.requests)
        universe = CandidateUniverse.from_jsonl(args.candidate_universe, CreatedBabelV1)
        if args.command == "concurrent-replay":
            max_in_flight = args.max_in_flight or getattr(manifest, "maxInFlight", 8)
            schedule_mode = args.schedule_mode or getattr(
                manifest, "scheduleMode", "open_loop"
            )
            if max_in_flight <= 0:
                raise ValueError("max-in-flight must be positive")
            condition_id = matches[0].name
            collector = None
            if args.resources is not None:
                services: dict[str, int] = {}
                for value in args.service_pid:
                    try:
                        service, raw_pid = value.split("=", 1)
                        pid = int(raw_pid)
                    except (ValueError, TypeError) as error:
                        raise ValueError("service PID must use NAME=PID") from error
                    if not service or pid <= 0 or service in services:
                        raise ValueError(
                            "service PID entries must be unique and positive"
                        )
                    services[service] = pid
                collector = PeriodicResourceCollector(
                    default_resource_sampler(manifest.benchmarkRunId, condition_id),
                    services=services,
                    interval_seconds=args.resource_interval_seconds,
                    maximum_samples=args.maximum_resource_samples,
                )
            result = asyncio.run(
                run_concurrent_condition(
                    manifest,
                    matches[0],
                    replay,
                    universe,
                    transport=AsyncHttpxTransport(
                        str(manifest.endpoint), max_connections=max_in_flight
                    ),
                    schedule_mode=schedule_mode,
                    max_in_flight=max_in_flight,
                    resource_collector=collector,
                )
            )
            _write(args.measurements, dump_jsonl(result.measurements))
            if args.resources is not None:
                _write(args.resources, dump_jsonl(result.resources))
            return 0
        driver = AlreadyConfiguredConditionDriver()
        if args.command == "live-replay":
            from .live import LiveTrainingDriver, build_atomic_sync_operation

            condition = matches[0]
            sync_operation = None
            if condition.syncEnabled:
                if args.sync_root is None:
                    raise ValueError("sync condition requires --sync-root")
                sync_operation = build_atomic_sync_operation(
                    dsn=args.dsn,
                    run_id=args.run_id,
                    model_id=condition.expectedModelId,
                    model_version=args.model_version,
                    pgvector_snapshot_sha256=condition.expectedPgvectorSnapshotSha256,
                    sync_root=args.sync_root,
                )
            driver = LiveTrainingDriver(
                dsn=args.dsn,
                kafka_bootstrap_servers=args.kafka_bootstrap,
                feedback_path=args.feedback,
                run_id=args.run_id,
                model_id=condition.expectedModelId,
                model_version=args.model_version,
                sync_operation=sync_operation,
                sync_every_steps=args.sync_every_steps,
                publish_limit=args.publish_limit,
            )
        result = run_condition(
            manifest,
            matches[0],
            replay,
            universe,
            transport=HttpxTransport(str(manifest.endpoint)),
            condition_driver=driver,
        )
        _write(args.measurements, dump_jsonl(result.measurements))
        if args.command == "live-replay":
            _write(args.telemetry, dump_jsonl(result.telemetry))
        return 0

    measurements = [
        row for path in args.measurements for row in load_request_measurements(path)
    ]
    telemetry = [
        row for path in args.telemetry for row in load_jsonl(path, ConditionTelemetryV1)
    ]
    summary = analyze(measurements, telemetry)
    _write(
        args.summary,
        json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )
    _write(args.markdown, render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["_parser", "main"]
