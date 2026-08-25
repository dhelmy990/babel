from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from babel_benchmark.contracts import (
    BenchmarkManifestV1,
    CreatedBabelV1,
    ReplayRequestV1,
    load_jsonl,
)


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
