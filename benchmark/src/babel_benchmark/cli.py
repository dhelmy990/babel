"""CLI for real loopback replays and deterministic reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    if args.command == "replay":
        manifest = BenchmarkManifestV1.model_validate_json(args.manifest.read_text())
        matches = [row for row in manifest.conditions if row.name == args.condition]
        if len(matches) != 1:
            raise ValueError(f"condition is not in the frozen manifest: {args.condition}")
        replay = ReplayCorpus.from_jsonl(args.requests, ReplayRequestV1)
        universe = CandidateUniverse.from_jsonl(args.candidate_universe, CreatedBabelV1)
        result = run_condition(
            manifest,
            matches[0],
            replay,
            universe,
            transport=HttpxTransport(str(manifest.endpoint)),
            condition_driver=AlreadyConfiguredConditionDriver(),
        )
        _write(args.measurements, dump_jsonl(result.measurements))
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


__all__ = ["main"]
