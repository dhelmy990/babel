from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from babel_benchmark.hub import (
    AcceptedRunExists,
    SecretBearingFile,
    build_run_bundle,
    publish_run_bundle,
)


RUN_ID = UUID("00000000-0000-5000-8000-000000000013")


def _parquet(path: Path, kind: str) -> Path:
    pq.write_table(pa.Table.from_pylist([{"kind": kind, "value": 1}]), path)
    return path


def _sources(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "source"
    source.mkdir()
    summary = source / "summary.json"
    summary.write_text(json.dumps({"schemaVersion": 1, "requestCount": 3}))
    report = source / "report.md"
    report.write_text("# Formal split run\n\nAll results are data-backed.\n")
    model = source / "model-manifest.json"
    child_manifest = {
        "schemaVersion": 2,
        "modelId": "00000000-0000-5000-8000-000000000002",
        "parentModelId": "00000000-0000-5000-8000-000000000001",
        "producingRunId": str(RUN_ID),
        "immutable": True,
    }
    model.write_text(json.dumps(child_manifest))
    model_artifact = source / "model-artifact"
    model_artifact.mkdir()
    online_state = model_artifact / "online-state.json"
    online_state.write_text(json.dumps({"weights": [0.1, 0.2], "version": 7}))
    state_sha = hashlib.sha256(online_state.read_bytes()).hexdigest()
    (model_artifact / "state-descriptor.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "childManifest": child_manifest,
                "onlineStatePath": "online-state.json",
                "files": {"online-state.json": state_sha},
                "immutable": True,
            }
        )
    )
    return {
        "feedback_parquet": _parquet(source / "feedback.parquet", "feedback"),
        "edges_parquet": _parquet(source / "edges.parquet", "edge"),
        "requests_parquet": _parquet(source / "requests.parquet", "request"),
        "resources_parquet": _parquet(source / "resources.parquet", "resource"),
        "summary_json": summary,
        "report_markdown": report,
        "model_manifest": model,
        "model_artifact_root": model_artifact,
    }


def _bundle(tmp_path: Path):
    return build_run_bundle(
        tmp_path / "output",
        run_id=RUN_ID,
        **_sources(tmp_path),
        acceptance_label="formal",
        progress={"phase": "completed", "conditionIndex": 9, "conditionCount": 9},
        topology="same_host_split",
        placement={"servingPid": 41, "trainerPid": 42},
        hardware={"cpu": "fixture", "gpu": "unavailable"},
        model_ledger=[
            {
                "modelId": "00000000-0000-5000-8000-000000000001",
                "parentModelId": None,
                "role": "original",
                "immutable": True,
            },
            {
                "modelId": "00000000-0000-5000-8000-000000000002",
                "parentModelId": "00000000-0000-5000-8000-000000000001",
                "role": "child",
                "immutable": True,
            },
        ],
        vector_snapshots=[{"sha256": "a" * 64, "rows": 10_000, "dimension": 100}],
    )


def test_run_bundle_closes_required_files_and_experiment_evidence(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    assert bundle.root == tmp_path / "output" / "runs" / str(RUN_ID)
    required = {
        "manifest.json",
        "feedback.parquet",
        "edges.parquet",
        "requests.parquet",
        "resources.parquet",
        "summary.json",
        "report.md",
        "checksums.json",
        "model-manifest.json",
    }
    assert {path.name for path in bundle.root.iterdir()} == required | {"model-artifact"}
    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["runId"] == str(RUN_ID)
    assert manifest["acceptanceLabel"] == "formal"
    assert manifest["progress"]["conditionCount"] == 9
    assert manifest["topology"] == "same_host_split"
    assert manifest["placement"]["trainerPid"] == 42
    assert manifest["hardware"]["cpu"] == "fixture"
    assert [row["role"] for row in manifest["modelLedger"]] == ["original", "child"]
    assert manifest["vectorSnapshots"][0]["dimension"] == 100
    assert manifest["modelArtifact"] == {
        "descriptorPath": "model-artifact/state-descriptor.json",
        "files": [
            "model-artifact/online-state.json",
            "model-artifact/state-descriptor.json",
        ],
    }

    checksums = json.loads(bundle.checksums_path.read_text())
    assert set(checksums) == (required - {"checksums.json"}) | {
        "model-artifact/online-state.json",
        "model-artifact/state-descriptor.json",
    }
    for name, digest in checksums.items():
        assert digest == hashlib.sha256((bundle.root / name).read_bytes()).hexdigest()


def test_run_bundle_rejects_secrets_and_existing_accepted_path(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    sources["report_markdown"].write_text("HF_TOKEN=do-not-publish\n")
    with pytest.raises(SecretBearingFile, match="report"):
        build_run_bundle(
            tmp_path / "output",
            run_id=RUN_ID,
            **sources,
            acceptance_label="smoke",
            progress={"phase": "completed"},
            topology="same_process",
            placement={},
            hardware={},
            model_ledger=[{"role": "original", "immutable": True}],
            vector_snapshots=[{"sha256": "a" * 64, "rows": 1, "dimension": 100}],
        )

    sources["report_markdown"].write_text("# safe\n")
    build_run_bundle(
        tmp_path / "output",
        run_id=RUN_ID,
        **sources,
        acceptance_label="smoke",
        progress={"phase": "completed"},
        topology="same_process",
        placement={},
        hardware={},
        model_ledger=[{"role": "original", "immutable": True}],
        vector_snapshots=[{"sha256": "a" * 64, "rows": 1, "dimension": 100}],
    )
    with pytest.raises(AcceptedRunExists):
        build_run_bundle(
            tmp_path / "output",
            run_id=RUN_ID,
            **sources,
            acceptance_label="smoke",
            progress={"phase": "completed"},
            topology="same_process",
            placement={},
            hardware={},
            model_ledger=[{"role": "original", "immutable": True}],
            vector_snapshots=[{"sha256": "a" * 64, "rows": 1, "dimension": 100}],
        )


class FakeHubApi:
    def __init__(self, remote: Path) -> None:
        self.remote = remote
        self.commits: list[dict[str, object]] = []

    def list_repo_files(self, **_kwargs: object) -> list[str]:
        if not self.remote.exists():
            return []
        return [
            str(path.relative_to(self.remote))
            for path in self.remote.rglob("*")
            if path.is_file()
        ]

    def create_commit(self, **kwargs: object) -> SimpleNamespace:
        self.commits.append(kwargs)
        for operation in kwargs["operations"]:
            destination = self.remote / operation.path_in_repo
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(operation.path_or_fileobj, destination)
        return SimpleNamespace(oid="b" * 40)

    def hf_hub_download(self, *, filename: str, **_kwargs: object) -> str:
        return str(self.remote / filename)


def test_publish_is_one_immutable_commit_then_remotely_reloads_every_artifact(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    api = FakeHubApi(tmp_path / "remote")

    receipt = publish_run_bundle(
        api,
        bundle,
        repo_id="dhelmy990/babel-wikipedia-experiment",
        token="token-is-not-written",
    )

    assert receipt.commit_sha == "b" * 40
    assert receipt.bundle_path == f"runs/{RUN_ID}"
    assert receipt.model_artifact_path == (
        f"runs/{RUN_ID}/model-artifact/state-descriptor.json"
    )
    assert receipt.verified_model_files == {
        "model-artifact/online-state.json": hashlib.sha256(
            (bundle.root / "model-artifact/online-state.json").read_bytes()
        ).hexdigest(),
        "model-artifact/state-descriptor.json": hashlib.sha256(
            (bundle.root / "model-artifact/state-descriptor.json").read_bytes()
        ).hexdigest(),
    }
    assert receipt.verified_parquet_rows == {
        "feedback.parquet": 1,
        "edges.parquet": 1,
        "requests.parquet": 1,
        "resources.parquet": 1,
    }
    assert len(api.commits) == 1
    assert {operation.path_in_repo for operation in api.commits[0]["operations"]} == {
        f"runs/{RUN_ID}/{path.relative_to(bundle.root)}"
        for path in bundle.root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(AcceptedRunExists, match="remote"):
        publish_run_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="token-is-not-written",
        )


def test_remote_reload_rejects_checksum_mismatch(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    api = FakeHubApi(tmp_path / "remote")
    original = api.create_commit

    def corrupting_commit(**kwargs: object) -> SimpleNamespace:
        result = original(**kwargs)
        path = api.remote / f"runs/{RUN_ID}/summary.json"
        path.write_text("{}")
        return result

    api.create_commit = corrupting_commit  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="checksum"):
        publish_run_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="token-is-not-written",
        )


def test_remote_reload_rejects_a_rewritten_checksum_inventory(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    api = FakeHubApi(tmp_path / "remote")
    original = api.create_commit

    def rewriting_commit(**kwargs: object) -> SimpleNamespace:
        result = original(**kwargs)
        prefix = api.remote / f"runs/{RUN_ID}"
        summary = prefix / "summary.json"
        summary.write_text("{}")
        checksums_path = prefix / "checksums.json"
        checksums = json.loads(checksums_path.read_text())
        checksums["summary.json"] = hashlib.sha256(summary.read_bytes()).hexdigest()
        checksums_path.write_text(json.dumps(checksums))
        return result

    api.create_commit = rewriting_commit  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="inventory"):
        publish_run_bundle(
            api,
            bundle,
            repo_id="dhelmy990/babel-wikipedia-experiment",
            token="token-is-not-written",
        )
