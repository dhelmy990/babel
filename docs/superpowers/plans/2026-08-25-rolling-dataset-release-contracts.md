# Rolling Dataset Release Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a strictly validated, remotely loadable rolling dataset bundle and require complete-release evidence before the CLI can claim completion.

**Architecture:** A new `babel_data.release` module is the single semantic boundary for manifest, readiness, dataset-card, rolling-extension, and full-proof validation. Sharding emits the additional identity evidence and deterministic metadata; Hub publication treats shards plus manifest/readiness/README as one pinned compare-and-swap bundle; CLI validates complete proof before authentication or API creation.

**Tech Stack:** Python 3.10, JSON Schema 2020-12, PyArrow, Hugging Face Hub/Datasets, pytest.

## Global Constraints

- No remote calls or writes and do not inspect `HF_TOKEN` during verification.
- Keep configuration fixed to `distillation_2016` and row schema fixed to `distillation-example-v1`.
- `rows_sha256` is the canonical ordered digest of `[article_key, page_id]` identities.
- Rolling additions require unique blob and identity digests plus disjoint ordered rank intervals.
- Canonical and packaged schemas must be byte-identical and installed-wheel safe.
- Commit implementation as `fix: complete rolling dataset release contracts`.

---

### Task 1: Strict schema-backed release validation

**Files:**
- Create: `schemas/dataset-manifest-v1.json`
- Create: `schemas/full-release-proof-v1.json`
- Create: `data_pipeline/src/babel_data/schemas/dataset-manifest-v1.json`
- Create: `data_pipeline/src/babel_data/schemas/full-release-proof-v1.json`
- Create: `data_pipeline/src/babel_data/release.py`
- Modify: `data_pipeline/src/babel_data/contracts.py`
- Modify/Test: `data_pipeline/tests/test_contracts.py`

**Interfaces:**
- Produces: `validate_manifest_bytes(bytes, label) -> dict`, `validate_manifest_extension(old, new)`, `render_dataset_card() -> bytes`, `validate_readiness_alignment(readiness, manifest)`, and `validate_full_release_proof(proof, manifest)`.

- [x] Add failing schema/semantic tests for unknown/missing fields, malformed shard fields, count/aggregate/row-digest inconsistencies, duplicate paths/checksums/identity digests, and overlapping split rank intervals.
- [x] Run the new tests and confirm contract-specific failures.
- [x] Add both closed schemas to the registry and implement centralized semantic validation in `release.py`.
- [x] Run contract tests until green.

### Task 2: Emit identity evidence and deterministic metadata

**Files:**
- Modify: `data_pipeline/src/babel_data/shard.py`
- Modify/Test: `data_pipeline/tests/test_shard.py`

**Interfaces:**
- `ShardInfo.rows_sha256` records ordered article/page identities.
- `ShardResult` exposes `readiness_path` and `readme_path` after preparation.

- [x] Add failing tests that inspect every emitted shard identity digest, strict manifest validation, exact dataset-card YAML/globs, and readiness alignment.
- [x] Run them and confirm missing digest/card behavior.
- [x] Compute identity digests per chunk, validate the completed manifest centrally, render root `README.md`, and build aligned root `readiness.json` before atomic directory publication.
- [x] Run shard tests until green.

### Task 3: Publish and verify the complete bundle

**Files:**
- Modify: `data_pipeline/src/babel_data/hub.py`
- Modify: `data_pipeline/src/babel_data/cli.py`
- Modify/Test: `data_pipeline/tests/test_hub.py`

**Interfaces:**
- Publication requires exact shard paths plus `distillation_2016/manifest.json`, `readiness.json`, and `README.md`.
- `verify_remote` accepts/derives local readiness and README paths and includes them in `VerifiedRemote.verified_paths`.

- [x] Add failing tests for copied shard aliases, metadata-altered duplicate aliases, omitted readiness/card, exact-byte remote mismatches, readiness-manifest disagreement, and README path coverage.
- [x] Run them and confirm the current manifest-only publisher fails.
- [x] Replace Hub-local manifest logic with `release.py`; classify README as immutable and manifest/readiness as contract-checked monotonic documents; fetch and validate all bundle metadata at the pinned SHA before streaming rows.
- [x] Run hub tests until green.

### Task 4: Gate complete release with explicit proof

**Files:**
- Modify: `data_pipeline/src/babel_data/cli.py`
- Modify/Test: `data_pipeline/tests/test_hub.py`

**Interfaces:**
- `publish-2016 --state complete --full-release-proof PATH` is required for completion.
- Proof validation occurs before `_token()` and `_api()`.

- [x] Add failing CLI tests for missing proof, pilot row counts, incomplete reconciliation, mismatched provenance/artifact/source inventories, and a valid complete proof.
- [x] Confirm invalid cases perform no API call and create no revision handoff.
- [x] Add the argument and call centralized proof validation before authentication/publication; leave `pilot_ready` unchanged.
- [x] Run CLI/hub tests until green.

### Task 5: Verification and implementation commit

**Files:**
- Modify: `docs/superpowers/plans/2026-08-25-rolling-dataset-release-contracts.md` (checkbox state only)

- [x] Run focused release tests with pytest plugin autoload disabled.
- [x] Run all `data_pipeline/tests` and `training/tests` and record documented skips.
- [x] Build copied-source offline wheels, install outside the repository, import both packages, inspect packaged schemas, and run `babel-data --help`.
- [x] Confirm `git diff --check`, no generated package `build`/`dist` or `uv.lock`, and only scoped files staged.
- [x] Request independent requirement review and resolve every Critical/Important finding.
- [x] Commit implementation with `fix: complete rolling dataset release contracts`.
