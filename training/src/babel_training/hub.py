"""Deterministic distilled artifacts and verified append-only model publication."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from safetensors.numpy import load_file, save_file

from .config import DistillationConfig


DEFAULT_MODEL_REPO = "dhelmy990/babel-qwen-navigation-2016"
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DATASET_REPO = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIG = "distillation_2016"
_SHA40 = re.compile(r"[a-f0-9]{40}")
_SHA64 = re.compile(r"[a-f0-9]{64}")
_PAYLOAD_NAMES = frozenset(
    {
        "projection.safetensors",
        "adapter_model.safetensors",
        "adapter_config.json",
        "training_config.json",
        "validation_report.json",
    }
)
_MANIFEST_NAME = "artifact_manifest.json"

# Immutable architecture facts for Qwen/Qwen3-Embedding-0.6B at MODEL_REVISION.
# The pinned config has 28 layers, hidden_size=1024, 16 query heads, 8 KV
# heads, and head_dim=128; therefore q_proj is 1024->2048 and v_proj is
# 1024->1024. The adapter artifact must cover both modules in every layer.
_QWEN_NUM_HIDDEN_LAYERS = 28
_QWEN_HIDDEN_SIZE = 1024
_QWEN_LORA_OUTPUTS = {"q_proj": 2048, "v_proj": 1024}


class ArtifactExportError(ValueError):
    """Local artifact inputs violate the immutable export contract."""


class ArtifactPublicationError(RuntimeError):
    """The private append-only publication could not be proved correct."""


def _canonical_json(value: object) -> bytes:
    try:
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
    except (TypeError, ValueError) as error:
        raise ArtifactExportError("artifact metadata must be finite JSON data") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha(value: object, length: int) -> bool:
    pattern = _SHA40 if length == 40 else _SHA64
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _ensure_no_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ArtifactExportError("artifact output path may not contain a symlink")


def _tensor_array(value: object, name: str) -> np.ndarray:
    candidate = value
    detach = getattr(candidate, "detach", None)
    if callable(detach):
        candidate = detach()
    device = getattr(candidate, "device", None)
    if device is not None and str(device).split(":", 1)[0] != "cpu":
        cpu = getattr(candidate, "cpu", None)
        if not callable(cpu):
            raise ArtifactExportError(f"tensor {name!r} cannot be moved to CPU")
        candidate = cpu()
    contiguous = getattr(candidate, "contiguous", None)
    if callable(contiguous):
        candidate = contiguous()
    numpy_method = getattr(candidate, "numpy", None)
    if callable(numpy_method):
        candidate = numpy_method()
    try:
        array = np.asarray(candidate)
    except BaseException as error:
        raise ArtifactExportError(f"tensor {name!r} cannot be represented safely") from error
    if array.dtype.kind not in "iufc" or array.dtype.kind == "b" or array.ndim == 0:
        raise ArtifactExportError(f"tensor {name!r} must be a numeric non-scalar")
    if not np.isfinite(array).all():
        raise ArtifactExportError(f"tensor {name!r} must contain only finite values")
    return np.ascontiguousarray(array)


def _checked_tensors(
    tensors: Mapping[str, object], *, kind: str
) -> dict[str, np.ndarray]:
    if not isinstance(tensors, Mapping) or not tensors:
        raise ArtifactExportError(f"{kind} tensors must be a nonempty mapping")
    result: dict[str, np.ndarray] = {}
    folded: set[str] = set()
    for name, tensor in tensors.items():
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or name.casefold() in folded
        ):
            raise ArtifactExportError(f"{kind} tensor names must be unique and nonblank")
        folded.add(name.casefold())
        lower = name.casefold()
        if kind == "projection":
            if lower not in {"weight", "bias", "projection.weight", "projection.bias"}:
                raise ArtifactExportError(
                    "projection export may contain only projection weight and bias"
                )
        elif (
            re.search(r"(?:^|\.)lora_(?:a|b|embedding_a|embedding_b)(?:\.|$)", lower)
            is None
            or "base_layer" in lower
            or "embed_tokens" in lower
            or "lm_head" in lower
        ):
            raise ArtifactExportError(
                "adapter export would leak base model or non-LoRA weights"
            )
        result[name] = _tensor_array(tensor, name)
    return dict(sorted(result.items()))


def _validate_projection_layout(tensors: Mapping[str, np.ndarray]) -> None:
    weight_names = set(tensors) & {"weight", "projection.weight"}
    if len(weight_names) != 1:
        raise ArtifactExportError("projection requires exactly one weight tensor")
    weight_name = next(iter(weight_names))
    bias_name = "bias" if weight_name == "weight" else "projection.bias"
    if set(tensors) != {weight_name, bias_name}:
        raise ArtifactExportError(
            "projection requires exactly one weight and its matching bias"
        )
    weight = tensors[weight_name]
    if weight.dtype.kind != "f" or weight.shape != (100, 1024):
        raise ArtifactExportError("projection weight must have float shape (100, 1024)")
    bias = tensors[bias_name]
    if (
        bias.dtype.kind != "f"
        or bias.dtype != weight.dtype
        or bias.shape != (100,)
        or not bias.flags.c_contiguous
    ):
        raise ArtifactExportError(
            "projection bias must match the weight dtype and have contiguous float shape (100,)"
        )


def _validate_adapter_layout(
    tensors: Mapping[str, np.ndarray],
    adapter_config: Mapping[str, object],
    training_config: Mapping[str, object],
) -> None:
    if set(adapter_config) != {
        "r", "lora_alpha", "lora_dropout", "bias", "target_modules"
    }:
        raise ArtifactExportError("adapter_config must use the closed LoRA fields")
    rank = adapter_config.get("r")
    targets = adapter_config.get("target_modules")
    if (
        rank != 16
        or adapter_config.get("lora_alpha") != 32
        or adapter_config.get("lora_dropout") != 0.05
        or adapter_config.get("bias") != "none"
        or not isinstance(targets, (list, tuple))
        or any(not isinstance(target, str) for target in targets)
        or set(targets) != {"q_proj", "v_proj"}
        or len(targets) != 2
    ):
        raise ArtifactExportError("adapter_config does not match the frozen LoRA contract")
    expected: dict[str, tuple[int, int]] = {}
    for layer in range(_QWEN_NUM_HIDDEN_LAYERS):
        prefix = f"base_model.model.layers.{layer}.self_attn"
        for target, output_dimension in _QWEN_LORA_OUTPUTS.items():
            expected[f"{prefix}.{target}.lora_A.default.weight"] = (
                rank,
                _QWEN_HIDDEN_SIZE,
            )
            expected[f"{prefix}.{target}.lora_B.default.weight"] = (
                output_dimension,
                rank,
            )
    if set(tensors) != set(expected):
        raise ArtifactExportError(
            "adapter must contain the complete exact pinned Qwen q_proj/v_proj layout"
        )
    if any(
        tensor.dtype.kind != "f"
        or tensor.ndim != 2
        or tensor.shape != expected[name]
        or not tensor.flags.c_contiguous
        for name, tensor in tensors.items()
    ):
        raise ArtifactExportError(
            "adapter tensor dtype or shape does not match the pinned Qwen LoRA layout"
        )
    if any(
        training_config.get(name) != expected
        for name, expected in {
            "lora_rank": rank,
            "lora_alpha": adapter_config.get("lora_alpha"),
            "lora_dropout": adapter_config.get("lora_dropout"),
            "lora_bias": adapter_config.get("bias"),
            "lora_targets": list(targets),
        }.items()
    ):
        raise ArtifactExportError("adapter_config contradicts training_config")


def _validate_training_config(
    value: Mapping[str, object],
    *,
    dataset_commit_sha: str,
    dataset_manifest_sha256: str,
    dataset_readiness_sha256: str,
) -> None:
    expected_keys = {
        "config_version", "model_id", "model_revision", "tokenizer_revision",
        "dataset_repo_id", "dataset_config", "dataset_commit_sha",
        "dataset_manifest_sha256", "dataset_readiness_sha256",
        "teacher_dimension", "projection_input_dimension",
        "projection_output_dimension", "max_length", "lambda_rel", "lora_rank",
        "lora_alpha", "lora_dropout", "lora_targets", "lora_bias", "seed",
    }
    if set(value) != expected_keys:
        raise ArtifactExportError("training_config must use the closed version-1 fields")
    fixed = {
        "config_version": 1,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "tokenizer_revision": MODEL_REVISION,
        "dataset_repo_id": DATASET_REPO,
        "dataset_config": DATASET_CONFIG,
        "dataset_commit_sha": dataset_commit_sha,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "dataset_readiness_sha256": dataset_readiness_sha256,
        "teacher_dimension": 100,
        "projection_input_dimension": 1024,
        "projection_output_dimension": 100,
        "lora_rank": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "lora_targets": ["q_proj", "v_proj"],
        "lora_bias": "none",
    }
    if any(value[name] != expected for name, expected in fixed.items()):
        raise ArtifactExportError("training_config contradicts frozen model or dataset identity")
    if not isinstance(value["seed"], int) or isinstance(value["seed"], bool):
        raise ArtifactExportError("training_config seed must be an integer")
    try:
        DistillationConfig(
            model_id=str(value["model_id"]),
            model_revision=str(value["model_revision"]),
            teacher_dimension=int(value["teacher_dimension"]),
            max_length=value["max_length"],  # type: ignore[arg-type]
            lambda_rel=value["lambda_rel"],  # type: ignore[arg-type]
            lora_rank=value["lora_rank"],  # type: ignore[arg-type]
            lora_alpha=value["lora_alpha"],  # type: ignore[arg-type]
            lora_dropout=value["lora_dropout"],  # type: ignore[arg-type]
            lora_targets=tuple(value["lora_targets"]),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ArtifactExportError("training_config is not a valid DistillationConfig") from error


def _validate_validation_report(
    value: Mapping[str, object],
    *,
    dataset_commit_sha: str,
    dataset_manifest_sha256: str,
    dataset_readiness_sha256: str,
) -> None:
    if set(value) != {
        "report_version", "dataset", "model", "pool_size", "metrics",
        "invalid_vector_count", "norm_statistics", "examples",
    } or value["report_version"] != 1:
        raise ArtifactExportError("validation_report must use the closed version-1 fields")
    dataset = value["dataset"]
    if not isinstance(dataset, Mapping) or dict(dataset) != {
        "repo_id": DATASET_REPO,
        "config": DATASET_CONFIG,
        "commit_sha": dataset_commit_sha,
        "manifest_sha256": dataset_manifest_sha256,
        "readiness_sha256": dataset_readiness_sha256,
        "split": "validation",
        "subset": "pilot",
        "example_count": value["pool_size"],
    }:
        raise ArtifactExportError("validation_report dataset identity is contradictory")
    model = value["model"]
    if not isinstance(model, Mapping) or dict(model) != {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "tokenizer_revision": MODEL_REVISION,
    }:
        raise ArtifactExportError("validation_report model identity is contradictory")
    pool_size = value["pool_size"]
    invalid_count = value["invalid_vector_count"]
    if (
        not isinstance(pool_size, int)
        or isinstance(pool_size, bool)
        or pool_size <= 0
        or not isinstance(invalid_count, int)
        or isinstance(invalid_count, bool)
        or not 0 <= invalid_count <= pool_size
    ):
        raise ArtifactExportError("validation_report counts are invalid")
    metrics = value["metrics"]
    metric_names = {
        "mean_paired_cosine", "recall_at_10", "recall_at_50",
        "ndcg_at_10", "ndcg_at_50",
    }
    if not isinstance(metrics, Mapping) or set(metrics) != metric_names:
        raise ArtifactExportError("validation_report metrics are incomplete")
    if (
        not _finite(metrics["mean_paired_cosine"])
        or not -1 <= float(metrics["mean_paired_cosine"]) <= 1
        or any(
            not _finite(metrics[name]) or not 0 <= float(metrics[name]) <= 1
            for name in metric_names - {"mean_paired_cosine"}
        )
    ):
        raise ArtifactExportError("validation_report metrics must be finite and bounded")
    norms = value["norm_statistics"]
    norm_names = {
        "student_min", "student_mean", "student_max",
        "teacher_min", "teacher_mean", "teacher_max",
    }
    if not isinstance(norms, Mapping) or set(norms) != norm_names or any(
        not _finite(norms[name]) or float(norms[name]) <= 0 for name in norm_names
    ) or not (
        float(norms["student_min"]) <= float(norms["student_mean"]) <= float(norms["student_max"])
        and float(norms["teacher_min"]) <= float(norms["teacher_mean"]) <= float(norms["teacher_max"])
    ):
        raise ArtifactExportError("validation_report norm statistics are invalid")
    examples = value["examples"]
    if not isinstance(examples, list):
        raise ArtifactExportError("validation_report examples must be a list")
    seen: set[str] = set()
    for example in examples:
        if not isinstance(example, Mapping) or set(example) != {
            "article_key", "student_neighbors", "teacher_neighbors"
        }:
            raise ArtifactExportError("validation_report example fields are invalid")
        article_key = example["article_key"]
        if (
            not isinstance(article_key, str)
            or not article_key
            or article_key in seen
            or any(
                not isinstance(items, list)
                or any(not isinstance(item, str) or not item for item in items)
                for items in (example["student_neighbors"], example["teacher_neighbors"])
            )
        ):
            raise ArtifactExportError("validation_report example identity is invalid")
        seen.add(article_key)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-clobber rename is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    ) != 0:
        value = ctypes.get_errno()
        if value == errno.EEXIST:
            raise FileExistsError(f"artifact already exists: {destination}")
        raise OSError(value, os.strerror(value), destination)


@dataclass(frozen=True, slots=True)
class ArtifactManifest(Mapping[str, object]):
    path: Path
    document: dict[str, object]
    artifact_id: str
    manifest_sha256: str

    @property
    def model_revision(self) -> str:
        return str(self.document["model"]["revision"])  # type: ignore[index]

    @property
    def tokenizer_revision(self) -> str:
        return str(self.document["tokenizer"]["revision"])  # type: ignore[index]

    @property
    def dataset_commit_sha(self) -> str:
        return str(self.document["dataset"]["commit_sha"])  # type: ignore[index]

    @property
    def projection_sha256(self) -> str:
        return str(self.document["files"]["projection.safetensors"]["sha256"])  # type: ignore[index]

    @property
    def adapter_sha256(self) -> str:
        return str(self.document["files"]["adapter_model.safetensors"]["sha256"])  # type: ignore[index]

    def __getitem__(self, key: str) -> object:
        return self.document[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.document)

    def __len__(self) -> int:
        return len(self.document)


def export_distilled_artifact(
    output_root: str | os.PathLike[str],
    *,
    projection_tensors: Mapping[str, object],
    adapter_tensors: Mapping[str, object],
    adapter_config: Mapping[str, object],
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
    tokenizer_revision: str = MODEL_REVISION,
    dataset_commit_sha: str,
    dataset_manifest_sha256: str,
    dataset_readiness_sha256: str,
    training_config: Mapping[str, object],
    validation_report: Mapping[str, object],
) -> ArtifactManifest:
    """Create one byte-stable, atomic, no-clobber distilled artifact directory."""
    root = Path(output_root)
    _ensure_no_symlink_components(root)
    if root.exists() and not root.is_dir():
        raise ArtifactExportError("artifact output root must be a directory")
    if model_id != MODEL_ID or model_revision != MODEL_REVISION or tokenizer_revision != MODEL_REVISION:
        raise ArtifactExportError("artifact must pin the exact approved Qwen model and tokenizer revision")
    if not _is_sha(dataset_commit_sha, 40):
        raise ArtifactExportError("dataset commit must be an exact lowercase SHA")
    if not _is_sha(dataset_manifest_sha256, 64) or not _is_sha(dataset_readiness_sha256, 64):
        raise ArtifactExportError("dataset metadata identities must be lowercase SHA-256 values")
    projection = _checked_tensors(projection_tensors, kind="projection")
    adapter = _checked_tensors(adapter_tensors, kind="adapter")
    if {name.casefold() for name in projection} & {name.casefold() for name in adapter}:
        raise ArtifactExportError("projection and adapter tensor names must not duplicate")
    for label, value in {
        "adapter_config": adapter_config,
        "training_config": training_config,
        "validation_report": validation_report,
    }.items():
        if not isinstance(value, Mapping):
            raise ArtifactExportError(f"{label} must be a mapping")
        _canonical_json(dict(value))
    _validate_training_config(
        training_config,
        dataset_commit_sha=dataset_commit_sha,
        dataset_manifest_sha256=dataset_manifest_sha256,
        dataset_readiness_sha256=dataset_readiness_sha256,
    )
    _validate_validation_report(
        validation_report,
        dataset_commit_sha=dataset_commit_sha,
        dataset_manifest_sha256=dataset_manifest_sha256,
        dataset_readiness_sha256=dataset_readiness_sha256,
    )
    _validate_projection_layout(projection)
    _validate_adapter_layout(adapter, adapter_config, training_config)

    root_parent = root.parent
    root_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".babel-artifact-staging-", dir=root_parent))
    destination: Path | None = None
    try:
        save_file(projection, staging / "projection.safetensors")
        save_file(adapter, staging / "adapter_model.safetensors")
        (staging / "adapter_config.json").write_bytes(_canonical_json(dict(adapter_config)))
        (staging / "training_config.json").write_bytes(_canonical_json(dict(training_config)))
        (staging / "validation_report.json").write_bytes(_canonical_json(dict(validation_report)))
        for path in staging.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ArtifactExportError("artifact staging contains an unsafe entry")
            _fsync(path)
        files = {
            path.name: {"sha256": _sha256(path), "size": path.stat().st_size}
            for path in sorted(staging.iterdir(), key=lambda item: item.name)
        }
        if set(files) != _PAYLOAD_NAMES:
            raise ArtifactExportError("artifact payload file set is invalid")
        identity: dict[str, object] = {
            "artifact_version": 1,
            "model": {"id": model_id, "revision": model_revision},
            "tokenizer": {"id": model_id, "revision": tokenizer_revision, "padding_side": "left"},
            "dataset": {
                "repo_id": DATASET_REPO,
                "config": DATASET_CONFIG,
                "commit_sha": dataset_commit_sha,
                "manifest_sha256": dataset_manifest_sha256,
                "readiness_sha256": dataset_readiness_sha256,
            },
            "training_config": dict(training_config),
            "validation_report": dict(validation_report),
            "files": files,
        }
        artifact_id = hashlib.sha256(_canonical_json(identity)).hexdigest()
        document = {"artifact_id": artifact_id, **identity}
        manifest_bytes = _canonical_json(document)
        (staging / _MANIFEST_NAME).write_bytes(manifest_bytes)
        _fsync(staging / _MANIFEST_NAME)
        _fsync(staging)
        root.mkdir(parents=True, exist_ok=True)
        _fsync(root_parent)
        _fsync(root)
        destination = root / artifact_id
        _rename_noreplace(staging, destination)
        _fsync(root)
        _fsync(root_parent)
        return ArtifactManifest(
            destination,
            document,
            artifact_id,
            hashlib.sha256(manifest_bytes).hexdigest(),
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _artifact_document(
    artifact_path: Path,
    *,
    expected_identity: ArtifactManifest | None = None,
) -> tuple[dict[str, object], dict[str, Path]]:
    try:
        _ensure_no_symlink_components(artifact_path)
    except ArtifactExportError as error:
        raise ArtifactPublicationError(f"artifact path is unsafe: {error}") from None
    if not artifact_path.is_dir() or artifact_path.is_symlink():
        raise ArtifactPublicationError("artifact path must be a physical directory")
    entries = list(artifact_path.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise ArtifactPublicationError("artifact contains a symlink or non-file entry")
    expected = _PAYLOAD_NAMES | {_MANIFEST_NAME}
    if {path.name for path in entries} != expected:
        raise ArtifactPublicationError("artifact file set is incomplete or contains extras")
    try:
        raw = (artifact_path / _MANIFEST_NAME).read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactPublicationError("artifact manifest is malformed") from error
    try:
        canonical_manifest = _canonical_json(document)
    except ArtifactExportError as error:
        raise ArtifactPublicationError(f"artifact manifest is invalid: {error}") from None
    if not isinstance(document, dict) or canonical_manifest != raw:
        raise ArtifactPublicationError("artifact manifest is not canonical JSON")
    if expected_identity is not None and (
        artifact_path != expected_identity.path
        or hashlib.sha256(raw).hexdigest() != expected_identity.manifest_sha256
        or document.get("artifact_id") != expected_identity.artifact_id
    ):
        raise ArtifactPublicationError("artifact does not match immutable export identity")
    if set(document) != {
        "artifact_id", "artifact_version", "model", "tokenizer", "dataset",
        "training_config", "validation_report", "files",
    }:
        raise ArtifactPublicationError("artifact manifest fields are invalid")
    artifact_id = document["artifact_id"]
    if not _is_sha(artifact_id, 64) or artifact_path.name != artifact_id:
        raise ArtifactPublicationError("artifact directory identity is invalid")
    identity = dict(document); identity.pop("artifact_id")
    if hashlib.sha256(_canonical_json(identity)).hexdigest() != artifact_id:
        raise ArtifactPublicationError("artifact content identity is invalid")
    if (
        document["artifact_version"] != 1
        or document["model"] != {"id": MODEL_ID, "revision": MODEL_REVISION}
        or document["tokenizer"] != {
            "id": MODEL_ID, "revision": MODEL_REVISION, "padding_side": "left"
        }
    ):
        raise ArtifactPublicationError("artifact model identity is invalid")
    dataset = document["dataset"]
    if (
        not isinstance(dataset, Mapping)
        or set(dataset) != {
            "repo_id", "config", "commit_sha", "manifest_sha256", "readiness_sha256"
        }
        or dataset["repo_id"] != DATASET_REPO
        or dataset["config"] != DATASET_CONFIG
        or not _is_sha(dataset["commit_sha"], 40)
        or not _is_sha(dataset["manifest_sha256"], 64)
        or not _is_sha(dataset["readiness_sha256"], 64)
    ):
        raise ArtifactPublicationError("artifact dataset identity is invalid")
    files = document["files"]
    if not isinstance(files, Mapping) or set(files) != _PAYLOAD_NAMES:
        raise ArtifactPublicationError("artifact payload manifest is invalid")
    paths = {path.name: path for path in entries}
    for name, recorded in files.items():
        path = paths[name]
        if (
            not isinstance(recorded, Mapping)
            or set(recorded) != {"sha256", "size"}
            or recorded["sha256"] != _sha256(path)
            or recorded["size"] != path.stat().st_size
        ):
            raise ArtifactPublicationError(f"artifact payload checksum mismatch: {name}")
    try:
        metadata: dict[str, Mapping[str, object]] = {}
        for name in (
            "adapter_config.json", "training_config.json", "validation_report.json"
        ):
            raw_metadata = paths[name].read_bytes()
            parsed = json.loads(raw_metadata)
            if not isinstance(parsed, dict) or _canonical_json(parsed) != raw_metadata:
                raise ArtifactExportError(f"{name} must be canonical JSON")
            metadata[name] = parsed
        if metadata["training_config.json"] != document["training_config"]:
            raise ArtifactExportError("training_config payload contradicts manifest")
        if metadata["validation_report.json"] != document["validation_report"]:
            raise ArtifactExportError("validation_report payload contradicts manifest")
        training_config = metadata["training_config.json"]
        _validate_training_config(
            training_config,
            dataset_commit_sha=str(dataset["commit_sha"]),
            dataset_manifest_sha256=str(dataset["manifest_sha256"]),
            dataset_readiness_sha256=str(dataset["readiness_sha256"]),
        )
        _validate_validation_report(
            metadata["validation_report.json"],
            dataset_commit_sha=str(dataset["commit_sha"]),
            dataset_manifest_sha256=str(dataset["manifest_sha256"]),
            dataset_readiness_sha256=str(dataset["readiness_sha256"]),
        )
        projection = _checked_tensors(
            load_file(paths["projection.safetensors"]), kind="projection"
        )
        adapter = _checked_tensors(
            load_file(paths["adapter_model.safetensors"]), kind="adapter"
        )
        _validate_projection_layout(projection)
        _validate_adapter_layout(
            adapter, metadata["adapter_config.json"], training_config
        )
    except ArtifactExportError as error:
        raise ArtifactPublicationError(f"artifact semantic validation failed: {error}") from None
    except Exception as error:
        raise ArtifactPublicationError(
            f"artifact payload could not be validated ({type(error).__name__})"
        ) from None
    return document, paths


def _is_missing(error: BaseException) -> bool:
    name = type(error).__name__.casefold()
    response = getattr(error, "response", None)
    return isinstance(error, FileNotFoundError) or "notfound" in name or "missing" in name or getattr(response, "status_code", None) == 404


def _is_retryable(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    text = str(error).casefold()
    return status in {409, 412, 429} or (isinstance(status, int) and status >= 500) or "parent" in text and "conflict" in text


def _private_model_info(api: object, repo_id: str, revision: str, token: str) -> object:
    try:
        info = api.model_info(repo_id, revision=revision, token=token)
    except BaseException as error:
        raise ArtifactPublicationError(
            f"model repository identity could not be resolved ({type(error).__name__})"
        ) from None
    if getattr(info, "private", None) is not True:
        raise ArtifactPublicationError("model repository privacy could not be proved private")
    sha = getattr(info, "sha", None)
    if not _is_sha(sha, 40):
        raise ArtifactPublicationError("model repository returned an invalid commit SHA")
    if _is_sha(revision, 40) and sha != revision:
        raise ArtifactPublicationError("model repository commit identity mismatch")
    return info


def _remote_or_missing(
    api: object, repo_id: str, remote_path: str, revision: str, token: str
) -> bytes | None:
    try:
        getter = getattr(api, "get_file_bytes", None)
        if callable(getter):
            value = getter(
                repo_id=repo_id,
                path_in_repo=remote_path,
                repo_type="model",
                revision=revision,
                token=token,
            )
            if not isinstance(value, bytes):
                raise TypeError("remote adapter returned non-bytes")
            return value
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            repo_id=repo_id,
            filename=remote_path,
            repo_type="model",
            revision=revision,
            token=token,
        )
        return Path(path).read_bytes()
    except BaseException as error:
        if _is_missing(error):
            return None
        raise ArtifactPublicationError(
            f"remote artifact preflight failed ({type(error).__name__})"
        ) from None


def _add_operation(api: object, remote_path: str, local_path: Path) -> object:
    factory = getattr(api, "make_add_operation", None)
    if callable(factory):
        return factory(path_in_repo=remote_path, path_or_fileobj=str(local_path))
    if not type(api).__module__.startswith("huggingface_hub"):
        return SimpleNamespace(
            path_in_repo=remote_path,
            path_or_fileobj=str(local_path),
        )
    from huggingface_hub import CommitOperationAdd

    return CommitOperationAdd(path_in_repo=remote_path, path_or_fileobj=str(local_path))


def _returned_sha(result: object) -> str:
    for field in ("oid", "commit_id", "sha"):
        value = getattr(result, field, None)
        if _is_sha(value, 40):
            return value
    raise ArtifactPublicationError("model commit returned an invalid commit SHA")


def _remote_chunks(
    api: object, repo_id: str, remote_path: str, revision: str, token: str
) -> Iterator[bytes]:
    streamer = getattr(api, "iter_file_bytes", None)
    if callable(streamer):
        yield from streamer(
            repo_id=repo_id,
            path_in_repo=remote_path,
            repo_type="model",
            revision=revision,
            token=token,
        )
        return
    value = _remote_or_missing(api, repo_id, remote_path, revision, token)
    if value is None:
        raise ArtifactPublicationError(f"remote artifact file is missing: {remote_path}")
    yield value


def _verify_remote(
    api: object,
    repo_id: str,
    revision: str,
    token: str,
    uploads: Mapping[str, Path],
    expected: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    _private_model_info(api, repo_id, revision, token)
    verified: dict[str, dict[str, object]] = {}
    for remote_path, local in sorted(uploads.items()):
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in _remote_chunks(api, repo_id, remote_path, revision, token):
                if not isinstance(chunk, bytes):
                    raise TypeError("remote stream yielded non-bytes")
                digest.update(chunk); size += len(chunk)
        except ArtifactPublicationError:
            raise
        except BaseException as error:
            raise ArtifactPublicationError(
                f"remote artifact file could not be verified ({type(error).__name__})"
            ) from None
        expected_size = int(expected[remote_path]["size"])
        expected_sha = str(expected[remote_path]["sha256"])
        if size != expected_size or digest.hexdigest() != expected_sha:
            raise ArtifactPublicationError(f"remote artifact checksum mismatch: {remote_path}")
        verified[remote_path] = {"sha256": expected_sha, "size": expected_size}
    return verified


def _snapshot_artifact(
    exported: ArtifactManifest,
) -> tuple[Path, dict[str, object], dict[str, Path]]:
    source = exported.path
    original_document, original_paths = _artifact_document(
        source, expected_identity=exported
    )
    snapshot_root = Path(
        tempfile.mkdtemp(prefix=".babel-publication-snapshot-", dir=source.parent)
    )
    os.chmod(snapshot_root, 0o700)
    snapshot = snapshot_root / source.name
    snapshot.mkdir(mode=0o700)
    try:
        for name in sorted(original_paths):
            source_descriptor = os.open(
                original_paths[name], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                source_info = os.fstat(source_descriptor)
                if not stat.S_ISREG(source_info.st_mode):
                    raise ArtifactPublicationError("artifact snapshot source is not a physical file")
                destination = snapshot / name
                destination_descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    while True:
                        block = os.read(source_descriptor, 1024 * 1024)
                        if not block:
                            break
                        view = memoryview(block)
                        while view:
                            written = os.write(destination_descriptor, view)
                            view = view[written:]
                    os.fsync(destination_descriptor)
                finally:
                    os.close(destination_descriptor)
            finally:
                os.close(source_descriptor)
        _fsync(snapshot)
        snapshot_document, snapshot_paths = _artifact_document(snapshot)
        if snapshot_document != original_document:
            raise ArtifactPublicationError("artifact changed while creating immutable snapshot")
        return snapshot_root, snapshot_document, snapshot_paths
    except BaseException:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise


def _expected_identities(
    document: Mapping[str, object], uploads: Mapping[str, Path]
) -> dict[str, dict[str, object]]:
    artifact_id = str(document["artifact_id"])
    prefix = f"artifacts/{artifact_id}/"
    payload = document["files"]
    assert isinstance(payload, Mapping)
    expected: dict[str, dict[str, object]] = {}
    for remote_path in uploads:
        name = remote_path.removeprefix(prefix)
        if name == _MANIFEST_NAME:
            manifest_bytes = _canonical_json(dict(document))
            expected[remote_path] = {
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "size": len(manifest_bytes),
            }
        else:
            recorded = payload[name]
            assert isinstance(recorded, Mapping)
            expected[remote_path] = {
                "sha256": str(recorded["sha256"]),
                "size": int(recorded["size"]),
            }
    return expected


def _bytes_match_identity(value: bytes, identity: Mapping[str, object]) -> bool:
    return (
        len(value) == int(identity["size"])
        and hashlib.sha256(value).hexdigest() == identity["sha256"]
    )


@dataclass(frozen=True, slots=True)
class PublicationEvidence:
    repo_id: str
    artifact_id: str
    commit_sha: str
    verified_files: dict[str, dict[str, object]]
    path: Path


def _persist_evidence(path: Path, document: Mapping[str, object]) -> None:
    raw = _canonical_json(dict(document))
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ArtifactPublicationError("local immutable publication evidence conflicts")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    linked = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary_name, path)
        linked = True
        _fsync(path.parent)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw:
            raise ArtifactPublicationError("local immutable publication evidence conflicts")
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        else:
            _fsync(path.parent)
    if not linked and not path.exists():
        raise ArtifactPublicationError("local publication evidence was not persisted")


def _publish_snapshot(
    api: object,
    document: Mapping[str, object],
    local_paths: Mapping[str, Path],
    evidence_parent: Path,
    token: str,
    *,
    repo_id: str,
    retries: int,
    backoff_seconds: float,
    sleep: Any,
) -> PublicationEvidence:
    artifact_id = str(document["artifact_id"])
    uploads = {
        f"artifacts/{artifact_id}/{name}": local
        for name, local in local_paths.items()
    }
    expected = _expected_identities(document, uploads)
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="model",
            private=True,
            exist_ok=True,
            token=token,
        )
    except BaseException as error:
        raise ArtifactPublicationError(
            f"private model repository could not be created ({type(error).__name__})"
        ) from None

    commit_sha: str | None = None
    for attempt in range(retries):
        info = _private_model_info(api, repo_id, "main", token)
        parent_sha = str(getattr(info, "sha"))
        existing = {
            remote_path: _remote_or_missing(api, repo_id, remote_path, parent_sha, token)
            for remote_path in sorted(uploads)
        }
        present = {name for name, value in existing.items() if value is not None}
        if present:
            if present != set(uploads):
                raise ArtifactPublicationError("remote artifact conflict: partial immutable path exists")
            if any(
                not _bytes_match_identity(existing[name], expected[name])  # type: ignore[arg-type]
                for name in uploads
            ):
                raise ArtifactPublicationError("remote artifact conflict: immutable bytes differ")
            commit_sha = parent_sha
            break
        try:
            result = api.create_commit(
                repo_id=repo_id,
                repo_type="model",
                revision="main",
                parent_commit=parent_sha,
                operations=[
                    _add_operation(api, remote_path, local)
                    for remote_path, local in sorted(uploads.items())
                ],
                commit_message=f"Publish distilled artifact {artifact_id}",
                token=token,
            )
            commit_sha = _returned_sha(result)
            break
        except ArtifactPublicationError:
            raise
        except BaseException as error:
            if _is_retryable(error) and attempt + 1 < retries:
                sleep(backoff_seconds * (2**attempt))
                continue
            raise ArtifactPublicationError(
                f"atomic model commit failed after {attempt + 1} attempt(s) ({type(error).__name__})"
            ) from None
    if commit_sha is None:
        raise ArtifactPublicationError("atomic model publication retries were exhausted")
    verified = _verify_remote(api, repo_id, commit_sha, token, uploads, expected)
    evidence_path = evidence_parent / f"{artifact_id}.publication-verification.json"
    evidence_document = {
        "evidence_version": 1,
        "repo_id": repo_id,
        "artifact_id": artifact_id,
        "commit_sha": commit_sha,
        "verified_files": verified,
    }
    _persist_evidence(evidence_path, evidence_document)
    return PublicationEvidence(repo_id, artifact_id, commit_sha, verified, evidence_path)


def publish_model_artifact(
    api: object,
    artifact: ArtifactManifest,
    token: str,
    *,
    repo_id: str = DEFAULT_MODEL_REPO,
    retries: int = 4,
    backoff_seconds: float = 0.5,
    sleep: Any = time.sleep,
) -> PublicationEvidence:
    """Publish one immutable artifact using parent-CAS and verify every remote byte."""
    if repo_id != DEFAULT_MODEL_REPO:
        raise ArtifactPublicationError(f"model repository is fixed to {DEFAULT_MODEL_REPO}")
    if not isinstance(token, str) or not token:
        raise ValueError("a private-Hub token is required")
    if not isinstance(retries, int) or isinstance(retries, bool) or retries <= 0:
        raise ValueError("retries must be a positive integer")
    if not isinstance(artifact, ArtifactManifest):
        raise ArtifactPublicationError(
            "publication requires the immutable export identity returned by export"
        )
    path = artifact.path
    snapshot_root, document, local_paths = _snapshot_artifact(artifact)
    try:
        return _publish_snapshot(
            api,
            document,
            local_paths,
            path.parent,
            token,
            repo_id=repo_id,
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
    finally:
        shutil.rmtree(snapshot_root, ignore_errors=True)


__all__ = [
    "ArtifactExportError",
    "ArtifactManifest",
    "ArtifactPublicationError",
    "DEFAULT_MODEL_REPO",
    "PublicationEvidence",
    "export_distilled_artifact",
    "publish_model_artifact",
]
