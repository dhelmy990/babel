"""Checksum-verifying immutable model artifact loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..contracts import ModelManifestV1


class ArtifactIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedArtifact:
    manifest: ModelManifestV1
    checkpoint_path: Path


def load_artifact(root: Path) -> LoadedArtifact:
    """Load one immutable manifest and verify its checkpoint bytes."""
    artifact_root = Path(root).resolve()
    manifest_path = artifact_root / "manifest.json"
    manifest = ModelManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    checkpoint = (artifact_root / manifest.checkpointPath).resolve()
    try:
        checkpoint.relative_to(artifact_root)
    except ValueError as error:
        raise ArtifactIntegrityError("checkpoint path escapes artifact root") from error
    if not checkpoint.is_file():
        raise ArtifactIntegrityError("checkpoint file is missing")
    actual = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if actual != manifest.checkpointSha256:
        raise ArtifactIntegrityError("checkpoint SHA-256 does not match manifest")
    return LoadedArtifact(manifest=manifest, checkpoint_path=checkpoint)


__all__ = ["ArtifactIntegrityError", "LoadedArtifact", "load_artifact"]
