from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from babel_online.runtime import cli as runtime_cli


def test_cli_prepares_default_bounded_2x3_rerun(monkeypatch, tmp_path: Path, capsys):
    source_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    rerun_id = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    calls = []
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda dsn: ("database", dsn))

    def create(**values):
        calls.append(values)
        return SimpleNamespace(
            rerun_id=rerun_id,
            source_trial_id=source_id,
            evidence_scope="representative_same_process_vs_split",
            population_manifest_sha256="a" * 64,
            workload_identity=("b" * 64,) * 6,
            request_limit=150,
            warmup_seconds=5,
            duration_seconds=25,
            target_rps=5.0,
        )

    monkeypatch.setattr(
        "babel_online.runtime.performance_rerun.create_representative_rerun", create
    )

    assert runtime_cli.main(
        [
            "performance-rerun-create",
            "--source-trial-id",
            str(source_id),
            "--state-root",
            str(tmp_path),
            "--nonce",
            "interview-rerun-1",
        ]
    ) == 0

    assert calls[0]["database"] == ("database", "postgresql://unused")
    assert calls[0]["evidence_scope"] == "representative_same_process_vs_split"
    assert calls[0]["warmup_seconds"] == 5
    assert calls[0]["duration_seconds"] == 25
    assert calls[0]["target_rps"] == 5.0
    output = json.loads(capsys.readouterr().out)
    assert output["trialId"] == str(rerun_id)
    assert output["conditionCount"] == 6
    assert output["formalPerformanceClaim"] is False
    assert output["operatorApprovalRequired"] is True


def test_cli_labels_optional_split_only_smoke(monkeypatch, tmp_path: Path, capsys):
    source_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    rerun_id = UUID("bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb")
    calls = []
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda _dsn: object())
    monkeypatch.setattr(
        "babel_online.runtime.performance_rerun.create_representative_rerun",
        lambda **values: calls.append(values)
        or SimpleNamespace(
            rerun_id=rerun_id,
            source_trial_id=source_id,
            evidence_scope="representative_split_smoke",
            population_manifest_sha256="a" * 64,
            workload_identity=("b" * 64,) * 6,
            request_limit=10,
            warmup_seconds=0,
            duration_seconds=2,
            target_rps=5.0,
        ),
    )

    runtime_cli.main(
        [
            "performance-rerun-create",
            "--source-trial-id",
            str(source_id),
            "--state-root",
            str(tmp_path),
            "--nonce",
            "smoke-1",
            "--matrix",
            "split-smoke",
            "--warmup-seconds",
            "0",
            "--duration-seconds",
            "2",
        ]
    )

    assert calls[0]["evidence_scope"] == "representative_split_smoke"
    assert json.loads(capsys.readouterr().out)["conditionCount"] == 3


def test_cli_exports_representative_results_with_non_formal_receipt(
    monkeypatch, tmp_path: Path, capsys
):
    trial_id = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")
    output = tmp_path / "export"
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "records": 6,
                "canonicalEdges": 4,
                "evidenceScope": "representative_same_process_vs_split",
                "formalPerformanceClaim": False,
            }
        )
    )
    feedback, edges = output / "feedback.parquet", output / "edges.parquet"
    feedback.write_bytes(b"feedback")
    edges.write_bytes(b"edges")
    calls = []

    class Consumer:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda _dsn: object())
    monkeypatch.setattr(runtime_cli, "KafkaFeedbackConsumer", Consumer)
    monkeypatch.setattr(
        runtime_cli,
        "export_completed_representative_trial",
        lambda **values: calls.append(values)
        or SimpleNamespace(
            manifest_path=manifest, parquet_path=feedback, edge_parquet_path=edges
        ),
    )

    runtime_cli.main(
        [
            "performance-export",
            "--representative",
            "--experiment-id",
            str(trial_id),
            "--evidence-root",
            str(tmp_path / "conditions"),
            "--output-root",
            str(output),
        ]
    )

    assert calls[0]["experiment_id"] == trial_id
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["evidenceScope"] == "representative_same_process_vs_split"
    assert receipt["formalPerformanceClaim"] is False
