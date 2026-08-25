"""Complete atomic online checkpoints binding working state to next offsets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pickle
import random
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from babel_online.feedback.bus import TopicPartition


_CHECKPOINT = re.compile(r"checkpoint-step-([0-9]{8})$")


@dataclass(frozen=True, slots=True)
class CheckpointState:
    path: Path
    step: int
    version: int
    next_offsets: dict[TopicPartition, int]
    metrics: dict[str, float | int]
    model_state: dict[str, Any]
    rng_state: bytes
    manifest_sha256: str


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _rng_bytes() -> bytes:
    return pickle.dumps(
        {"python": random.getstate(), "numpy": np.random.get_state()}, protocol=5
    )


def restore_rng(rng_state: bytes) -> None:
    state = pickle.loads(rng_state)
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])


def _offset_rows(offsets: Mapping[TopicPartition, int]) -> list[dict[str, object]]:
    return [
        {
            "topic": partition.topic,
            "partition": partition.partition,
            "nextOffset": offset,
        }
        for partition, offset in sorted(offsets.items())
    ]


def _decode_offsets(rows: list[dict[str, object]]) -> dict[TopicPartition, int]:
    return {
        TopicPartition(str(row["topic"]), int(row["partition"])): int(
            row["nextOffset"]
        )
        for row in rows
    }


def save_online_checkpoint(
    root: str | Path,
    *,
    step: int,
    version: int,
    next_offsets: Mapping[TopicPartition, int],
    metrics: Mapping[str, float | int],
    model_state: Mapping[str, Any],
    rng_state: bytes | None = None,
) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    name = f"checkpoint-step-{step:08d}"
    partial = root_path / f"{name}.partial"
    final = root_path / name
    if final.exists():
        raise FileExistsError(f"checkpoint destination already exists: {name}")
    if partial.is_dir():
        shutil.rmtree(partial)
    elif partial.exists():
        partial.unlink()
    partial.mkdir()
    state_path = partial / "state.json"
    state_path.write_bytes(
        _canonical_json(
            {
                "checkpointVersion": 1,
                "step": step,
                "version": version,
                "nextOffsets": _offset_rows(next_offsets),
                "metrics": dict(metrics),
                "modelState": dict(model_state),
                "optimizerState": {
                    "kind": "sgd",
                    "learningRate": model_state.get("learningRate"),
                },
                "rngStateBase64": base64.b64encode(rng_state or _rng_bytes()).decode(
                    "ascii"
                ),
            }
        )
    )
    state_sha = hashlib.sha256(state_path.read_bytes()).hexdigest()
    manifest_path = partial / "manifest.json"
    manifest_path.write_bytes(
        _canonical_json(
            {
                "checkpointVersion": 1,
                "statePath": "state.json",
                "stateSha256": state_sha,
            }
        )
    )
    for path in (state_path, manifest_path):
        with path.open("rb") as source:
            os.fsync(source.fileno())
    partial_fd = os.open(partial, os.O_RDONLY)
    try:
        os.fsync(partial_fd)
    finally:
        os.close(partial_fd)
    os.replace(partial, final)
    root_fd = os.open(root_path, os.O_RDONLY)
    try:
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
    return final


def _load_checkpoint(path: Path) -> CheckpointState:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    state_bytes = (path / "state.json").read_bytes()
    state_sha = hashlib.sha256(state_bytes).hexdigest()
    if manifest != {
        "checkpointVersion": 1,
        "statePath": "state.json",
        "stateSha256": state_sha,
    }:
        raise ValueError(f"checkpoint checksum mismatch: {path}")
    state = json.loads(state_bytes)
    return CheckpointState(
        path=path,
        step=int(state["step"]),
        version=int(state["version"]),
        next_offsets=_decode_offsets(state["nextOffsets"]),
        metrics=dict(state["metrics"]),
        model_state=dict(state["modelState"]),
        rng_state=base64.b64decode(state["rngStateBase64"], validate=True),
        manifest_sha256=state_sha,
    )


def load_latest_checkpoint(root: str | Path) -> CheckpointState | None:
    root_path = Path(root)
    if not root_path.exists():
        return None
    candidates = sorted(
        (
            (int(match.group(1)), path)
            for path in root_path.iterdir()
            if path.is_dir() and (match := _CHECKPOINT.fullmatch(path.name))
        ),
        reverse=True,
    )
    return _load_checkpoint(candidates[0][1]) if candidates else None


__all__ = [
    "CheckpointState",
    "load_latest_checkpoint",
    "restore_rng",
    "save_online_checkpoint",
]
