"""Command-line entrypoint for deterministic 2016 data preparation/publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterator, Sequence

from .hub import (
    DEFAULT_REPO_ID,
    publish_verified_shards,
    verify_remote,
    write_revision_file,
)
from .shard import build_readiness, load_readiness, write_shards


DEFAULT_DATA_ROOT = Path("/home/dhelmy990/Data/babel-data")


class _UsageError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _api() -> object:
    from huggingface_hub import HfApi

    return HfApi()


def _token(arguments: argparse.Namespace) -> str:
    token = arguments.token or os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("a private-Hub token is required via --token or HF_TOKEN")
    return token


def _jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} is not an object")
            yield value


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_files(manifest_path: Path) -> list[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent.parent
    files = [root / item["path"] for item in manifest["shards"]]
    files.append(manifest_path)
    return files


def _prepare(arguments: argparse.Namespace) -> dict[str, object]:
    source = Path(arguments.input)
    output = (
        Path(arguments.output_root)
        if arguments.output_root
        else Path(arguments.data_root) / "prepared" / "2016-pilot"
    )
    source_checksum = _source_sha256(source)
    result = write_shards(
        _jsonl(source),
        output,
        pilot_size=arguments.pilot_size,
        target_shard_bytes=arguments.target_shard_bytes,
        source_provenance={"accepted_jsonl_sha256": source_checksum},
        dataset_provenance={
            "configuration": "distillation_2016",
            "snapshot_date": "2016-10-01",
        },
        model_provenance={"teacher_dimension": 100},
    )
    build_readiness(
        result,
        source_checksums={"accepted_jsonl": source_checksum},
        path=output / "readiness.json",
    )
    return {
        "status": "ok",
        "command": "prepare-2016",
        "manifest": str(result.manifest_path),
        "pilot_examples": result.row_count,
    }


def _publish(arguments: argparse.Namespace) -> dict[str, object]:
    root = (
        Path(arguments.input_root)
        if arguments.input_root
        else Path(arguments.data_root) / "prepared" / "2016-pilot"
    )
    manifest_path = root / "distillation_2016" / "manifest.json"
    token = _token(arguments)
    revision = publish_verified_shards(
        _api(), arguments.repo, _manifest_files(manifest_path), token, root=root
    )
    readiness_path = root / "readiness.json"
    readiness = load_readiness(readiness_path, manifest_path)
    readiness.mark_remote_verified(revision)
    if readiness.state == "building":
        readiness.transition("pilot_ready")
    if arguments.state == "complete" and readiness.state == "pilot_ready":
        readiness.transition("complete")
    readiness.save(readiness_path)
    if arguments.revision_out:
        write_revision_file(arguments.revision_out, revision)
    return {
        "status": "ok",
        "command": "publish-2016",
        "revision": revision,
        "state": readiness.state,
    }


def _verify(arguments: argparse.Namespace) -> dict[str, object]:
    token = _token(arguments)
    root = (
        Path(arguments.input_root)
        if arguments.input_root
        else Path(arguments.data_root) / "prepared" / "2016-pilot"
    )
    manifest = (
        Path(arguments.manifest)
        if arguments.manifest
        else root / "distillation_2016" / "manifest.json"
    )
    verified = verify_remote(
        _api(), arguments.repo, arguments.revision, manifest, token
    )
    if arguments.revision_out:
        write_revision_file(arguments.revision_out, verified.commit_sha)
    return {
        "status": "ok",
        "command": "verify-remote",
        "revision": verified.commit_sha,
        "verified_splits": verified.split_examples,
    }


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="babel-data")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare-2016")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    prepare.add_argument("--output-root")
    prepare.add_argument("--pilot-size", type=int, default=10_000)
    prepare.add_argument("--target-shard-bytes", type=int, default=384 * 1024 * 1024)
    prepare.set_defaults(handler=_prepare)

    publish = commands.add_parser("publish-2016")
    publish.add_argument("--repo", default=DEFAULT_REPO_ID)
    publish.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    publish.add_argument("--input-root")
    publish.add_argument("--state", choices=("pilot_ready", "complete"), default="pilot_ready")
    publish.add_argument("--token")
    publish.add_argument("--revision-out")
    publish.set_defaults(handler=_publish)

    verify = commands.add_parser("verify-remote")
    verify.add_argument("--repo", default=DEFAULT_REPO_ID)
    verify.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    verify.add_argument("--input-root")
    verify.add_argument("--manifest")
    verify.add_argument("--revision", required=True)
    verify.add_argument("--token")
    verify.add_argument("--revision-out")
    verify.set_defaults(handler=_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        arguments = _parser().parse_args(effective_argv)
    except _UsageError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "command": effective_argv[0] if effective_argv else None,
                    "error": "usage",
                    "message": str(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        result = arguments.handler(arguments)
    except BaseException as error:
        token = getattr(arguments, "token", None) or os.environ.get("HF_TOKEN")
        message = str(error)
        if token:
            message = message.replace(token, "[REDACTED]")
        print(
            json.dumps(
                {
                    "status": "error",
                    "command": arguments.command,
                    "error": type(error).__name__,
                    "message": message,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
