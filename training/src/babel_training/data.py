"""Pinned, validated, restartable streaming access to the 2016 dataset."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from copy import deepcopy
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators


DEFAULT_DATASET_REPO = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIG = "distillation_2016"
MANIFEST_PATH = f"{DATASET_CONFIG}/manifest.json"
READINESS_PATH = "readiness.json"
README_PATH = "README.md"
_SHA40 = re.compile(r"[a-f0-9]{40}")
_SHA64 = re.compile(r"[a-f0-9]{64}")
_ARTICLE_KEY = re.compile(r"enwiki:2016-10-01:([1-9][0-9]*)")
_WIKIDATA = re.compile(r"Q[1-9][0-9]*")
_ALLOWED_ROW_FIELDS = frozenset(
    {
        "article_key",
        "page_id",
        "canonical_title",
        "wikidata_id",
        "lead_text",
        "article_text",
        "teacher_vector",
        "teacher_norm",
        "source_revision_id",
        "snapshot_date",
        "split",
        "reconciliation_status",
    }
)
_TRAINING_ROW_FIELD_ORDER = (
    "article_key",
    "page_id",
    "canonical_title",
    "lead_text",
    "teacher_vector",
    "teacher_norm",
    "split",
)
_TRAINING_ROW_FIELDS = frozenset(_TRAINING_ROW_FIELD_ORDER)
# Qwen inputs are capped at 1024 tokens. A 16 KiB UTF-8 title+lead budget
# allows a conservative 16 bytes/token while bounding the 10k shuffle buffer.
_MAX_TRAINING_TEXT_BYTES = 16 * 1024
_MAX_PROJECTED_ROW_BYTES = 24 * 1024
_STATE_BASE_BYTES = 1024 * 1024
_DATASET_CARD = b"""---
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
"""
_SCHEMA_NAMES = frozenset(
    {
        "dataset-manifest-v1",
        "dataset-readiness-v1",
        "provenance-v1",
        "distillation-example-v1",
    }
)


def _is_finite_json_number(checker: object, instance: object) -> bool:
    return _finite(instance)


_FiniteNumberValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "number", _is_finite_json_number
    ),
)


@cache
def _schema_validator(name: str) -> Draft202012Validator:
    if name not in _SCHEMA_NAMES:
        raise ValueError(f"unknown packaged schema: {name!r}")
    schema_path = files("babel_training").joinpath("schemas", f"{name}.json")
    with schema_path.open(encoding="utf-8") as source:
        schema = json.load(source)
    Draft202012Validator.check_schema(schema)
    return _FiniteNumberValidator(schema, format_checker=FormatChecker())


def _validate_schema(name: str, value: Mapping[str, object]) -> None:
    try:
        _schema_validator(name).validate(dict(value))
    except ValidationError as error:
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise DatasetContractError(
            f"{name} schema field validation failed at {location}"
        ) from None


class InvalidDatasetRevision(ValueError):
    """The requested or resolved dataset revision is not one immutable commit."""


class ForbiddenDatasetConfiguration(ValueError):
    """A caller attempted to access data outside the public 2016 contract."""


class DatasetContractError(ValueError):
    """Pinned remote metadata or a streamed row violates the v1 contract."""


def _is_sha(value: object, length: int) -> bool:
    pattern = _SHA40 if length == 40 else _SHA64
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def resolve_dataset_revision(
    api: object,
    repo_id: str,
    requested_ref: str,
    token: str,
) -> str:
    """Resolve and verify a dataset ref with exactly one authoritative info call."""
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ValueError("dataset repo_id must be nonblank")
    if not isinstance(requested_ref, str) or not requested_ref:
        raise InvalidDatasetRevision("requested dataset revision is malformed")
    if not isinstance(token, str) or not token:
        raise ValueError("a private-Hub token is required")
    if re.fullmatch(r"[A-Fa-f0-9]{40}", requested_ref) and not _is_sha(
        requested_ref, 40
    ):
        raise InvalidDatasetRevision(
            "requested dataset revision must use lowercase hexadecimal"
        )
    try:
        info = api.dataset_info(repo_id, revision=requested_ref, token=token)
        resolved = getattr(info, "sha", None)
    except BaseException as error:
        raise InvalidDatasetRevision(
            f"dataset revision could not be verified ({type(error).__name__})"
        ) from None
    if not _is_sha(resolved, 40):
        raise InvalidDatasetRevision(
            "authoritative dataset revision response is malformed or ambiguous"
        )
    if _is_sha(requested_ref, 40) and resolved != requested_ref:
        raise InvalidDatasetRevision(
            "exact dataset revision identity does not match the authoritative response"
        )
    return resolved


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise DatasetContractError(f"{label} has missing or unknown fields")


def _integer(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _split_for(article_key: str) -> str:
    bucket = int.from_bytes(
        hashlib.sha256(article_key.encode("utf-8")).digest()[:8], "big"
    ) % 100
    return "train" if bucket < 98 else "validation" if bucket == 98 else "test"


def validate_distillation_row(
    value: object, *, expected_split: str | None = None
) -> dict[str, object]:
    """Apply the complete closed v1 schema and Task 5 semantic checks."""
    if not isinstance(value, Mapping):
        raise DatasetContractError("stream row must be a mapping")
    if len(value) != len(_ALLOWED_ROW_FIELDS):
        raise DatasetContractError("stream row has an invalid number of fields")
    document = dict(value)
    _validate_schema("distillation-example-v1", document)
    if set(document) != _ALLOWED_ROW_FIELDS:
        raise DatasetContractError("stream row has a missing, unknown, or hidden field")
    key = document["article_key"]
    page_id = document["page_id"]
    key_match = _ARTICLE_KEY.fullmatch(key) if isinstance(key, str) else None
    if not key_match or not _integer(page_id, minimum=1):
        raise DatasetContractError("stream row article identity is invalid")
    if int(key_match.group(1)) != page_id:
        raise DatasetContractError("stream row article_key/page_id identity mismatches")
    for field in ("canonical_title", "lead_text", "article_text", "reconciliation_status"):
        if not isinstance(document[field], str) or not document[field]:
            raise DatasetContractError(f"stream row {field} is invalid")
    wikidata = document["wikidata_id"]
    if wikidata is not None and (
        not isinstance(wikidata, str) or _WIKIDATA.fullmatch(wikidata) is None
    ):
        raise DatasetContractError("stream row wikidata_id is invalid")
    revision = document["source_revision_id"]
    if revision is not None and not _integer(revision, minimum=1):
        raise DatasetContractError("stream row source_revision_id is invalid")
    if document["snapshot_date"] != "2016-10-01":
        raise DatasetContractError("stream row snapshot_date is invalid")
    split = document["split"]
    if split not in {"train", "validation", "test"} or split != _split_for(key):
        raise DatasetContractError("stream row split is invalid")
    if expected_split is not None and split != expected_split:
        raise DatasetContractError("stream row belongs to the wrong requested split")
    vector = document["teacher_vector"]
    if not isinstance(vector, (list, tuple)) or len(vector) != 100:
        raise DatasetContractError("stream row teacher_vector must have shape (100,)")
    if any(not _finite(item) for item in vector):
        raise DatasetContractError("stream row teacher_vector must be finite")
    checked_vector = [float(item) for item in vector]
    if any(abs(item) > 3.4028234663852886e38 for item in checked_vector):
        raise DatasetContractError("stream row teacher_vector exceeds float32 range")
    norm = document["teacher_norm"]
    if not _finite(norm) or float(norm) <= 0:
        raise DatasetContractError("stream row teacher_norm must be positive and finite")
    calculated = math.sqrt(math.fsum(item * item for item in checked_vector))
    if not math.isclose(float(norm), calculated, rel_tol=1e-6, abs_tol=1e-7):
        raise DatasetContractError("stream row teacher_norm does not match teacher_vector")
    document["teacher_vector"] = checked_vector
    document["teacher_norm"] = float(norm)
    return document


def _bounded_training_text(title: object, lead: object) -> None:
    if not isinstance(title, str) or not title or not isinstance(lead, str) or not lead:
        raise DatasetContractError("training title and lead text must be nonblank strings")
    if len(title) + len(lead) > _MAX_TRAINING_TEXT_BYTES:
        raise DatasetContractError("training text is too large for the bounded stream")
    if len(title.encode("utf-8")) + len(lead.encode("utf-8")) > _MAX_TRAINING_TEXT_BYTES:
        raise DatasetContractError("training UTF-8 text size exceeds the bounded stream")


def validate_training_row(
    value: object, *, expected_split: str | None = None
) -> dict[str, object]:
    """Validate or project one row to the only fields training may retain."""
    if not isinstance(value, Mapping):
        raise DatasetContractError("training row must be a mapping")
    if len(value) not in {len(_ALLOWED_ROW_FIELDS), len(_TRAINING_ROW_FIELDS)}:
        raise DatasetContractError("training row has an invalid number of fields")
    document = dict(value)
    if set(document) == _ALLOWED_ROW_FIELDS:
        document = validate_distillation_row(document, expected_split=expected_split)
        projected = {name: document[name] for name in _TRAINING_ROW_FIELD_ORDER}
    elif set(document) == _TRAINING_ROW_FIELDS:
        projected = document
    else:
        raise DatasetContractError("training row has missing, unknown, or retained hidden fields")
    key = projected["article_key"]
    page_id = projected["page_id"]
    match = _ARTICLE_KEY.fullmatch(key) if isinstance(key, str) else None
    split = projected["split"]
    if (
        match is None
        or not _integer(page_id, minimum=1)
        or int(match.group(1)) != page_id
        or split not in {"train", "validation", "test"}
        or split != _split_for(key)
        or (expected_split is not None and split != expected_split)
    ):
        raise DatasetContractError("training row identity or split is invalid")
    _bounded_training_text(projected["canonical_title"], projected["lead_text"])
    vector = projected["teacher_vector"]
    norm = projected["teacher_norm"]
    if (
        not isinstance(vector, (list, tuple))
        or len(vector) != 100
        or any(not _finite(item) for item in vector)
        or not _finite(norm)
        or float(norm) <= 0
    ):
        raise DatasetContractError("training teacher vector or norm is invalid")
    checked_vector = [float(item) for item in vector]
    calculated = math.sqrt(math.fsum(item * item for item in checked_vector))
    if (
        any(abs(item) > 3.4028234663852886e38 for item in checked_vector)
        or not math.isclose(float(norm), calculated, rel_tol=1e-6, abs_tol=1e-7)
    ):
        raise DatasetContractError("training teacher vector semantics are invalid")
    projected["teacher_vector"] = checked_vector
    projected["teacher_norm"] = float(norm)
    if len(_canonical_json(projected)) > _MAX_PROJECTED_ROW_BYTES:
        raise DatasetContractError("projected training row exceeds its byte budget")
    return projected


def _parse_document(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetContractError(f"pinned {label} is malformed") from error
    if not isinstance(value, dict):
        raise DatasetContractError(f"pinned {label} must be an object")
    return value


def _validate_manifest(value: Mapping[str, object]) -> dict[str, object]:
    document = deepcopy(dict(value))
    _validate_schema("dataset-manifest-v1", document)
    _require_exact_keys(
        document,
        {
            "manifest_version", "schema_version", "state", "schema",
            "dataset_config", "pilot_article_keys", "counts", "shards",
            "aggregate_sha256", "rows_sha256", "provenance",
        },
        "pinned manifest",
    )
    if (
        document["manifest_version"] != 1
        or document["schema_version"] != 1
        or document["state"] != "prepared"
        or document["schema"] != "distillation-example-v1"
        or document["dataset_config"] != DATASET_CONFIG
        or not _is_sha(document["aggregate_sha256"], 64)
        or not _is_sha(document["rows_sha256"], 64)
    ):
        raise DatasetContractError("pinned manifest release identity is invalid")
    counts = document["counts"]
    if not isinstance(counts, Mapping):
        raise DatasetContractError("pinned manifest counts are invalid")
    _require_exact_keys(counts, {"total", "train", "validation", "test"}, "manifest counts")
    if any(not _integer(value) for value in counts.values()) or counts["total"] < 1:
        raise DatasetContractError("pinned manifest counts are invalid")
    shards = document["shards"]
    if not isinstance(shards, list) or not shards:
        raise DatasetContractError("pinned manifest shards are invalid")
    expected_shard_keys = {
        "path", "split", "rows", "bytes", "sha256", "rows_sha256", "schema",
        "version", "min_article_key", "max_article_key", "min_rank", "max_rank",
    }
    totals = {name: 0 for name in ("train", "validation", "test")}
    paths: set[str] = set()
    checksums: set[str] = set()
    row_checksums: set[str] = set()
    last_rank: dict[str, str] = {}
    for item in shards:
        if not isinstance(item, Mapping):
            raise DatasetContractError("pinned manifest shard is invalid")
        _require_exact_keys(item, expected_shard_keys, "manifest shard")
        path, split = item["path"], item["split"]
        if (
            split not in totals
            or not isinstance(path, str)
            or re.fullmatch(
                rf"{DATASET_CONFIG}/(train|validation|test)/part-[0-9]{{5}}\.parquet",
                path,
            ) is None
            or f"/{split}/" not in path
            or item["schema"] != "distillation-example-v1"
            or item["version"] != 1
            or not _integer(item["rows"], minimum=1)
            or not _integer(item["bytes"], minimum=1)
            or not _is_sha(item["sha256"], 64)
            or not _is_sha(item["rows_sha256"], 64)
            or not _is_sha(item["min_rank"], 64)
            or not _is_sha(item["max_rank"], 64)
            or _ARTICLE_KEY.fullmatch(str(item["min_article_key"])) is None
            or _ARTICLE_KEY.fullmatch(str(item["max_article_key"])) is None
            or item["min_article_key"] > item["max_article_key"]
            or item["min_rank"] > item["max_rank"]
            or (split in last_rank and item["min_rank"] <= last_rank[split])
        ):
            raise DatasetContractError("pinned manifest shard semantics are invalid")
        if path in paths or item["sha256"] in checksums or item["rows_sha256"] in row_checksums:
            raise DatasetContractError("pinned manifest has duplicate shard identity")
        paths.add(path); checksums.add(str(item["sha256"])); row_checksums.add(str(item["rows_sha256"]))
        last_rank[str(split)] = str(item["max_rank"])
        totals[str(split)] += int(item["rows"])
    expected_counts = {"total": sum(totals.values()), **totals}
    if dict(counts) != expected_counts:
        raise DatasetContractError("pinned manifest counts do not match shards")
    aggregate = hashlib.sha256(_canonical_json(shards)).hexdigest()
    if document["aggregate_sha256"] != aggregate:
        raise DatasetContractError("pinned manifest aggregate checksum is invalid")
    pilot = document["pilot_article_keys"]
    if (
        not isinstance(pilot, list)
        or not pilot
        or len(pilot) != len(set(pilot))
        or any(not isinstance(key, str) or _ARTICLE_KEY.fullmatch(key) is None for key in pilot)
    ):
        raise DatasetContractError("pinned manifest pilot identity is invalid")
    provenance = document["provenance"]
    if not isinstance(provenance, Mapping):
        raise DatasetContractError("pinned manifest provenance is invalid")
    _require_exact_keys(provenance, {"schema", "identifiers", "document"}, "manifest provenance")
    identifiers = provenance["identifiers"]
    if (
        provenance["schema"] != "provenance-v1"
        or not isinstance(identifiers, Mapping)
        or dict(identifiers) != {
            "dataset_config": DATASET_CONFIG,
            "example_schema": "distillation-example-v1",
            "snapshot_date": "2016-10-01",
            "teacher_dimension": 100,
        }
    ):
        raise DatasetContractError("pinned manifest provenance identity is invalid")
    provenance_document = provenance["document"]
    if not isinstance(provenance_document, Mapping):
        raise DatasetContractError("pinned provenance document is invalid")
    _validate_schema("provenance-v1", provenance_document)
    _require_exact_keys(
        provenance_document, {"schema_version", "sources", "artifacts", "reports"},
        "provenance document",
    )
    if provenance_document["schema_version"] != 1:
        raise DatasetContractError("pinned provenance version is invalid")
    sources = provenance_document["sources"]
    artifacts = provenance_document["artifacts"]
    reports = provenance_document["reports"]
    if not isinstance(sources, list) or not sources or not isinstance(artifacts, Mapping) or not artifacts:
        raise DatasetContractError("pinned provenance evidence is incomplete")
    source_identities = {
        (str(source["role"]), str(source["filename"]), str(source["md5"]))
        for source in sources
    }
    required_source_identities = {
        (
            "teacher",
            "2016-09-01_2016-09-30_en_100.zip",
            "ac70acfc41aff7a23cc9439e3bb1771f",
        ),
        (
            "wikipedia",
            "enwiki-20161001-pages-articles-multistream.xml.bz2",
            "5df8e610829c336138dcb9191071b283",
        ),
    }
    if not required_source_identities <= source_identities:
        raise DatasetContractError(
            "pinned provenance does not bind the approved teacher and Wikipedia snapshot"
        )
    for artifact in artifacts.values():
        if (
            not isinstance(artifact, Mapping)
            or set(artifact) != {"sha256", "size"}
            or not _is_sha(artifact["sha256"], 64)
            or not _integer(artifact["size"])
        ):
            raise DatasetContractError("pinned provenance artifact is invalid")
    required_reports = {
        "row_counts", "match_rate", "exclusion_counts", "text_statistics",
        "vector_statistics",
    }
    allowed_reports = required_reports | {
        "dataset_aggregate_sha256", "dataset_rows_sha256", "dataset_counts",
    }
    if (
        not isinstance(reports, Mapping)
        or not required_reports <= set(reports) <= allowed_reports
        or any(
        reports.get(name) != expected
        for name, expected in {
            "dataset_aggregate_sha256": aggregate,
            "dataset_rows_sha256": document["rows_sha256"],
            "dataset_counts": expected_counts,
        }.items()
        )
    ):
        raise DatasetContractError("pinned provenance report binding is stale")
    for name in ("row_counts", "exclusion_counts"):
        values = reports[name]
        if (
            not isinstance(values, Mapping)
            or any(not isinstance(key, str) or not _integer(item) for key, item in values.items())
        ):
            raise DatasetContractError(f"pinned provenance {name} is invalid")
    match_rate = reports["match_rate"]
    if not _finite(match_rate) or not 0 <= float(match_rate) <= 1:
        raise DatasetContractError("pinned provenance match_rate is invalid")
    text_statistics = reports["text_statistics"]
    text_fields = {
        "count", "min_length", "max_length", "mean_length", "stddev_length",
        "p50_length", "p95_length", "p99_length", "histogram",
    }
    if not isinstance(text_statistics, Mapping) or set(text_statistics) != text_fields:
        raise DatasetContractError("pinned provenance text statistics schema is invalid")
    if any(
        not _integer(text_statistics[name])
        for name in ("count", "min_length", "max_length")
    ) or any(
        not _finite(text_statistics[name]) or float(text_statistics[name]) < 0
        for name in ("mean_length", "stddev_length", "p50_length", "p95_length", "p99_length")
    ):
        raise DatasetContractError("pinned provenance text statistics are invalid")
    histogram = text_statistics["histogram"]
    if not isinstance(histogram, list) or not histogram or any(not _integer(item) for item in histogram):
        raise DatasetContractError("pinned provenance text histogram is invalid")
    vector_statistics = reports["vector_statistics"]
    vector_fields = {
        "dimension", "count", "min_norm", "max_norm", "mean_norm", "stddev_norm",
        "p50_norm", "p95_norm", "non_finite_count",
    }
    if not isinstance(vector_statistics, Mapping) or set(vector_statistics) != vector_fields:
        raise DatasetContractError("pinned provenance vector statistics schema is invalid")
    if (
        vector_statistics["dimension"] != 100
        or any(not _integer(vector_statistics[name]) for name in ("count", "non_finite_count"))
        or any(
            not _finite(vector_statistics[name]) or float(vector_statistics[name]) < 0
            for name in ("min_norm", "max_norm", "mean_norm", "stddev_norm", "p50_norm", "p95_norm")
        )
    ):
        raise DatasetContractError("pinned provenance vector statistics are invalid")
    return document


def _validate_readiness(
    value: Mapping[str, object], manifest: Mapping[str, object], revision: str
) -> dict[str, object]:
    document = deepcopy(dict(value))
    _validate_schema("dataset-readiness-v1", document)
    _require_exact_keys(
        document,
        {
            "state", "schema_version", "teacher_dimension", "available_examples",
            "verified_shards", "source_checksums", "remote_verified", "remote_commit_sha",
        },
        "pinned readiness",
    )
    remote_pair_is_valid = (
        document["remote_verified"] is False
        and document["remote_commit_sha"] is None
    ) or (
        document["remote_verified"] is True
        and document["remote_commit_sha"] == revision
    )
    if (
        document["state"] not in {"pilot_ready", "complete"}
        or document["schema_version"] != 1
        or document["teacher_dimension"] != 100
        or not _integer(document["available_examples"], minimum=1)
        or document["available_examples"] != manifest["counts"]["total"]  # type: ignore[index]
        or not remote_pair_is_valid
    ):
        raise DatasetContractError("pinned readiness is not remotely usable at this revision")
    verified = document["verified_shards"]
    expected = [
        {"path": item["path"], "sha256": item["sha256"], "examples": item["rows"]}
        for item in manifest["shards"]  # type: ignore[union-attr]
    ]
    if verified != expected:
        raise DatasetContractError("pinned readiness shards do not match manifest")
    checksums = document["source_checksums"]
    artifact = manifest["provenance"]["document"]["artifacts"].get("accepted_jsonl")  # type: ignore[index]
    if (
        not isinstance(checksums, Mapping)
        or not checksums
        or not all(isinstance(name, str) and name and _is_sha(value, 64) for name, value in checksums.items())
        or not isinstance(artifact, Mapping)
        or checksums.get("accepted_jsonl") != artifact.get("sha256")
    ):
        raise DatasetContractError("pinned readiness source evidence does not match manifest")
    return document


def _remote_bytes(
    api: object, repo_id: str, path_in_repo: str, revision: str, token: str
) -> bytes:
    try:
        getter = getattr(api, "get_file_bytes", None)
        if callable(getter):
            value = getter(
                repo_id=repo_id,
                path_in_repo=path_in_repo,
                repo_type="dataset",
                revision=revision,
                token=token,
            )
            if not isinstance(value, bytes):
                raise TypeError("remote metadata adapter returned non-bytes")
            return value
        downloader = getattr(api, "hf_hub_download", None)
        if callable(downloader):
            path = downloader(
                repo_id=repo_id,
                filename=path_in_repo,
                repo_type="dataset",
                revision=revision,
                token=token,
            )
        else:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(
                repo_id=repo_id,
                filename=path_in_repo,
                repo_type="dataset",
                revision=revision,
                token=token,
            )
        return Path(path).read_bytes()
    except DatasetContractError:
        raise
    except BaseException as error:
        raise DatasetContractError(
            f"unable to fetch pinned {path_in_repo} ({type(error).__name__})"
        ) from None


def _validate_metadata(
    api: object, repo_id: str, revision: str, token: str
) -> tuple[str, str, dict[str, int]]:
    manifest_bytes = _remote_bytes(api, repo_id, MANIFEST_PATH, revision, token)
    readiness_bytes = _remote_bytes(api, repo_id, READINESS_PATH, revision, token)
    readme_bytes = _remote_bytes(api, repo_id, README_PATH, revision, token)
    manifest = _validate_manifest(_parse_document(manifest_bytes, "manifest"))
    _validate_readiness(_parse_document(readiness_bytes, "readiness"), manifest, revision)
    if readme_bytes != _DATASET_CARD:
        raise DatasetContractError(
            "pinned README does not advertise exactly the approved dataset configuration"
        )
    counts = manifest["counts"]
    assert isinstance(counts, Mapping)
    return (
        hashlib.sha256(manifest_bytes).hexdigest(),
        hashlib.sha256(readiness_bytes).hexdigest(),
        {name: int(counts[name]) for name in ("train", "validation", "test")},
    )


def _prove_pinned_private_dataset(
    api: object, repo_id: str, revision: str, token: str
) -> None:
    try:
        info = api.dataset_info(repo_id, revision=revision, token=token)
    except BaseException as error:
        raise DatasetContractError(
            f"pinned private dataset identity could not be proved ({type(error).__name__})"
        ) from None
    if getattr(info, "private", None) is not True:
        raise DatasetContractError("dataset repository privacy could not be proved private")
    if getattr(info, "sha", None) != revision:
        raise DatasetContractError("pinned dataset commit identity does not match")


def _json_state(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_state(item) for item in value]
    if isinstance(value, list):
        return [_json_state(item) for item in value]
    return value


def _tuple_state(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuple_state(item) for item in value)
    return value


def _preflight_state_value(value: object, *, max_nodes: int, max_characters: int) -> None:
    """Reject hostile state containers before JSON encoding or deep copying."""
    stack = [value]
    nodes = 0
    characters = 0
    while stack:
        item = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("stream state contains too many values")
        if isinstance(item, str):
            if len(item) > _MAX_TRAINING_TEXT_BYTES:
                raise ValueError("stream state contains an oversized string")
            characters += len(item)
            if characters > max_characters:
                raise ValueError("stream state exceeds its character budget")
        elif isinstance(item, Mapping):
            if len(item) > max_nodes - nodes:
                raise ValueError("stream state contains too many values")
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            if len(item) > max_nodes - nodes:
                raise ValueError("stream state contains too many values")
            stack.extend(item)
        elif item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError("stream state contains a non-JSON value")


class StatefulDistillationStream(Iterable[dict[str, object]]):
    """A bounded-memory deterministic shuffle whose entire cursor is serializable."""

    def __init__(
        self,
        dataset: Iterable[Mapping[str, object]],
        *,
        repo_id: str,
        revision: str,
        split: str,
        seed: int,
        shuffle_buffer_size: int,
        epoch: int,
        manifest_sha256: str,
        readiness_sha256: str,
        expected_examples: int,
    ) -> None:
        if iter(dataset) is dataset:
            raise DatasetContractError(
                "resumable streaming requires a restartable dataset iterable"
            )
        self._dataset = dataset
        self.repo_id = repo_id
        self.revision = revision
        self.config_name = DATASET_CONFIG
        self.split = split
        self.seed = seed
        self.shuffle_buffer_size = shuffle_buffer_size
        self.epoch = epoch
        self.manifest_sha256 = manifest_sha256
        self.readiness_sha256 = readiness_sha256
        self.expected_examples = expected_examples
        self._live: dict[str, object] = self._initial_cursor()
        self._positioned_iterator: Iterator[dict[str, object]] | None = None
        self._iterating = False

    def _initial_cursor(self) -> dict[str, object]:
        derived_seed = int.from_bytes(
            hashlib.sha256(f"{self.seed}:{self.epoch}".encode("ascii")).digest()[:16],
            "big",
        )
        rng = random.Random(derived_seed)
        return {
            "source_cursor": 0,
            "processed_examples": 0,
            "shuffle_buffer": [],
            "shuffle_rng_state": _json_state(rng.getstate()),
            "source_exhausted": False,
            "complete": False,
        }

    def _identity(self) -> dict[str, object]:
        return {
            "repo_id": self.repo_id,
            "revision": self.revision,
            "config_name": self.config_name,
            "split": self.split,
            "seed": self.seed,
            "shuffle_buffer_size": self.shuffle_buffer_size,
            "epoch": self.epoch,
            "manifest_sha256": self.manifest_sha256,
            "readiness_sha256": self.readiness_sha256,
            "expected_examples": self.expected_examples,
        }

    @property
    def checkpoint_byte_limit(self) -> int:
        buffered = min(self.shuffle_buffer_size, self.expected_examples)
        return _STATE_BASE_BYTES + buffered * _MAX_PROJECTED_ROW_BYTES

    def set_epoch(self, epoch: int) -> None:
        if self._iterating or self._positioned_iterator is not None:
            raise RuntimeError("cannot change epoch while stream state is active")
        if not _integer(epoch):
            raise ValueError("epoch must be a nonnegative integer")
        self.epoch = epoch
        self._live = self._initial_cursor()

    def state_dict(self) -> dict[str, object]:
        state = self._live
        document: dict[str, object] = {
            "state_version": 1,
            "identity": self._identity(),
            "cursor": deepcopy(state),
            "shard": {
                "index": None,
                "example_cursor": int(state["source_cursor"]),
            },
        }
        document["state_sha256"] = hashlib.sha256(_canonical_json(document)).hexdigest()
        if len(_canonical_json(document)) > self.checkpoint_byte_limit:
            raise ValueError("stream checkpoint exceeds its deterministic byte budget")
        return document

    def load_state_dict(self, state: Mapping[str, object]) -> None:
        if self._iterating:
            raise RuntimeError("cannot restore state while iterating")
        if not isinstance(state, Mapping) or len(state) != 5 or set(state) != {
            "state_version", "identity", "cursor", "shard", "state_sha256"
        }:
            raise ValueError("stream state has an invalid shape")
        _preflight_state_value(
            state,
            max_nodes=5_000
            + min(self.shuffle_buffer_size, self.expected_examples) * 120,
            max_characters=self.checkpoint_byte_limit,
        )
        if state["state_version"] != 1 or state["identity"] != self._identity():
            raise ValueError("stream state immutable identity mismatch")
        cursor = state["cursor"]
        if not isinstance(cursor, Mapping) or set(cursor) != {
            "source_cursor", "processed_examples", "shuffle_buffer",
            "shuffle_rng_state", "source_exhausted", "complete",
        }:
            raise ValueError("stream state cursor is invalid")
        source_cursor = cursor["source_cursor"]
        processed = cursor["processed_examples"]
        buffer = cursor["shuffle_buffer"]
        shard = state["shard"]
        if (
            not _integer(source_cursor)
            or not _integer(processed)
            or not isinstance(buffer, list)
            or len(buffer) > self.shuffle_buffer_size
            or len(buffer) > self.expected_examples
            or not isinstance(cursor["source_exhausted"], bool)
            or not isinstance(cursor["complete"], bool)
            or cursor["shuffle_rng_state"] is None
        ):
            raise ValueError("stream state cursor values are invalid")
        try:
            verifier = random.Random()
            verifier.setstate(_tuple_state(cursor["shuffle_rng_state"]))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("stream state shuffle RNG is invalid") from error
        checked_buffer = [
            validate_training_row(item, expected_split=self.split) for item in buffer
        ]
        restored = deepcopy(dict(cursor))
        restored["shuffle_buffer"] = checked_buffer
        if (
            not isinstance(shard, Mapping)
            or set(shard) != {"index", "example_cursor"}
            or shard["index"] is not None
            or shard["example_cursor"] != source_cursor
            or source_cursor > self.expected_examples
            or processed > self.expected_examples
            or source_cursor != processed + len(checked_buffer)
            or len({str(item["article_key"]) for item in checked_buffer})
            != len(checked_buffer)
            or (
                not cursor["source_exhausted"]
                and source_cursor > 0
                and len(checked_buffer) != self.shuffle_buffer_size
            )
            or (
                cursor["complete"]
                and (
                    not cursor["source_exhausted"]
                    or checked_buffer
                    or source_cursor != processed
                    or processed != self.expected_examples
                )
            )
        ):
            raise ValueError("stream state cursor/shard invariants are invalid")
        supplied_digest = state["state_sha256"]
        unsigned = {name: state[name] for name in state if name != "state_sha256"}
        # This unkeyed digest detects accidental serialization corruption only;
        # authenticity comes from the deterministic history comparison below.
        if (
            not _is_sha(supplied_digest, 64)
            or len(_canonical_json(state)) > self.checkpoint_byte_limit
            or hashlib.sha256(_canonical_json(unsigned)).hexdigest() != supplied_digest
        ):
            raise ValueError("stream state checksum or byte budget is invalid")
        if self._positioned_iterator is not None:
            self._positioned_iterator.close()
            self._positioned_iterator = None
        self._live = self._initial_cursor()
        history = self._iterate()
        try:
            for _ in range(int(processed)):
                next(history)
            if cursor["complete"]:
                try:
                    next(history)
                except StopIteration:
                    pass
                else:
                    raise ValueError("stream state completes before deterministic history")
            if self._live != restored:
                raise ValueError("stream state does not match deterministic stream history")
        except StopIteration as error:
            history.close()
            self._live = self._initial_cursor()
            raise ValueError("stream state exceeds deterministic stream history") from error
        except BaseException:
            history.close()
            self._live = self._initial_cursor()
            raise
        if cursor["complete"]:
            history.close()
            self._positioned_iterator = None
        else:
            # The sole prefix traversal is retained at its exact source position.
            # Resumed iteration continues this generator and never scans again.
            self._iterating = False
            self._positioned_iterator = history

    def __iter__(self) -> Iterator[dict[str, object]]:
        if self._iterating:
            raise RuntimeError("stateful stream does not support concurrent iteration")
        if self._positioned_iterator is not None:
            positioned = self._positioned_iterator
            self._positioned_iterator = None
            self._iterating = True
            return self._continue_positioned(positioned)
        return self._iterate()

    def _continue_positioned(
        self, positioned: Iterator[dict[str, object]]
    ) -> Iterator[dict[str, object]]:
        try:
            yield from positioned
        finally:
            positioned.close()
            self._iterating = False

    def _iterate(self) -> Iterator[dict[str, object]]:
        self._iterating = True
        seen = sqlite3.connect("")
        seen.execute("CREATE TABLE article_keys (article_key TEXT PRIMARY KEY)")
        try:
            source = iter(self._dataset)
            live = deepcopy(self._live)
            rng = random.Random()
            rng.setstate(_tuple_state(live["shuffle_rng_state"]))  # type: ignore[arg-type]

            def check_and_remember(raw: object) -> dict[str, object]:
                physical = validate_distillation_row(raw, expected_split=self.split)
                checked = validate_training_row(
                    {name: physical[name] for name in _TRAINING_ROW_FIELD_ORDER},
                    expected_split=self.split,
                )
                try:
                    seen.execute(
                        "INSERT INTO article_keys(article_key) VALUES (?)",
                        (checked["article_key"],),
                    )
                except sqlite3.IntegrityError as error:
                    raise DatasetContractError(
                        "stream contains a duplicate physical article identity"
                    ) from error
                return checked

            for _ in range(int(live["source_cursor"])):
                try:
                    check_and_remember(next(source))
                except StopIteration as error:
                    raise DatasetContractError(
                        "physical split ended before the restored cursor"
                    ) from error
            self._live = live
            buffer = live["shuffle_buffer"]
            assert isinstance(buffer, list)

            def next_checked() -> dict[str, object]:
                if int(live["source_cursor"]) >= self.expected_examples:
                    try:
                        next(source)
                    except StopIteration:
                        raise
                    raise DatasetContractError(
                        "physical split contains additional rows beyond manifest count"
                    )
                try:
                    raw = next(source)
                except StopIteration as error:
                    raise DatasetContractError(
                        "physical split ended early before manifest count"
                    ) from error
                checked = check_and_remember(raw)
                live["source_cursor"] = int(live["source_cursor"]) + 1
                return checked

            if not buffer and not live["source_exhausted"]:
                while len(buffer) < self.shuffle_buffer_size:
                    try:
                        buffer.append(next_checked())
                    except StopIteration:
                        live["source_exhausted"] = True
                        break
            while buffer:
                if not live["source_exhausted"]:
                    try:
                        incoming = next_checked()
                    except StopIteration:
                        live["source_exhausted"] = True
                    else:
                        index = rng.randrange(len(buffer))
                        outgoing = buffer[index]
                        buffer[index] = incoming
                        live["processed_examples"] = int(live["processed_examples"]) + 1
                        live["shuffle_rng_state"] = _json_state(rng.getstate())
                        yield deepcopy(outgoing)
                        continue
                index = rng.randrange(len(buffer))
                outgoing = buffer.pop(index)
                live["processed_examples"] = int(live["processed_examples"]) + 1
                live["shuffle_rng_state"] = _json_state(rng.getstate())
                yield deepcopy(outgoing)
            live["complete"] = True
        finally:
            seen.close()
            self._iterating = False


def _load_stream(
    *,
    repo_id: str,
    revision: str,
    split: str,
    token: str,
    api: object | None,
    config_name: str,
    seed: int,
    shuffle_buffer_size: int,
    epoch: int,
    load_dataset_fn: Callable[..., Iterable[Mapping[str, object]]] | None,
) -> StatefulDistillationStream:
    if repo_id != DEFAULT_DATASET_REPO:
        raise ValueError(f"dataset repository is fixed to {DEFAULT_DATASET_REPO}")
    if config_name != DATASET_CONFIG:
        raise ForbiddenDatasetConfiguration(
            f"dataset configuration is fixed to {DATASET_CONFIG}"
        )
    if not _is_sha(revision, 40):
        raise InvalidDatasetRevision("dataset revision must be an exact lowercase commit SHA")
    if not isinstance(token, str) or not token:
        raise ValueError("a private-Hub token is required")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("shuffle seed must be an integer")
    if not _integer(shuffle_buffer_size, minimum=1):
        raise ValueError("shuffle buffer size must be a positive integer")
    if not _integer(epoch):
        raise ValueError("epoch must be a nonnegative integer")
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    _prove_pinned_private_dataset(api, repo_id, revision, token)
    manifest_sha, readiness_sha, split_counts = _validate_metadata(
        api, repo_id, revision, token
    )
    if load_dataset_fn is None:
        from datasets import load_dataset as load_dataset_fn
    dataset = load_dataset_fn(
        repo_id,
        DATASET_CONFIG,
        split=split,
        revision=revision,
        token=token,
        streaming=True,
    )
    if isinstance(dataset, Mapping) or not isinstance(dataset, Iterable):
        raise DatasetContractError("streaming dataset loader returned an invalid split")
    return StatefulDistillationStream(
        dataset,
        repo_id=repo_id,
        revision=revision,
        split=split,
        seed=seed,
        shuffle_buffer_size=shuffle_buffer_size,
        epoch=epoch,
        manifest_sha256=manifest_sha,
        readiness_sha256=readiness_sha,
        expected_examples=split_counts[split],
    )


def load_distillation_stream(
    repo_id: str = DEFAULT_DATASET_REPO,
    revision: str = "",
    split: str = "train",
    token: str = "",
    *,
    api: object | None = None,
    config_name: str = DATASET_CONFIG,
    seed: int = 0,
    shuffle_buffer_size: int = 10_000,
    epoch: int = 0,
    load_dataset_fn: Callable[..., Iterable[Mapping[str, object]]] | None = None,
) -> StatefulDistillationStream:
    """Load only the pinned training split; test and validation cannot leak in."""
    if split != "train":
        raise ValueError("training data API exposes only the train split")
    return _load_stream(
        repo_id=repo_id, revision=revision, split="train", token=token, api=api,
        config_name=config_name, seed=seed, shuffle_buffer_size=shuffle_buffer_size,
        epoch=epoch, load_dataset_fn=load_dataset_fn,
    )


def load_validation_stream(
    repo_id: str = DEFAULT_DATASET_REPO,
    revision: str = "",
    token: str = "",
    *,
    api: object | None = None,
    config_name: str = DATASET_CONFIG,
    seed: int = 0,
    shuffle_buffer_size: int = 1,
    epoch: int = 0,
    load_dataset_fn: Callable[..., Iterable[Mapping[str, object]]] | None = None,
) -> StatefulDistillationStream:
    """Load the pinned validation split through an intentionally separate API."""
    return _load_stream(
        repo_id=repo_id, revision=revision, split="validation", token=token, api=api,
        config_name=config_name, seed=seed, shuffle_buffer_size=shuffle_buffer_size,
        epoch=epoch, load_dataset_fn=load_dataset_fn,
    )


__all__ = [
    "DATASET_CONFIG",
    "DEFAULT_DATASET_REPO",
    "DatasetContractError",
    "ForbiddenDatasetConfiguration",
    "InvalidDatasetRevision",
    "StatefulDistillationStream",
    "load_distillation_stream",
    "load_validation_stream",
    "resolve_dataset_revision",
    "validate_distillation_row",
]
