from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import babel_benchmark.trial_bundle as trial_bundle
import pytest

from babel_benchmark.cli import (
    _attach_backend_artifact_receipt,
    _parser,
    _publication_receipt_json,
    main,
)
from babel_benchmark.contracts import RequestMeasurementV1, load_jsonl
from babel_benchmark.hub import RunBundleReceipt
from babel_benchmark.resources import ResourceObservationV1
from babel_benchmark.trial_bundle import FormalPins


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "performance"


class RecommendationHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["content-length"])
        request = json.loads(self.rfile.read(length))
        candidate_number = 203 if request["creatorId"].endswith("102") else 202
        creator_number = 103 if candidate_number == 203 else 102
        body = json.dumps(
            {
                "schemaVersion": 1,
                "requestId": request["requestId"],
                "runId": request["runId"],
                "modelId": "00000000-0000-5000-8000-000000000002",
                "modelVersion": 0,
                "retrievalBackend": "pgvector",
                "embeddingSpaceId": "00000000-0000-5000-8000-000000000003",
                "pgvectorSnapshotSha256": "a" * 64,
                "backendSnapshotSha256": "a" * 64,
                "queryVectorSha256": "b" * 64,
                "candidates": [
                    {
                        "babelId": f"00000000-0000-5000-8000-{candidate_number:012d}",
                        "creatorId": f"00000000-0000-5000-8000-{creator_number:012d}",
                        "sourceArticleKey": "enwiki:2032",
                        "rank": 1,
                        "modelScore": 0.5,
                    }
                ],
                "timingsNs": {
                    "queue": 1,
                    "encode": 1,
                    "context": 1,
                    "ann": 1,
                    "filtering": 1,
                    "serialization": 1,
                    "serverTotal": 1000,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return None


def test_replay_command_wraps_the_real_loopback_http_post(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecommendationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        manifest = json.loads((FIXTURES / "manifest.json").read_text())
        manifest["endpoint"] = f"http://127.0.0.1:{server.server_port}"
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        output = tmp_path / "serving.jsonl"

        assert (
            main(
                [
                    "replay",
                    "--manifest",
                    str(manifest_path),
                    "--requests",
                    str(FIXTURES / "requests.jsonl"),
                    "--candidate-universe",
                    str(FIXTURES / "created-babels.jsonl"),
                    "--condition",
                    "pgvector_serving_only",
                    "--measurements",
                    str(output),
                ]
            )
            == 0
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    rows = load_jsonl(output, RequestMeasurementV1)
    assert len(rows) == 6
    assert all(row.outcome == "success" for row in rows)
    assert all(row.clientTotalNs >= row.serverTimingsNs["serverTotal"] for row in rows)


def test_live_replay_cli_exposes_real_trainer_and_sync_inputs() -> None:
    args = _parser().parse_args(
        [
            "live-replay",
            "--manifest",
            "manifest.json",
            "--requests",
            "requests.jsonl",
            "--candidate-universe",
            "created.jsonl",
            "--condition",
            "pgvector_training_and_sync",
            "--measurements",
            "measurements.jsonl",
            "--telemetry",
            "telemetry.jsonl",
            "--dsn",
            "postgresql://localhost/babel",
            "--kafka-bootstrap",
            "127.0.0.1:29092",
            "--feedback",
            "feedback.jsonl",
            "--run-id",
            "00000000-0000-5000-8000-000000000001",
            "--model-version",
            "48",
            "--sync-root",
            "sync",
        ]
    )

    assert args.command == "live-replay"
    assert args.publish_limit == 4000
    assert args.sync_every_steps == 50


def test_trial_bundle_cli_closes_build_and_publish_inputs() -> None:
    build = _parser().parse_args(
        [
            "trial-bundle-build",
            "--output-root",
            "accepted",
            "--trial-id",
            "00000000-0000-5000-8000-000000000130",
            "--evidence",
            *[f"condition-{index}.json" for index in range(1, 10)],
            "--population-manifest",
            "population.json",
            "--feedback-parquet",
            "feedback.parquet",
            "--edges-parquet",
            "edges.parquet",
            "--feedback-export-manifest",
            "feedback-export-manifest.json",
            "--model-manifest",
            "model-manifest.json",
            "--model-artifact-root",
            "model-artifact",
            "--selected-child",
            "selected-child.json",
            "--model-repository",
            "owner/model",
            "--model-revision",
            "a" * 40,
            "--dataset-repository",
            "owner/dataset",
            "--dataset-revision",
            "b" * 40,
        ]
    )
    publish = _parser().parse_args(
        [
            "trial-bundle-publish",
            "--bundle-root",
            "accepted/runs/00000000-0000-5000-8000-000000000130",
            "--repo-id",
            "owner/dataset",
        ]
    )

    assert build.command == "trial-bundle-build"
    assert len(build.evidence) == 9
    assert build.feedback_export_manifest == Path("feedback-export-manifest.json")
    assert publish.command == "trial-bundle-publish"
    assert publish.token_env == "HF_TOKEN"


def test_trial_bundle_build_accepts_one_generated_input_manifest(
    monkeypatch, tmp_path, capsys
) -> None:
    trial_id = UUID("00000000-0000-5000-8000-000000000130")
    inputs_path = tmp_path / "trial-bundle-inputs.json"
    inputs_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "trialId": str(trial_id),
                "selectedConditionIndex": 6,
                "evidencePaths": [
                    str(tmp_path / f"condition-{index}.json")
                    for index in range(1, 10)
                ],
                "populationManifest": str(tmp_path / "population.json"),
                "feedbackParquet": str(tmp_path / "feedback.parquet"),
                "edgesParquet": str(tmp_path / "edges.parquet"),
                "feedbackExportManifest": str(tmp_path / "feedback-manifest.json"),
                "modelManifest": str(tmp_path / "model-manifest.json"),
                "modelArtifactRoot": str(tmp_path / "model-artifact"),
                "selectedChild": str(tmp_path / "selected-child.json"),
                "pins": {
                    "modelRepository": "owner/model",
                    "modelRevision": "a" * 40,
                    "datasetRepository": "owner/dataset",
                    "datasetRevision": "b" * 40,
                },
            }
        )
    )
    calls = []

    def build(output_root, **kwargs):
        calls.append((output_root, kwargs))
        root = tmp_path / "accepted/runs" / str(trial_id)
        return SimpleNamespace(
            run_id=trial_id,
            root=root,
            manifest_path=root / "manifest.json",
            checksums_path=root / "checksums.json",
        )

    monkeypatch.setattr(trial_bundle, "build_formal_trial_bundle", build)

    assert main(
        [
            "trial-bundle-build",
            "--output-root",
            str(tmp_path / "accepted"),
            "--inputs",
            str(inputs_path),
        ]
    ) == 0

    assert calls == [
        (
            tmp_path / "accepted",
            {
                "trial_id": trial_id,
                "evidence_paths": tuple(
                    tmp_path / f"condition-{index}.json" for index in range(1, 10)
                ),
                "population_manifest_path": tmp_path / "population.json",
                "feedback_parquet": tmp_path / "feedback.parquet",
                "edges_parquet": tmp_path / "edges.parquet",
                "feedback_export_manifest_path": tmp_path / "feedback-manifest.json",
                "model_manifest": tmp_path / "model-manifest.json",
                "model_artifact_root": tmp_path / "model-artifact",
                "selected_child_path": tmp_path / "selected-child.json",
                "pins": FormalPins(
                    "owner/model", "a" * 40, "owner/dataset", "b" * 40
                ),
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["runId"] == str(trial_id)


def test_trial_bundle_publish_retains_full_receipt_and_adds_backend_payload() -> None:
    receipt = RunBundleReceipt(
        repository="owner/dataset",
        commit_sha="b" * 40,
        bundle_path="runs/00000000-0000-5000-8000-000000000130",
        artifact_sha256="a" * 64,
        verified_parquet_rows={"requests.parquet": 1},
        model_artifact_path="runs/trial/model-artifact/state-descriptor.json",
        verified_model_files={"weights.safetensors": "c" * 64},
    )

    published = _publication_receipt_json(receipt)

    assert published["repository"] == "owner/dataset"
    assert published["verified_parquet_rows"] == {"requests.parquet": 1}
    assert published["backendArtifactReceipt"] == {
        "artifactSha256": "a" * 64,
        "remoteHfCommitSha": "b" * 40,
        "remoteHfBundlePath": "runs/00000000-0000-5000-8000-000000000130",
    }


def test_trial_bundle_attach_posts_backend_payload_with_loopback_admin_headers() -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "trial": {
                    "experimentId": "00000000-0000-5000-8000-000000000130"
                }
            }

    class Transport:
        def __init__(self) -> None:
            self.request_args: tuple[object, ...] | None = None
            self.request_kwargs: dict[str, object] | None = None

        def request(self, *args: object, **kwargs: object) -> Response:
            self.request_args = args
            self.request_kwargs = kwargs
            return Response()

    transport = Transport()
    payload = {
        "artifactSha256": "a" * 64,
        "remoteHfCommitSha": "b" * 40,
        "remoteHfBundlePath": "runs/00000000-0000-5000-8000-000000000130",
    }

    attached = _attach_backend_artifact_receipt(
        base_url="http://127.0.0.1:8787",
        trial_id=UUID("00000000-0000-5000-8000-000000000130"),
        receipt=payload,
        admin_nonce="nonce",
        transport=transport,
    )

    assert attached == Response.json()
    assert transport.request_args == (
        "POST",
        "http://127.0.0.1:8787/admin/api/v1/performance/"
        "00000000-0000-5000-8000-000000000130/artifact",
    )
    assert transport.request_kwargs == {
        "headers": {
            "Origin": "http://127.0.0.1:8787",
            "X-Babel-Admin-Nonce": "nonce",
        },
        "json": payload,
        "timeout": 10.0,
    }


def test_trial_bundle_attach_rejects_receipt_for_another_trial() -> None:
    with pytest.raises(ValueError, match="bundle path differs from trial"):
        _attach_backend_artifact_receipt(
            base_url="http://127.0.0.1:8787",
            trial_id=UUID("00000000-0000-5000-8000-000000000130"),
            receipt={
                "artifactSha256": "a" * 64,
                "remoteHfCommitSha": "b" * 40,
                "remoteHfBundlePath": "runs/00000000-0000-5000-8000-000000000999",
            },
            admin_nonce="nonce",
        )


def test_trial_bundle_attach_parser_requires_saved_receipt_and_trial_identity() -> None:
    attach = _parser().parse_args(
        [
            "trial-bundle-attach",
            "--receipt",
            "receipt.json",
            "--trial-id",
            "00000000-0000-5000-8000-000000000130",
        ]
    )

    assert attach.command == "trial-bundle-attach"
    assert attach.base_url == "http://127.0.0.1:8787"
    assert attach.nonce_env == "BABEL_ADMIN_NONCE"


def test_v2_concurrent_replays_feed_the_three_ratio_report(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecommendationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        legacy = json.loads((FIXTURES / "manifest.json").read_text())
        conditions = []
        identities = (
            (False, False),
            (True, False),
            (True, True),
        )
        for source, (training, activation) in zip(
            legacy["conditions"], identities, strict=True
        ):
            conditions.append(
                {
                    "identity": {
                        "topology": "same_process",
                        "trainingEnabled": training,
                        "activationEnabled": activation,
                        "retrievalBackend": "pgvector",
                    },
                    "requestCorpusSha256": legacy["requestCorpusSha256"],
                    "scheduleOffsetsNs": legacy["scheduleOffsetsNs"],
                    "expectedModelId": source["expectedModelId"],
                    "expectedEmbeddingSpaceId": source["expectedEmbeddingSpaceId"],
                    "expectedDatasetSnapshotSha256": legacy["candidateUniverseSha256"],
                    "expectedPgvectorSnapshotSha256": "a" * 64,
                    "expectedBackendSnapshotSha256": "a" * 64,
                    "activationTargets": (
                        [
                            {
                                "modelId": "00000000-0000-5000-8000-000000000099",
                                "parentModelId": source["expectedModelId"],
                                "modelVersion": 1,
                                "pgvectorSnapshotSha256": "c" * 64,
                                "backendSnapshotSha256": "c" * 64,
                            }
                        ]
                        if activation
                        else []
                    ),
                }
            )
        manifest = {
            **{key: value for key, value in legacy.items() if key != "conditions"},
            "schemaVersion": 2,
            "endpoint": f"http://127.0.0.1:{server.server_port}",
            "scheduleMode": "open_loop",
            "maxInFlight": 2,
            "conditions": conditions,
        }
        manifest_path = tmp_path / "manifest-v2.json"
        manifest_path.write_text(json.dumps(manifest))
        outputs = []
        resource_output = tmp_path / "resources.jsonl"
        names = (
            "same_process.serving.no_activation.pgvector",
            "same_process.training.no_activation.pgvector",
            "same_process.training.activation.pgvector",
        )
        for index, name in enumerate(names):
            output = tmp_path / f"condition-{index}.jsonl"
            outputs.append(output)
            command = [
                "concurrent-replay",
                "--manifest",
                str(manifest_path),
                "--requests",
                str(FIXTURES / "requests.jsonl"),
                "--candidate-universe",
                str(FIXTURES / "created-babels.jsonl"),
                "--condition",
                name,
                "--measurements",
                str(output),
            ]
            if index == 0:
                command.extend(
                    [
                        "--resources",
                        str(resource_output),
                        "--service-pid",
                        f"benchmark={os.getpid()}",
                        "--resource-interval-seconds",
                        "0.01",
                        "--maximum-resource-samples",
                        "2",
                    ]
                )
            assert main(command) == 0
        summary = tmp_path / "summary.json"
        markdown = tmp_path / "report.md"
        assert (
            main(
                [
                    "report",
                    "--measurements",
                    *(str(path) for path in outputs),
                    "--summary",
                    str(summary),
                    "--markdown",
                    str(markdown),
                ]
            )
            == 0
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert json.loads(summary.read_text())["interference"] is not None
    resources = load_jsonl(resource_output, ResourceObservationV1)
    assert {row.service for row in resources} == {"host", "benchmark"}
    report = markdown.read_text()
    assert all(
        name in report for name in ("Itraining", "Ifull", "IActivationIncrement")
    )
