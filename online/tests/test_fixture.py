from __future__ import annotations

import json
from pathlib import Path

from babel_online.contracts import validate_contract


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
        "recommendation-request-v1": "observable/request.json",
    }
    for contract, relative in documents.items():
        document = json.loads((FIXTURE / relative).read_text())
        assert validate_contract(contract, document)


def test_model_and_serving_sources_do_not_import_hidden_world() -> None:
    source = REPOSITORY_ROOT / "online" / "src" / "babel_online"
    violations = []
    for package in ("model", "serving"):
        for path in (source / package).rglob("*.py") if (source / package).exists() else []:
            if "babel_online.hidden" in path.read_text():
                violations.append(str(path))
    assert violations == []
