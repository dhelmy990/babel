"""In-memory immutable model registry used by the demo serving state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from ..contracts import ModelManifest, ModelManifestV2


class DuplicateModel(ValueError):
    pass


class IncompatibleChildModel(ValueError):
    pass


class DuplicateModelPublication(ValueError):
    pass


class UnknownModel(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class ModelPublication:
    model_id: UUID
    parent_model_id: UUID | None
    original_model_id: UUID
    role: Literal["original", "child"]
    repository: str
    commit_sha: str
    manifest_path: str
    serving_artifact_path: str

    def as_row(self) -> dict[str, object]:
        return {
            "modelId": str(self.model_id),
            "parentModelId": (
                None if self.parent_model_id is None else str(self.parent_model_id)
            ),
            "originalModelId": str(self.original_model_id),
            "role": self.role,
            "repository": self.repository,
            "commitSha": self.commit_sha,
            "manifestPath": self.manifest_path,
            "servingArtifactPath": self.serving_artifact_path,
            "immutable": True,
        }


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[UUID, ModelManifest] = {}
        self._original_id: UUID | None = None
        self._publications: dict[UUID, ModelPublication] = {}

    @property
    def original(self) -> ModelManifest:
        if self._original_id is None:
            raise UnknownModel("original model is not registered")
        return self._models[self._original_id]

    def register_original(self, manifest: ModelManifest) -> None:
        if manifest.parentModelId is not None or manifest.producingRunId is not None:
            raise IncompatibleChildModel("original model cannot declare parent lineage")
        if self._original_id is not None or manifest.modelId in self._models:
            raise DuplicateModel("original model is already registered")
        self._models[manifest.modelId] = manifest
        self._original_id = manifest.modelId

    def register_real_original(self, manifest: ModelManifestV2) -> None:
        if not isinstance(manifest, ModelManifestV2):
            raise IncompatibleChildModel("scale original must be the accepted real Qwen manifest")
        self.register_original(manifest)

    def register_child(self, manifest: ModelManifest) -> None:
        if manifest.modelId in self._models:
            raise DuplicateModel(f"model already registered: {manifest.modelId}")
        if manifest.parentModelId is None or manifest.parentModelId not in self._models:
            raise IncompatibleChildModel("child parent must already be registered")
        parent = self._models[manifest.parentModelId]
        if type(manifest) is not type(parent):
            raise IncompatibleChildModel("child manifest generation must match its parent")
        if manifest.embeddingSpace != parent.embeddingSpace:
            raise IncompatibleChildModel("child embedding space is incompatible")
        if (
            manifest.encoderRepo != parent.encoderRepo
            or manifest.encoderRevision != parent.encoderRevision
            or manifest.datasetRevision != parent.datasetRevision
        ):
            raise IncompatibleChildModel("child source identity is incompatible")
        self._models[manifest.modelId] = manifest

    def get(self, model_id: UUID) -> ModelManifest:
        try:
            return self._models[model_id]
        except KeyError as error:
            raise UnknownModel(str(model_id)) from error

    def select(self, model_id: UUID) -> ModelManifest:
        """Explicitly select original or child without mutating either."""
        return self.get(model_id)

    def select_for_scale(self, model_id: UUID) -> ModelManifestV2:
        """Reject V1 fixture manifests at the formal-scale boundary."""
        manifest = self.get(model_id)
        if not isinstance(manifest, ModelManifestV2):
            raise IncompatibleChildModel(
                "scale serving requires an accepted real Qwen ModelManifestV2"
            )
        return manifest

    def record_publication(
        self,
        model_id: UUID,
        *,
        repository: str,
        commit_sha: str,
        manifest_path: str,
        serving_artifact_path: str,
    ) -> ModelPublication:
        """Record one returned immutable Hub commit without replacing its model."""
        manifest = self.get(model_id)
        if self._original_id is None:
            raise UnknownModel("original model is not registered")
        if model_id in self._publications:
            raise DuplicateModelPublication(
                f"model publication already recorded: {model_id}"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("model publication repository must be owner/name")
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
            raise ValueError("model publication requires an immutable commit SHA")
        path = PurePosixPath(manifest_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or str(path) != manifest_path
            or path.name not in {"manifest.json", "model-manifest.json"}
        ):
            raise ValueError("model manifest path must be canonical and relative")
        serving_path = PurePosixPath(serving_artifact_path)
        if (
            serving_path.is_absolute()
            or ".." in serving_path.parts
            or str(serving_path) != serving_artifact_path
        ):
            raise ValueError("serving artifact path must be canonical and relative")
        publication = ModelPublication(
            model_id=model_id,
            parent_model_id=manifest.parentModelId,
            original_model_id=self._original_id,
            role="original" if model_id == self._original_id else "child",
            repository=repository,
            commit_sha=commit_sha,
            manifest_path=manifest_path,
            serving_artifact_path=serving_artifact_path,
        )
        self._publications[model_id] = publication
        return publication

    def publication_ledger(self) -> tuple[ModelPublication, ...]:
        """Return original first, then children in immutable registration order."""
        return tuple(
            self._publications[model_id]
            for model_id in self._models
            if model_id in self._publications
        )


__all__ = [
    "DuplicateModel",
    "DuplicateModelPublication",
    "IncompatibleChildModel",
    "ModelPublication",
    "ModelRegistry",
    "UnknownModel",
]
