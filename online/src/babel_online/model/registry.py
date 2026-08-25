"""In-memory immutable model registry used by the demo serving state."""

from __future__ import annotations

from uuid import UUID

from ..contracts import ModelManifestV1


class DuplicateModel(ValueError):
    pass


class IncompatibleChildModel(ValueError):
    pass


class UnknownModel(KeyError):
    pass


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[UUID, ModelManifestV1] = {}
        self._original_id: UUID | None = None

    @property
    def original(self) -> ModelManifestV1:
        if self._original_id is None:
            raise UnknownModel("original model is not registered")
        return self._models[self._original_id]

    def register_original(self, manifest: ModelManifestV1) -> None:
        if manifest.parentModelId is not None or manifest.producingRunId is not None:
            raise IncompatibleChildModel("original model cannot declare parent lineage")
        if self._original_id is not None or manifest.modelId in self._models:
            raise DuplicateModel("original model is already registered")
        self._models[manifest.modelId] = manifest
        self._original_id = manifest.modelId

    def register_child(self, manifest: ModelManifestV1) -> None:
        if manifest.modelId in self._models:
            raise DuplicateModel(f"model already registered: {manifest.modelId}")
        if manifest.parentModelId is None or manifest.parentModelId not in self._models:
            raise IncompatibleChildModel("child parent must already be registered")
        parent = self._models[manifest.parentModelId]
        if manifest.embeddingSpace != parent.embeddingSpace:
            raise IncompatibleChildModel("child embedding space is incompatible")
        if (
            manifest.encoderRepo != parent.encoderRepo
            or manifest.encoderRevision != parent.encoderRevision
            or manifest.datasetRevision != parent.datasetRevision
        ):
            raise IncompatibleChildModel("child source identity is incompatible")
        self._models[manifest.modelId] = manifest

    def get(self, model_id: UUID) -> ModelManifestV1:
        try:
            return self._models[model_id]
        except KeyError as error:
            raise UnknownModel(str(model_id)) from error

    def select(self, model_id: UUID) -> ModelManifestV1:
        """Explicitly select original or child without mutating either."""
        return self.get(model_id)


__all__ = [
    "DuplicateModel",
    "IncompatibleChildModel",
    "ModelRegistry",
    "UnknownModel",
]
