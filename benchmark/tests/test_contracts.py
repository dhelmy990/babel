from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from babel_benchmark.contracts import (
    BenchmarkManifestV1,
    CreatedBabelV1,
    ReplayRequestV1,
    ReplayRequestV2,
    load_jsonl,
)
from babel_benchmark.replay import ReplayCorpus


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "performance"


def test_friday_manifest_pins_three_conditions_to_one_replay() -> None:
    manifest = BenchmarkManifestV1.model_validate_json(
        (FIXTURES / "manifest.json").read_text()
    )

    assert [condition.name for condition in manifest.conditions] == [
        "pgvector_serving_only",
        "pgvector_training_no_sync",
        "pgvector_training_and_sync",
    ]
    assert {condition.requestCorpusSha256 for condition in manifest.conditions} == {
        manifest.requestCorpusSha256
    }
    assert {condition.scheduleOffsetsNs for condition in manifest.conditions} == {
        tuple(manifest.scheduleOffsetsNs)
    }


def test_representative_replay_is_deterministic_and_request_ids_are_unique() -> None:
    rows = load_jsonl(FIXTURES / "requests.jsonl", ReplayRequestV1)

    assert len(rows) == 6
    assert len({row.request.requestId for row in rows}) == len(rows)
    assert [row.scheduleOffsetNs for row in rows] == sorted(
        row.scheduleOffsetNs for row in rows
    )


def test_replay_loader_preserves_v2_traversal_requests(tmp_path: Path) -> None:
    source = tmp_path / "requests-v2.jsonl"
    source.write_text(
        json.dumps(
            {
                "scheduleOffsetNs": 0,
                "request": {
                    "schemaVersion": 2,
                    "requestId": "00000000-0000-5000-8000-000000000401",
                    "runId": "00000000-0000-5000-8000-000000000001",
                    "creatorId": "00000000-0000-5000-8000-000000000101",
                    "sourceBabelId": "00000000-0000-5000-8000-000000000301",
                    "sourceArticleKey": "enwiki:5739",
                    "traversalSessionId": "00000000-0000-5000-8000-000000000501",
                    "parentRequestId": None,
                    "traversalDepth": 0,
                    "title": "Compiler notes",
                    "text": "An observable note about compiler design.",
                    "historyBabelIds": [],
                    "candidateCount": 3,
                },
            }
        )
        + "\n"
    )

    corpus = ReplayCorpus.from_jsonl(source)

    assert isinstance(corpus.rows[0], ReplayRequestV2)
    assert corpus.rows[0].request.schemaVersion == 2


def test_candidate_universe_accepts_only_created_synthetic_babels() -> None:
    rows = load_jsonl(FIXTURES / "created-babels.jsonl", CreatedBabelV1)
    assert rows
    assert all(row.createdBySyntheticCreator and row.createdInRun for row in rows)

    with pytest.raises(ValidationError, match="created synthetic Babel"):
        CreatedBabelV1.model_validate(
            {
                **rows[0].model_dump(mode="json"),
                "createdInRun": False,
            }
        )


@pytest.mark.parametrize(
    "name",
    [
        "benchmark-manifest-v1.json",
        "request-measurement-v1.json",
        "condition-telemetry-v1.json",
        "performance-summary-v1.json",
    ],
)
def test_checked_in_performance_schemas_are_closed(name: str) -> None:
    schema = json.loads((ROOT / "schemas" / "performance" / name).read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
