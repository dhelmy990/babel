"""Immutable, content-addressed publication of bounded fault-only evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .hub import (
    AcceptedRunExists,
    UploadOperation,
    _download,
    _json_object,
    _reject_secret,
    _sha256,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FAULTS = (
    "trainer_kill_restart",
    "kafka_pause_resume",
    "invalid_model_state",
    "serving_restart",
)


@dataclass(frozen=True, slots=True)
class FaultEvidenceBundle:
    root: Path
    trial_id: UUID
    campaign_id: str
    model_id: UUID
    manifest_path: Path
    checksums_path: Path
    receipt_path: Path

    @property
    def bundle_path(self) -> str:
        return f"fault-runs/{self.trial_id}/{self.campaign_id}"


@dataclass(frozen=True, slots=True)
class FaultEvidencePublicationReceipt:
    repository: str
    commit_sha: str
    bundle_path: str
    artifact_sha256: str
    receipt_sha256: str
    campaign_id: str
    trial_id: UUID
    model_id: UUID
    verified_files: dict[str, str]


def _canonical(value: object) -> bytes:
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


def _validate_receipt(document: dict[str, Any], *, trial_id: UUID) -> tuple[UUID, int]:
    required = {
        "schemaVersion",
        "experimentId",
        "creatorCount",
        "conditionCount",
        "faultConditionIndex",
        "faultRunId",
        "acceptedTrialSha256",
        "populationManifestSha256",
        "deploymentScope",
        "evidenceUse",
        "status",
        "campaignWindow",
        "faults",
        "cleanup",
        "failure",
        "failedFault",
    }
    if set(document) != required or document.get("schemaVersion") != 1:
        raise ValueError("fault receipt schema differs")
    if document.get("experimentId") != str(trial_id):
        raise ValueError("fault receipt belongs to another trial")
    if (
        document.get("deploymentScope") != "same_host"
        or document.get("evidenceUse") != "fault_only_not_topology_performance"
    ):
        raise ValueError("fault receipt evidence label differs")
    creator_count = int(document.get("creatorCount", 0))
    condition_count = int(document.get("conditionCount", 0))
    expected_count = 9 if creator_count == 50 else 6
    if creator_count not in {50, 100, 500} or condition_count != expected_count:
        raise ValueError("fault receipt formal cohort identity differs")
    if int(document.get("faultConditionIndex", 0)) != 6:
        raise ValueError("fault receipt must target condition 6")
    try:
        run_id = UUID(str(document["faultRunId"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("fault receipt run identity differs") from error
    if any(
        not _SHA256.fullmatch(str(document.get(field, "")))
        for field in ("acceptedTrialSha256", "populationManifestSha256")
    ):
        raise ValueError("fault receipt source SHA differs")
    if (
        document.get("status") != "completed"
        or document.get("failure") is not None
        or document.get("failedFault") is not None
    ):
        raise ValueError("only a completed fault campaign can be published")
    cleanup = document.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("verified") is not True:
        raise ValueError("fault receipt cleanup is not verified")
    faults = document.get("faults")
    if not isinstance(faults, list) or tuple(
        row.get("fault") for row in faults if isinstance(row, dict)
    ) != _FAULTS:
        raise ValueError("fault receipt does not contain the exact fault order")
    if len(faults) != len(_FAULTS) or any(
        not isinstance(row, dict)
        or row.get("status") != "completed"
        or row.get("failure") is not None
        for row in faults
    ):
        raise ValueError("fault receipt contains an incomplete fault")
    return run_id, creator_count


def _validate_model(
    document: dict[str, Any], *, producing_run_id: UUID
) -> tuple[UUID, UUID]:
    required = {"schemaVersion", "modelId", "parentModelId", "producingRunId", "immutable"}
    if not required.issubset(document) or document.get("schemaVersion") != 2:
        raise ValueError("fault model identity is incomplete")
    try:
        model_id = UUID(str(document["modelId"]))
        parent_id = UUID(str(document["parentModelId"]))
        run_id = UUID(str(document["producingRunId"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("fault model identity is invalid") from error
    if (
        document.get("immutable") is not True
        or run_id != producing_run_id
        or model_id == parent_id
    ):
        raise ValueError("fault model identity differs from condition 6")
    return model_id, parent_id


def build_fault_evidence_bundle(
    output_root: str | Path,
    *,
    receipt_path: str | Path,
    expected_receipt_sha256: str,
    trial_id: UUID,
    model_manifest_path: str | Path,
) -> FaultEvidenceBundle:
    """Validate and stage one fault campaign without touching formal runs/."""
    source_receipt = Path(receipt_path)
    source_model = Path(model_manifest_path)
    _reject_secret(source_receipt)
    _reject_secret(source_model)
    actual_receipt_sha = _sha256(source_receipt)
    if (
        not _SHA256.fullmatch(expected_receipt_sha256)
        or actual_receipt_sha != expected_receipt_sha256
    ):
        raise ValueError("fault receipt SHA differs from the expected campaign receipt")
    receipt = _json_object(source_receipt, "fault receipt")
    fault_run_id, creator_count = _validate_receipt(receipt, trial_id=trial_id)
    model = _json_object(source_model, "fault model manifest")
    model_id, parent_id = _validate_model(model, producing_run_id=fault_run_id)

    campaign_id = actual_receipt_sha
    final = Path(output_root) / "fault-runs" / str(trial_id) / campaign_id
    partial = final.with_name(f"{campaign_id}.partial")
    if final.exists():
        raise AcceptedRunExists(f"accepted local fault campaign already exists: {campaign_id}")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        copied_receipt = partial / "fault-receipt.json"
        shutil.copyfile(source_receipt, copied_receipt)
        manifest = {
            "schemaVersion": 1,
            "artifactType": "fault_evidence",
            "trialId": str(trial_id),
            "campaignId": campaign_id,
            "creatorCount": creator_count,
            "faultConditionIndex": 6,
            "faultRunId": str(fault_run_id),
            "modelId": str(model_id),
            "parentModelId": str(parent_id),
            "modelManifestSha256": _sha256(source_model),
            "receiptSha256": actual_receipt_sha,
            "deploymentScope": "same_host",
            "evidenceUse": "fault_only_not_topology_performance",
            "files": ["fault-receipt.json", "report.md"],
        }
        manifest_path = partial / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest))
        report_path = partial / "report.md"
        report_path.write_text(
            "\n".join(
                (
                    "# Same-host fault evidence",
                    "",
                    f"Trial: `{trial_id}`",
                    f"Campaign: `{campaign_id}`",
                    f"Model: `{model_id}`",
                    "Condition: `6` (`same_host_split`, training + activation)",
                    "",
                    "Four bounded faults completed with verified cleanup. This artifact is",
                    "fault-only evidence and is not part of topology latency ratios.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        checksums = {
            path.name: _sha256(path)
            for path in (copied_receipt, manifest_path, report_path)
        }
        checksums_path = partial / "checksums.json"
        checksums_path.write_bytes(_canonical(checksums))
        for path in partial.iterdir():
            _reject_secret(path)
            with path.open("rb") as source:
                os.fsync(source.fileno())
        os.replace(partial, final)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
    return FaultEvidenceBundle(
        root=final,
        trial_id=trial_id,
        campaign_id=campaign_id,
        model_id=model_id,
        manifest_path=final / "manifest.json",
        checksums_path=final / "checksums.json",
        receipt_path=final / "fault-receipt.json",
    )


def _operations(api: Any, bundle: FaultEvidenceBundle) -> list[Any]:
    paths = sorted(bundle.root.iterdir(), key=lambda path: path.name)
    if type(api).__module__.startswith("huggingface_hub"):
        from huggingface_hub import CommitOperationAdd

        return [
            CommitOperationAdd(
                path_in_repo=f"{bundle.bundle_path}/{path.name}",
                path_or_fileobj=path,
            )
            for path in paths
        ]
    return [
        UploadOperation(f"{bundle.bundle_path}/{path.name}", str(path))
        for path in paths
    ]


def publish_fault_evidence_bundle(
    api: Any,
    bundle: FaultEvidenceBundle,
    *,
    repo_id: str,
    token: str | None,
    revision: str = "main",
) -> FaultEvidencePublicationReceipt:
    """Upload one content-addressed fault campaign and verify its remote bytes."""
    prefix = bundle.bundle_path
    remote_files = api.list_repo_files(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    if any(path == prefix or path.startswith(prefix + "/") for path in remote_files):
        raise AcceptedRunExists(f"remote fault campaign already exists: {prefix}")
    for path in bundle.root.iterdir():
        if not path.is_file():
            raise ValueError("fault bundle may contain only files")
        _reject_secret(path)
    result = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        operations=_operations(api, bundle),
        commit_message=f"Publish immutable fault campaign {bundle.campaign_id}",
        token=token,
    )
    commit_sha = str(
        getattr(result, "oid", None)
        or getattr(result, "commit_id", None)
        or getattr(result, "commit_sha", "")
    )
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise ValueError("Hugging Face did not return an immutable commit SHA")

    remote_checksums_path = _download(
        api,
        repo_id=repo_id,
        filename=f"{prefix}/checksums.json",
        revision=commit_sha,
        token=token,
    )
    if _sha256(remote_checksums_path) != _sha256(bundle.checksums_path):
        raise ValueError("remote checksum inventory differs from fault bundle")
    checksums = _json_object(remote_checksums_path, "remote fault checksums")
    if set(checksums) != {"fault-receipt.json", "manifest.json", "report.md"}:
        raise ValueError("remote fault checksum inventory is incomplete")
    loaded: dict[str, Path] = {}
    for name, digest in checksums.items():
        path = _download(
            api,
            repo_id=repo_id,
            filename=f"{prefix}/{name}",
            revision=commit_sha,
            token=token,
        )
        loaded[name] = path
        if _sha256(path) != digest:
            raise ValueError(f"remote checksum mismatch: {name}")
    manifest = _json_object(loaded["manifest.json"], "remote fault manifest")
    receipt = _json_object(loaded["fault-receipt.json"], "remote fault receipt")
    if (
        manifest.get("trialId") != str(bundle.trial_id)
        or manifest.get("campaignId") != bundle.campaign_id
        or manifest.get("modelId") != str(bundle.model_id)
        or manifest.get("receiptSha256") != _sha256(loaded["fault-receipt.json"])
        or receipt.get("experimentId") != str(bundle.trial_id)
        or not loaded["report.md"].read_text(encoding="utf-8").strip()
    ):
        raise ValueError("remote fault identity is incomplete")
    return FaultEvidencePublicationReceipt(
        repository=repo_id,
        commit_sha=commit_sha,
        bundle_path=prefix,
        artifact_sha256=_sha256(bundle.checksums_path),
        receipt_sha256=bundle.campaign_id,
        campaign_id=bundle.campaign_id,
        trial_id=bundle.trial_id,
        model_id=bundle.model_id,
        verified_files={name: str(digest) for name, digest in checksums.items()},
    )


__all__ = [
    "FaultEvidenceBundle",
    "FaultEvidencePublicationReceipt",
    "build_fault_evidence_bundle",
    "publish_fault_evidence_bundle",
]
