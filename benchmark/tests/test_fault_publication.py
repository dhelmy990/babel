from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from babel_benchmark.fault_publication import (
    build_fault_evidence_bundle,
    publish_fault_evidence_bundle,
)
from babel_benchmark.hub import AcceptedRunExists, SecretBearingFile


TRIAL_ID = UUID("00000000-0000-5000-8000-000000000130")
RUN_ID = UUID("00000000-0000-5000-8000-000000000006")
MODEL_ID = UUID("00000000-0000-5000-8000-000000000132")
PARENT_ID = UUID("00000000-0000-5000-8000-000000000131")


def _fault_row(name: str) -> dict[str, object]:
    return {
        "fault": name,
        "invalidStateKind": "child_or_checkpoint" if name == "invalid_model_state" else None,
        "status": "completed",
        "failure": None,
        "faultWindow": {
            "startedNs": 1,
            "detectedNs": 2,
            "recoveryStartedNs": 3,
            "recoveredNs": 4,
            "endedNs": 5,
        },
        "detectionNs": 1,
        "recoveryNs": 1,
        "availability": {
            "availableSamples": 1,
            "totalSamples": 1,
            "availableRatio": 1.0,
            "availableDuringFault": name != "serving_restart",
            "availableAfterRecovery": True,
        },
        "kafkaLag": {
            "before": 0,
            "maximum": 1 if name == "kafka_pause_resume" else 0,
            "after": 0,
            "recoveredToBaseline": True,
        },
        "duplicates": 0,
        "lost": 0,
        "eventCounterResetDetected": False,
        "versions": {
            "trainerBefore": 1,
            "trainerAfter": 2,
            "servingBefore": 3,
            "servingAfter": 3,
        },
        "invalidStateRejected": True if name == "invalid_model_state" else None,
        "lastValidServingVersionRetained": (
            True if name == "invalid_model_state" else None
        ),
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    receipt = {
        "schemaVersion": 1,
        "experimentId": str(TRIAL_ID),
        "creatorCount": 50,
        "conditionCount": 9,
        "faultConditionIndex": 6,
        "faultRunId": str(RUN_ID),
        "acceptedTrialSha256": "a" * 64,
        "populationManifestSha256": "b" * 64,
        "deploymentScope": "same_host",
        "evidenceUse": "fault_only_not_topology_performance",
        "status": "completed",
        "campaignWindow": {
            "startedNs": 1,
            "endedNs": 20,
            "detectionTimeoutNs": 10,
            "recoveryTimeoutNs": 10,
            "faultHoldNs": 1,
        },
        "faults": [
            _fault_row(name)
            for name in (
                "trainer_kill_restart",
                "kafka_pause_resume",
                "invalid_model_state",
                "serving_restart",
            )
        ],
        "cleanup": {
            "verified": True,
            "error": None,
            "duplicateEvents": 0,
            "lostEvents": 0,
        },
        "failure": None,
        "failedFault": None,
    }
    receipt_path = tmp_path / "fault-campaign.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    model = {
        "schemaVersion": 2,
        "modelId": str(MODEL_ID),
        "parentModelId": str(PARENT_ID),
        "producingRunId": str(RUN_ID),
        "immutable": True,
    }
    model_path = tmp_path / "model-manifest.json"
    model_path.write_text(json.dumps(model, sort_keys=True) + "\n")
    return receipt_path, model_path, hashlib.sha256(receipt_path.read_bytes()).hexdigest()


def test_build_fault_bundle_is_content_addressed_and_does_not_touch_formal_run(
    tmp_path: Path,
) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    formal = tmp_path / "accepted/runs" / str(TRIAL_ID) / "manifest.json"
    formal.parent.mkdir(parents=True)
    formal.write_text("formal bundle is immutable\n")

    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )

    assert bundle.campaign_id == receipt_sha
    assert bundle.root == (
        tmp_path / "accepted/fault-runs" / str(TRIAL_ID) / receipt_sha
    )
    assert formal.read_text() == "formal bundle is immutable\n"
    assert {path.name for path in bundle.root.iterdir()} == {
        "fault-receipt.json",
        "manifest.json",
        "report.md",
        "checksums.json",
    }
    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["trialId"] == str(TRIAL_ID)
    assert manifest["campaignId"] == receipt_sha
    assert manifest["faultConditionIndex"] == 6
    assert manifest["faultRunId"] == str(RUN_ID)
    assert manifest["modelId"] == str(MODEL_ID)
    assert manifest["receiptSha256"] == receipt_sha
    checksums = json.loads(bundle.checksums_path.read_text())
    assert set(checksums) == {"fault-receipt.json", "manifest.json", "report.md"}
    assert all(
        digest == hashlib.sha256((bundle.root / name).read_bytes()).hexdigest()
        for name, digest in checksums.items()
    )

    with pytest.raises(AcceptedRunExists, match="fault campaign"):
        build_fault_evidence_bundle(
            tmp_path / "accepted",
            receipt_path=receipt,
            expected_receipt_sha256=receipt_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schemaVersion", 2, "schema"),
        ("deploymentScope", "cross_host", "label"),
        ("evidenceUse", "topology_performance", "label"),
        ("faultConditionIndex", 3, "condition 6"),
        ("experimentId", "00000000-0000-5000-8000-000000000999", "trial"),
    ),
)
def test_build_rejects_receipt_identity_or_label_drift(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    receipt, model, _receipt_sha = _inputs(tmp_path)
    document = json.loads(receipt.read_text())
    document[field] = value
    receipt.write_text(json.dumps(document, sort_keys=True) + "\n")
    changed_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match=message):
        build_fault_evidence_bundle(
            tmp_path / "accepted",
            receipt_path=receipt,
            expected_receipt_sha256=changed_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


def test_build_rejects_receipt_sha_or_model_identity_drift(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    with pytest.raises(ValueError, match="receipt SHA"):
        build_fault_evidence_bundle(
            tmp_path / "accepted",
            receipt_path=receipt,
            expected_receipt_sha256="f" * 64,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )

    document = json.loads(model.read_text())
    document["producingRunId"] = "00000000-0000-5000-8000-000000000999"
    model.write_text(json.dumps(document) + "\n")
    with pytest.raises(ValueError, match="model identity"):
        build_fault_evidence_bundle(
            tmp_path / "other",
            receipt_path=receipt,
            expected_receipt_sha256=receipt_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


def test_build_rejects_secret_markers(tmp_path: Path) -> None:
    receipt, model, _receipt_sha = _inputs(tmp_path)
    document = json.loads(receipt.read_text())
    document["failure"] = "HF_TOKEN=must-not-publish"
    receipt.write_text(json.dumps(document) + "\n")
    changed_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()

    with pytest.raises(SecretBearingFile):
        build_fault_evidence_bundle(
            tmp_path / "accepted",
            receipt_path=receipt,
            expected_receipt_sha256=changed_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


def test_build_rejects_symlinked_or_dangling_fault_destinations(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    output = tmp_path / "accepted"
    formal = output / "runs" / str(TRIAL_ID)
    formal.mkdir(parents=True)
    (output / "fault-runs").symlink_to(output / "runs", target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        build_fault_evidence_bundle(
            output,
            receipt_path=receipt,
            expected_receipt_sha256=receipt_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )
    assert not (formal / receipt_sha).exists()

    (output / "fault-runs").unlink()
    campaign_parent = output / "fault-runs" / str(TRIAL_ID)
    campaign_parent.mkdir(parents=True)
    (campaign_parent / receipt_sha).symlink_to(tmp_path / "missing")
    with pytest.raises(AcceptedRunExists):
        build_fault_evidence_bundle(
            output,
            receipt_path=receipt,
            expected_receipt_sha256=receipt_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


def test_build_rejects_relative_output_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    accepted = tmp_path / "accepted"
    formal = accepted / "runs" / str(TRIAL_ID)
    formal.mkdir(parents=True)
    (accepted / "fault-runs").symlink_to(accepted / "runs", target_is_directory=True)
    (tmp_path / "alias").symlink_to(accepted, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="symbolic link"):
        build_fault_evidence_bundle(
            Path("alias"),
            receipt_path=receipt,
            expected_receipt_sha256=receipt_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )
    assert not (formal / receipt_sha).exists()


def test_build_rejects_incomplete_nested_fault_schema(tmp_path: Path) -> None:
    receipt, model, _receipt_sha = _inputs(tmp_path)
    document = json.loads(receipt.read_text())
    document["campaignWindow"] = None
    document["cleanup"] = {"verified": True}
    document["faults"] = [
        {"fault": row["fault"], "status": "completed", "failure": None}
        for row in document["faults"]
    ]
    receipt.write_text(json.dumps(document, sort_keys=True) + "\n")
    changed_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="schema"):
        build_fault_evidence_bundle(
            tmp_path / "accepted",
            receipt_path=receipt,
            expected_receipt_sha256=changed_sha,
            trial_id=TRIAL_ID,
            model_manifest_path=model,
        )


class FakeHubApi:
    def __init__(self, remote: Path) -> None:
        self.remote = remote
        self.commits: list[dict[str, object]] = []

    def list_repo_files(self, **_kwargs: object) -> list[str]:
        if not self.remote.exists():
            return []
        return [
            path.relative_to(self.remote).as_posix()
            for path in self.remote.rglob("*")
            if path.is_file()
        ]

    def create_commit(self, **kwargs: object) -> SimpleNamespace:
        self.commits.append(kwargs)
        for operation in kwargs["operations"]:
            destination = self.remote / operation.path_in_repo
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(operation.path_or_fileobj, destination)
        return SimpleNamespace(oid="c" * 40)

    def hf_hub_download(self, *, filename: str, **_kwargs: object) -> str:
        return str(self.remote / filename)


def test_publish_uses_separate_fault_path_and_remotely_verifies_every_file(
    tmp_path: Path,
) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )
    api = FakeHubApi(tmp_path / "remote")

    published = publish_fault_evidence_bundle(
        api,
        bundle,
        repo_id="dhelmy990/babel-wikipedia-experiment",
        token="test-token-is-never-written",
    )

    expected_path = f"fault-runs/{TRIAL_ID}/{receipt_sha}"
    assert published.bundle_path == expected_path
    assert published.commit_sha == "c" * 40
    assert published.receipt_sha256 == receipt_sha
    assert published.model_id == MODEL_ID
    assert {operation.path_in_repo for operation in api.commits[0]["operations"]} == {
        f"{expected_path}/{path.name}" for path in bundle.root.iterdir()
    }
    assert not any(path.startswith(f"runs/{TRIAL_ID}/") for path in api.list_repo_files())

    with pytest.raises(AcceptedRunExists, match="remote fault campaign"):
        publish_fault_evidence_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="test-token-is-never-written",
        )


def test_remote_reload_rejects_corrupted_fault_receipt(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )
    api = FakeHubApi(tmp_path / "remote")
    original = api.create_commit

    def corrupt(**kwargs: object) -> SimpleNamespace:
        result = original(**kwargs)
        prefix = api.remote / f"fault-runs/{TRIAL_ID}/{receipt_sha}"
        (prefix / "fault-receipt.json").write_text("{}\n")
        return result

    api.create_commit = corrupt  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="remote checksum"):
        publish_fault_evidence_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="test-token-is-never-written",
        )


def test_publish_rejects_mutated_content_at_original_campaign_id(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )
    changed = json.loads(bundle.receipt_path.read_text())
    changed["campaignWindow"]["endedNs"] += 1
    bundle.receipt_path.write_text(json.dumps(changed, sort_keys=True) + "\n")
    changed_sha = hashlib.sha256(bundle.receipt_path.read_bytes()).hexdigest()
    manifest = json.loads(bundle.manifest_path.read_text())
    manifest["receiptSha256"] = changed_sha
    bundle.manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    checksums = {
        name: hashlib.sha256((bundle.root / name).read_bytes()).hexdigest()
        for name in ("fault-receipt.json", "manifest.json", "report.md")
    }
    bundle.checksums_path.write_text(json.dumps(checksums, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="campaign|immutable"):
        publish_fault_evidence_bundle(
            FakeHubApi(tmp_path / "remote"),
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="test-token-is-never-written",
        )


def test_publish_rejects_files_outside_closed_inventory(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )
    (bundle.root / "untracked.txt").write_text("not in checksums\n")
    api = FakeHubApi(tmp_path / "remote")

    with pytest.raises(ValueError, match="inventory"):
        publish_fault_evidence_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="test-token-is-never-written",
        )
    assert api.commits == []


def test_publish_rejects_file_added_after_initial_validation(tmp_path: Path) -> None:
    receipt, model, receipt_sha = _inputs(tmp_path)
    bundle = build_fault_evidence_bundle(
        tmp_path / "accepted",
        receipt_path=receipt,
        expected_receipt_sha256=receipt_sha,
        trial_id=TRIAL_ID,
        model_manifest_path=model,
    )
    api = FakeHubApi(tmp_path / "remote")
    original = api.list_repo_files

    def add_late_file(**kwargs: object) -> list[str]:
        files = original(**kwargs)
        (bundle.root / "untracked.txt").write_text("appeared after validation\n")
        return files

    api.list_repo_files = add_late_file  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="inventory"):
        publish_fault_evidence_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="test-token-is-never-written",
        )
    assert api.commits == []
