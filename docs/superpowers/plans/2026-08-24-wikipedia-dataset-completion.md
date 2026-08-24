# Wikipedia Dataset Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete and tag the full 2016 distillation release, then publish verified June and July 2026 observable catalogs, hidden environments, and cross-period identities to the unified private Hugging Face dataset.

**Architecture:** Reuse the Slice 1 contracts and resumable publication system. General monthly parsers convert Wikimedia SQL/XML and Clickstream into canonical snapshot tables; June materializes and is remotely verified before July uses the reclaimed local space. An explicit crosswalk relates page IDs and Wikidata QIDs without title-only joins.

**Tech Stack:** Python, PyArrow/Parquet, Datasets, Hugging Face Hub, streaming XML, Wikimedia SQL parsing, NumPy/SciPy sparse structures, pytest.

## Global Constraints

- Requires Slice 1 schemas, source verification, sharding, private-Hub publisher, and accepted pilot commit.
- Prepared repository remains private `dhelmy990/babel-wikipedia-experiment`.
- All downstream reads pin a Hub commit SHA; only acquisition reads upstream archives.
- Full 2016 uses the verified 100d September teacher and October 1 text snapshot.
- June uses `enwiki-20260601` plus `clickstream-enwiki-2026-06.tsv.gz`.
- July uses `enwiki-20260701` plus `clickstream-enwiki-2026-07.tsv.gz`.
- Monthly graph inputs are `page`, `redirect`, `page_props`, `linktarget`, and `pagelinks`, plus `pages-articles` text.
- Only namespace-zero canonical articles enter catalogs and graphs.
- Clickstream retains `type == "link"` and never treats missing rows as dislike.
- June bulk materialization and remote verification finish before July bulk materialization starts.
- Published shards are append-only; pilot shards are never rewritten.
- Raw files are deleted only after pinned remote verification.
- Use test-first development and commit after every task gate.

---

## Orchestrator Fleet Map

Maximum concurrency: one orchestrator plus three workers. Code work may run in
parallel; June and July bulk execution is intentionally sequential.

```text
Orchestrator / Task 1
freeze Slice 1 contracts + monthly schema contracts
          |
          +---------------- Wave 1 ----------------+
          |                    |                    |
 Agent A / Task 2      Agent B / Task 3     Agent C / Task 4
 full 2016 runner      monthly dump parser  cross-period identity
          |                    |                    |
          +------------- Integration gate ----------+
                               |
                   Orchestrator freezes parser APIs
                               |
                 Agent A / Task 5: June release
                               |
                   remote verify + local cleanup
                               |
                 Agent B / Task 6: July release
                               |
          +---------------- Wave 2 ----------------+
          |                                         |
 Agent C / Task 7                         Agent A / Task 8
 complete 2016 handoff                    cross-period QA/provenance
          |                                         |
          +------------- Final gate ----------------+
                               |
                    Orchestrator / Task 9
                    remote acceptance + tags
                               |
                    Orchestrator / Task 10
             migrate dashboard source to pinned HF
```

Ownership:

| Lane | Owned files |
|---|---|
| Orchestrator | shared schemas/manifests, integration commands, release tags, backend source migration |
| Agent A | `full_2016.py`, 2016 reports, cross-period QA after June/July freeze |
| Agent B | `monthly/` parsing modules, July run manifests/reports |
| Agent C | `crosswalk.py`, complete-run Colab configuration/runbook updates |

The two monthly execution tasks may not run concurrently. Workers never amend
another lane's published manifest. Shared parser changes return to the
orchestrator for a contract review before either month is built.
Task 10 is deliberately sequential because it changes the already-working
backend composition root and must be reviewed against the final pinned release.

## Target File Map

```text
schemas/
  monthly-article-v1.json
  monthly-edge-v1.json
  clickstream-edge-v1.json
  article-crosswalk-v1.json
data_pipeline/manifests/
  2016-sources.json
  2026-06-sources.json
  2026-07-sources.json
data_pipeline/src/babel_data/
  full_2016.py
  crosswalk.py
  monthly/
    sql_stream.py
    identity.py
    graph.py
    text.py
    clickstream.py
    profiles.py
    build.py
data_pipeline/tests/
  test_full_2016.py
  test_crosswalk.py
  monthly/*.py
docs/datasets/
  2016-complete-report.md
  2026-06-report.md
  2026-07-report.md
  cross-period-report.md
backend/
  migrations/004_huggingface_source_provenance.sql
  include/babel/adapters/huggingface/huggingface_article_source.hpp
  src/adapters/huggingface/huggingface_article_source.cpp
  tests/unit/huggingface_article_source_test.cpp
  tests/integration/huggingface_seed_source_test.cpp
training/configs/full-2016-t4.yaml
docs/runbooks/colab-distillation-complete.md
```

### Task 1: Freeze Monthly Contracts and Source Manifests

**Files:**
- Create: `schemas/monthly-article-v1.json`
- Create: `schemas/monthly-edge-v1.json`
- Create: `schemas/clickstream-edge-v1.json`
- Create: `schemas/article-crosswalk-v1.json`
- Create: `data_pipeline/manifests/2026-06-sources.json`
- Create: `data_pipeline/manifests/2026-07-sources.json`
- Create: `data_pipeline/tests/test_monthly_contracts.py`
- Modify: `schemas/provenance-v1.json`

**Interfaces:**
- Consumes: Slice 1 `article_key`, readiness, and provenance semantics.
- Produces: frozen v1 monthly row types and exact source specifications.

- [ ] **Step 1: Write failing schema tests**

```python
def test_monthly_article_requires_snapshot_identity():
    validate_document("monthly-article-v1", {
        "article_key": "enwiki:123",
        "snapshot": "2026-06-01",
        "page_id": 123,
        "canonical_title": "Virtual memory",
        "wikidata_id": "Q192106",
        "lead_text": "Lead",
        "article_text": "Lead\n\nSecond paragraph.",
        "redirect_titles": ["Virtual_memory"],
        "content_hash": "a" * 64,
        "source_revision_id": 99,
    })

def test_hidden_edge_cannot_validate_as_observable_article():
    with pytest.raises(ValidationError):
        validate_document("monthly-article-v1", {"source_page_id": 1, "target_page_id": 2})
```

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m pytest data_pipeline/tests/test_monthly_contracts.py -v`

Expected: FAIL because the schemas are absent.

- [ ] **Step 3: Define exact row contracts**

The observable article schema requires `article_text` and a sorted
`redirect_titles` array so the snapshot-backed backend can resolve seed titles
without a live API. `monthly-edge-v1` requires snapshot, canonical source/target article keys, and
forbids self-loops. `clickstream-edge-v1` additionally requires raw count `n`,
normalized nonnegative weight, and `type == "link"`. Crosswalk rows contain a
stable cross-period key plus nullable 2016/June/July page IDs, titles, and QID,
with an enum describing unchanged, moved, created, deleted, recreated, or
ambiguous identity.

- [ ] **Step 4: Record exact dump jobs and checksums**

Populate manifests from each official `dumpstatus.json`; include URL, byte
size, MD5, SHA1, dump job, and completion timestamp for every SQL/XML file.
For Clickstream, record official URL/size and compute SHA-256 immediately after
download because the directory does not publish a content checksum.

- [ ] **Step 5: Run contract tests and commit**

Run: `python3 -m pytest data_pipeline/tests/test_monthly_contracts.py -v`

```bash
git add schemas data_pipeline/manifests data_pipeline/tests/test_monthly_contracts.py
git commit -m "feat: freeze monthly Wikipedia dataset contracts"
```

### Task 2: Complete the 2016 Reconciliation Runner

**Files:**
- Create: `data_pipeline/src/babel_data/full_2016.py`
- Create: `data_pipeline/tests/test_full_2016.py`
- Create: `docs/datasets/2016-complete-report.md`
- Modify: `data_pipeline/src/babel_data/cli.py`

**Interfaces:**
- Consumes: Slice 1 verified sources, parsers, reconciliation, sharder, and Hub publisher.
- Produces: restartable `run_full_2016`, exclusion ledger, complete readiness manifest, and full-release report.

- [ ] **Step 1: Write restart/idempotency tests**

```python
def test_full_runner_resumes_after_completed_page_range(fake_pipeline, tmp_path):
    first = fake_pipeline.run(stop_after_ranges=2)
    second = fake_pipeline.run(resume=True)
    assert second.processed_ranges[:2] == first.processed_ranges
    assert second.reprocessed_ranges == []

def test_complete_requires_every_inventory_record_accounted_for(result):
    assert result.matched + result.excluded == result.teacher_inventory_count
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/test_full_2016.py -v`

Expected: FAIL because the full runner does not exist.

- [ ] **Step 3: Implement range journals and bounded memory**

```python
@dataclass(frozen=True)
class RangeJournal:
    range_id: str
    source_offset_start: int
    source_offset_end: int
    row_count: int
    exclusion_count: int
    shard_sha256: tuple[str, ...]
```

Process multistream ranges into atomic journals, never retain the full XML or
all article text in memory, and skip only journals whose sources and output
checksums still match.

- [ ] **Step 4: Generate the complete report**

Report teacher inventory, match/exclusion counts by reason, redirect statistics,
split counts, text lengths, vector norms, non-finite count, shard sizes, source
checksums, processing duration, and peak memory.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest data_pipeline/tests/test_full_2016.py -v`

```bash
git add data_pipeline/src/babel_data/full_2016.py data_pipeline/src/babel_data/cli.py data_pipeline/tests/test_full_2016.py docs/datasets/2016-complete-report.md
git commit -m "feat: complete restartable 2016 reconciliation"
```

### Task 3: Implement Reusable Monthly Dump Parsers

**Files:**
- Create: `data_pipeline/src/babel_data/monthly/__init__.py`
- Create: `data_pipeline/src/babel_data/monthly/sql_stream.py`
- Create: `data_pipeline/src/babel_data/monthly/identity.py`
- Create: `data_pipeline/src/babel_data/monthly/graph.py`
- Create: `data_pipeline/src/babel_data/monthly/text.py`
- Create: `data_pipeline/src/babel_data/monthly/clickstream.py`
- Create: `data_pipeline/src/babel_data/monthly/profiles.py`
- Create: `data_pipeline/src/babel_data/monthly/build.py`
- Create: `data_pipeline/tests/monthly/test_parsers.py`
- Create: `data_pipeline/tests/monthly/test_build.py`

**Interfaces:**
- Consumes: monthly schemas/source specs from Task 1.
- Produces: `iter_sql_inserts`, `build_identity`, `build_graph`, `build_catalog`, `parse_clickstream`, `parse_profile_archetypes`, `resolve_profile_seeds`, and `build_month`.

- [ ] **Step 1: Write fixture parser tests**

```python
def test_graph_resolves_linktarget_redirects_and_deduplicates(month_fixture):
    edges = list(build_graph(month_fixture))
    assert edges == [edge("enwiki:1", "enwiki:3")]

def test_clickstream_filters_non_links_and_invalid_endpoints(month_fixture):
    rows = list(parse_clickstream(month_fixture.clickstream, month_fixture.identity))
    assert all(row.type == "link" and row.n >= 10 for row in rows)

def test_all_authoritative_archetype_seeds_resolve(month_fixture):
    profiles = parse_profile_archetypes(PROFILE_MARKDOWN)
    resolved = resolve_profile_seeds(profiles, month_fixture.identity)
    assert len(resolved) == 20
    assert all(len(profile.seeds) == 4 for profile in resolved)
    assert all(tuple(seed.weight for seed in profile.seeds) == (0.40, 0.30, 0.20, 0.10) for profile in resolved)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/monthly -v`

Expected: FAIL because monthly parsers are absent.

- [ ] **Step 3: Implement a streaming MediaWiki SQL reader**

Parse escaped `INSERT INTO ... VALUES` tuples without loading whole compressed
files. Yield typed records for page, redirect, page_props, linktarget, and
pagelinks. Reject unknown column layouts using the dump's CREATE TABLE header.

- [ ] **Step 4: Build canonical identity and graph tables**

Retain namespace zero, resolve redirects with cycle detection, attach unique
Wikidata QIDs from `page_props`, join pagelinks through linktarget, canonicalize
both endpoints, remove duplicates/invalid/self edges, and write sorted Parquet.

- [ ] **Step 5: Build observable text and hidden Clickstream tables**

Reuse Slice 1 XML text extraction for monthly pages. Parse tab-separated
Clickstream, filter type/link/endpoints, aggregate canonical duplicates with
64-bit counts, and normalize with `log1p(n) / max(log1p(n))` per month while
preserving `n`. Parse the 20 archetypes and four weighted seeds each from
`prompts/wikipedia_user_profiles.md`, resolve every seed through the monthly
identity/redirect table, and write the resolved profile rows only to the hidden
configuration. An unresolved or ambiguous seed fails the monthly build with an
explicit report; it is never guessed by title similarity.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest data_pipeline/tests/monthly -v`

```bash
git add data_pipeline/src/babel_data/monthly data_pipeline/tests/monthly
git commit -m "feat: parse monthly Wikipedia environments"
```

### Task 4: Implement Cross-Period Identity

**Files:**
- Create: `data_pipeline/src/babel_data/crosswalk.py`
- Create: `data_pipeline/tests/test_crosswalk.py`

**Interfaces:**
- Consumes: canonical identity tables for 2016, June, and July.
- Produces: `build_crosswalk` and explicit ambiguity report.

- [ ] **Step 1: Write identity-lineage tests**

```python
def test_qid_tracks_title_move_across_months():
    row = build_crosswalk([snap("2016", 1, "Old", "Q1"), snap("june", 1, "New", "Q1")])[0]
    assert row.change_kind == "moved"

def test_title_reuse_without_matching_qid_is_not_same_identity():
    rows = build_crosswalk([snap("june", 1, "Name", "Q1"), snap("july", 2, "Name", "Q2")])
    assert {row.change_kind for row in rows} == {"deleted", "created"}
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/test_crosswalk.py -v`

Expected: FAIL because crosswalk logic is missing.

- [ ] **Step 3: Implement deterministic lineage rules**

Prefer unique QID continuity, then stable enwiki page ID when QID is absent.
Never merge solely by title. Emit separate rows and `ambiguous` findings for
conflicting QIDs or page-ID reuse signals.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m pytest data_pipeline/tests/test_crosswalk.py -v`

```bash
git add data_pipeline/src/babel_data/crosswalk.py data_pipeline/tests/test_crosswalk.py
git commit -m "feat: reconcile Wikipedia identities across periods"
```

### Task 5: Materialize and Publish June 2026

**Files:**
- Create: `data_pipeline/tests/monthly/test_june_release.py`
- Create: `docs/datasets/2026-06-report.md`
- Modify: `data_pipeline/src/babel_data/cli.py`

**Interfaces:**
- Consumes: Task 3 monthly builder and June manifest.
- Produces: `catalog_2026_06`, `simulator_2026_06_hidden`, `backend-seed/2026-06/catalog.jsonl`, verified commit SHA, and deletion receipt.

- [ ] **Step 1: Write release-state tests**

```python
def test_june_release_requires_catalog_and_hidden_remote_smokes(state):
    state.verify_config("catalog_2026_06")
    assert not state.can_delete_raw
    state.verify_config("simulator_2026_06_hidden")
    assert state.can_delete_raw
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_june_release.py -v`

Expected: FAIL until release orchestration records both configurations.

- [ ] **Step 3: Run verified June acquisition and build**

Run:

```bash
BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data babel-data acquire-month --manifest data_pipeline/manifests/2026-06-sources.json
BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data babel-data build-month --month 2026-06
```

Expected: all source checksums verify; catalog and hidden Parquet manifests are
complete; the hidden configuration includes 20 fully resolved archetypes; no
profile, seed, graph, PPR, or Clickstream field appears in catalog schema.
Also emit `backend-seed/2026-06/catalog.jsonl` containing exactly the 80 resolved
dashboard assignments with page/article identity, canonical and redirect
titles, snapshot date, article text, and per-row content SHA-256. Sort by page
ID, write a companion SHA-256 manifest, and derive both files solely from the
validated observable catalog. This small auxiliary artifact lets the C++
backend cache the relevant prepared rows without adding a Parquet dependency.

- [ ] **Step 4: Publish and remotely verify both configurations**

Run: `babel-data publish-month --month 2026-06 --repo dhelmy990/babel-wikipedia-experiment --revision-out /tmp/babel-june.sha`

Expected: command prints one commit SHA and remote row/edge counts for both
configs; record SHA in the report, then set
`BABEL_JUNE_SHA="$(tr -d '\n' </tmp/babel-june.sha)"`.

- [ ] **Step 5: Delete only verified June bulk inputs and record receipt**

Run: `babel-data cleanup-month --month 2026-06 --verified-revision "$BABEL_JUNE_SHA"`

Expected: only manifest-listed verified raw/staging files move to a recoverable
trash/staging cleanup location; reports/manifests remain.

- [ ] **Step 6: Test and commit report/CLI**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_june_release.py -v`

```bash
git add data_pipeline/src/babel_data/cli.py data_pipeline/tests/monthly/test_june_release.py docs/datasets/2026-06-report.md
git commit -m "data: publish June 2026 Wikipedia environment"
```

### Task 6: Materialize and Publish July 2026

**Files:**
- Create: `data_pipeline/tests/monthly/test_july_release.py`
- Create: `docs/datasets/2026-07-report.md`

**Interfaces:**
- Consumes: frozen Task 3 parser, July manifest, and verified June revision.
- Produces: `catalog_2026_07`, `simulator_2026_07_hidden`, and verified July commit SHA containing prior configs.

- [ ] **Step 1: Write temporal publication test**

```python
def test_july_revision_retains_verified_earlier_configs(remote_release):
    assert remote_release.configs == {
        "distillation_2016", "catalog_2026_06", "simulator_2026_06_hidden",
        "catalog_2026_07", "simulator_2026_07_hidden", "debug_fixture",
    }
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_july_release.py -v`

Expected: FAIL before July exists.

- [ ] **Step 3: Acquire/build July using the unchanged parser version**

Run:

```bash
BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data babel-data acquire-month --manifest data_pipeline/manifests/2026-07-sources.json
BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data babel-data build-month --month 2026-07
```

Expected: report includes catalog/graph/clickstream counts and June-to-July
schema equality.

- [ ] **Step 4: Publish, remote-smoke, and clean up**

Run: `babel-data publish-month --month 2026-07 --repo dhelmy990/babel-wikipedia-experiment --revision-out /tmp/babel-july.sha`

Set: `BABEL_JULY_SHA="$(tr -d '\n' </tmp/babel-july.sha)"`

Run: `babel-data cleanup-month --month 2026-07 --verified-revision "$BABEL_JULY_SHA"`

Expected: remote July data loads at the printed SHA and all earlier configs
still load at that SHA before local cleanup is allowed.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest data_pipeline/tests/monthly/test_july_release.py -v`

```bash
git add data_pipeline/tests/monthly/test_july_release.py docs/datasets/2026-07-report.md
git commit -m "data: publish July 2026 Wikipedia environment"
```

### Task 7: Tag Complete 2016 and Prepare the Full Colab Run

**Files:**
- Create: `training/configs/full-2016-t4.yaml`
- Create: `docs/runbooks/colab-distillation-complete.md`
- Create: `training/tests/test_full_run_config.py`
- Modify: `docs/datasets/2016-complete-report.md`

**Interfaces:**
- Consumes: complete 2016 readiness commit and Slice 1 notebook/package.
- Produces: immutable dataset tag, full-run config, and second handoff.

- [ ] **Step 1: Write full-run gate test**

```python
def test_full_config_requires_complete_dataset(config, readiness):
    assert config.dataset_revision == readiness.remote_commit_sha
    assert readiness.state == "complete"
    assert config.start_from_base_revision is True
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest training/tests/test_full_run_config.py -v`

Expected: FAIL before config exists.

- [ ] **Step 3: Publish remaining 2016 shards and tag**

Run: `babel-data publish-2016 --state complete --repo dhelmy990/babel-wikipedia-experiment --revision-out /tmp/babel-2016-complete.sha`

Set: `BABEL_2016_COMPLETE_SHA="$(tr -d '\n' </tmp/babel-2016-complete.sha)"`

Run: `babel-data verify-remote --repo dhelmy990/babel-wikipedia-experiment --revision "$BABEL_2016_COMPLETE_SHA" --config distillation_2016`

Create immutable tag `distillation-2016-v1` only after row accounting and
remote validation pass.

- [ ] **Step 4: Write explicit full-run settings and runbook**

The YAML pins dataset/model revisions, starts from the base Qwen rather than a
pilot checkpoint, sets validation/checkpoint cadence, Drive paths, and expected
dataset state `complete`. The runbook distinguishes exploratory pilot output
from the final reported model.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest training/tests/test_full_run_config.py -v`

```bash
git add training/configs/full-2016-t4.yaml training/tests/test_full_run_config.py docs/runbooks/colab-distillation-complete.md docs/datasets/2016-complete-report.md
git commit -m "docs: hand off complete 2016 distillation run"
```

### Task 8: Build and Validate the Cross-Period Release

**Files:**
- Create: `docs/datasets/cross-period-report.md`
- Create: `data_pipeline/tests/test_cross_period_release.py`
- Modify: `data_pipeline/src/babel_data/cli.py`

**Interfaces:**
- Consumes: all three identity tables and Task 4 crosswalk.
- Produces: versioned crosswalk Parquet, ambiguity ledger, and temporal statistics.

- [ ] **Step 1: Write release accounting test**

```python
def test_every_monthly_article_has_crosswalk_membership(release):
    for snapshot in ("2016", "2026-06", "2026-07"):
        assert release.catalog_keys(snapshot) <= release.crosswalk_keys(snapshot)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest data_pipeline/tests/test_cross_period_release.py -v`

Expected: FAIL before release crosswalk is built.

- [ ] **Step 3: Build, publish, and report**

Run: `babel-data build-crosswalk --periods 2016 2026-06 2026-07`

Run: `babel-data publish-crosswalk --repo dhelmy990/babel-wikipedia-experiment --revision-out /tmp/babel-connected.sha`

Set: `BABEL_CONNECTED_SHA="$(tr -d '\n' </tmp/babel-connected.sha)"`

Report unchanged/moved/created/deleted/recreated/ambiguous counts, QID coverage,
page-ID continuity, and manual samples of every change class.

- [ ] **Step 4: Test and commit**

Run: `python3 -m pytest data_pipeline/tests/test_cross_period_release.py -v`

```bash
git add data_pipeline/src/babel_data/cli.py data_pipeline/tests/test_cross_period_release.py docs/datasets/cross-period-report.md
git commit -m "data: publish cross-period Wikipedia identities"
```

### Task 9: Verify and Tag the Connected Dataset

**Files:**
- Create: `data_pipeline/tests/test_connected_remote_release.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 5–8 remote commit.
- Produces: final connected dataset acceptance evidence and release tag.

- [ ] **Step 1: Write token-gated connected-release test**

```python
@pytest.mark.remote
def test_every_config_streams_at_one_pinned_revision(hf_token, release_sha):
    for config in EXPECTED_CONFIGS:
        row = next(iter(load_dataset(REPO, config, revision=release_sha, token=hf_token, streaming=True)["train"]))
        validate_document(EXPECTED_CONFIG_ROW_SCHEMAS[config], row)

    seed_catalog = hf_hub_download(
        REPO, "backend-seed/2026-06/catalog.jsonl", repo_type="dataset",
        revision=release_sha, token=hf_token,
    )
    assert sum(1 for _ in open(seed_catalog, encoding="utf-8")) == 80
```

- [ ] **Step 2: Run offline suite**

Run: `python3 -m pytest data_pipeline/tests training/tests -m 'not remote' -v`

Expected: PASS.

- [ ] **Step 3: Run remote acceptance and leakage scan**

Run: `python3 -m pytest data_pipeline/tests/test_connected_remote_release.py -m remote -v`

Run: `babel-data scan-leakage --revision "$BABEL_CONNECTED_SHA"`

Expected: all configs stream; catalog schemas contain no graph, PPR,
Clickstream, archetype, or hidden relevance fields.

- [ ] **Step 4: Create immutable connected release tag**

Create `wikipedia-experiment-data-v1` at the exact accepted commit and record
the tag/SHA in README and all four reports.

- [ ] **Step 5: Commit**

```bash
git add data_pipeline/tests/test_connected_remote_release.py README.md
git commit -m "docs: verify connected Wikipedia dataset release"
```

### Task 10: Migrate Dashboard Seeding from Live MediaWiki to Pinned Hugging Face

**Files:**
- Create: `backend/migrations/004_huggingface_source_provenance.sql`
- Create: `backend/include/babel/adapters/huggingface/huggingface_article_source.hpp`
- Create: `backend/src/adapters/huggingface/huggingface_article_source.cpp`
- Create: `backend/tests/fixtures/huggingface_catalog.jsonl`
- Create: `backend/tests/unit/huggingface_article_source_test.cpp`
- Create: `backend/tests/integration/huggingface_seed_source_test.cpp`
- Modify: `backend/include/babel/application/ports.hpp`
- Modify: `backend/include/babel/domain/models.hpp`
- Modify: `backend/include/babel/application/wikipedia_import_service.hpp`
- Modify: `backend/src/application/wikipedia_import_service.cpp`
- Modify: `backend/include/babel/runtime/seed_job_runner.hpp`
- Modify: `backend/src/runtime/seed_job_runner.cpp`
- Modify: `backend/src/runtime/application.cpp`
- Modify: `backend/src/adapters/postgres/postgres_repositories.cpp`
- Modify: `backend/tests/unit/wikipedia_import_service_test.cpp`
- Modify: `backend/tests/unit/seed_job_runner_test.cpp`
- Modify: `backend/CMakeLists.txt`
- Modify: `README.md`
- Modify: `documentation.md`

**Interfaces:**
- Consumes: the Task 9 connected release tag/SHA and `catalog_2026_06` observable article rows.
- Produces: `ArticleSourceFactory::pin(SourceSelection) -> Result<std::shared_ptr<PinnedArticleSource>>`, `PinnedArticleSource::provenance()`, cached title/page lookup, paragraphized safe HTML, and Hugging Face provenance on `babel_sources`.

- [ ] **Step 1: Write failing source and no-fallback tests**

```cpp
TEST_CASE("snapshot text becomes deterministic escaped paragraph HTML") {
  auto source = fixtureSource("First & <unsafe>\n\nSecond line\ncontinued");
  auto article = source.fetchByPageId(WikipediaPageId::fromInt(42).value()).value();
  REQUIRE(article.rendered_html ==
          "<p>First &amp; &lt;unsafe&gt;</p>\n<p>Second line continued</p>");
  REQUIRE(article.provenance.commit_sha == std::string(40, 'a'));
}

TEST_CASE("unavailable Hugging Face snapshot never calls MediaWiki") {
  auto result = seedRunner(huggingFaceUnavailable(), mediaWikiSpy()).run();
  REQUIRE_FALSE(result);
  REQUIRE(mediaWikiSpy().requestCount() == 0);
}
```

- [ ] **Step 2: Run and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "huggingface_article_source|huggingface_seed_source" --output-on-failure`

Expected: tests do not compile because the pinned source factory is absent.

- [ ] **Step 3: Migrate the existing provenance schema**

Keep existing `provider = 'wikipedia'` rows valid, replace the provider check
with `provider IN ('wikipedia', 'huggingface_wikipedia')`, and add nullable
`source_repository`, `source_config`, `source_commit_sha`,
`source_article_key`, `source_snapshot_date`, and `source_content_sha256`.
Require all six fields when `provider = 'huggingface_wikipedia'`, require a
40-lowercase-hex commit and 64-lowercase-hex content digest, and retain
`UNIQUE(owner_id, provider, external_page_id)` so one creator cannot import the
same snapshot-backed article twice. Add `source_repository`, `source_config`,
`source_commit_sha`, and `source_snapshot_date` to `seed_runs`; require all four
to be set together so each run durably records the pin it used. Drop the current
generated constraint `babel_sources_provider_check` by name before adding the
new explicitly named provider/provenance constraints.

- [ ] **Step 4: Pin and cache the private dataset before processing items**

Add these application-level contracts; adapter-specific HTTP types remain out
of the port:

```cpp
struct SourceSelection {
  std::string repository;
  std::string configuration;
  std::string requested_revision;
  std::string artifact_path;
};

struct PinnedSourceProvenance {
  std::string repository;
  std::string configuration;
  std::string commit_sha;
  std::string snapshot_date;
};

class ArticleSourceFactory {
 public:
  virtual ~ArticleSourceFactory() = default;
  virtual Result<std::shared_ptr<PinnedArticleSource>> pin(const SourceSelection&) = 0;
};
```

`PinnedArticleSource` exposes the existing `resolveTitle` and `fetchByPageId`
operations plus immutable `provenance()`. A run-scoped pinned instance is passed
through the existing importer instead of retaining mutable global source state.

The backend reads `HF_TOKEN` and the configured repository/config/ref. At the
start of each seed run, resolve the ref once to a commit SHA, persist that SHA
on the run, download/cache
`backend-seed/2026-06/catalog.jsonl` and its checksum manifest under
`/home/dhelmy990/Data/babel-data/cache/backend-seed/{commit_sha}/`, verify
Hub metadata and file checksums, then construct immutable title/page indexes.
No seed item begins until this completes. The token and authenticated URLs are
never sent to the dashboard or stored in PostgreSQL.

- [ ] **Step 5: Implement exact snapshot lookup and safe paragraphization**

Resolve declared titles by Unicode/MediaWiki normalization against
`canonical_title` and `redirect_titles`; reject ambiguous/missing values without
fuzzy matching. Fetch locally by canonical page ID. Normalize newlines, split
on one or more blank lines, collapse intra-paragraph whitespace, HTML-escape all
text, omit empty paragraphs, and wrap each paragraph in `<p>...</p>`. Pass this
HTML through the existing `HtmlSanitizer` and the existing
`WikipediaImportService` insertion path. Preserve current per-item retries for
Hub acquisition/transient failures and all existing progress/error accounting.

- [ ] **Step 6: Replace only the runtime source composition**

Compose `HuggingFaceArticleSourceFactory` into `SeedJobRunner`; remove
`MediaWikiArticleSource` from the dashboard seed runtime path. Do not change
the rendered dashboard action, polling endpoint, job concurrency, importer,
successful-item durability, retry status, database insertion, or duplicate
behavior. Do not add a feature flag or automatic fallback to live Wikipedia.

- [ ] **Step 7: Run regression, integration, and live pinned-source gates**

Run:

```bash
cmake --build --preset test
ctest --preset test --output-on-failure
npm test
```

Expected: all existing seed/import/dashboard tests still pass, plus provenance,
paragraphization, cache, pinning, and no-fallback tests.

With `HF_TOKEN`, seed a disposable database through the rendered dashboard at
the Task 9 commit SHA. Expected: progress/retries behave as before; inserted
rows use `huggingface_wikipedia`, exact repo/config/SHA/article/content
provenance, sanitized `<p>` HTML, and no live MediaWiki request.

- [ ] **Step 8: Commit**

```bash
git add backend/migrations/004_huggingface_source_provenance.sql backend/include/babel/adapters/huggingface backend/src/adapters/huggingface backend/tests/fixtures/huggingface_catalog.jsonl backend/tests/unit/huggingface_article_source_test.cpp backend/tests/integration/huggingface_seed_source_test.cpp backend/include/babel/application/ports.hpp backend/include/babel/domain/models.hpp backend/include/babel/application/wikipedia_import_service.hpp backend/src/application/wikipedia_import_service.cpp backend/include/babel/runtime/seed_job_runner.hpp backend/src/runtime/seed_job_runner.cpp backend/src/runtime/application.cpp backend/src/adapters/postgres/postgres_repositories.cpp backend/tests/unit/wikipedia_import_service_test.cpp backend/tests/unit/seed_job_runner_test.cpp backend/CMakeLists.txt README.md documentation.md
git commit -m "feat: seed dashboard from pinned Hugging Face snapshot"
```

## Slice Acceptance Gate

- [ ] Full 2016 inventory is accounted for as matched or explicitly excluded.
- [ ] `distillation_2016` is `complete`, remotely validated, and tagged.
- [ ] Full-run Colab configuration starts cleanly from pinned Qwen.
- [ ] June and July observable/hidden configurations share frozen schemas.
- [ ] Graphs are canonical, directed, namespace zero, duplicate-free, and hidden.
- [ ] Clickstream rows are valid link transitions with preserved raw counts.
- [ ] Crosswalk never relies on title-only identity.
- [ ] One pinned connected revision streams every configuration.
- [ ] Remote verification precedes every bulk cleanup.
- [ ] Dashboard seeding pins and caches one private-Hub commit before importing.
- [ ] Prepared text becomes deterministic sanitized paragraph HTML.
- [ ] Hugging Face provenance is complete and live MediaWiki fallback is impossible.

## Orchestrator Context for the Next Slice

Slice 3 consumes immutable data rather than building it. Before dispatching its
fleet, the orchestrator must freeze the connected dataset tag/SHA, monthly
schema versions, crosswalk semantics, complete distilled encoder artifact, and
the hidden/observable loader APIs. Also freeze the backend seed-source pinning
contract and verify the live MediaWiki adapter is not reachable from dashboard
seeding. Review June/July reports for graph size,
catalog size, seed-resolution failures, and memory implications; these numbers
determine the simulator's top-L PPR configuration and ANN build strategy.
Slice 3 must not reopen source parsing or publish corrected shards in passing.
If a data defect is found, stop integration, create a new dataset version in a
dedicated corrective task, and update the pinned release before online work
continues.
