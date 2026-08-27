from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from babel_benchmark.cli import _parser, main
from babel_benchmark.contracts import RequestMeasurementV1, load_jsonl
from babel_benchmark.resources import ResourceObservationV1


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
    assert publish.command == "trial-bundle-publish"
    assert publish.token_env == "HF_TOKEN"


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
