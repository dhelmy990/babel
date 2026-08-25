from __future__ import annotations

import json
from pathlib import Path

import hashlib

from babel_online.contracts import (
    ModelManifestV1,
    canonical_pgvector_snapshot_sha256,
    canonical_vector_sha256,
    validate_contract,
)
from babel_online.model.item_tower import ItemTower
from babel_online.observable import CreatedBabel


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPOSITORY_ROOT / "fixtures" / "online" / "tiny"
FORBIDDEN = {"graph", "ppr", "clickstream", "profile", "randomDraw", "seedWeight"}


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def collect_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in collect_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in collect_keys(item)}
    return set()


def test_tiny_world_has_six_creators_and_physical_hidden_boundary() -> None:
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    assert manifest["releaseScope"] == "friday_demo_fixture"
    assert manifest["creatorCount"] == 6
    creators = read_jsonl(FIXTURE / "observable" / "creators.jsonl")
    june = read_jsonl(FIXTURE / "observable" / "catalog-2026-06.jsonl")
    july = read_jsonl(FIXTURE / "observable" / "catalog-2026-07.jsonl")
    created = read_jsonl(FIXTURE / "observable" / "created-babels.jsonl")
    assert len(creators) == len(june) == len(july) == len(created) == 6
    assert not FORBIDDEN & collect_keys(
        {"creators": creators, "june": june, "july": july, "created": created}
    )
    assert (FIXTURE / "hidden" / "graph-2026-06.jsonl").is_file()
    assert (FIXTURE / "hidden" / "profiles.jsonl").is_file()

    pairs = {(row["creatorId"], row["sourceArticleKey"]) for row in created}
    assert len(pairs) == len(created)
    assert len({row["sourceArticleKey"] for row in created}) == 5


def test_tiny_world_contract_documents_round_trip() -> None:
    documents = {
        "experiment-run-v1": "run.json",
        "embedding-space-v1": "embedding-space.json",
        "model-manifest-v1": "original-model.json",
        "hnsw-snapshot-v1": "hnsw-snapshot.json",
        "recommendation-request-v1": "observable/request.json",
    }
    for contract, relative in documents.items():
        document = json.loads((FIXTURE / relative).read_text())
        assert validate_contract(contract, document)
    run = json.loads((FIXTURE / "run.json").read_text())
    assert run["datasetConfig"] == "demo_catalog_2026_06"
    assert run["runSeed"] == 7


def test_hnsw_fixture_is_derived_from_the_same_created_pgvector_rows() -> None:
    model = ModelManifestV1.model_validate_json((FIXTURE / "original-model.json").read_text())
    tower = ItemTower(model.embeddingSpace)
    created = [
        CreatedBabel.model_validate(row)
        for row in read_jsonl(FIXTURE / "observable/created-babels.jsonl")
    ]
    catalog = {
        row["articleKey"]: row
        for row in read_jsonl(FIXTURE / "observable/catalog-2026-06.jsonl")
    }
    vectors = {row.babelId: tower.encode(row.text) for row in created}
    snapshot = json.loads((FIXTURE / "hnsw-snapshot.json").read_text())
    pgvector_rows = [
        {
            "babelId": str(row.babelId),
            "creatorId": str(row.creatorId),
            "sourceArticleKey": row.sourceArticleKey,
            "catalogContentHash": catalog[row.sourceArticleKey]["contentHash"],
            "embeddingSpaceId": str(model.embeddingSpace.embeddingSpaceId),
            "servingModelId": str(model.modelId),
            "materializedModelVersion": 0,
            "vectorSha256": hashlib.sha256(vectors[row.babelId].tobytes()).hexdigest(),
        }
        for row in created
    ]

    assert snapshot["vectorSha256"] == canonical_vector_sha256(vectors)
    assert snapshot["pgvectorSnapshotSha256"] == canonical_pgvector_snapshot_sha256(
        pgvector_rows
    )


def test_model_and_serving_sources_do_not_import_hidden_world() -> None:
    source = REPOSITORY_ROOT / "online" / "src" / "babel_online"
    violations = []
    for package in ("model", "serving"):
        for path in (source / package).rglob("*.py") if (source / package).exists() else []:
            if "babel_online.hidden" in path.read_text():
                violations.append(str(path))
    assert violations == []
