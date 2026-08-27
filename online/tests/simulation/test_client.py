from __future__ import annotations

from types import SimpleNamespace

from babel_online.contracts import RecommendationRequestV2, RecommendationResponseV2
from babel_online.simulation.client import RecommendationClient


def test_client_dispatches_v2_to_v2_endpoint_and_parser() -> None:
    response = {
        "schemaVersion": 2,
        "requestId": "00000000-0000-5000-8000-000000000001",
        "runId": "00000000-0000-5000-8000-000000000002",
        "modelId": "00000000-0000-5000-8000-000000000003",
        "modelVersion": 0,
        "retrievalBackend": "pgvector",
        "embeddingSpaceId": "00000000-0000-5000-8000-000000000004",
        "pgvectorSnapshotSha256": "a" * 64,
        "backendSnapshotSha256": "a" * 64,
        "queryVectorSha256": "b" * 64,
        "sourceVectorOrigin": "qwen_encode",
        "candidates": [],
        "timingsNs": {
            "queue": 0, "encode": 1, "context": 1, "ann": 1,
            "filtering": 1, "serialization": 1, "serverTotal": 5,
        },
    }
    calls = []

    class Http:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: response,
            )

    request = RecommendationRequestV2(
        schemaVersion=2,
        requestId=response["requestId"],
        runId=response["runId"],
        creatorId="00000000-0000-5000-8000-000000000005",
        sourceBabelId="00000000-0000-5000-8000-000000000006",
        sourceArticleKey="enwiki:1",
        traversalSessionId="00000000-0000-5000-8000-000000000007",
        parentRequestId=None,
        traversalDepth=0,
        title="Root",
        text="Lead",
        historyBabelIds=[],
        candidateCount=10,
    )

    result = RecommendationClient(
        "http://127.0.0.1:8791", http_client=Http()
    ).recommend(request)

    assert isinstance(result, RecommendationResponseV2)
    assert calls[0][0].endswith("/api/v2/recommendations")
