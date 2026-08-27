from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from babel_online.model.registry import ModelRegistry
from babel_online.model.state_distributor import (
    ActivationError,
    KnownVectorProbeV1,
    ModelStateDistributor,
    RealQwenChildStateV1,
    export_real_qwen_child,
)
from babel_online.model import ModelStateDistributor as PublicModelStateDistributor


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _export(tmp_path: Path, parent, registry: ModelRegistry):
    state = json.dumps({"residual": [0.0] * 100}, separators=(",", ":")).encode()
    probe = KnownVectorProbeV1(
        schemaVersion=1,
        inputVector=[1.0] + [0.0] * 99,
        expectedSemanticSha256="a" * 64,
    )
    return export_real_qwen_child(
        tmp_path,
        parent=parent,
        run_id=uuid4(),
        child_model_id=uuid4(),
        label="post-run real Qwen child",
        online_state=state,
        processed_feedback_events=17,
        model_version=4,
        vector_snapshot_sha256="b" * 64,
        probe=probe,
        registry=registry,
        created_at_ns=123,
    )


def test_export_preserves_v2_qwen_identity_and_checksums_online_state(
    tmp_path: Path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)

    exported = _export(tmp_path, real_model_manifest, registry)
    descriptor = RealQwenChildStateV1.model_validate_json(
        exported.descriptor_path.read_text()
    )

    assert descriptor.childManifest.schemaVersion == 2
    assert descriptor.childManifest.parentModelId == real_model_manifest.modelId
    assert descriptor.childManifest.encoderRevision == real_model_manifest.encoderRevision
    assert descriptor.childManifest.embeddingSpace == real_model_manifest.embeddingSpace
    assert descriptor.processedFeedbackEvents == 17
    assert descriptor.modelVersion == 4
    assert descriptor.vectorSnapshotSha256 == "b" * 64
    state_path = exported.root / descriptor.onlineStatePath
    assert descriptor.files[descriptor.onlineStatePath] == _sha(state_path.read_bytes())
    assert descriptor.childManifest.modelId != real_model_manifest.modelId
    assert PublicModelStateDistributor is ModelStateDistributor


def test_distributor_validates_then_probes_before_atomic_activation(
    tmp_path: Path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    exported = _export(tmp_path, real_model_manifest, registry)
    selected = {"value": "original"}
    persisted = []
    distributor = ModelStateDistributor(
        registry=registry,
        current_state=lambda: selected["value"],
        activate_state=lambda state: selected.__setitem__("value", state),
        register_persistent=lambda descriptor, path: persisted.append((descriptor, path)),
        clock_ns=lambda: 500,
    )

    receipt = distributor.activate(
        exported.root,
        prepare=lambda descriptor, _root: f"prepared:{descriptor.childManifest.modelId}",
        probe=lambda prepared, probe: (
            prepared.startswith("prepared:")
            and probe.expectedSemanticSha256 == "a" * 64
        ),
    )

    assert receipt.status == "activated"
    assert selected["value"].startswith("prepared:")
    assert persisted[0][0].childManifest.modelId == receipt.modelId
    assert receipt.requestedAtNs == 500
    assert receipt.activatedAtNs == 500


def test_failed_probe_keeps_previous_and_original_selectable(
    tmp_path: Path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    exported = _export(tmp_path, real_model_manifest, registry)
    selected = {"value": "previous-valid-state"}
    distributor = ModelStateDistributor(
        registry=registry,
        current_state=lambda: selected["value"],
        activate_state=lambda state: selected.__setitem__("value", state),
    )

    with pytest.raises(ActivationError, match="probe"):
        distributor.activate(
            exported.root,
            prepare=lambda *_: "candidate",
            probe=lambda *_: False,
        )

    assert selected["value"] == "previous-valid-state"
    assert registry.original.modelId == real_model_manifest.modelId


def test_activation_exception_rolls_back_previous_state(
    tmp_path: Path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    exported = _export(tmp_path, real_model_manifest, registry)
    selected = {"value": "previous"}

    def activate(state):
        if state == "candidate":
            selected["value"] = "partially-swapped"
            raise RuntimeError("swap failed")
        selected["value"] = state

    distributor = ModelStateDistributor(
        registry=registry,
        current_state=lambda: selected["value"],
        activate_state=activate,
    )
    with pytest.raises(ActivationError, match="rolled back"):
        distributor.activate(
            exported.root,
            prepare=lambda *_: "candidate",
            probe=lambda *_: True,
        )
    assert selected["value"] == "previous"


def test_distributor_rejects_tampered_or_incompatible_files(
    tmp_path: Path, real_model_manifest
) -> None:
    registry = ModelRegistry()
    registry.register_real_original(real_model_manifest)
    exported = _export(tmp_path, real_model_manifest, registry)
    state_path = exported.root / "online-state.json"
    state_path.chmod(0o644)
    state_path.write_bytes(b"tampered")
    distributor = ModelStateDistributor(
        registry=registry,
        current_state=lambda: "original",
        activate_state=lambda _state: None,
    )
    with pytest.raises(ActivationError, match="checksum"):
        distributor.activate(exported.root, prepare=lambda *_: None, probe=lambda *_: True)
