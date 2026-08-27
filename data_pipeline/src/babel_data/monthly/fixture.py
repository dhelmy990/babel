"""Materialize and verify the representative Friday monthly fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from babel_data.contracts import validate_document

from .catalog import HIDDEN_ARTICLE_FIELDS, canonical_jsonl, build_period_articles
from .crosswalk import build_crosswalk_expectations
from .hidden import build_archetypes, build_clickstream, build_graph
from .profiles import build_profile_catalog, extract_profile_manifest_assignments


REPO_ID = "dhelmy990/babel-wikipedia-experiment"
CONFIG = "distillation_2016"
REVISION = "c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b"
SNAPSHOT_CLAIM = "representative_fixture_not_official_monthly_snapshot"
_TOP_LEVEL_KEYS = {
    "manifest_version",
    "release_scope",
    "snapshot_claim",
    "readiness",
    "source",
    "periods",
}
_MONTHLY_ARTIFACTS = {
    "articles",
    "edges",
    "clickstream",
    "hidden_archetypes",
    "backend_seed_catalog",
}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}: JSONL line {line_number} is not an object")
            rows.append(value)
    return rows


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _descriptor(root: Path, relative_path: str, rows: int) -> dict[str, object]:
    payload = (root / relative_path).read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rows": rows,
    }


def build_demo_fixture(
    source_path: Path,
    output_root: Path,
    *,
    profile_source_path: Path,
    profile_manifest_path: Path,
) -> dict[str, object]:
    """Build exact deterministic artifacts from the pinned representative rows."""
    source_rows = _read_jsonl(source_path)
    profile_source_rows = _read_jsonl(profile_source_path)
    profile_source_sha256 = hashlib.sha256(profile_source_path.read_bytes()).hexdigest()
    profile_assignments = extract_profile_manifest_assignments(profile_manifest_path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    articles_2016 = build_period_articles(source_rows, "2016")
    june_articles = build_period_articles(source_rows, "2026-06")
    july_articles = build_period_articles(source_rows, "2026-07")
    periods = {
        "2016": ("2016", articles_2016),
        "2026-06": ("june", june_articles),
        "2026-07": ("july", july_articles),
    }
    artifact_rows: dict[str, dict[str, list[dict[str, object]]]] = {}
    for period, (directory, articles) in periods.items():
        if period == "2016":
            artifact_rows[period] = {"articles": articles}
            continue
        graph = build_graph(articles)
        archetypes = build_archetypes(articles)
        artifact_rows[period] = {
            "articles": articles,
            "edges": graph,
            "clickstream": build_clickstream(graph),
            "hidden_archetypes": archetypes,
            "backend_seed_catalog": build_profile_catalog(
                profile_source_rows, profile_assignments, period=period
            ),
        }

    filenames = {
        "articles": "articles.jsonl",
        "edges": "edges.jsonl",
        "clickstream": "clickstream.jsonl",
        "hidden_archetypes": "hidden-archetypes.jsonl",
        "backend_seed_catalog": "resolved-catalog-v3.jsonl",
    }
    descriptors: dict[str, dict[str, object]] = {}
    for period, rows_by_artifact in artifact_rows.items():
        directory = periods[period][0]
        period_descriptors: dict[str, object] = {}
        for artifact_name, rows in rows_by_artifact.items():
            relative_path = f"{directory}/{filenames[artifact_name]}"
            _atomic_write(output_root / relative_path, canonical_jsonl(rows))
            period_descriptors[artifact_name] = _descriptor(
                output_root, relative_path, len(rows)
            )
        descriptors[period] = {"artifacts": period_descriptors}

    crosswalk, ambiguities = build_crosswalk_expectations(june_articles, july_articles)
    _atomic_write(output_root / "article-crosswalk.jsonl", canonical_jsonl(crosswalk))
    _atomic_write(output_root / "ambiguities.jsonl", canonical_jsonl(ambiguities))
    readme = f"""# Monthly Friday Demo Fixture

Release scope: `friday_demo_fixture`.

This is a representative deterministic fixture, not an official June or July Wikipedia snapshot.
The labels `2026-06` and `2026-07` are scenario periods only. Article text and
revision IDs were derived byte-faithfully from the pinned October 2016 pilot
`{REPO_ID}` / `{CONFIG}` at `{REVISION}`.

Representative input SHA-256: `{source_sha256}`.
Profile catalog input SHA-256: `{profile_source_sha256}`.

The last four identities carry explicit simulated titles, page IDs, and QID
transition metadata solely to exercise moved, deleted, created, and ambiguous
cross-period behavior. All other page IDs preserve the representative source.
The
observable catalogs contain no graph, Clickstream, archetype, seed-weight, PPR,
hidden-relevance, or random-draw fields. `provenance.json` is the sole release
input manifest; the crosswalk and ambiguity files are local expectations only.
Each `resolved-catalog-v3.jsonl` contains 78 unique real October 2016 articles
that resolve all 80 backend-owned `ProfileManifest` creator assignments. The
two repeated requested titles reuse the same source article across creators;
the assignment ledger remains authoritative in the backend and is not copied
into this source catalog. These source articles were extracted with the
production parser from strict byte ranges of the official `enwiki-20161001`
multistream dump; no live Wikipedia API was used. `Corporate finance` is the
only row for which the production lead heuristic returned empty, so its
nonempty lead is deterministically the first nonempty prepared article
paragraph (`lead_derivation=first_nonempty_paragraph_fallback`).
"""
    _atomic_write(output_root / "README.md", readme.encode("utf-8"))
    manifest: dict[str, object] = {
        "manifest_version": 1,
        "release_scope": "friday_demo_fixture",
        "snapshot_claim": SNAPSHOT_CLAIM,
        "readiness": "fixture_ready",
        "source": {
            "repo_id": REPO_ID,
            "config": CONFIG,
            "revision": REVISION,
        },
        "periods": descriptors,
    }
    for period in ("2026-06", "2026-07"):
        catalog = descriptors[period]["artifacts"]["backend_seed_catalog"]
        assert isinstance(catalog, Mapping)
        catalog_path = output_root / str(catalog["path"])
        checksum_payload = (
            f"{catalog['sha256']}  {catalog_path.name}\n".encode("ascii")
        )
        _atomic_write(
            catalog_path.with_name(f"{catalog_path.name}.sha256"), checksum_payload
        )
    _atomic_write(output_root / "provenance.json", _json_bytes(manifest))
    verify_demo_fixture(output_root)
    return manifest


def _safe_artifact(root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("artifact path must be nonblank")
    resolved_root = root.resolve()
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"artifact path escapes fixture root: {relative_path}") from error
    return resolved


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _normalize_backend_title(value: str) -> str:
    collapsed = " ".join(value.replace("_", " ").split())
    return collapsed[:1].upper() + collapsed[1:]


def _verify_resolved_seed_catalog(rows: list[dict[str, object]], period: str) -> None:
    if len(rows) != 78:
        raise ValueError("backend seed catalog must contain exactly 78 source articles")
    page_ids = [row.get("page_id") for row in rows]
    if page_ids != sorted(page_ids) or len(set(page_ids)) != 78:
        raise ValueError("backend seed catalog page IDs must be sorted and unique")
    title_index: dict[str, set[object]] = {}
    for row in rows:
        article_text = row.get("article_text")
        content_hash = row.get("content_hash")
        redirects = row.get("redirect_titles")
        canonical_title = row.get("canonical_title")
        if (
            not isinstance(article_text, str)
            or not isinstance(content_hash, str)
            or hashlib.sha256(article_text.encode("utf-8")).hexdigest() != content_hash
            or not isinstance(canonical_title, str)
            or not isinstance(redirects, list)
            or row.get("snapshot") != period
            or row.get("article_key") != f"enwiki:{row.get('page_id')}"
        ):
            raise ValueError("backend seed resolved article fields are invalid")
        for title in (canonical_title, *redirects):
            if not isinstance(title, str):
                raise ValueError("backend seed title must be a string")
            title_index.setdefault(_normalize_backend_title(title), set()).add(
                row["page_id"]
            )
    if len(title_index) < 78 or any(
        len(page_ids_for_title) != 1 for page_ids_for_title in title_index.values()
    ):
        raise ValueError("backend seed title lookup is incomplete or ambiguous")


def verify_demo_fixture(root: Path) -> dict[str, object]:
    """Verify provenance hashes, row schemas, boundaries, and demo invariants."""
    manifest = _require_mapping(
        json.loads((root / "provenance.json").read_text(encoding="utf-8")),
        "provenance",
    )
    if set(manifest) != _TOP_LEVEL_KEYS:
        raise ValueError("provenance top-level keys do not match the frozen contract")
    if manifest.get("manifest_version") != 1:
        raise ValueError("manifest_version must equal 1")
    if manifest.get("release_scope") != "friday_demo_fixture":
        raise ValueError("release_scope must equal friday_demo_fixture")
    if manifest.get("snapshot_claim") != SNAPSHOT_CLAIM:
        raise ValueError("snapshot_claim does not declare the representative fixture")
    if manifest.get("readiness") != "fixture_ready":
        raise ValueError("readiness must equal fixture_ready")
    if manifest.get("source") != {
        "repo_id": REPO_ID,
        "config": CONFIG,
        "revision": REVISION,
    }:
        raise ValueError("source pin does not match the approved pilot revision")
    periods = _require_mapping(manifest.get("periods"), "periods")
    if set(periods) != {"2016", "2026-06", "2026-07"}:
        raise ValueError("period keys do not match the frozen contract")

    article_counts: dict[str, int] = {}
    seed_counts: dict[str, int] = {}
    for period, period_value in periods.items():
        period_document = _require_mapping(period_value, f"period {period}")
        if set(period_document) != {"artifacts"}:
            raise ValueError(f"period {period} may contain only artifacts")
        artifacts = _require_mapping(period_document["artifacts"], f"{period} artifacts")
        expected = {"articles"} if period == "2016" else _MONTHLY_ARTIFACTS
        if set(artifacts) != expected:
            raise ValueError(f"period {period} artifact keys do not match the frozen contract")
        for name, descriptor_value in artifacts.items():
            descriptor = _require_mapping(descriptor_value, f"{period}/{name} descriptor")
            if set(descriptor) != {"path", "sha256", "rows"}:
                raise ValueError(f"{period}/{name} descriptor keys are invalid")
            artifact = _safe_artifact(root, descriptor["path"])
            payload = artifact.read_bytes()
            actual_sha256 = hashlib.sha256(payload).hexdigest()
            if actual_sha256 != descriptor["sha256"]:
                raise ValueError(f"sha256 mismatch for {descriptor['path']}")
            rows = _read_jsonl(artifact)
            if len(rows) != descriptor["rows"]:
                raise ValueError(f"row count mismatch for {descriptor['path']}")
            if name == "articles":
                article_counts[str(period)] = len(rows)
                for row in rows:
                    validate_document("monthly-article-v1", row)
                    if set(row) & HIDDEN_ARTICLE_FIELDS:
                        raise ValueError("hidden field leaked into observable article catalog")
            elif name == "edges":
                for row in rows:
                    validate_document("monthly-edge-v1", row)
            elif name == "clickstream":
                for row in rows:
                    validate_document("clickstream-edge-v1", row)
            elif name == "hidden_archetypes":
                if len(rows) != 20 or any(len(row.get("seeds", [])) != 4 for row in rows):
                    raise ValueError("hidden archetypes must contain 20 four-seed rows")
                if any(
                    tuple(seed["weight"] for seed in row["seeds"]) != (0.4, 0.3, 0.2, 0.1)
                    for row in rows
                ):
                    raise ValueError("hidden archetype weights do not match the frozen contract")
            elif name == "backend_seed_catalog":
                seed_counts[str(period)] = len(rows)
                _verify_resolved_seed_catalog(rows, str(period))
        if period != "2016":
            catalog_descriptor = _require_mapping(
                artifacts["backend_seed_catalog"],
                f"{period} backend seed catalog descriptor",
            )
            catalog_path = _safe_artifact(root, catalog_descriptor["path"])
            checksum_path = catalog_path.with_name(f"{catalog_path.name}.sha256")
            expected_checksum = (
                f"{catalog_descriptor['sha256']}  {catalog_path.name}\n"
            )
            if checksum_path.read_text(encoding="ascii") != expected_checksum:
                raise ValueError(f"backend seed checksum companion mismatch for {period}")

    crosswalk = _read_jsonl(root / "article-crosswalk.jsonl")
    for row in crosswalk:
        validate_document("article-crosswalk-v1", row)
    ambiguities = _read_jsonl(root / "ambiguities.jsonl")
    if len(ambiguities) != 1:
        raise ValueError("fixture must expose exactly one explicit ambiguity")
    readme = (root / "README.md").read_text(encoding="utf-8")
    if "not an official June or July Wikipedia snapshot" not in readme:
        raise ValueError("README omits the required non-historical warning")
    return {
        "readiness": "fixture_ready",
        "article_rows": article_counts,
        "seed_assignments": seed_counts,
        "ambiguities": len(ambiguities),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--verify", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile-source", type=Path)
    parser.add_argument("--profile-manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.verify is not None:
        result = verify_demo_fixture(arguments.verify)
    else:
        if arguments.output is None:
            raise SystemExit("--source requires --output")
        if arguments.profile_source is None or arguments.profile_manifest is None:
            raise SystemExit("--source requires --profile-source and --profile-manifest")
        build_demo_fixture(
            arguments.source,
            arguments.output,
            profile_source_path=arguments.profile_source,
            profile_manifest_path=arguments.profile_manifest,
        )
        result = verify_demo_fixture(arguments.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
