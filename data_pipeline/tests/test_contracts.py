from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.contracts import load_schema, validate_document  # noqa: E402


FIXTURE = REPOSITORY_ROOT / "fixtures" / "distillation" / "debug-examples.jsonl"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_debug_rows_match_v1_schema() -> None:
    for row in read_jsonl(FIXTURE):
        validate_document("distillation-example-v1", row)


def test_schema_loader_is_independent_of_current_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    schema = load_schema("distillation-example-v1")

    assert schema["$id"].endswith("distillation-example-v1.json")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("teacher_vector", [0.0] * 99),
        ("teacher_vector", [0.0] * 99 + [float("nan")]),
        ("teacher_vector", [0.0] * 99 + [float("inf")]),
        ("teacher_norm", 0.0),
        ("snapshot_date", "2016-10-02"),
        ("split", "development"),
    ],
)
def test_row_contract_rejects_invalid_values(field: str, value: object) -> None:
    row = read_jsonl(FIXTURE)[0]
    row[field] = value

    with pytest.raises(ValidationError):
        validate_document("distillation-example-v1", row)


def test_readiness_fixture_matches_v1_schema() -> None:
    readiness = json.loads(
        (REPOSITORY_ROOT / "fixtures" / "distillation" / "readiness.json").read_text()
    )

    validate_document("dataset-readiness-v1", readiness)


def test_provenance_schema_loads() -> None:
    assert load_schema("provenance-v1")["title"] == "Distillation provenance v1"
