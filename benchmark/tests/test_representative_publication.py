from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from babel_benchmark import representative_publication
from babel_benchmark.cli import _parser, main
from babel_benchmark.hub import AcceptedRunExists
from babel_benchmark.representative_publication import (
    build_representative_run_bundle,
    publish_representative_run_bundle,
)

TRIAL_ID = UUID("00000000-0000-5000-8000-000000000130")
SCOPE = "representative_same_process_vs_split"
ISOLATED_SCOPE = "representative_isolated_smoke"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    export = tmp_path / "feedback-export"
    export.mkdir()
    feedback = export / "feedback.parquet"
    edges = export / "edges.parquet"
    pq.write_table(pa.table({"event": ["a", "b"]}), feedback)
    pq.write_table(pa.table({"source": ["a", "b", "c"]}), edges)
    (export / "feedback.jsonl").write_text('{"event":"a"}\n{"event":"b"}\n')
    (export / "edges.jsonl").write_text(
        '{"source":"a"}\n{"source":"b"}\n{"source":"c"}\n'
    )
    conditions = []
    evidence_root = tmp_path / "conditions"
    for index in range(1, 7):
        run_id = UUID(f"00000000-0000-5000-8000-{index:012d}")
        conditions.append({"conditionId": str(run_id), "runId": str(run_id)})
        condition_root = evidence_root / f"{index:02d}"
        condition_root.mkdir(parents=True)
        condition = {
            "conditionId": str(run_id),
            "runId": str(run_id),
            "requestCount": 2,
            "p95Ms": float(index),
            "rawEvidence": {
                "evidenceScope": SCOPE,
                "conditionIdentity": {
                    "topology": "same_process" if index <= 3 else "same_host_split",
                    "trainingEnabled": index % 3 != 1,
                    "activationEnabled": index % 3 == 0,
                    "retrievalBackend": "pgvector",
                },
                "measurements": [
                    {
                        "modelId": "00000000-0000-5000-8000-000000000099",
                        "outcome": "success",
                        "isWarmup": False,
                    },
                    {
                        "modelId": "00000000-0000-5000-8000-000000000099",
                        "outcome": "success",
                        "isWarmup": False,
                    },
                ],
                "finalServingIdentity": {
                    "modelId": f"00000000-0000-5000-8000-{100 + index:012d}",
                    "modelVersion": index,
                    "embeddingSpaceId": "00000000-0000-5000-8000-000000000098",
                    "pgvectorSnapshotSha256": "a" * 64,
                    "backendSnapshotSha256": "a" * 64,
                },
                "observedActivationTargets": [
                    ["00000000-0000-5000-8000-000000000099", 0, "b" * 64, "b" * 64]
                ],
                "feedbackKafka": {
                    "recordCount": 2,
                    "finalTrainerState": {
                        "available": True,
                        "kafkaLag": 0,
                        "offsetsCoverPublishedRanges": index % 3 != 1,
                    },
                },
            },
        }
        (condition_root / "live-evidence.json").write_text(
            json.dumps(condition, sort_keys=True) + "\n"
        )
    manifest = {
        "schemaVersion": 1,
        "experimentId": str(TRIAL_ID),
        "creatorCohort": 50,
        "conditionCount": 6,
        "conditions": conditions,
        "records": 2,
        "canonicalEdges": 3,
        "feedbackParquet": {
            "path": "feedback.parquet",
            "rows": 2,
            "sha256": _sha256(feedback),
        },
        "edgesParquet": {
            "path": "edges.parquet",
            "rows": 3,
            "sha256": _sha256(edges),
        },
        "parquetSha256": _sha256(feedback),
        "edgeParquetSha256": _sha256(edges),
        "jsonlSha256": _sha256(export / "feedback.jsonl"),
        "edgeJsonlSha256": _sha256(export / "edges.jsonl"),
        "evidenceScope": SCOPE,
        "formalPerformanceClaim": False,
    }
    (export / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    report = tmp_path / "report.md"
    report.write_text("# Representative performance report\n\nNot a formal claim.\n")
    return export, evidence_root, report


def _write_isolated_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    export, evidence_root, report = _write_inputs(tmp_path)
    manifest_path = export / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["evidenceScope"] = ISOLATED_SCOPE
    manifest["conditionCount"] = 3
    manifest["conditions"] = [
        {
            **binding,
            "conditionIndex": index,
            "formalConditionIndex": index + 6,
        }
        for index, binding in enumerate(manifest["conditions"][:3], start=1)
    ]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    for index in range(1, 4):
        evidence_path = evidence_root / f"{index:02d}" / "live-evidence.json"
        document = json.loads(evidence_path.read_text())
        raw = document["rawEvidence"]
        raw["evidenceScope"] = ISOLATED_SCOPE
        raw["conditionIdentity"]["topology"] = "same_host_isolated"
        evidence_path.write_text(json.dumps(document, sort_keys=True) + "\n")
    for index in range(4, 7):
        shutil.rmtree(evidence_root / f"{index:02d}")
    return export, evidence_root, report


def test_build_and_publish_closed_representative_bundle(tmp_path: Path) -> None:
    export, evidence, report = _write_inputs(tmp_path)
    formal = tmp_path / "accepted" / "runs" / str(TRIAL_ID) / "manifest.json"
    formal.parent.mkdir(parents=True)
    formal.write_text("formal remains untouched\n")

    bundle = build_representative_run_bundle(
        tmp_path / "accepted",
        trial_id=TRIAL_ID,
        export_root=export,
        evidence_root=evidence,
        report_path=report,
    )

    assert bundle.root.parent == tmp_path / "accepted/representative-runs" / str(
        TRIAL_ID
    )
    assert bundle.root.name == bundle.artifact_sha256
    assert formal.read_text() == "formal remains untouched\n"
    assert {
        path.relative_to(bundle.root).as_posix()
        for path in bundle.root.rglob("*")
        if path.is_file()
    } == {
        "checksums.json",
        "conditions/01/live-evidence.json",
        "conditions/02/live-evidence.json",
        "conditions/03/live-evidence.json",
        "conditions/04/live-evidence.json",
        "conditions/05/live-evidence.json",
        "conditions/06/live-evidence.json",
        "export/edges.jsonl",
        "export/edges.parquet",
        "export/feedback.jsonl",
        "export/feedback.parquet",
        "export/manifest.json",
        "manifest.json",
        "model-lineage.json",
        "report.md",
        "trial-results.json",
        "trial-summary.json",
    }
    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["namespace"] == "representative-runs"
    assert manifest["formalPerformanceClaim"] is False
    assert manifest["evidenceScope"] == SCOPE
    assert (
        json.loads((bundle.root / "trial-summary.json").read_text())["feedbackRows"]
        == 2
    )
    results = json.loads((bundle.root / "trial-results.json").read_text())
    assert len(results["conditions"]) == 6
    assert [row["formalConditionIndex"] for row in results["conditions"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert json.loads((bundle.root / "model-lineage.json").read_text())[
        "sourceModelIds"
    ] == ["00000000-0000-5000-8000-000000000099"]

    api = FakeHubApi(tmp_path / "remote")
    receipt = publish_representative_run_bundle(
        api,
        bundle,
        repo_id="owner/private-dataset",
        token="secret-token-never-written",
    )

    assert receipt.bundle_path == (
        f"representative-runs/{TRIAL_ID}/{bundle.artifact_sha256}"
    )
    assert receipt.commit_sha == "c" * 40
    assert receipt.verified_files["checksums.json"] == bundle.artifact_sha256
    assert not any(
        path.startswith(f"runs/{TRIAL_ID}/") for path in api.list_repo_files()
    )
    with pytest.raises(AcceptedRunExists, match="representative run"):
        publish_representative_run_bundle(
            api,
            bundle,
            repo_id="owner/private-dataset",
            token="secret-token-never-written",
        )


def test_build_isolated_bundle_closes_three_conditions_at_formal_positions(
    tmp_path: Path,
) -> None:
    export, evidence, report = _write_isolated_inputs(tmp_path)

    bundle = build_representative_run_bundle(
        tmp_path / "accepted",
        trial_id=TRIAL_ID,
        export_root=export,
        evidence_root=evidence,
        report_path=report,
    )

    condition_files = {
        path.relative_to(bundle.root).as_posix()
        for path in (bundle.root / "conditions").rglob("*")
        if path.is_file()
    }
    assert condition_files == {
        "conditions/01/live-evidence.json",
        "conditions/02/live-evidence.json",
        "conditions/03/live-evidence.json",
    }
    manifest = json.loads(bundle.manifest_path.read_text())
    summary = json.loads((bundle.root / "trial-summary.json").read_text())
    results = json.loads((bundle.root / "trial-results.json").read_text())
    assert manifest["evidenceScope"] == ISOLATED_SCOPE
    assert manifest["formalPerformanceClaim"] is False
    assert summary["conditionCount"] == 3
    assert [row["conditionIndex"] for row in results["conditions"]] == [1, 2, 3]
    assert [row["formalConditionIndex"] for row in results["conditions"]] == [
        7,
        8,
        9,
    ]


def test_build_isolated_bundle_rejects_fourth_evidence_file(tmp_path: Path) -> None:
    export, evidence, report = _write_isolated_inputs(tmp_path)
    fourth = evidence / "04/live-evidence.json"
    fourth.parent.mkdir()
    shutil.copyfile(evidence / "03/live-evidence.json", fourth)

    with pytest.raises(ValueError, match="inventory"):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


def test_build_isolated_bundle_rejects_unpadded_condition_directories(
    tmp_path: Path,
) -> None:
    export, evidence, report = _write_isolated_inputs(tmp_path)
    for index in range(1, 4):
        shutil.move(evidence / f"{index:02d}", evidence / str(index))

    with pytest.raises(ValueError, match="zero-padded"):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


@pytest.mark.parametrize("condition_index", (2, 3))
def test_build_isolated_bundle_rejects_nonzero_training_kafka_lag(
    tmp_path: Path, condition_index: int
) -> None:
    export, evidence, report = _write_isolated_inputs(tmp_path)
    evidence_path = evidence / f"{condition_index:02d}/live-evidence.json"
    document = json.loads(evidence_path.read_text())
    document["rawEvidence"]["feedbackKafka"]["finalTrainerState"]["kafkaLag"] = 1
    evidence_path.write_text(json.dumps(document) + "\n")

    with pytest.raises(ValueError, match="zero final Kafka lag"):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


def test_build_isolated_bundle_rejects_same_host_split_identity_drift(
    tmp_path: Path,
) -> None:
    export, evidence, report = _write_isolated_inputs(tmp_path)
    evidence_path = evidence / "01/live-evidence.json"
    document = json.loads(evidence_path.read_text())
    document["rawEvidence"]["conditionIdentity"]["topology"] = "same_host_split"
    evidence_path.write_text(json.dumps(document) + "\n")

    with pytest.raises(ValueError, match="ordered.*matrix"):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


@pytest.mark.parametrize("formal_claim", (True, 0))
def test_build_requires_formal_claim_to_be_the_exact_false_value(
    tmp_path: Path, formal_claim: object
) -> None:
    export, evidence, report = _write_inputs(tmp_path)
    manifest_path = export / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["formalPerformanceClaim"] = formal_claim
    manifest_path.write_text(json.dumps(manifest) + "\n")

    with pytest.raises(ValueError, match="exactly false"):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda manifest: manifest.__setitem__("experimentId", str(UUID(int=999))),
            "trial",
        ),
        (lambda manifest: manifest.__setitem__("evidenceScope", "formal"), "scope"),
        (
            lambda manifest: manifest["feedbackParquet"].__setitem__(
                "sha256", "f" * 64
            ),
            "checksum",
        ),
        (lambda manifest: manifest["edgesParquet"].__setitem__("rows", 99), "rows"),
    ),
)
def test_build_rejects_trial_scope_or_parquet_drift(
    tmp_path: Path, mutation, message: str
) -> None:
    export, evidence, report = _write_inputs(tmp_path)
    manifest_path = export / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    mutation(manifest)
    manifest_path.write_text(json.dumps(manifest) + "\n")

    with pytest.raises(ValueError, match=message):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence,
            report_path=report,
        )


@pytest.mark.parametrize(
    ("condition_index", "mutation", "message"),
    (
        (
            1,
            lambda evidence: evidence["rawEvidence"]["conditionIdentity"].__setitem__(
                "topology", "same_host_split"
            ),
            "ordered 2x3",
        ),
        (
            2,
            lambda evidence: evidence["rawEvidence"]["feedbackKafka"][
                "finalTrainerState"
            ].__setitem__("kafkaLag", 1),
            "zero final Kafka lag",
        ),
        (
            3,
            lambda evidence: evidence["rawEvidence"]["measurements"][0].__setitem__(
                "outcome", "timeout"
            ),
            "complete successfully",
        ),
    ),
)
def test_build_rejects_incomplete_or_misordered_condition_evidence(
    tmp_path: Path, condition_index: int, mutation, message: str
) -> None:
    export, evidence_root, report = _write_inputs(tmp_path)
    evidence_path = evidence_root / f"{condition_index:02d}" / "live-evidence.json"
    evidence = json.loads(evidence_path.read_text())
    mutation(evidence)
    evidence_path.write_text(json.dumps(evidence) + "\n")

    with pytest.raises(ValueError, match=message):
        build_representative_run_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            export_root=export,
            evidence_root=evidence_root,
            report_path=report,
        )


def test_publish_rejects_formal_namespace_confusion(tmp_path: Path) -> None:
    export, evidence, report = _write_inputs(tmp_path)
    bundle = build_representative_run_bundle(
        tmp_path / "accepted",
        trial_id=TRIAL_ID,
        export_root=export,
        evidence_root=evidence,
        report_path=report,
    )
    confused = tmp_path / "accepted/runs" / str(TRIAL_ID) / bundle.root.name
    confused.parent.mkdir(parents=True)
    shutil.copytree(bundle.root, confused)
    relocated = replace(
        bundle,
        root=confused,
        manifest_path=confused / "manifest.json",
        checksums_path=confused / "checksums.json",
    )

    with pytest.raises(ValueError, match="representative-runs"):
        publish_representative_run_bundle(
            FakeHubApi(tmp_path / "remote"),
            relocated,
            repo_id="owner/private-dataset",
            token="secret-token-never-written",
        )


def test_publish_cli_receipt_is_secret_safe(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    token = "private-token-must-not-appear"
    monkeypatch.setenv("REPRESENTATIVE_HF_TOKEN", token)
    root = tmp_path / "accepted/representative-runs" / str(TRIAL_ID) / ("a" * 64)
    bundle = SimpleNamespace(root=root)
    receipt = SimpleNamespace(
        repository="owner/private-dataset",
        commit_sha="b" * 40,
        bundle_path=f"representative-runs/{TRIAL_ID}/{'a' * 64}",
        artifact_sha256="a" * 64,
        trial_id=TRIAL_ID,
        evidence_scope=SCOPE,
        verified_files={"manifest.json": "d" * 64, "checksums.json": "a" * 64},
    )
    calls: dict[str, object] = {}
    monkeypatch.setattr("huggingface_hub.HfApi", lambda: "api")

    def load(bundle_root, *, trial_id):
        calls["load"] = (bundle_root, trial_id)
        return bundle

    def publish(api, given_bundle, **kwargs):
        calls["publish"] = (api, given_bundle, kwargs)
        return receipt

    monkeypatch.setattr(
        representative_publication, "load_representative_run_bundle", load
    )
    monkeypatch.setattr(
        representative_publication, "publish_representative_run_bundle", publish
    )

    assert (
        main(
            [
                "representative-run-publish",
                "--trial-id",
                str(TRIAL_ID),
                "--bundle-root",
                str(root),
                "--repo-id",
                "owner/private-dataset",
                "--token-env",
                "REPRESENTATIVE_HF_TOKEN",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["bundlePath"].startswith("representative-runs/")
    assert document["formalPerformanceClaim"] is False
    assert token not in output
    assert calls["publish"][-1]["token"] == token


def test_representative_cli_exposes_exact_build_and_publish_inputs() -> None:
    build = _parser().parse_args(
        [
            "representative-run-build",
            "--trial-id",
            str(TRIAL_ID),
            "--export-root",
            "feedback-export",
            "--evidence-root",
            "conditions",
            "--report",
            "report.md",
            "--output-root",
            "accepted",
        ]
    )
    publish = _parser().parse_args(
        [
            "representative-run-publish",
            "--trial-id",
            str(TRIAL_ID),
            "--bundle-root",
            "accepted/representative-runs/id/digest",
            "--repo-id",
            "owner/private-dataset",
        ]
    )

    assert build.command == "representative-run-build"
    assert build.report == Path("report.md")
    assert publish.command == "representative-run-publish"
    assert publish.revision == "main"
    assert publish.token_env == "HF_TOKEN"


class FakeHubApi:
    def __init__(self, remote: Path) -> None:
        self.remote = remote
        self.commits: list[dict[str, object]] = []

    def dataset_info(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(private=True)

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
