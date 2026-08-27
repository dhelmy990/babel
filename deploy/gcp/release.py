#!/usr/bin/env python3
"""Validate immutable demo releases and emit their deployment receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid5


MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
ORIGIN_TRIAL_ID = UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43")
ORIGIN_RUN_ID = UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9")
SERVING_MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
REQUIRED_KEYS = frozenset(
    {
        "BABEL_SOURCE_COMMIT",
        "BABEL_BACKEND_IMAGE",
        "BABEL_SERVING_IMAGE",
        "BABEL_TRAINER_IMAGE",
        "BABEL_MODEL_REVISION",
        "BABEL_DATASET_REVISION",
        "BABEL_GCP_TRIAL_ID",
        "BABEL_GCP_RUN_ID",
        "BABEL_POPULATION_VECTOR_SHA256",
        "BABEL_POPULATION_SNAPSHOT_SHA256",
        "BABEL_DEPLOYMENT_RUN_ID",
        "BABEL_DEPLOYMENT_RUN_ATTEMPT",
    }
)
SHA40 = re.compile(r"^[a-f0-9]{40}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
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
        parsed_trial_id = UUID(values["BABEL_GCP_TRIAL_ID"])
        parsed_run_id = UUID(values["BABEL_GCP_RUN_ID"])
    except ValueError as error:
        raise ValueError("GCP trial and run IDs must be UUIDs") from error
    if parsed_trial_id.version != 4 or parsed_trial_id == ORIGIN_TRIAL_ID:
        raise ValueError("BABEL_GCP_TRIAL_ID must be a fresh UUIDv4")
    if parsed_run_id != uuid5(parsed_trial_id, "population"):
        raise ValueError("BABEL_GCP_RUN_ID must equal uuid5(fresh trial,'population')")
    if parsed_run_id == ORIGIN_RUN_ID:
        raise ValueError("BABEL_GCP_RUN_ID must differ from the origin run")
    if str(parsed_trial_id) != values["BABEL_GCP_TRIAL_ID"] or str(parsed_run_id) != values["BABEL_GCP_RUN_ID"]:
        raise ValueError("GCP IDs must be canonical lowercase UUIDs")
    for name in ("BABEL_POPULATION_VECTOR_SHA256", "BABEL_POPULATION_SNAPSHOT_SHA256"):
        if SHA256.fullmatch(values[name]) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256")
    for name in ("BABEL_DEPLOYMENT_RUN_ID", "BABEL_DEPLOYMENT_RUN_ATTEMPT"):
        if not values[name].isdigit() or int(values[name]) <= 0:
            raise ValueError(f"{name} must be a positive integer")
    return values


def validate_trainer_readiness(
    document: dict[str, object], *, expected_run_id: str, not_before_ns: int
) -> dict[str, object]:
    if set(document) != {"schemaVersion", "runId", "consumerGroup", "readyAtNs"}:
        raise ValueError("trainer readiness has an unexpected schema")
    if document["schemaVersion"] != 1 or document["runId"] != expected_run_id:
        raise ValueError("trainer readiness differs from the expected run")
    if not isinstance(document["consumerGroup"], str) or not document[
        "consumerGroup"
    ].endswith(f".{expected_run_id}"):
        raise ValueError("trainer readiness consumer group differs from the expected run")
    ready_at_ns = document["readyAtNs"]
    if not isinstance(ready_at_ns, int) or isinstance(ready_at_ns, bool):
        raise ValueError("trainer readiness timestamp is invalid")
    if ready_at_ns < not_before_ns:
        raise ValueError("trainer readiness is stale")
    return document


def validate_serving_health(document: dict[str, object]) -> dict[str, object]:
    expected = {
        "status": "ok",
        "modelId": str(SERVING_MODEL_ID),
        "modelVersion": 0,
    }
    if document != expected:
        raise ValueError("serving health model identity/version differs")
    return document


def validate_serving_smoke(
    document: dict[str, object], *, expected_run_id: str
) -> dict[str, object]:
    expected = {
        "schemaVersion": 2,
        "runId": expected_run_id,
        "modelId": str(SERVING_MODEL_ID),
        "modelVersion": 0,
        "sourceVectorOrigin": "qwen_encode",
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise ValueError("serving smoke did not use the expected CUDA Qwen model")
    candidates = document.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("serving smoke returned no candidates")
    return document


def require_newer_deployment(
    candidate_run_id: int,
    candidate_attempt: int,
    *,
    previous_run_id: int,
    previous_attempt: int,
) -> bool:
    if (candidate_run_id, candidate_attempt) <= (previous_run_id, previous_attempt):
        raise ValueError("refusing an older or duplicate deployment attempt")
    return True


def load_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON evidence must be an object")
    return value


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
        "trialId": valid["BABEL_GCP_TRIAL_ID"],
        "runId": valid["BABEL_GCP_RUN_ID"],
        "populationVectorSha256": valid["BABEL_POPULATION_VECTOR_SHA256"],
        "populationSnapshotSha256": valid["BABEL_POPULATION_SNAPSHOT_SHA256"],
        "deploymentRunId": int(valid["BABEL_DEPLOYMENT_RUN_ID"]),
        "deploymentRunAttempt": int(valid["BABEL_DEPLOYMENT_RUN_ATTEMPT"]),
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
    readiness = commands.add_parser("validate-trainer-readiness")
    readiness.add_argument("document", type=Path)
    readiness.add_argument("--run-id", required=True)
    readiness.add_argument("--not-before-ns", required=True, type=int)
    health = commands.add_parser("validate-serving-health")
    health.add_argument("document", type=Path)
    smoke = commands.add_parser("validate-serving-smoke")
    smoke.add_argument("document", type=Path)
    smoke.add_argument("--run-id", required=True)
    newer = commands.add_parser("assert-newer")
    newer.add_argument("candidate", type=Path)
    newer.add_argument("previous", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command in {"validate", "receipt"}:
            values = validate_release(parse_env(arguments.release_env))
        if arguments.command == "receipt":
            payload = canonical_json(
                deployment_receipt(values, deployed_at=arguments.deployed_at)
            )
            arguments.output.write_bytes(payload)
            os.chmod(arguments.output, 0o600)
        elif arguments.command == "validate-trainer-readiness":
            validate_trainer_readiness(
                load_json_object(arguments.document),
                expected_run_id=arguments.run_id,
                not_before_ns=arguments.not_before_ns,
            )
        elif arguments.command == "validate-serving-health":
            validate_serving_health(load_json_object(arguments.document))
        elif arguments.command == "validate-serving-smoke":
            validate_serving_smoke(
                load_json_object(arguments.document), expected_run_id=arguments.run_id
            )
        elif arguments.command == "assert-newer":
            candidate = validate_release(parse_env(arguments.candidate))
            previous = validate_release(parse_env(arguments.previous))
            require_newer_deployment(
                int(candidate["BABEL_DEPLOYMENT_RUN_ID"]),
                int(candidate["BABEL_DEPLOYMENT_RUN_ATTEMPT"]),
                previous_run_id=int(previous["BABEL_DEPLOYMENT_RUN_ID"]),
                previous_attempt=int(previous["BABEL_DEPLOYMENT_RUN_ATTEMPT"]),
            )
    except (json.JSONDecodeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
