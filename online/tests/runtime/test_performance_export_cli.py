from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from babel_online.runtime import cli as runtime_cli


EXPERIMENT_ID = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")


def test_performance_export_cli_uses_kafka_closes_and_prints_canonical_receipt(
    monkeypatch, tmp_path, capsys
) -> None:
    database_url = "postgresql://operator:secret@127.0.0.1/babel"
    kafka = "127.0.0.1:29092"
    monkeypatch.setenv("BABEL_DATABASE_URL", database_url)
    monkeypatch.setenv("BABEL_KAFKA_BOOTSTRAP_SERVERS", kafka)
    constructed = {}

    class Consumer:
        def __init__(self, bootstrap_servers, *, group_id):
            constructed["consumer"] = (bootstrap_servers, group_id)
            self.closed = False

        def close(self):
            self.closed = True
            constructed["closed"] = True

    database = object()
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda value: database)
    monkeypatch.setattr(runtime_cli, "KafkaFeedbackConsumer", Consumer, raising=False)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": 9, "canonicalEdges": 7}))
    result = SimpleNamespace(
        manifest_path=manifest,
        parquet_path=tmp_path / "feedback.parquet",
        edge_parquet_path=tmp_path / "edges.parquet",
    )

    def export(**keywords):
        constructed["export"] = keywords
        return result

    monkeypatch.setattr(
        runtime_cli, "export_completed_performance_trial", export, raising=False
    )

    assert runtime_cli.main(
        [
            "performance-export",
            "--experiment-id",
            str(EXPERIMENT_ID),
            "--evidence-root",
            str(tmp_path / "conditions"),
            "--output-root",
            str(tmp_path / "result"),
        ]
    ) == 0

    assert constructed["consumer"] == (
        kafka,
        f"babel-performance-export-{EXPERIMENT_ID}",
    )
    assert constructed["closed"] is True
    assert constructed["export"] == {
        "database": database,
        "experiment_id": EXPERIMENT_ID,
        "evidence_root": tmp_path / "conditions",
        "output_root": tmp_path / "result",
        "feedback_source": constructed["export"]["feedback_source"],
    }
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {
        "canonicalEdges": 7,
        "edgesParquet": str((tmp_path / "edges.parquet").resolve()),
        "experimentId": str(EXPERIMENT_ID),
        "feedbackParquet": str((tmp_path / "feedback.parquet").resolve()),
        "feedbackExportManifest": str(manifest.resolve()),
        "feedbackRecords": 9,
    }
    assert "secret" not in json.dumps(receipt)


def test_performance_export_cli_closes_consumer_when_export_fails(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("BABEL_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    closed = []
    consumer = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda _value: object())
    monkeypatch.setattr(
        runtime_cli,
        "KafkaFeedbackConsumer",
        lambda *_args, **_kwargs: consumer,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_cli,
        "export_completed_performance_trial",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("replay failed")),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="replay failed"):
        runtime_cli.main(
            [
                "performance-export",
                "--experiment-id",
                str(EXPERIMENT_ID),
                "--evidence-root",
                str(tmp_path / "conditions"),
                "--output-root",
                str(tmp_path / "result"),
                "--kafka-group",
                "operator-retry-1",
            ]
        )

    assert closed == [True]


def test_performance_export_cli_generates_default_condition_six_bundle_inputs(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("BABEL_DATABASE_URL", "postgresql://unused")
    consumer = SimpleNamespace(close=lambda: None)
    database = object()
    monkeypatch.setattr(runtime_cli, "RuntimeDatabase", lambda _value: database)
    monkeypatch.setattr(
        runtime_cli,
        "KafkaFeedbackConsumer",
        lambda *_args, **_kwargs: consumer,
        raising=False,
    )
    export_root = tmp_path / "export/feedback-export"
    export_root.mkdir(parents=True)
    manifest = export_root / "manifest.json"
    manifest.write_text(json.dumps({"records": 9, "canonicalEdges": 7}))
    result = SimpleNamespace(
        manifest_path=manifest,
        parquet_path=export_root / "feedback.parquet",
        edge_parquet_path=export_root / "edges.parquet",
    )
    monkeypatch.setattr(
        runtime_cli,
        "export_completed_performance_trial",
        lambda **_kwargs: result,
        raising=False,
    )
    calls = []
    generated = tmp_path / "handoff/trial-bundle-inputs.json"

    def generate(**kwargs):
        calls.append(kwargs)
        generated.parent.mkdir(parents=True)
        generated.write_text("{}")
        return generated

    monkeypatch.setattr(
        runtime_cli, "write_trial_bundle_inputs", generate, raising=False
    )

    assert runtime_cli.main(
        [
            "performance-export",
            "--experiment-id",
            str(EXPERIMENT_ID),
            "--evidence-root",
            str(tmp_path / "conditions"),
            "--output-root",
            str(tmp_path / "export"),
            "--bundle-inputs",
            str(generated),
        ]
    ) == 0

    assert calls == [
        {
            "database": database,
            "experiment_id": EXPERIMENT_ID,
            "evidence_root": tmp_path / "conditions",
            "feedback_parquet": result.parquet_path,
            "edges_parquet": result.edge_parquet_path,
            "feedback_export_manifest": result.manifest_path,
            "selected_condition_index": 6,
            "output_path": generated,
        }
    ]
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["bundleInputs"] == str(generated.resolve())
    assert receipt["selectedConditionIndex"] == 6
