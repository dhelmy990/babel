"""Strict contracts for one rolling distillation dataset release bundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .contracts import validate_document


DATASET_CONFIG = "distillation_2016"
MANIFEST_PATH = f"{DATASET_CONFIG}/manifest.json"
READINESS_PATH = "readiness.json"
README_PATH = "README.md"
METADATA_PATHS = frozenset({MANIFEST_PATH, READINESS_PATH, README_PATH})
SPLITS = ("train", "validation", "test")

DATASET_CARD = """---
configs:
- config_name: distillation_2016
  data_files:
  - split: train
    path: distillation_2016/train/*.parquet
  - split: validation
    path: distillation_2016/validation/*.parquet
  - split: test
    path: distillation_2016/test/*.parquet
---
# Babel 2016 distillation dataset
""".encode("utf-8")


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def identity_rows_sha256(rows: Iterable[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, row in enumerate(rows):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(
                [str(row["article_key"]), int(row["page_id"])],
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
    digest.update(b"]\n")
    return digest.hexdigest()


def _manifest_error(label: str, message: str) -> ValueError:
    return ValueError(f"{label} manifest {message}")


def validate_manifest_document(
    value: Mapping[str, object], *, label: str = "dataset"
) -> dict[str, object]:
    document = deepcopy(dict(value))
    validate_document("dataset-manifest-v1", document)
    provenance = document["provenance"]
    validate_document("provenance-v1", provenance["document"])  # type: ignore[index]
    shards = document["shards"]
    assert isinstance(shards, list)

    paths: set[str] = set()
    checksums: set[str] = set()
    row_digests: set[str] = set()
    split_counts = {split: 0 for split in SPLITS}
    last_rank: dict[str, str] = {}
    for shard in shards:
        assert isinstance(shard, dict)
        path = str(shard["path"])
        split = str(shard["split"])
        if f"/{split}/" not in path:
            raise _manifest_error(label, f"shard path/split mismatch: {path}")
        if shard["min_article_key"] > shard["max_article_key"]:
            raise _manifest_error(label, f"shard key bounds are inverted: {path}")
        if shard["min_rank"] > shard["max_rank"]:
            raise _manifest_error(label, f"shard rank bounds are inverted: {path}")
        if path in paths:
            raise _manifest_error(label, f"has duplicate shard path: {path}")
        checksum = str(shard["sha256"])
        if checksum in checksums:
            raise _manifest_error(label, "has duplicate shard checksum")
        row_digest = str(shard["rows_sha256"])
        if row_digest in row_digests:
            raise _manifest_error(label, "has duplicate shard row identity digest")
        if split in last_rank and str(shard["min_rank"]) <= last_rank[split]:
            raise _manifest_error(label, f"has overlapping {split} rank intervals")
        paths.add(path)
        checksums.add(checksum)
        row_digests.add(row_digest)
        last_rank[split] = str(shard["max_rank"])
        split_counts[split] += int(shard["rows"])

    expected_counts = {"total": sum(split_counts.values()), **split_counts}
    if document["counts"] != expected_counts:
        raise _manifest_error(label, "counts do not match shards")
    aggregate = hashlib.sha256(canonical_json(shards)).hexdigest()
    if document["aggregate_sha256"] != aggregate:
        raise _manifest_error(label, "aggregate checksum is invalid")
    return document


def validate_manifest_bytes(value: bytes, *, label: str = "dataset") -> dict[str, object]:
    try:
        document = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _manifest_error(label, "is malformed") from error
    if not isinstance(document, dict):
        raise _manifest_error(label, "is malformed")
    return validate_manifest_document(document, label=label)


def _is_monotonic_value(old: object, new: object) -> bool:
    if isinstance(old, dict):
        return isinstance(new, dict) and all(
            key in new and _is_monotonic_value(value, new[key])
            for key, value in old.items()
        )
    if isinstance(old, list):
        return isinstance(new, list) and new[: len(old)] == old
    if isinstance(old, (int, float)) and not isinstance(old, bool):
        return (
            isinstance(new, (int, float))
            and not isinstance(new, bool)
            and new >= old
        )
    return new == old


def validate_manifest_extension(old_bytes: bytes, new_bytes: bytes) -> None:
    old = validate_manifest_bytes(old_bytes, label="remote")
    new = validate_manifest_bytes(new_bytes, label="local")
    for field in ("manifest_version", "schema_version", "state", "schema", "dataset_config"):
        if old[field] != new[field]:
            raise ValueError(f"manifest extension is not monotonic: changed {field}")
    old_shards = old["shards"]
    new_shards = new["shards"]
    assert isinstance(old_shards, list) and isinstance(new_shards, list)
    if new_shards[: len(old_shards)] != old_shards:
        raise ValueError("manifest extension is not monotonic: prior shard changed")
    old_pilot = old["pilot_article_keys"]
    new_pilot = new["pilot_article_keys"]
    assert isinstance(old_pilot, list) and isinstance(new_pilot, list)
    if new_pilot[: len(old_pilot)] != old_pilot:
        raise ValueError("manifest extension is not monotonic: pilot keys changed")
    if not _is_monotonic_value(old["provenance"], new["provenance"]):
        raise ValueError("manifest extension is not monotonic: provenance regressed")


def render_dataset_card() -> bytes:
    return DATASET_CARD


def validate_readiness_alignment(
    readiness: Mapping[str, object], manifest: Mapping[str, object]
) -> None:
    validate_document("dataset-readiness-v1", readiness)
    checked_manifest = validate_manifest_document(manifest)
    if readiness["available_examples"] != checked_manifest["counts"]["total"]:  # type: ignore[index]
        raise ValueError("readiness example count does not match manifest")
    readiness_shards = {
        str(item["path"]): (str(item["sha256"]), int(item["examples"]))
        for item in readiness["verified_shards"]  # type: ignore[union-attr]
    }
    manifest_shards = {
        str(item["path"]): (str(item["sha256"]), int(item["rows"]))
        for item in checked_manifest["shards"]  # type: ignore[union-attr]
    }
    if readiness_shards != manifest_shards:
        raise ValueError("readiness shard identities do not match manifest")
    artifact = checked_manifest["provenance"]["document"]["artifacts"].get(  # type: ignore[index]
        "accepted_jsonl"
    )
    if not isinstance(artifact, dict):
        raise ValueError("manifest provenance lacks accepted JSONL identity")
    if readiness["source_checksums"].get("accepted_jsonl") != artifact.get(  # type: ignore[union-attr]
        "sha256"
    ):
        raise ValueError("readiness accepted input checksum does not match provenance")


def validate_readiness_extension(
    old_bytes: bytes,
    new_bytes: bytes,
    old_manifest: Mapping[str, object],
    new_manifest: Mapping[str, object],
) -> None:
    try:
        old = json.loads(old_bytes)
        new = json.loads(new_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("readiness extension is malformed") from error
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("readiness extension is malformed")
    validate_readiness_alignment(old, old_manifest)
    validate_readiness_alignment(new, new_manifest)
    order = {"building": 0, "pilot_ready": 1, "complete": 2}
    if order[str(new["state"])] < order[str(old["state"])]:
        raise ValueError("readiness extension cannot regress state")
    old_shards = old["verified_shards"]
    new_shards = new["verified_shards"]
    if not isinstance(old_shards, list) or not isinstance(new_shards, list):
        raise ValueError("readiness extension has invalid shards")
    if new_shards[: len(old_shards)] != old_shards:
        raise ValueError("readiness extension changed a prior shard")
    if old["source_checksums"] != new["source_checksums"]:
        raise ValueError("readiness extension changed source checksums")
    if old["remote_verified"] and not new["remote_verified"]:
        raise ValueError("readiness extension regressed remote verification")


def validate_full_release_proof(
    proof: Mapping[str, object], manifest: Mapping[str, object]
) -> None:
    checked_proof = deepcopy(dict(proof))
    validate_document("full-release-proof-v1", checked_proof)
    checked_manifest = validate_manifest_document(manifest)
    provenance = checked_manifest["provenance"]["document"]  # type: ignore[index]
    if checked_proof["provenance_sha256"] != hashlib.sha256(
        canonical_json(provenance)
    ).hexdigest():
        raise ValueError("full release proof provenance checksum does not match")
    accepted = checked_proof["accepted_jsonl"]
    report = checked_proof["reconciliation_report"]
    assert isinstance(accepted, dict) and isinstance(report, dict)
    artifacts = provenance["artifacts"]  # type: ignore[index]
    if {key: accepted[key] for key in ("sha256", "size")} != artifacts.get(
        "accepted_jsonl"
    ):
        raise ValueError("full release proof accepted JSONL identity does not match")
    if {key: report[key] for key in ("sha256", "size")} != artifacts.get(
        "reconciliation_report"
    ):
        raise ValueError("full release proof reconciliation report is not provenance-bound")
    raw = int(report["raw_rows"])
    accepted_rows = int(report["accepted_rows"])
    excluded = int(report["excluded_rows"])
    if raw != accepted_rows + excluded or int(accepted["rows"]) != accepted_rows:
        raise ValueError("full release proof reconciliation counts are inconsistent")
    if checked_manifest["counts"]["total"] != accepted_rows:  # type: ignore[index]
        raise ValueError("full release proof requires every accepted row in the manifest")
    row_counts = provenance["reports"]["row_counts"]  # type: ignore[index]
    if any(
        row_counts.get(name) != expected
        for name, expected in {
            "raw": raw,
            "accepted": accepted_rows,
            "excluded": excluded,
        }.items()
    ):
        raise ValueError("full release proof counts do not match provenance report")
    inventories = checked_proof["source_inventories"]
    assert isinstance(inventories, list)
    count_fields = {
        "role",
        "records",
        "emitted_records",
        "upstream_excluded_records",
    }
    proof_sources = [
        {key: value for key, value in item.items() if key not in count_fields}
        for item in inventories
    ]
    if proof_sources != provenance["sources"]:  # type: ignore[index]
        raise ValueError("full release proof source inventories do not match provenance")
    if any(
        int(item["records"])
        != int(item["emitted_records"]) + int(item["upstream_excluded_records"])
        for item in inventories
    ):
        raise ValueError("full release proof source inventory accounting is incomplete")
    teacher = [item for item in inventories if item["role"] == "teacher"]
    if not teacher:
        raise ValueError("full release proof requires a teacher source inventory")
    teacher_emitted = sum(int(item["emitted_records"]) for item in teacher)
    if teacher_emitted != raw or row_counts.get("teacher_input_rows") != raw:
        raise ValueError("full release proof teacher input count does not reconcile")
