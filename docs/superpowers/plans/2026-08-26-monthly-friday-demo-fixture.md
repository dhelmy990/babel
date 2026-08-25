# Monthly Friday Demo Fixture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a deterministic 80-row-per-period representative monthly fixture with closed observable/hidden contracts and an unambiguous non-historical provenance boundary.

**Architecture:** Closed JSON Schemas enforce exchange rows. Pure monthly builders convert the pinned October 2016 pilot rows into a 2016 source catalog plus June/July scenario catalogs, hidden graph/Clickstream/archetype artifacts, deterministic backend seed catalogs, and local crosswalk expectations. `provenance.json` is the sole consumer manifest and binds every release artifact by relative path, row count, and SHA-256.

**Tech Stack:** Python 3.10+, JSON Schema Draft 2020-12, pytest, standard-library JSON/hash/path utilities.

## Global Constraints

- Release scope is exactly `friday_demo_fixture`; no artifact may claim an official June/July Wikipedia snapshot.
- Representative text source is the private pinned `dhelmy990/babel-wikipedia-experiment`, config `distillation_2016`, revision `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`, source snapshot `2016-10-01`.
- Produce exactly 80 observable article rows for each of 2016, June, and July; namespace must be zero and observable rows must contain no hidden fields.
- Produce 20 hidden archetypes with four weights `(0.4, 0.3, 0.2, 0.1)`, deterministic directed duplicate-free graph edges, and link-only Clickstream rows with positive raw counts and finite normalized weights.
- Demonstrate June-to-July unchanged, moved, created, deleted, and one explicit ambiguous identity; never join solely by title.
- `provenance.json` is the sole release input and uses the exact consumer fields frozen in the approved design.
- Do not modify `training/` or `backend/`; preserve root `build/` and `node_modules/`.

---

### Task 1: Freeze Monthly Row Contracts

**Files:**
- Create: `schemas/monthly-article-v1.json`
- Create: `schemas/monthly-edge-v1.json`
- Create: `schemas/clickstream-edge-v1.json`
- Create: `schemas/article-crosswalk-v1.json`
- Modify: `data_pipeline/src/babel_data/contracts.py`
- Test: `data_pipeline/tests/monthly/test_contracts.py`

**Interfaces:**
- Consumes: `validate_document(schema_name: str, value: Mapping[str, object]) -> None`.
- Produces: registered schema names `monthly-article-v1`, `monthly-edge-v1`, `clickstream-edge-v1`, and `article-crosswalk-v1`.

- [ ] **Step 1: Write failing tests for valid rows and common invalid mutations**

Create fixtures for one article, graph edge, Clickstream edge, and crosswalk row. Assert valid rows pass. Assert observable hidden fields, nonzero namespace, unsorted redirects, bad content hashes, self-loops, non-link behavior, invalid normalized weights, and title-only identity evidence fail.

- [ ] **Step 2: Run tests and verify schema lookup fails**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_contracts.py -v`

Expected: FAIL with `UnknownSchema` or absent schema files.

- [ ] **Step 3: Implement the four closed schemas and registry entries**

Require monthly articles to contain `article_key`, `period`, `release_scope`, `source_snapshot`, `namespace`, `page_id`, `canonical_title`, nullable `wikidata_id`, `lead_text`, `article_text`, sorted `redirect_titles`, `content_hash`, and nullable `source_revision_id`. Require graph/Clickstream canonical endpoint keys and crosswalk evidence enum values `qid`, `page_id`, `none`, or `conflict`; forbid `title` as an identity basis.

- [ ] **Step 4: Run the contract tests**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_contracts.py -v`

Expected: PASS.

### Task 2: Implement Deterministic Monthly Builders

**Files:**
- Create: `data_pipeline/src/babel_data/monthly/__init__.py`
- Create: `data_pipeline/src/babel_data/monthly/catalog.py`
- Create: `data_pipeline/src/babel_data/monthly/hidden.py`
- Create: `data_pipeline/src/babel_data/monthly/crosswalk.py`
- Create: `data_pipeline/tests/monthly/test_builders.py`

**Interfaces:**
- Consumes: normalized representative article dictionaries and the authoritative 20-profile roster.
- Produces: `content_sha256(text: str) -> str`, `build_period_articles(source_rows, period) -> list[dict]`, `build_graph(articles) -> list[dict]`, `build_clickstream(edges) -> list[dict]`, `build_archetypes(articles) -> list[dict]`, `build_seed_catalog(archetypes) -> list[dict]`, and `build_crosswalk(june, july) -> tuple[list[dict], list[dict]]`.

- [ ] **Step 1: Write failing builder invariant tests**

Assert deterministic article bytes and text hashes; 80 unique namespace-zero identities; sorted redirects; graph direction/deduplication/no self-loop; Clickstream graph-subset/type/count/weight rules; exactly 20 four-seed archetypes and 80 assignments with fixed weight order; no hidden observable keys; and crosswalk result counts `76 unchanged`, `1 moved`, `1 deleted`, `2 created`, `1 ambiguous` group.

- [ ] **Step 2: Run tests and verify imports fail**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_builders.py -v`

Expected: FAIL because `babel_data.monthly` builders are absent.

- [ ] **Step 3: Implement minimal pure builders**

Use key-sorted canonical JSON, SHA-256 over UTF-8 article text, stable sorted article order, a directed ring plus offset-seven jump, `log1p(n) / max(log1p(n))` Clickstream normalization, and exact profile/seed constants from the checked-in backend roster. Crosswalk matching must prefer unique QID, then stable page ID only when QID is absent, and emit conflict evidence when page-ID and QID signals disagree.

- [ ] **Step 4: Run builder and contract tests**

Run: `python3 -m pytest data_pipeline/tests/monthly -v`

Expected: PASS.

### Task 3: Generate and Verify the Checked-In Fixture

**Files:**
- Create: `data_pipeline/src/babel_data/monthly/fixture.py`
- Create: `data_pipeline/tests/monthly/test_demo_fixture.py`
- Create: `fixtures/monthly/demo/**`
- Create: `docs/datasets/monthly-friday-demo-fixture.md`

**Interfaces:**
- Consumes: the pinned local accepted source JSONL for the one-time representative-input bootstrap and the Task 2 pure builders.
- Produces: `build_demo_fixture(source_path: Path, output_root: Path) -> dict`, `verify_demo_fixture(root: Path) -> dict`, and sole release input `fixtures/monthly/demo/provenance.json`.

- [ ] **Step 1: Write failing end-to-end fixture tests**

Assert exact files, schemas, counts, deterministic bytes, SHA-256 sidecars, manifest key closure, exact period/artifact keys, all artifact descriptors, `fixture_ready`, non-historical claim strings, source pin, hidden-field firewall, and successful verification after rebuilding in a temporary directory.

- [ ] **Step 2: Run tests and verify fixture API/files are absent**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_demo_fixture.py -v`

Expected: FAIL because the fixture generator and artifacts are absent.

- [ ] **Step 3: Implement the fixture writer and verifier**

Write compact sorted JSONL and pretty sorted JSON atomically, compute byte hashes after writing, populate `provenance.json` only after all descriptors verify, and reject any path escape, hash mismatch, row-count mismatch, schema failure, or readiness value other than `fixture_ready`.

- [ ] **Step 4: Materialize from the pinned representative input**

Run: `PYTHONPATH=data_pipeline/src python3 -m babel_data.monthly.fixture --source /home/dhelmy990/Data/babel-data/work/2016-pilot/accepted.jsonl --output fixtures/monthly/demo`

Expected: 80 rows in every article catalog, 80 seed assignments per scenario period, and `fixture_ready` provenance.

- [ ] **Step 5: Run verification and full data-pipeline tests**

Run: `PYTHONPATH=data_pipeline/src python3 -m babel_data.monthly.fixture --verify fixtures/monthly/demo`

Run: `python3 -m pytest data_pipeline/tests -q`

Expected: both commands succeed.

- [ ] **Step 6: Record the exact handoff and commit owned files**

Write the source identity, counts, artifact checksums, sole manifest path/schema, verification commands, and non-historical limitation in `docs/datasets/monthly-friday-demo-fixture.md`. Stage only owned schemas, monthly code/tests, fixture artifacts, contract registry, and docs; commit with `feat: add representative monthly demo fixture`.
