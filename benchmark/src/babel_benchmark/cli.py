"""CLI for real loopback replays and deterministic reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from .analysis import analyze, render_markdown
from .contracts import (
    BenchmarkManifestV1,
    ConditionTelemetryV1,
    CreatedBabelV1,
    ReplayRequestV1,
    RequestMeasurementV1,
    dump_jsonl,
    load_jsonl,
)
from .replay import CandidateUniverse, ReplayCorpus
from .runner import AlreadyConfiguredConditionDriver, HttpxTransport, run_condition


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babel-friday-benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="run one externally configured condition")
    replay.add_argument("--manifest", required=True, type=Path)
    replay.add_argument("--requests", required=True, type=Path)
    replay.add_argument("--candidate-universe", required=True, type=Path)
    replay.add_argument("--condition", required=True)
    replay.add_argument("--measurements", required=True, type=Path)

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
    if args.command in {"replay", "live-replay"}:
        manifest = BenchmarkManifestV1.model_validate_json(args.manifest.read_text())
        matches = [row for row in manifest.conditions if row.name == args.condition]
        if len(matches) != 1:
            raise ValueError(f"condition is not in the frozen manifest: {args.condition}")
        replay = ReplayCorpus.from_jsonl(args.requests, ReplayRequestV1)
        universe = CandidateUniverse.from_jsonl(args.candidate_universe, CreatedBabelV1)
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
        row
        for path in args.measurements
        for row in load_jsonl(path, RequestMeasurementV1)
    ]
    telemetry = [
        row
        for path in args.telemetry
        for row in load_jsonl(path, ConditionTelemetryV1)
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
