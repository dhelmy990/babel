from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.contracts import UnknownSchema, load_schema, validate_document  # noqa: E402


FIXTURE = REPOSITORY_ROOT / "fixtures" / "distillation" / "debug-examples.jsonl"
BUILD_REQUIREMENTS = ("setuptools==75.8.0", "wheel==0.45.1")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def provenance_document() -> dict[str, object]:
    return {
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
            "text_statistics": {
                "count": 2,
                "min_length": 8,
                "max_length": 12,
                "mean_length": 10.0,
                "stddev_length": 2.0,
                "p50_length": 10.0,
                "p95_length": 11.8,
                "p99_length": 11.96,
                "histogram": [0, 1, 1, 0],
            },
            "vector_statistics": {
                "dimension": 100,
                "count": 2,
                "min_norm": 0.8,
                "max_norm": 1.2,
                "mean_norm": 1.0,
                "stddev_norm": 0.2,
                "p50_norm": 1.0,
                "p95_norm": 1.18,
                "non_finite_count": 0,
            },
        },
    }


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
    "name",
    ["unknown-v1", "provenance", "../schemas/provenance-v1", "../../etc/passwd"],
)
def test_schema_loader_rejects_unknown_and_unversioned_names(name: str) -> None:
    with pytest.raises(UnknownSchema):
        load_schema(name)


def test_schema_loader_does_not_return_cached_mutable_state() -> None:
    schema = load_schema("provenance-v1")
    schema["title"] = "caller mutation"

    assert load_schema("provenance-v1")["title"] == "Distillation provenance v1"


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


@pytest.mark.parametrize(
    "field,value",
    [("verified_shards", []), ("source_checksums", {}), ("remote_verified", False), ("remote_commit_sha", None)],
)
def test_pilot_ready_requires_published_evidence(field: str, value: object) -> None:
    readiness = json.loads(
        (REPOSITORY_ROOT / "fixtures" / "distillation" / "readiness.json").read_text()
    )
    readiness["state"] = "pilot_ready"
    readiness[field] = value
    with pytest.raises(ValidationError):
        validate_document("dataset-readiness-v1", readiness)


def test_readiness_rejects_contradictory_remote_pair() -> None:
    readiness = {"state": "building", "schema_version": 1, "teacher_dimension": 100,
                 "available_examples": 0, "verified_shards": [], "source_checksums": {},
                 "remote_verified": True, "remote_commit_sha": None}
    with pytest.raises(ValidationError):
        validate_document("dataset-readiness-v1", readiness)


def test_provenance_schema_loads() -> None:
    assert load_schema("provenance-v1")["title"] == "Distillation provenance v1"


def test_provenance_contract_validates_source_and_report_identity() -> None:
    provenance = provenance_document()
    validate_document("provenance-v1", provenance)

    provenance["sources"][0]["url"] = "not a uri"  # type: ignore[index]
    with pytest.raises(ValidationError):
        validate_document("provenance-v1", provenance)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(sources=[]),
        lambda value: value.update(artifacts={}),
        lambda value: value["artifacts"]["shard"].update(unplanned=True),
        lambda value: value["reports"].update(unplanned=True),
        lambda value: value["reports"]["text_statistics"].update(unplanned=True),
        lambda value: value["reports"]["vector_statistics"].update(unplanned=True),
    ],
)
def test_provenance_rejects_missing_identity_and_unplanned_nested_fields(
    mutation: object,
) -> None:
    invalid = copy.deepcopy(provenance_document())
    mutation(invalid)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        validate_document("provenance-v1", invalid)


@pytest.mark.parametrize(
    ("statistics", "field", "value"),
    [
        ("text_statistics", "stddev_lenght", 2.0),
        ("vector_statistics", "p95_nrom", 1.18),
        ("text_statistics", "histogram", [0, -1, 1]),
        ("text_statistics", "histogram", [0, 1.5, 1]),
    ],
)
def test_provenance_statistics_reject_typos_and_invalid_histogram_buckets(
    statistics: str, field: str, value: object
) -> None:
    provenance = provenance_document()
    provenance["reports"][statistics][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate_document("provenance-v1", provenance)


def test_installed_data_wheel_imports_and_validates_outside_repository(tmp_path: Path) -> None:
    source = REPOSITORY_ROOT / "data_pipeline"
    assert not (source / "build").exists()
    copied_source = tmp_path / "data_pipeline"
    shutil.copytree(
        source,
        copied_source,
        ignore=shutil.ignore_patterns(
            "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache"
        ),
    )
    builder = tmp_path / "builder"
    subprocess.run([sys.executable, "-m", "venv", str(builder)], check=True)
    builder_python = builder / "bin" / "python"
    subprocess.run(
        [builder_python, "-m", "pip", "install", *BUILD_REQUIREMENTS],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_directory = tmp_path / "wheel"
    subprocess.run(
        [
            builder_python,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_directory),
            str(copied_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("babel_data-*.whl"))
    installed = tmp_path / "installed"
    subprocess.run(
        [
            builder_python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    outside_repository = tmp_path / "outside"
    outside_repository.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import babel_data; "
                "schema = babel_data.load_schema('distillation-example-v1'); "
                "assert schema['title'] == 'Distillation example v1'; "
                "babel_data.validate_document('dataset-readiness-v1', "
                "{'state': 'building', 'schema_version': 1, 'teacher_dimension': 100, "
                "'available_examples': 0, 'verified_shards': [], 'source_checksums': {}, "
                "'remote_verified': False, 'remote_commit_sha': None})"
            ),
        ],
        cwd=outside_repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not (source / "build").exists()


def test_packaged_schemas_exactly_match_canonical_contracts() -> None:
    for schema in ("distillation-example-v1", "dataset-readiness-v1", "provenance-v1"):
        canonical = (REPOSITORY_ROOT / "schemas" / f"{schema}.json").read_bytes()
        packaged = (REPOSITORY_ROOT / "data_pipeline" / "src" / "babel_data" / "schemas" / f"{schema}.json").read_bytes()
        assert packaged == canonical
