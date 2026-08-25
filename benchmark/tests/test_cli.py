from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from babel_benchmark.cli import main
from babel_benchmark.contracts import RequestMeasurementV1, load_jsonl


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
