from __future__ import annotations

import json
import subprocess
import sys
import zipfile
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


def test_complete_readiness_requires_remote_verification_and_evidence() -> None:
    readiness = json.loads(
        (REPOSITORY_ROOT / "fixtures" / "distillation" / "readiness.json").read_text()
    )
    readiness["remote_verified"] = False

    with pytest.raises(ValidationError):
        validate_document("dataset-readiness-v1", readiness)


def test_building_readiness_can_defer_remote_commit() -> None:
    validate_document(
        "dataset-readiness-v1",
        {
            "state": "building",
            "schema_version": 1,
            "teacher_dimension": 100,
            "available_examples": 0,
            "verified_shards": [],
            "source_checksums": {},
            "remote_verified": False,
            "remote_commit_sha": None,
        },
    )


def test_provenance_schema_loads() -> None:
    assert load_schema("provenance-v1")["title"] == "Distillation provenance v1"


def test_provenance_contract_validates_source_and_report_identity() -> None:
    provenance = {
        "schema_version": 1,
        "sources": [{
            "filename": "vectors.bin",
            "url": "https://example.test/vectors.bin",
            "size": 12,
            "md5": "a" * 32,
            "sha1": "b" * 40,
            "downloaded_at": "2016-10-01",
        }],
        "artifacts": {"shard": {"sha256": "c" * 64, "size": 12}},
        "reports": {
            "row_counts": {"input": 2, "matched": 1},
            "match_rate": 0.5,
            "exclusion_counts": {"unmatched": 1},
            "text_statistics": {"mean_length": 10.0},
            "vector_statistics": {"dimension": 100, "mean_norm": 1.0},
        },
    }
    validate_document("provenance-v1", provenance)

    provenance["sources"][0]["url"] = "not a uri"
    with pytest.raises(ValidationError):
        validate_document("provenance-v1", provenance)


def test_installed_wheel_contains_schema_resources(tmp_path: Path) -> None:
    wheel_directory = tmp_path / "wheel"
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_directory), str(REPOSITORY_ROOT / "data_pipeline")],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("babel_data-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "babel_data/schemas/distillation-example-v1.json" in archive.namelist()
