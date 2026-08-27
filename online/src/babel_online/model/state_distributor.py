"""Immutable real-Qwen online-state publication and activation.

The trained Qwen adapter/projection remains the immutable encoder artifact.
An online child therefore binds that same V2 identity to an additional,
checksummed ranking state instead of pretending that NumPy state is a new
Qwen checkpoint.  Kafka carries feedback only; this directory is the sole
weight/state handoff between trainer and serving processes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import ModelManifestV2
from .registry import DuplicateModel, ModelRegistry, UnknownModel


class ActivationError(ValueError):
    pass


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class KnownVectorProbeV1(_FrozenModel):
    schemaVersion: Literal[1]
    inputVector: list[float] = Field(min_length=100, max_length=100)
    expectedSemanticSha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("inputVector")
    @classmethod
    def probe_is_finite_and_nonzero(cls, value: list[float]) -> list[float]:
        import math

        if not all(math.isfinite(item) for item in value) or not any(value):
            raise ValueError("known-vector probe must be finite and nonzero")
        return value


class RealQwenChildStateV1(_FrozenModel):
    schemaVersion: Literal[1]
    childManifest: ModelManifestV2
    onlineStatePath: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    files: dict[str, str]
    processedFeedbackEvents: int = Field(ge=0)
    modelVersion: int = Field(ge=0)
    vectorSnapshotSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    knownVectorProbe: KnownVectorProbeV1
    createdAtNs: int = Field(ge=0)
    immutable: Literal[True]

    @model_validator(mode="after")
    def lineage_and_files_are_closed(self) -> "RealQwenChildStateV1":
        manifest = self.childManifest
        if manifest.parentModelId is None or manifest.producingRunId is None:
            raise ValueError("real Qwen child must declare complete parent/run lineage")
        if self.onlineStatePath not in self.files:
            raise ValueError("online state must be included in checksummed files")
        if not self.files:
            raise ValueError("child state file list cannot be empty")
        for name, checksum in self.files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or str(path) != name:
                raise ValueError("child state paths must be canonical and relative")
            if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise ValueError("child state checksums must be lowercase SHA-256")
        return self


@dataclass(frozen=True, slots=True)
class ExportedRealQwenChild:
    root: Path
    descriptor_path: Path
    descriptor: RealQwenChildStateV1
    descriptor_sha256: str


@dataclass(frozen=True, slots=True)
class ActivationReceipt:
    modelId: UUID
    parentModelId: UUID
    modelVersion: int
    status: Literal["activated"]
    descriptorSha256: str
    requestedAtNs: int
    activatedAtNs: int
    stalenessNs: int


def semantic_vector_sha256(vector: list[float] | tuple[float, ...]) -> str:
    """Hash one normalized 100d semantic probe as canonical float32 bytes."""
    if len(vector) != 100 or any(not math.isfinite(float(value)) for value in vector):
        raise ValueError("semantic probe must contain 100 finite values")
    norm = math.sqrt(math.fsum(float(value) ** 2 for value in vector))
    if norm == 0.0:
        raise ValueError("semantic probe must be nonzero")
    return hashlib.sha256(
        struct.pack("<100f", *(float(value) / norm for value in vector))
    ).hexdigest()


def _canonical_json(value: object) -> bytes:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _compatible_child(parent: ModelManifestV2, child: ModelManifestV2) -> None:
    if child.parentModelId != parent.modelId:
        raise ActivationError("child parent does not match registered parent")
    if child.embeddingSpace != parent.embeddingSpace:
        raise ActivationError("child embedding space is incompatible")
    identity_fields = (
        "encoderRepo",
        "encoderRevision",
        "artifactPath",
        "artifactId",
        "artifactManifestSha256",
        "checkpointTreeSha256",
        "baseModelId",
        "baseModelRevision",
        "tokenizerRevision",
        "datasetRepo",
        "datasetConfig",
        "datasetRevision",
        "datasetManifestSha256",
        "trainingSourceRevision",
        "adapterSha256",
        "projectionSha256",
        "validationSha256",
        "acceptance",
    )
    if any(getattr(child, field) != getattr(parent, field) for field in identity_fields):
        raise ActivationError("child changes immutable Qwen artifact identity")


def export_real_qwen_child(
    root: str | Path,
    *,
    parent: ModelManifestV2,
    run_id: UUID,
    child_model_id: UUID,
    label: str,
    online_state: bytes,
    processed_feedback_events: int,
    model_version: int,
    vector_snapshot_sha256: str,
    probe: KnownVectorProbeV1,
    registry: ModelRegistry,
    created_at_ns: int | None = None,
) -> ExportedRealQwenChild:
    """Publish one complete child directory by atomic rename."""
    if not isinstance(parent, ModelManifestV2):
        raise TypeError("real Qwen child requires a ModelManifestV2 parent")
    if child_model_id == parent.modelId:
        raise ValueError("child model ID must differ from its parent")
    destination_root = Path(root)
    destination_root.mkdir(parents=True, exist_ok=True)
    name = f"model-{child_model_id}"
    final = destination_root / name
    partial = destination_root / f"{name}.partial"
    if final.exists():
        raise FileExistsError(f"child model already exists: {child_model_id}")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir()
    state_path = partial / "online-state.json"
    state_path.write_bytes(online_state)
    child_document = parent.model_dump(mode="json")
    child_document.update(
        {
            "modelId": child_model_id,
            "label": label,
            "parentModelId": parent.modelId,
            "producingRunId": run_id,
        }
    )
    child = ModelManifestV2.model_validate(child_document)
    descriptor = RealQwenChildStateV1(
        schemaVersion=1,
        childManifest=child,
        onlineStatePath="online-state.json",
        files={"online-state.json": _sha256(state_path)},
        processedFeedbackEvents=processed_feedback_events,
        modelVersion=model_version,
        vectorSnapshotSha256=vector_snapshot_sha256,
        knownVectorProbe=probe,
        createdAtNs=time.time_ns() if created_at_ns is None else created_at_ns,
        immutable=True,
    )
    descriptor_path = partial / "state-descriptor.json"
    descriptor_path.write_bytes(_canonical_json(descriptor.model_dump(mode="json")))
    for path in partial.iterdir():
        with path.open("rb") as source:
            os.fsync(source.fileno())
    directory_fd = os.open(partial, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    os.replace(partial, final)
    for path in final.iterdir():
        path.chmod(0o444)
    final.chmod(0o555)
    try:
        registry.register_child(child)
    except Exception:
        # The artifact is complete and immutable, but must not be silently
        # advertised if registry lineage rejects it.
        raise
    final_descriptor = final / descriptor_path.name
    return ExportedRealQwenChild(
        root=final,
        descriptor_path=final_descriptor,
        descriptor=descriptor,
        descriptor_sha256=_sha256(final_descriptor),
    )


class ModelStateDistributor:
    """Validate, prepare/probe, then atomically activate one immutable child."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        current_state: Callable[[], Any],
        activate_state: Callable[[Any], None],
        register_persistent: Callable[[RealQwenChildStateV1, Path], None] | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.registry = registry
        self.current_state = current_state
        self.activate_state = activate_state
        self.register_persistent = register_persistent or (lambda _descriptor, _path: None)
        self.clock_ns = clock_ns

    def _load(self, root: str | Path) -> tuple[RealQwenChildStateV1, Path, str]:
        artifact_root = Path(root).resolve()
        descriptor_path = artifact_root / "state-descriptor.json"
        try:
            descriptor = RealQwenChildStateV1.model_validate_json(
                descriptor_path.read_text(encoding="utf-8")
            )
        except Exception as error:
            raise ActivationError("child state descriptor is invalid") from error
        try:
            parent = self.registry.get(descriptor.childManifest.parentModelId)
        except UnknownModel as error:
            raise ActivationError("child parent is not registered") from error
        if not isinstance(parent, ModelManifestV2):
            raise ActivationError("real Qwen child cannot descend from V1 fixture")
        _compatible_child(parent, descriptor.childManifest)
        for relative, expected in descriptor.files.items():
            path = (artifact_root / relative).resolve()
            try:
                path.relative_to(artifact_root)
            except ValueError as error:
                raise ActivationError("child state path escapes artifact root") from error
            if not path.is_file() or _sha256(path) != expected:
                raise ActivationError(f"child state checksum failed: {relative}")
        return descriptor, artifact_root, _sha256(descriptor_path)

    def activate(
        self,
        root: str | Path,
        *,
        prepare: Callable[[RealQwenChildStateV1, Path], Any],
        probe: Callable[[Any, KnownVectorProbeV1], bool],
        published_at_ns: int | None = None,
    ) -> ActivationReceipt:
        requested = self.clock_ns()
        descriptor, artifact_root, descriptor_sha = self._load(root)
        child = descriptor.childManifest
        try:
            registered = self.registry.get(child.modelId)
            if registered != child:
                raise ActivationError("child model ID is registered with other metadata")
        except UnknownModel:
            try:
                self.registry.register_child(child)
            except DuplicateModel as error:  # pragma: no cover - race guard
                raise ActivationError("child model registration raced") from error
        try:
            self.register_persistent(descriptor, artifact_root)
        except Exception as error:
            raise ActivationError("persistent child registration failed") from error
        try:
            prepared = prepare(descriptor, artifact_root)
        except Exception as error:
            raise ActivationError("child preparation failed") from error
        try:
            probe_ok = probe(prepared, descriptor.knownVectorProbe)
        except Exception as error:
            raise ActivationError("known-vector probe failed") from error
        if not probe_ok:
            raise ActivationError("known-vector probe rejected child state")
        previous = self.current_state()
        try:
            self.activate_state(prepared)
        except Exception as error:
            try:
                self.activate_state(previous)
            except Exception as rollback_error:  # pragma: no cover - catastrophic evidence
                raise ActivationError(
                    "activation failed and rollback also failed"
                ) from rollback_error
            raise ActivationError("activation failed; previous state rolled back") from error
        activated = self.clock_ns()
        published = requested if published_at_ns is None else published_at_ns
        return ActivationReceipt(
            modelId=child.modelId,
            parentModelId=child.parentModelId,
            modelVersion=descriptor.modelVersion,
            status="activated",
            descriptorSha256=descriptor_sha,
            requestedAtNs=requested,
            activatedAtNs=activated,
            stalenessNs=max(0, activated - published),
        )


__all__ = [
    "ActivationError",
    "ActivationReceipt",
    "ExportedRealQwenChild",
    "KnownVectorProbeV1",
    "ModelStateDistributor",
    "RealQwenChildStateV1",
    "export_real_qwen_child",
    "semantic_vector_sha256",
]
