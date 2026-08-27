"""Verified private-Hub artifact and explicit training-to-serving binding."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..contracts import DistilledServingArtifactV1


REAL_MODEL_REPO = "dhelmy990/babel-qwen-navigation-2016-interview"
REAL_ARTIFACT_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
REAL_ARTIFACT_ID = "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"
REAL_TRAINING_SOURCE_REVISION = "92f3ac697d78eb827d75b033df92dcbed887def7"
BASE_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
BASE_MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DATASET_REPO = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIG = "distillation_2016_interview"

_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_SHA64 = re.compile(r"^[a-f0-9]{64}$")
_PAYLOAD_NAMES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "final_checkpoint_identity.json",
        "projection.safetensors",
        "training_config.json",
        "validation_report.json",
    }
)
_ALL_NAMES = _PAYLOAD_NAMES | {"artifact_manifest.json"}


class ArtifactIntegrityError(ValueError):
    """The downloaded artifact is incomplete or contradicts its manifest."""


class ArtifactAcceptanceError(RuntimeError):
    """An artifact has not proved the exact private real-model acceptance gate."""


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _DatasetIdentity(_ClosedModel):
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    config: Literal["distillation_2016_interview"]
    counts: dict[str, int]
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ordered_identity_sha256: dict[str, str]
    parquet_sha256: dict[str, str]
    readiness_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    repo_id: Literal["dhelmy990/babel-wikipedia-experiment"]
    test_usage: Literal["identity metadata only; examples unopened"]

    @model_validator(mode="after")
    def fixed_selection(self) -> "_DatasetIdentity":
        if self.counts != {"train": 50_000, "validation": 5_000, "test": 5_000, "total": 60_000}:
            raise ValueError("dataset counts do not describe the fixed interview selection")
        if set(self.ordered_identity_sha256) != {"train", "validation", "test"}:
            raise ValueError("ordered selection checksums are incomplete")
        if set(self.parquet_sha256) != {"train", "validation", "test"}:
            raise ValueError("Parquet checksums are incomplete")
        if any(_SHA64.fullmatch(value) is None for value in self.ordered_identity_sha256.values()):
            raise ValueError("ordered selection checksum is malformed")
        if any(_SHA64.fullmatch(value) is None for value in self.parquet_sha256.values()):
            raise ValueError("Parquet checksum is malformed")
        return self


class _CheckpointIdentity(_ClosedModel):
    epoch: Literal[1]
    global_step: Literal[3125]
    next_ordered_row: Literal[50000]
    schema_version: Literal[1]
    tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class _LoraIdentity(_ClosedModel):
    bias: Literal["none"]
    lora_alpha: Literal[32]
    lora_dropout: Literal[0.05]
    r: Literal[16]
    target_modules: list[Literal["q_proj", "v_proj"]]

    @field_validator("target_modules")
    @classmethod
    def exact_targets(cls, value: list[str]) -> list[str]:
        if value != ["q_proj", "v_proj"]:
            raise ValueError("LoRA targets must be q_proj then v_proj")
        return value


class _ModelIdentity(_ClosedModel):
    id: Literal["Qwen/Qwen3-Embedding-0.6B"]
    revision: Literal[BASE_MODEL_REVISION]
    tokenizer_revision: Literal[BASE_MODEL_REVISION]


class _ProjectionIdentity(_ClosedModel):
    input_dimension: Literal[1024]
    output_dimension: Literal[100]


class _Protocol(_ClosedModel):
    epochs: Literal[1]
    max_length: Literal[384]
    smoke_rows: Literal[1000]
    train_rows: Literal[50000]
    validation_rows: Literal[5000]


class _PublicationIdentity(_ClosedModel):
    artifact_payload_commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    private: Literal[True]
    repo_id: Literal[REAL_MODEL_REPO]


class _SourceIdentity(_ClosedModel):
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")


class ArtifactManifestV1(_ClosedModel):
    artifact_hashes: dict[str, str]
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_schema: Literal["babel-distillation-2016-interview-v1"]
    dataset: _DatasetIdentity
    final_checkpoint: _CheckpointIdentity
    immutable: Literal[True]
    lora: _LoraIdentity
    model: _ModelIdentity
    projection: _ProjectionIdentity
    protocol: _Protocol
    publication: _PublicationIdentity
    source: _SourceIdentity
    training_config: dict[str, object]
    validation: dict[str, object]

    @field_validator("artifact_hashes")
    @classmethod
    def exact_payload_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != _PAYLOAD_NAMES or any(_SHA64.fullmatch(item) is None for item in value.values()):
            raise ValueError("artifact hashes must cover the exact payload file set")
        return value

    @model_validator(mode="after")
    def identities_agree(self) -> "ArtifactManifestV1":
        training = self.training_config
        expected = {
            "dataset_repo_id": self.dataset.repo_id,
            "dataset_config": self.dataset.config,
            "dataset_commit_sha": self.dataset.commit_sha,
            "model_id": self.model.id,
            "model_revision": self.model.revision,
            "tokenizer_revision": self.model.tokenizer_revision,
            "max_length": self.protocol.max_length,
            "train_rows": self.protocol.train_rows,
            "validation_rows": self.protocol.validation_rows,
            "projection_input_dimension": self.projection.input_dimension,
            "projection_output_dimension": self.projection.output_dimension,
            "lora_rank": self.lora.r,
            "lora_alpha": self.lora.lora_alpha,
            "lora_dropout": self.lora.lora_dropout,
            "lora_bias": self.lora.bias,
            "lora_targets": self.lora.target_modules,
        }
        if any(training.get(name) != expected_value for name, expected_value in expected.items()):
            raise ValueError("training configuration contradicts artifact identity")
        validation_dataset = self.validation.get("dataset")
        validation_model = self.validation.get("model")
        if not isinstance(validation_dataset, Mapping) or (
            validation_dataset.get("commit_sha") != self.dataset.commit_sha
            or validation_dataset.get("example_count") != 5_000
        ):
            raise ValueError("validation dataset identity contradicts artifact identity")
        if not isinstance(validation_model, Mapping) or (
            validation_model.get("id") != self.model.id
            or validation_model.get("revision") != self.model.revision
            or validation_model.get("tokenizer_revision") != self.model.tokenizer_revision
        ):
            raise ValueError("validation model identity contradicts artifact identity")
        for name in (
            "invalid_student_vector_count",
            "invalid_teacher_vector_count",
            "invalid_vector_count",
        ):
            if self.validation.get(name) != 0:
                raise ValueError("validation reports invalid vectors")
        if self.validation.get("pool_size") != 5_000:
            raise ValueError("validation must use the fixed 5,000-row pool")
        return self


Download = Callable[[str, str, str, str, Path | None], str]


def _default_download(
    repo_id: str, filename: str, revision: str, token: str, cache_dir: Path | None
) -> str:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        repo_type="model",
        revision=revision,
        token=token,
        cache_dir=str(cache_dir) if cache_dir is not None else None,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"{label} must be a JSON object")
    return value


class DistilledArtifactV1:
    """One locally cached, hash-verified immutable artifact revision."""

    def __init__(
        self,
        *,
        repo_id: str,
        revision: str,
        artifact_id: str,
        manifest: ArtifactManifestV1,
        files: Mapping[str, Path],
        private_remote_verified: bool,
    ) -> None:
        self.repo_id = repo_id
        self.revision = revision
        self.artifact_id = artifact_id
        self.manifest = manifest
        self._files = dict(files)
        self.private_remote_verified = private_remote_verified

    @property
    def serving_contract(self) -> DistilledServingArtifactV1:
        """Bind omitted serving semantics to the pinned training source.

        The three semantic fields are intentionally not read from the upstream
        manifest: source commit ``manifest.source.commit_sha`` is their
        authority.  The runbook records the exact source lines used.
        """
        return DistilledServingArtifactV1(
            schemaVersion=1,
            artifactRepo=self.repo_id,
            artifactRevision=self.revision,
            artifactPath=f"artifacts/{self.artifact_id}",
            artifactId=self.artifact_id,
            artifactSchema=self.manifest.artifact_schema,
            baseModelId=self.manifest.model.id,
            baseModelRevision=self.manifest.model.revision,
            tokenizerRevision=self.manifest.model.tokenizer_revision,
            datasetRepo=self.manifest.dataset.repo_id,
            datasetConfig=self.manifest.dataset.config,
            datasetRevision=self.manifest.dataset.commit_sha,
            trainingSourceRevision=self.manifest.source.commit_sha,
            semanticsAuthority="pinned_training_source",
            inputFormat="canonical_title\\n\\nlead_text",
            maxLength=self.manifest.protocol.max_length,
            paddingSide="left",
            pooling="last_non_padding_token",
            projectionInputDimension=self.manifest.projection.input_dimension,
            embeddingDimension=self.manifest.projection.output_dimension,
            normalization="l2",
            adapterSha256=self.manifest.artifact_hashes["adapter_model.safetensors"],
            projectionSha256=self.manifest.artifact_hashes["projection.safetensors"],
            validationSha256=self.manifest.artifact_hashes["validation_report.json"],
            immutable=True,
        )

    def path_for(self, name: str) -> Path:
        try:
            return self._files[name]
        except KeyError as error:
            raise ValueError(f"unknown artifact payload: {name}") from error

    @classmethod
    def _from_paths(
        cls,
        *,
        repo_id: str,
        revision: str,
        artifact_id: str,
        files: Mapping[str, Path],
        private_remote_verified: bool,
    ) -> Self:
        try:
            manifest = ArtifactManifestV1.model_validate(
                _json(files["artifact_manifest.json"], "artifact manifest")
            )
        except (KeyError, ValidationError, ValueError) as error:
            if isinstance(error, ArtifactIntegrityError):
                raise
            raise ArtifactIntegrityError(f"artifact manifest is invalid: {error}") from None
        if manifest.artifact_id != artifact_id:
            raise ArtifactIntegrityError("artifact manifest identity does not match its directory")
        for name, expected in manifest.artifact_hashes.items():
            if _sha256(files[name]) != expected:
                raise ArtifactIntegrityError(f"artifact payload checksum mismatch: {name}")
        if _json(files["adapter_config.json"], "adapter config") != manifest.lora.model_dump():
            raise ArtifactIntegrityError("adapter config contradicts artifact manifest")
        if _json(files["training_config.json"], "training config") != manifest.training_config:
            raise ArtifactIntegrityError("training config contradicts artifact manifest")
        if _json(files["validation_report.json"], "validation report") != manifest.validation:
            raise ArtifactIntegrityError("validation report contradicts artifact manifest")
        if _json(files["final_checkpoint_identity.json"], "checkpoint identity") != manifest.final_checkpoint.model_dump():
            raise ArtifactIntegrityError("checkpoint identity contradicts artifact manifest")
        return cls(
            repo_id=repo_id,
            revision=revision,
            artifact_id=artifact_id,
            manifest=manifest,
            files=files,
            private_remote_verified=private_remote_verified,
        )

    @classmethod
    def load(
        cls,
        *,
        repo_id: str,
        revision: str,
        artifact_id: str,
        token: str,
        api: object | None = None,
        downloader: Download | None = None,
        cache_dir: str | Path | None = None,
    ) -> Self:
        if not token:
            raise ValueError("a backend Hugging Face token is required")
        if _SHA40.fullmatch(revision) is None or _SHA64.fullmatch(artifact_id) is None:
            raise ValueError("artifact revision and ID must be exact lowercase hashes")
        if api is None:
            from huggingface_hub import HfApi

            api = HfApi()
        try:
            info = api.model_info(
                repo_id,
                revision=revision,
                token=token,
                files_metadata=True,
            )
        except BaseException as error:
            raise ArtifactIntegrityError(
                f"private model revision could not be resolved ({type(error).__name__})"
            ) from None
        if getattr(info, "private", None) is not True or getattr(info, "sha", None) != revision:
            raise ArtifactIntegrityError("artifact must resolve to the exact private model commit")
        prefix = f"artifacts/{artifact_id}/"
        remote_names = {
            str(getattr(row, "rfilename", ""))[len(prefix) :]
            for row in getattr(info, "siblings", [])
            if str(getattr(row, "rfilename", "")).startswith(prefix)
        }
        if remote_names != _ALL_NAMES:
            raise ArtifactIntegrityError("remote artifact file set is incomplete or contains extras")
        fetch = downloader or _default_download
        cache = Path(cache_dir) if cache_dir is not None else None
        paths: dict[str, Path] = {}
        try:
            for name in sorted(_ALL_NAMES):
                paths[name] = Path(fetch(repo_id, prefix + name, revision, token, cache))
        except BaseException as error:
            raise ArtifactIntegrityError(
                f"artifact payload could not be downloaded ({type(error).__name__})"
            ) from None
        return cls._from_paths(
            repo_id=repo_id,
            revision=revision,
            artifact_id=artifact_id,
            files=paths,
            private_remote_verified=True,
        )

    @classmethod
    def load_fixture(cls, root: str | Path) -> Self:
        directory = Path(root)
        names = {path.name for path in directory.iterdir()} if directory.is_dir() else set()
        if names != _ALL_NAMES:
            raise ArtifactIntegrityError("fixture artifact file set is invalid")
        return cls._from_paths(
            repo_id="local-fixture",
            revision="0" * 40,
            artifact_id=directory.name,
            files={name: directory / name for name in names},
            private_remote_verified=False,
        )

    def assert_real_acceptance(self) -> None:
        if not (
            self.private_remote_verified
            and self.repo_id == REAL_MODEL_REPO
            and self.revision == REAL_ARTIFACT_REVISION
            and self.artifact_id == REAL_ARTIFACT_ID
            and self.manifest.source.commit_sha == REAL_TRAINING_SOURCE_REVISION
            and self.manifest.dataset.commit_sha == "b440e98b04ab77afed7caf0455eca3189235fc3b"
        ):
            raise ArtifactAcceptanceError(
                "real acceptance requires the exact private model commit and artifact identity"
            )


__all__ = [
    "ArtifactAcceptanceError",
    "ArtifactIntegrityError",
    "ArtifactManifestV1",
    "BASE_MODEL_ID",
    "BASE_MODEL_REVISION",
    "DistilledArtifactV1",
    "REAL_ARTIFACT_ID",
    "REAL_ARTIFACT_REVISION",
    "REAL_MODEL_REPO",
    "REAL_TRAINING_SOURCE_REVISION",
]
