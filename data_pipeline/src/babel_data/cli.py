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
    publish_interview_configuration,
    publish_verified_shards,
    stage_versioned_release_shards,
    verify_remote,
    write_revision_file,
)
from .interview_export import (
    DEFAULT_SOURCE_REVISIONS,
    DEFAULT_SOURCE_SHA256,
    freeze_frontier,
    select_interview_ids,
    write_interview_release,
)
from .full_2016 import (
    Full2016SourcePin,
    build_complete_2016,
    rebind_supersession_predecessor,
)
from .mirror import mirror_source, persist_receipt, validate_data_root
from .release import (
    EMPTY_TEST_PATH,
    README_PATH,
    READINESS_PATH,
    validate_full_release_proof,
    validate_manifest_bytes,
)
from .shard import load_readiness, write_shards
from .sources import load_source_manifest
from .monthly.selection import (
    CandidateIdentity,
    EngineeringSnapshotPolicyV1,
    freeze_joint_selection,
)


DEFAULT_DATA_ROOT = Path("/home/dhelmy990/Data/babel-data")
INTERVIEW_ENV_FILE = Path("/home/dhelmy990/Code/babel/.env")
INTERVIEW_DATABASE = Path(
    "/home/dhelmy990/Data/babel-data/full-2016-work/"
    "1a319328641844e29537/reconcile.sqlite3"
)
INTERVIEW_OUTPUT_ROOT = Path(
    "/home/dhelmy990/Data/babel-data/prepared/2016-interview-50k"
)
DEFAULT_SOURCE_MANIFEST = (
    Path(__file__).resolve().parents[2] / "manifests" / "2016-sources.json"
)
_REGISTERED_SECRETS: set[str] = set()


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
    _REGISTERED_SECRETS.add(token)
    return token


def _interview_token() -> str:
    try:
        lines = INTERVIEW_ENV_FILE.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"private-Hub token file is unavailable: {INTERVIEW_ENV_FILE}") from error
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        if separator and name.strip() == "HF_TOKEN":
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if value:
                _REGISTERED_SECRETS.add(value)
                return value
    raise ValueError(f"HF_TOKEN is missing from {INTERVIEW_ENV_FILE}")


def _sanitized_message(error: BaseException) -> str:
    message = str(error)
    for secret in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    return message


def _register_explicit_tokens(arguments: Sequence[str]) -> None:
    for index, value in enumerate(arguments):
        if value == "--token" and index + 1 < len(arguments):
            token = arguments[index + 1]
        elif value.startswith("--token="):
            token = value.partition("=")[2]
        else:
            continue
        if token:
            _REGISTERED_SECRETS.add(token)


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


def _monthly_candidates(path: Path) -> list[CandidateIdentity]:
    return [
        CandidateIdentity(
            period=str(row["period"]),
            page_id=int(row["page_id"]),
            canonical_title=str(row["canonical_title"]),
            traffic=int(row["traffic"]),
            priority=int(row.get("priority", 3)),
        )
        for row in _jsonl(path)
    ]


def _select_monthly_snapshot(arguments: argparse.Namespace) -> dict[str, object]:
    policy = EngineeringSnapshotPolicyV1(
        target_rows=arguments.target_rows,
        minimum_rows=arguments.minimum_rows,
        deadline_seconds=arguments.deadline_seconds,
        seed=arguments.seed,
    )
    result = freeze_joint_selection(
        _monthly_candidates(Path(arguments.june_candidates)),
        _monthly_candidates(Path(arguments.july_candidates)),
        policy=policy,
    )
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.to_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "command": "select-monthly-snapshot",
        "output": str(output),
        "rows_per_month": result.rows_per_month,
        "ordered_identity_sha256": result.ordered_identity_sha256,
    }


def _manifest_files(manifest_path: Path) -> list[Path]:
    manifest = validate_manifest_bytes(manifest_path.read_bytes(), label="local")
    root = manifest_path.parent.parent
    files = [root / item["path"] for item in manifest["shards"]]
    files.extend(
        [root / READINESS_PATH, root / README_PATH, root / EMPTY_TEST_PATH, manifest_path]
    )
    return files


def _prepare(arguments: argparse.Namespace) -> dict[str, object]:
    source = Path(arguments.input)
    output = (
        Path(arguments.output_root)
        if arguments.output_root
        else Path(arguments.data_root) / "prepared" / "2016-pilot"
    )
    source_checksum = _source_sha256(source)
    provenance_path = Path(arguments.provenance)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict):
        raise ValueError("provenance document must be a JSON object")
    try:
        artifact = provenance["artifacts"]["accepted_jsonl"]
    except (KeyError, TypeError) as error:
        raise ValueError(
            "provenance must include the accepted_jsonl artifact"
        ) from error
    if not isinstance(artifact, dict) or artifact != {
        "sha256": source_checksum,
        "size": source.stat().st_size,
    }:
        raise ValueError(
            "provenance accepted_jsonl artifact does not match the input"
        )
    result = write_shards(
        _jsonl(source),
        output,
        pilot_size=arguments.pilot_size,
        target_shard_bytes=arguments.target_shard_bytes,
        provenance=provenance,
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
    manifest = validate_manifest_bytes(manifest_path.read_bytes(), label="local")
    if arguments.state == "complete":
        if not arguments.full_release_proof:
            raise ValueError(
                "--state complete requires --full-release-proof"
            )
        proof_path = Path(arguments.full_release_proof)
        try:
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("full release proof is malformed") from error
        if not isinstance(proof, dict):
            raise ValueError("full release proof must be a JSON object")
        try:
            validate_full_release_proof(proof, manifest)
        except Exception as error:
            raise ValueError(f"full release proof is invalid: {error}") from error
    token = _token(arguments)
    api = _api()
    staging_commits: tuple[str, ...] = ()
    if arguments.state == "complete" and manifest.get("active_release_root") is not None:
        staging_commits = stage_versioned_release_shards(
            api, arguments.repo, manifest_path, token
        )
        if not staging_commits:
            raise ValueError("versioned complete release has no staged shard commits")
        rebind_supersession_predecessor(root, staging_commits[-1])
        manifest = validate_manifest_bytes(manifest_path.read_bytes(), label="local")
        validate_full_release_proof(proof, manifest)
    readiness_path = root / READINESS_PATH
    readiness = load_readiness(readiness_path, manifest_path)
    readiness.stage_publication(arguments.state)
    readiness.save(readiness_path)
    revision = publish_verified_shards(
        api, arguments.repo, _manifest_files(manifest_path), token, root=root
    )
    readiness.mark_remote_verified(revision)
    readiness.save_verification_evidence(readiness_path)
    if arguments.revision_out:
        write_revision_file(arguments.revision_out, revision)
    return {
        "status": "ok",
        "command": "publish-2016",
        "revision": revision,
        "state": readiness.state,
        "publication_commits": [*staging_commits, revision],
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


def _export_interview(arguments: argparse.Namespace) -> dict[str, object]:
    database = Path(arguments.database)
    frontier = freeze_frontier(database)
    selection = select_interview_ids(database, frontier)
    result = write_interview_release(
        database,
        frontier,
        selection,
        Path(arguments.output_root),
        source_sha256={
            "teacher": arguments.teacher_sha256,
            "wikipedia": arguments.wikipedia_sha256,
        },
        source_revisions={
            "teacher": arguments.teacher_revision,
            "wikipedia": arguments.wikipedia_revision,
        },
        code_commit=arguments.code_commit,
    )
    return {
        "status": "ok",
        "command": "export-interview-2016",
        "counts": dict(selection.counts),
        "ordered_sha256": dict(selection.ordered_sha256),
        "frontier": frontier.to_document(),
        "manifest": str(result.manifest_path),
        "readiness": str(result.readiness_path),
    }


def _publish_interview(arguments: argparse.Namespace) -> dict[str, object]:
    token = _interview_token()
    revision = publish_interview_configuration(
        _api(),
        arguments.repo,
        Path(arguments.input_root),
        token,
    )
    if arguments.revision_out:
        write_revision_file(arguments.revision_out, revision)
    return {
        "status": "ok",
        "command": "publish-interview-2016",
        "configuration": "distillation_2016_interview",
        "revision": revision,
        "input_root": str(Path(arguments.input_root)),
    }


def _mirror(arguments: argparse.Namespace) -> dict[str, object]:
    root = validate_data_root(arguments.data_root or os.environ.get("BABEL_DATA_ROOT"))
    sources = load_source_manifest(Path(arguments.manifest))
    try:
        source = sources[arguments.source_id]
    except KeyError as error:
        raise ValueError(
            f"unknown source ID {arguments.source_id}; choose one of: "
            + ", ".join(sorted(sources))
        ) from error
    token = _token(arguments)
    receipt = mirror_source(
        source,
        _api(),
        repository=arguments.repo,
        token=token,
        data_root=root,
        source_identifier=arguments.source_id,
    )
    persisted = persist_receipt(root / "hf-cache", receipt)
    if arguments.receipt_out:
        requested = Path(arguments.receipt_out)
        if not requested.is_absolute():
            raise ValueError("receipt output must be an absolute path")
        requested.parent.mkdir(parents=True, exist_ok=True)
        content = receipt.to_json_bytes()
        if requested.exists() and requested.read_bytes() != content:
            raise ValueError("refusing to replace a different source mirror receipt")
        requested.write_bytes(content)
        persisted = requested
    return {
        "status": "ok",
        "command": "mirror-source",
        "source_id": receipt.source_id,
        "revision": receipt.remote_commit_sha,
        "state": receipt.state,
        "receipt": str(persisted),
    }


def _build_complete(arguments: argparse.Namespace) -> dict[str, object]:
    root = validate_data_root(arguments.data_root or os.environ.get("BABEL_DATA_ROOT"))
    output = (
        Path(arguments.output_root)
        if arguments.output_root
        else root / "prepared" / "2016-complete"
    )
    pin = Full2016SourcePin(
        repository=arguments.repo,
        teacher_revision=arguments.teacher_revision,
        teacher_path=arguments.teacher_path,
        teacher_sha256=arguments.teacher_sha256,
        wikipedia_revision=arguments.wikipedia_revision,
        wikipedia_path=arguments.wikipedia_path,
        wikipedia_sha256=arguments.wikipedia_sha256,
        token=_token(arguments),
    )
    result = build_complete_2016(
        pin,
        root,
        output,
        resume=not arguments.no_resume,
    )
    return {
        "status": "ok",
        "command": "build-complete-2016",
        "teacher_total": result.teacher_total,
        "matched": result.matched,
        "excluded": result.excluded,
        "rows_written": result.rows_written,
        "readiness_state": result.readiness_state,
        "manifest": str(result.manifest_path),
        "range_journal": str(result.range_journal),
        "full_release_proof": str(result.full_release_proof),
        "active_release_root": result.active_release_root,
        "supersedes_commit_sha": result.supersedes_commit_sha,
    }


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="babel-data")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare-2016")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--provenance", required=True)
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
    publish.add_argument("--full-release-proof")
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

    interview_export = commands.add_parser("export-interview-2016")
    interview_export.add_argument("--database", default=str(INTERVIEW_DATABASE))
    interview_export.add_argument("--output-root", default=str(INTERVIEW_OUTPUT_ROOT))
    interview_export.add_argument("--code-commit", required=True)
    interview_export.add_argument(
        "--teacher-sha256", default=DEFAULT_SOURCE_SHA256["teacher"]
    )
    interview_export.add_argument(
        "--wikipedia-sha256", default=DEFAULT_SOURCE_SHA256["wikipedia"]
    )
    interview_export.add_argument(
        "--teacher-revision", default=DEFAULT_SOURCE_REVISIONS["teacher"]
    )
    interview_export.add_argument(
        "--wikipedia-revision", default=DEFAULT_SOURCE_REVISIONS["wikipedia"]
    )
    interview_export.set_defaults(handler=_export_interview)

    interview_publish = commands.add_parser("publish-interview-2016")
    interview_publish.add_argument("--repo", default=DEFAULT_REPO_ID)
    interview_publish.add_argument("--input-root", default=str(INTERVIEW_OUTPUT_ROOT))
    interview_publish.add_argument("--revision-out")
    interview_publish.set_defaults(handler=_publish_interview)

    mirror = commands.add_parser("mirror-source")
    mirror.add_argument("--source-id", required=True)
    mirror.add_argument("--manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    mirror.add_argument("--repo", default=DEFAULT_REPO_ID)
    mirror.add_argument("--data-root")
    mirror.add_argument("--token")
    mirror.add_argument("--receipt-out")
    mirror.set_defaults(handler=_mirror)

    complete = commands.add_parser("build-complete-2016")
    complete.add_argument("--repo", default=DEFAULT_REPO_ID)
    complete.add_argument("--data-root")
    complete.add_argument("--output-root")
    complete.add_argument("--teacher-revision", required=True)
    complete.add_argument(
        "--teacher-path",
        default="sources/teacher-zip/2016-09-01_2016-09-30_en_100.zip",
    )
    complete.add_argument("--teacher-sha256", required=True)
    complete.add_argument("--wikipedia-revision", required=True)
    complete.add_argument(
        "--wikipedia-path",
        default=(
            "sources/wikipedia-xml/"
            "enwiki-20161001-pages-articles-multistream.xml.bz2"
        ),
    )
    complete.add_argument("--wikipedia-sha256", required=True)
    complete.add_argument("--token")
    complete.add_argument("--no-resume", action="store_true")
    complete.set_defaults(handler=_build_complete)

    monthly = commands.add_parser("select-monthly-snapshot")
    monthly.add_argument("--june-candidates", required=True)
    monthly.add_argument("--july-candidates", required=True)
    monthly.add_argument("--output", required=True)
    monthly.add_argument("--target-rows", type=int, default=10_000)
    monthly.add_argument("--minimum-rows", type=int, default=5_000)
    monthly.add_argument("--deadline-seconds", type=float, default=45 * 60)
    monthly.add_argument("--seed", default="babel-monthly-engineering-v1")
    monthly.set_defaults(handler=_select_monthly_snapshot)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _REGISTERED_SECRETS.clear()
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    _register_explicit_tokens(effective_argv)
    try:
        arguments = _parser().parse_args(effective_argv)
    except _UsageError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "command": effective_argv[0] if effective_argv else None,
                    "error": "usage",
                    "message": _sanitized_message(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    explicit_token = getattr(arguments, "token", None)
    if isinstance(explicit_token, str) and explicit_token:
        _REGISTERED_SECRETS.add(explicit_token)
    try:
        result = arguments.handler(arguments)
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "command": arguments.command,
                    "error": type(error).__name__,
                    "message": _sanitized_message(error),
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
