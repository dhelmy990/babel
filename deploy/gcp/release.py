#!/usr/bin/env python3
"""Validate immutable demo releases and emit their deployment receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID


MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
REQUIRED_KEYS = frozenset(
    {
        "BABEL_SOURCE_COMMIT",
        "BABEL_BACKEND_IMAGE",
        "BABEL_SERVING_IMAGE",
        "BABEL_TRAINER_IMAGE",
        "BABEL_MODEL_REVISION",
        "BABEL_DATASET_REVISION",
        "BABEL_GCP_RUN_ID",
    }
)
SHA40 = re.compile(r"^[a-f0-9]{40}$")
IMAGE = re.compile(
    r"^[a-z0-9-]+-docker\.pkg\.dev/"
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]/"
    r"[a-z0-9._-]+/[a-z0-9._-]+@sha256:[a-f0-9]{64}$"
)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def parse_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            raise ValueError(f"release environment line {number} is not KEY=value")
        key, value = raw.split("=", 1)
        if key in values:
            raise ValueError(f"release environment repeats {key}")
        values[key] = value
    return values


def validate_release(values: dict[str, str]) -> dict[str, str]:
    if set(values) != REQUIRED_KEYS:
        raise ValueError("release environment must contain the exact keys")
    if any(not value or "\n" in value or "\r" in value for value in values.values()):
        raise ValueError("release values must be non-empty single-line strings")
    if SHA40.fullmatch(values["BABEL_SOURCE_COMMIT"]) is None:
        raise ValueError("BABEL_SOURCE_COMMIT must be a lowercase 40-hex commit")
    for name in ("BABEL_BACKEND_IMAGE", "BABEL_SERVING_IMAGE", "BABEL_TRAINER_IMAGE"):
        if IMAGE.fullmatch(values[name]) is None:
            raise ValueError(f"{name} must be an Artifact Registry sha256 digest reference")
    if values["BABEL_MODEL_REVISION"] != MODEL_REVISION:
        raise ValueError("BABEL_MODEL_REVISION differs from the accepted Qwen artifact")
    if values["BABEL_DATASET_REVISION"] != DATASET_REVISION:
        raise ValueError("BABEL_DATASET_REVISION differs from the scaled dataset pin")
    try:
        parsed_run_id = UUID(values["BABEL_GCP_RUN_ID"])
    except ValueError as error:
        raise ValueError("BABEL_GCP_RUN_ID must be a UUID") from error
    if str(parsed_run_id) != values["BABEL_GCP_RUN_ID"]:
        raise ValueError("BABEL_GCP_RUN_ID must be a canonical lowercase UUID")
    return values


def deployment_receipt(values: dict[str, str], *, deployed_at: str) -> dict[str, object]:
    valid = validate_release(values)
    return {
        "schemaVersion": 1,
        "sourceCommit": valid["BABEL_SOURCE_COMMIT"],
        "backendImageDigest": valid["BABEL_BACKEND_IMAGE"].rsplit("@", 1)[1],
        "servingImageDigest": valid["BABEL_SERVING_IMAGE"].rsplit("@", 1)[1],
        "trainerImageDigest": valid["BABEL_TRAINER_IMAGE"].rsplit("@", 1)[1],
        "modelRevision": valid["BABEL_MODEL_REVISION"],
        "datasetRevision": valid["BABEL_DATASET_REVISION"],
        "runId": valid["BABEL_GCP_RUN_ID"],
        "deployedAt": deployed_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("release_env", type=Path)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("release_env", type=Path)
    receipt.add_argument("output", type=Path)
    receipt.add_argument("--deployed-at", required=True)
    arguments = parser.parse_args(argv)
    try:
        values = validate_release(parse_env(arguments.release_env))
        if arguments.command == "receipt":
            payload = canonical_json(
                deployment_receipt(values, deployed_at=arguments.deployed_at)
            )
            arguments.output.write_bytes(payload)
            os.chmod(arguments.output, 0o600)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
