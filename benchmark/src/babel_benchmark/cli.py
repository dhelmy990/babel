"""CLI for real loopback replays and deterministic reports."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
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
    return parser


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
