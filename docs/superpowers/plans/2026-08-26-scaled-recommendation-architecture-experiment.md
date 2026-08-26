# Scaled Recommendation Architecture Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the working Friday miniature into a sufficiently large, real-Qwen recommendation architecture experiment that compares monolithic and split serving/training topologies, preserves immutable model lineage, and saves reproducible scalability evidence.

**Architecture:** Preserve the implemented C++ dashboard control plane, PostgreSQL/pgvector storage, synchronous recommendation POST, Kafka feedback, online trainer, immutable model registry, and benchmark package. Replace the miniature datasets and NumPy encoder with commit-pinned Hugging Face source releases, the complete 2016 distilled Qwen artifact, real 100k June/July engineering snapshots, durable experiment edges, bounded recommendation walks, selectable same-process/same-host topologies, concurrent load, and immutable result publication.

**Tech Stack:** C++20/Drogon, PostgreSQL 18 with pgvector 0.8.6, Python 3.10+, PyTorch, Transformers, PEFT, Accelerate, FastAPI/Uvicorn, Hugging Face Hub/Datasets, PyArrow/Parquet, FAISS exact search, optional hnswlib, Apache Kafka 4.3.1 KRaft, psycopg 3, asyncio/httpx, psutil/pynvml, Docker Compose, Catch2/CTest, pytest, Node test runner.

## Canonical Authority

This is the canonical execution plan. It supersedes conflicting or
miniature-only instructions in the four 2026-08-24 slice plans. Those documents
remain implementation history; reuse their completed components, but do not
follow their old task order, fixture substitutions, migration numbers, or
global constraints when this plan differs. In particular,
`006_online_runtime.sql` already exists: new migrations are
`007_scaled_experiment.sql` and `008_performance_experiments.sql`.

`2026-08-24-backend-seeding-dashboard.md` and
`2026-08-25-rolling-dataset-release-contracts.md` are completed prerequisite
references. Their working seeding and release-contract behavior is preserved;
this plan supersedes only later sequencing or scale assumptions that conflict.

Design authority:
`docs/superpowers/specs/2026-08-26-serving-training-topology-experiment-design.md`.

## Global Constraints

- Dataset repository: `dhelmy990/babel-wikipedia-experiment` (private).
- Model repository: `dhelmy990/babel-qwen-navigation-2016` (private).
- Base model: `Qwen/Qwen3-Embedding-0.6B` at `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
- Student output is exactly 100-dimensional and L2 normalized.
- Initial acquisition may download an authoritative Wikipedia source solely to mirror it. All semantic processing then reads the backend-authenticated, commit-pinned HF mirror.
- Bulk raw/prepared data require an absolute `BABEL_DATA_ROOT` outside the repository; never default it to `$HOME`, `~`, `/`, or the worktree.
- Publish rolling additions to the single dataset repository's `main` and record every returned commit SHA.
- Dashboard is the operator control point for population, start, graceful stop, topology/model selection, and scale advancement.
- `same_host_split` is default; `same_process` is control; `same_host_isolated` measures enforceable isolation; `cross_host` remains optional.
- pgvector is durable/default. hnswlib is an explicit snapshot from checksum-identical pgvector rows, never a fallback.
- Only synthetic-created Babels may enter the candidate index. A creator cannot create the same source article twice in a run.
- Include creates one durable directed edge from current source Babel to recommended Babel; exclude/ignore create none.
- Recommendation-walk probability is 0.40 and independent of hidden relevance decisions. Depth is two graph hops: request root and depth-one nodes, record but do not expand depth-two nodes. Cap each session at ten requests.
- Complete 2016 distilled Qwen is the immutable original; online runs create immutable children and never overwrite parents.
- Kafka carries feedback and identity, never model weights.
- A separate, user-launched training agent owns full Qwen training. Both human gates below are non-bypassable.
- Automated 3×3 validation uses the tiny fixture only. Full matrices are explicit operator experiments.
- Persist raw evidence before summaries, including dataset/model/vector/request/feedback/code/topology/placement/resource/hardware identity.
- Read `HF_TOKEN` from `/home/dhelmy990/Code/babel/.env` without printing, logging, committing, or embedding it.
- Reviews are experiment-focused and time-boxed. Block only defects invalidating data, real-model integration, graph semantics, gates, or measurements; backlog production hardening.
- Preserve untracked `artifacts/`, `build/`, `node_modules/`, and `state/`.

## Preserve, Replace, and Defer

| Classification | Components |
|---|---|
| Preserve | C++ dashboard/security, seed progress/retries/importer, HF seeding/no-live fallback, PostgreSQL, Kafka, synchronous POST timings, immutable registry, graceful stop, pgvector materialization, checkpoints/sync, logs, benchmark raw timing. |
| Replace/extend | 80-row pilot, synthetic June/July, NumPy encoder, V1 new-Babel-only requests, non-durable accepted edges, fixed controls, sequential load, single topology, local-only artifacts. |
| Smoke evidence | Existing 50-creator/100-Babel run and 500-request report prove plumbing only. |
| Defer | Malicious artifact defenses, multi-writer Hub CAS, hashed CUDA closure, SBOM/signing, distributed training, polished governance, cross-host without same-host evidence. |

## Human Gates and Execution Order

```text
Tasks 1-3: audit, source mirror, complete 2016
    -> GATE A: STOP; user launches separate training agent
    -> user confirms real batch/backward/checkpoint healthy
Tasks 4-5: real sampled June/July snapshots plus serving-adapter preparation
    -> GATE B: STOP until final trained artifact is published
Tasks 6-13: real integration, graph, topology, dashboard, experiments
```

Elapsed time, a partial checkpoint, or unrelated user activity never passes a
gate. Only an explicit user message does.

## Orchestrator Fleet Map

Maximum concurrency: orchestrator plus three workers. Orchestrator owns gates,
integration, commit selection, remote verification, and review severity.

```text
Wave 0: Orchestrator -> Task 1 baseline

Wave 1 before Gate A:
  Agent A -> Task 2 source mirror
  Agent B -> Task 3 full-2016 builder
  Agent C -> Task 3 validation/handoff lane
  Orchestrator integrates -> GATE A -> STOP

Wave 2 after user reports training healthy:
  Agent A -> Task 4 June/July
  Agent B -> Task 5 adapter skeleton
  Agent C -> dataset/leakage acceptance
  Orchestrator integrates -> GATE B -> STOP

Wave 3 after final artifact:
  Agent A -> Task 6 real Qwen serving
  Agent B -> Task 7 population tests/database preparation; real population waits for Task 6
  Agent C -> Task 8 graph walks
  Orchestrator integrates Task 6, then completes Task 7 real population

Wave 4:
  Agent A -> Task 9 topologies/distributor
  Agent B -> Task 10 dashboard/progress/results
  Agent C -> Task 11 benchmark/resources
  Orchestrator integration gate

Wave 5:
  Agent A -> Task 12 tiny 3x3/fault smoke
  Agent B -> Task 12 controlled scale lane
  Agent C -> Task 13 publication/handoff
  Orchestrator final acceptance
```

Each worker commits only scoped files and reports its SHA. The orchestrator
integrates in task order and supplies the next phase's pinned identities.

---

### Task 1: Freeze the Baseline and Gap Receipt

**Files:**
- Create: `docs/experiments/scaled-baseline-audit.md`
- Modify: `docs/backlog/post-interview-hardening.md`

**Interfaces:**
- Consumes: branch at/after `0a27396`, old plans, Friday handoff/report.
- Produces: a closed `preserve|replace|new|deferred` receipt mapping every critical gap to Tasks 2-13.

- [ ] Record Git/HF/data/model/run identities. Label pilot/miniature artifacts non-scale.
- [ ] Map these exact replacements: complete 2016, real 100k June/July engineering snapshots, real Qwen adapter, real pgvector vectors, durable edges, walks, topology modes, concurrent load, saved trials, rolling publication.
- [ ] Run the preserved baseline without bulk jobs:

```bash
python3 -m pytest data_pipeline/tests training/tests -q
PYTHONPATH=online/src python3 -m pytest online/tests -q
PYTHONPATH=benchmark/src:online/src python3 -m pytest benchmark/tests -q
cmake --build --preset test && ctest --preset test --output-on-failure
npm test
```

- [ ] Record exact results. Diagnose failures; do not replace working subsystems.
- [ ] Keep only production-only concerns in the hardening backlog.
- [ ] Commit:

```bash
git add docs/experiments/scaled-baseline-audit.md docs/backlog/post-interview-hardening.md
git commit -m "docs: freeze scaled experiment baseline"
```

**Next-phase context:** Reject reimplementation of preserved plumbing; verify
that every miniature substitution has an owner.

---

### Task 2: Mirror Authoritative Sources to Private Hugging Face

**Files:**
- Create: `data_pipeline/src/babel_data/mirror.py`
- Create: `data_pipeline/tests/test_mirror.py`
- Modify: `data_pipeline/src/babel_data/sources.py`
- Modify: `data_pipeline/src/babel_data/cli.py`
- Modify: `data_pipeline/src/babel_data/release.py`
- Create: `docs/runbooks/private-source-mirror.md`

**Interfaces:**
- Consumes: existing source manifests/checksums, `BABEL_DATA_ROOT`, `HF_TOKEN`.
- Produces: `SourceMirrorReceiptV1`, `mirror_source()`, `open_processing_source()`, CLI `babel-data mirror-source`, and pinned HF commits.

- [ ] Write failing policy/verification tests:

```python
def test_processing_requires_verified_hf_mirror(fake_hub, source):
    receipt = mirror_source(source, fake_hub, repository=DATASET_REPO)
    assert receipt.state == "remote_verified"
    assert receipt.local_sha256 == receipt.remote_sha256

def test_direct_wikimedia_processing_is_forbidden():
    with pytest.raises(SourcePolicyError, match="pinned Hugging Face mirror"):
        open_processing_source("https://archive.org/download/enwiki-20161001/enwiki-20161001-pages-articles-multistream.xml.bz2")
```

- [ ] Run `python3 -m pytest data_pipeline/tests/test_mirror.py -v`; expect missing APIs.
- [ ] Define `SourceMirrorReceiptV1(source_id, authoritative_url, expected_sha256, bytes, repository, path_in_repo, remote_commit_sha, remote_sha256, state="remote_verified")`.
- [ ] Download beneath `BABEL_DATA_ROOT/raw-mirror-staging`, verify authoritative checksum, upload beneath `sources/{receipt.source_id}/`, resolve commit, download/verify remote bytes, then return the receipt. Never process staging input.
- [ ] Make `open_processing_source(repository, revision, path, token, cache_root)` resolve exactly one commit, cache beneath `BABEL_DATA_ROOT/hf-cache/{revision}/`, verify the receipt, and return the pinned file.
- [ ] Mirror one source at a time and save non-secret receipts. Do not display environment variables.
- [ ] Test and commit:

```bash
python3 -m pytest data_pipeline/tests/test_mirror.py data_pipeline/tests/test_sources.py data_pipeline/tests/test_hub.py -v
git diff --check
git add data_pipeline/src/babel_data data_pipeline/tests docs/runbooks/private-source-mirror.md
git commit -m "feat: mirror authoritative sources through private hub"
```

**Next-phase context:** Review authenticity and remote-only processing, not
multi-writer or filesystem attack scenarios.

---

### Task 3: Build and Publish Complete 2016 plus Training Handoff

**Files:**
- Create: `data_pipeline/src/babel_data/full_2016.py`
- Create: `data_pipeline/tests/test_full_2016.py`
- Modify: `data_pipeline/src/babel_data/reconcile.py`
- Modify: `data_pipeline/src/babel_data/shard.py`
- Modify: `data_pipeline/src/babel_data/cli.py`
- Modify: `training/src/babel_training/validation.py`
- Create: `training/src/babel_training/full_run.py`
- Create: `training/tests/test_full_run.py`
- Create: `prompts/full-2016-training-handoff.md`
- Create: `docs/runbooks/full-2016-distillation.md`

**Interfaces:**
- Consumes: Task 2 pins; existing teacher/XML parsers, release contracts, Hub publisher, training package.
- Produces: complete `distillation_2016`, final commit, `FullValidationPlanV1`, Gate A handoff.

- [ ] Write failing accounting and validation-plan tests:

```python
def test_every_teacher_row_is_accounted(result):
    assert result.teacher_total == result.matched + result.excluded
    assert result.rows_written == result.matched
    assert result.duplicate_article_keys == result.invalid_vector_count == 0

def test_validation_defaults():
    plan = FullValidationPlanV1.for_corpus(400_000)
    assert (plan.monitor_query_count, plan.monitor_candidate_count) == (2_000, 50_000)
    assert (plan.final_query_count, plan.final_candidate_count) == (20_000, 100_000)
    assert plan.exact_index == "faiss.IndexFlatIP"
    assert plan.hnsw_exact_audit_queries == 2_000
```

- [ ] Run focused tests and confirm `full_2016`/`FullValidationPlanV1` are absent.
- [ ] Implement `build_complete_2016(source_pin, data_root, output_root, resume=True)`: read only HF-pinned sources, stream bounded batches, preserve page/revision/title/text, require finite 100d vectors, deterministic split, range journal, and explicit exclusion reasons.
- [ ] Emit rolling Parquet shards beneath `distillation_2016/{split}/`; after each upload record commit and remotely load one row. Promote readiness to `complete` only after inventory, counts, digests, and proof agree.
- [ ] Freeze validation/search:

```text
monitor: 2,000 deterministic queries vs 50,000 candidates
final queries: deterministic 5%, clamped 10,000..50,000 and to available rows
final candidates: min(100,000, complete corpus), whole corpus when smaller
exact oracle: normalized float32 faiss.IndexFlatIP
metrics: Recall/NDCG@10/50, paired cosine, NaNs, norms, examples
HNSW audit: full index with 2,000 exact-oracle audit queries
```

- [ ] Persist selected article keys/checksum. Monitoring may reuse the fixed subset; final artifact acceptance uses the final subset.
- [ ] Write `prompts/full-2016-training-handoff.md` with pinned non-secret dataset/model/source SHAs, exact Colab commands, full settings, checkpoint/resume, validation, and publication instructions. Require the training agent to report first successful backward/checkpoint and final model/validation commits.
- [ ] Execute the real build and remote acceptance. Do not launch training.
- [ ] Commit:

```bash
git add data_pipeline/src/babel_data data_pipeline/tests training/src/babel_training training/tests \
  prompts/full-2016-training-handoff.md docs/runbooks/full-2016-distillation.md
git commit -m "feat: publish complete 2016 training release"
```

## GATE A — STOP FOR USER-LAUNCHED TRAINING

Give the user the pinned dataset SHA and handoff. Continue only after the user
explicitly confirms that the separate training agent loaded the exact release,
completed real forward/backward work, and saved its first normal checkpoint.

**Next-phase context:** Review completeness, remote loading, finite vectors,
deterministic validation, and handoff usability only.

---

### Task 4: Produce Real 100k June and July Engineering Snapshots

**Files:**
- Create: `data_pipeline/src/babel_data/monthly/sources.py`
- Create: `data_pipeline/src/babel_data/monthly/selection.py`
- Create: `data_pipeline/src/babel_data/monthly/build.py`
- Create: `data_pipeline/tests/monthly/test_selection.py`
- Create: `data_pipeline/tests/monthly/test_real_build.py`
- Modify: `data_pipeline/src/babel_data/monthly/catalog.py`
- Modify: `data_pipeline/src/babel_data/monthly/hidden.py`
- Modify: `data_pipeline/src/babel_data/monthly/crosswalk.py`
- Modify: `data_pipeline/src/babel_data/cli.py`
- Create: `docs/runbooks/monthly-environment-build.md`

**Interfaces:**
- Consumes: Gate A approval and pinned monthly Wikipedia/Clickstream mirrors.
- Produces: real 100k June/July observed+hidden engineering-snapshot configurations and one connected commit.

- [ ] Write tests rejecting demo fixtures as real releases and scanning observed schemas for hidden fields.
- [ ] Write selection tests proving exactly 80,000 crosswalked shared identities, exactly 20,000 disjoint monthly-supplement identities per month, exactly 100,000 rows per monthly catalog, and an exactly 120,000-identity union. Insufficient eligible rows, quota drift, nondeterministic ordering, or supplement overlap must fail publication.
- [ ] Implement `EngineeringSnapshotPolicyV1` in `selection.py`. Select required dashboard seeds and valid one-hop neighbors first, then high-traffic Clickstream identities, then a seeded-hash tail. Resolve supplement collisions through the title-independent crosswalk, deterministically retain one month, and backfill the other. Emit policy version, seed, ordered identity checksums, membership, source pins, and exclusion counts.
- [ ] Implement one reusable builder for namespace-zero pages, redirects, pagelinks, Wikidata identity, and Clickstream from pinned HF objects. Restrict output to the selected monthly identities and induced endpoints. Output canonical catalog/text, directed deduplicated graph, preserved transition counts, hidden inputs, and title-independent cross-period identity.
- [ ] Build/publish/remote-verify June first as a `100k_sampled_engineering_snapshot`. Reclaim only explicitly enumerated verified bulk inputs.
- [ ] Build July with the identical parser and policy versions, then publish the June↔July crosswalk and shared/supplement manifests. Reject title-only joins, duplicate keys, missing edge endpoints, invalid transitions, schema drift, and any claim that the snapshots are complete monthly Wikipedias.
- [ ] Publish a connected commit containing complete 2016 plus both real sampled monthly configurations. Treat complete monthly environments as post-interview evidence expansion, not an integration prerequisite.
- [ ] Verify dashboard seeding pins/caches this commit, emits safe paragraph HTML, preserves canonical page IDs, retries/duplicates/progress, and cannot fall back to MediaWiki.
- [ ] Test and commit:

```bash
python3 -m pytest data_pipeline/tests/monthly data_pipeline/tests/test_hub.py -v
cmake --build --preset test
ctest --preset test -R "huggingface|seed|wikipedia_import" --output-on-failure
git add data_pipeline/src/babel_data data_pipeline/tests docs/runbooks/monthly-environment-build.md
git commit -m "feat: publish sampled June and July environments"
```

**Next-phase context:** The 100k snapshots must use real pinned text, graph, and
Clickstream inputs and must not be replaced by the demo generator. Review exact
80k/20k quotas, the 120k union, deterministic selection, schemas, provenance,
and leakage. Do not block integration on complete monthly Wikipedia expansion.

---

### Task 5: Prepare the Qwen Training-to-Serving Adapter

**Files:**
- Create: `online/src/babel_online/model/distilled_artifact.py`
- Create: `online/src/babel_online/model/qwen_encoder.py`
- Create: `online/tests/model/test_distilled_artifact.py`
- Create: `online/tests/model/test_qwen_encoder.py`
- Modify: `online/src/babel_online/contracts.py`
- Modify: `online/pyproject.toml`
- Create: `docs/runbooks/qwen-serving-adapter.md`

**Interfaces:**
- Consumes: Gate A health approval and `babel_training.hub` export layout.
- Produces: `DistilledArtifactV1.load()` and `Qwen100Encoder.encode(texts) -> np.ndarray`. Fixture tests cannot pass real acceptance.

- [ ] Write tests asserting base revision, last-token pooling, 1024→100 projection, LoRA, normalization, dataset/model/checksums, and rejection of fixture acceptance.
- [ ] Add one closed training-to-serving manifest containing dataset/model commits, input format/max length, pooling, projection/adapter checksums, normalization, and validation checksum. Reuse training export weights.
- [ ] Load pinned base/tokenizer/LoRA/projection using Transformers/PEFT/Safetensors and return finite normalized float32 `[batch,100]` on configured CPU/CUDA.
- [ ] Prove structure with a fixture/mock but keep `assert_real_acceptance()` false without a private model commit.
- [ ] Test and commit:

```bash
PYTHONPATH=online/src python3 -m pytest online/tests/model/test_distilled_artifact.py online/tests/model/test_qwen_encoder.py -v
git add online/src/babel_online online/tests/model online/pyproject.toml docs/runbooks/qwen-serving-adapter.md
git commit -m "feat: define distilled Qwen serving adapter"
```

## GATE B — STOP FOR FINAL TRAINED ARTIFACT

Continue only when the user confirms the final private model commit containing
complete-run adapter, projection, manifest, validation report, and checksums. A
pilot, first checkpoint, mock, or handwritten manifest does not pass.

**Next-phase context:** Contract compatibility may pass; real integration may
not be declared before Gate B.

---

### Task 6: Serve the Real Distilled Qwen Original

**Files:**
- Modify: `online/src/babel_online/model/qwen_encoder.py`
- Modify: `online/src/babel_online/model/item_tower.py`
- Modify: `online/src/babel_online/model/artifact.py`
- Modify: `online/src/babel_online/model/registry.py`
- Modify: `online/src/babel_online/serving/state.py`
- Modify: `online/src/babel_online/serving/app.py`
- Create: `online/tests/e2e/test_real_qwen_serving.py`
- Modify: `online/tests/serving/test_recommendations.py`

**Interfaces:**
- Consumes: Gate B model commit and Task 5 loader.
- Produces: immutable real `ModelManifestV2` and measured Qwen serving.

- [ ] Add a token-gated test loading the real commit and asserting finite normalized `(1,100)` output.
- [ ] Register an immutable original with model/dataset/base/projection/adapter/validation checksums and null parent. Reject the Friday stand-in for scale runs.
- [ ] Replace deterministic query/item encoding with `Qwen100Encoder`; keep creator context and online ranking separate.
- [ ] Preserve seven timing stages. `encode` covers tokenization through normalization; record cache/device/batch identity separately.
- [ ] Restart serving and prove fixed text yields the same vector checksum and original checksum.
- [ ] Commit:

```bash
git add online/src/babel_online online/tests
git commit -m "feat: serve the real distilled Qwen encoder"
```

**Next-phase context:** Require real artifact evidence and timing correctness,
not fleet-grade GPU management.

---

### Task 7: Populate Real Qwen Vectors in pgvector

**Files:**
- Create: `online/src/babel_online/model/population.py`
- Create: `online/src/babel_online/model/source_vector_cache.py`
- Create: `online/tests/model/test_population.py`
- Create: `online/tests/model/test_source_vector_cache.py`
- Modify: `online/src/babel_online/model/pgvector_index.py`
- Modify: `online/src/babel_online/runtime/database.py`
- Modify: `online/src/babel_online/runtime/worker.py`

**Interfaces:**
- Consumes: Task 6 encoder, real catalog, existing vector tables.
- Produces: resumable `populate_created_babel_vectors()` and active checksum-bearing snapshot.

- [ ] Test exact created/indexed ID equality, no catalog-only vectors, dimension 100, and formal readiness false below 10,000.
- [ ] Encode deterministic ID-ordered bounded batches; insert versioned vectors and journal last committed Babel. Reject content/model/space/checksum mismatch on resume.
- [ ] Activate only after created count equals indexed count; record snapshot checksum, duration, rows/s, table/index bytes, failures.
- [ ] Implement `SourceVectorResolver`: a newly created root is encoded by real Qwen and inserted into a bounded LRU; an existing walk source uses the LRU when present and otherwise loads its active pgvector row. Record `qwen_encode|cache_hit|pgvector_load` without changing the vector bytes. This is the cache-recency behavior exercised by Task 8 walks.
- [ ] Save `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` at formal scale. Do not claim HNSW until the plan actually uses the cosine HNSW index.
- [ ] Test and commit:

```bash
PYTHONPATH=online/src python3 -m pytest online/tests/model/test_population.py online/tests/model/test_source_vector_cache.py online/tests/model/test_candidate_index.py -v
git add online/src/babel_online online/tests/model
git commit -m "feat: populate real Qwen vectors in pgvector"
```

**Next-phase context:** Long encoding is legitimate work, not authority to use
fixture vectors.

---

### Task 8: Persist Edges and Bounded Recommendation Walks

**Files:**
- Create: `backend/migrations/007_scaled_experiment.sql`
- Modify: `online/src/babel_online/contracts.py`
- Create: `online/src/babel_online/simulation/walk.py`
- Create: `online/src/babel_online/simulation/scheduler.py`
- Create: `online/tests/simulation/test_walk.py`
- Create: `online/tests/simulation/test_scheduler.py`
- Modify: `online/src/babel_online/simulation/engine.py`
- Modify: `online/src/babel_online/runtime/database.py`
- Modify: `online/src/babel_online/runtime/worker.py`
- Modify: `schemas/online/*`

**Interfaces:**
- Consumes: created Babels, recommendation service, hidden decision function.
- Produces: V2 run/request/feedback contracts, `RecommendationWalk`, `experiment_edges`.

- [ ] Write tests proving include creates unique `A→B`, exclude/ignore create none, requests occur only at depths 0/1, edges may reach depth 2, cap is ten, and replay is deterministic.
- [ ] Migration 007 adds immutable topology/scale/walk fields and:

```sql
CREATE TABLE experiment_edges (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  source_babel_id uuid NOT NULL,
  target_babel_id uuid NOT NULL,
  acting_creator_id uuid NOT NULL,
  request_id uuid NOT NULL,
  feedback_event_id uuid NOT NULL,
  traversal_session_id uuid NOT NULL,
  traversal_depth integer NOT NULL CHECK (traversal_depth BETWEEN 1 AND 2),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, source_babel_id, target_babel_id),
  CHECK (source_babel_id <> target_babel_id),
  FOREIGN KEY (run_id, source_babel_id) REFERENCES experiment_babels(run_id, babel_id),
  FOREIGN KEY (run_id, target_babel_id) REFERENCES experiment_babels(run_id, babel_id)
);
```

- [ ] V2 uses `sourceBabelId/sourceArticleKey`, traversal session, parent request, and depth. Retain V1 readers for old artifacts; scaled runs emit V2.
- [ ] Implement deterministic breadth-first walk: 0.40 start roll, hidden include/exclude/ignore, persist include edge, independent 0.40 continuation, unvisited set, depth/cap stop.
- [ ] Implement a bounded concurrent creator scheduler. Each creator retains a deterministic local event sequence, while up to `concurrentUsers` creation/walk sessions overlap. Persist the resulting schedule so topology conditions replay identical creation and recommendation work.
- [ ] On duplicate `(run,source,target)` inclusion, keep the first deterministic edge row and retain every feedback action. Export edges to Parquet and reconstruct the distinct graph from bounded Kafka feedback by selecting the earliest `(occurredAtNs,eventId)` per pair; require exact edge-set and canonical provenance agreement.
- [ ] Test and commit:

```bash
PYTHONPATH=online/src python3 -m pytest online/tests/simulation online/tests/runtime/test_database.py -v
cmake --build --preset test && ctest --preset test --output-on-failure
git add backend/migrations/007_scaled_experiment.sql online/src online/tests schemas/online
git commit -m "feat: persist bounded recommendation walks"
```

**Next-phase context:** Review direction, cross-creator endpoints, separate
decisions, depth, determinism, and caps.

---

### Task 9: Implement Same-Process and Split Topologies

**Files:**
- Create: `online/src/babel_online/runtime/topology.py`
- Create: `online/src/babel_online/model/state_distributor.py`
- Create: `online/tests/runtime/test_topology.py`
- Create: `online/tests/model/test_state_distributor.py`
- Modify: `online/src/babel_online/runtime/supervisor.py`
- Modify: `online/src/babel_online/runtime/cli.py`
- Modify: `online/src/babel_online/runtime/control.py`
- Modify: `compose.yaml`
- Modify: `Justfile`

**Interfaces:**
- Consumes: Tasks 6-8 runtime/checkpoints.
- Produces: `Topology`, `PlacementManifestV1`, `ModelStateDistributor.activate()`.

- [ ] Test identical replay semantic checksums for `same_process` and `same_host_split`; killing split trainer must leave serving HTTP 200 on last version.
- [ ] `same_process` hosts serving/trainer components in one process. `same_host_split` launches independent healthy executables sharing only immutable model-state files for weights.
- [ ] Add same-host isolation with recorded/verified CPU affinity and memory limits. Report shared ordinary GPU as not isolated unless separate devices/MIG are verified.
- [ ] Distributor validates parent/space/files/checksums, registers immutable child, requests activation, runs known-vector probe, atomically swaps, and retains previous/original on failure. Never use Kafka for weights.
- [ ] Persist placement, requested/verified limits, process/container IDs, trainer/serving versions, publish/activation times, and staleness. Make split the Python default; Task 10 makes it the C++ dashboard default.
- [ ] Test and commit:

```bash
PYTHONPATH=online/src python3 -m pytest online/tests/runtime/test_topology.py online/tests/model/test_state_distributor.py -v
docker compose config --quiet
git add online/src online/tests compose.yaml Justfile
git commit -m "feat: compare split and monolithic runtimes"
```

**Next-phase context:** Do not require a second machine; verify genuine process
independence and semantic parity.

---

### Task 10: Add Saved Dashboard Trials and Independent Progress

**Files:**
- Create: `backend/migrations/008_performance_experiments.sql`
- Modify: `backend/include/babel/application/experiment_models.hpp`
- Modify: `backend/include/babel/application/experiment_ports.hpp`
- Modify: `backend/include/babel/application/experiment_service.hpp`
- Modify: `backend/src/application/experiment_service.cpp`
- Modify: `backend/include/babel/http/experiment_controller.hpp`
- Modify: `backend/src/http/experiment_controller.cpp`
- Create: `backend/admin/trial-progress.js`
- Create: `backend/admin/scalability-dashboard.js`
- Modify: `backend/admin/experiment-dashboard.js`
- Modify: `backend/admin/index.html`
- Modify: `backend/admin/dashboard.css`
- Create: `tests/js/trial-progress.test.js`
- Create: `tests/js/scalability-dashboard.test.js`

**Interfaces:**
- Consumes: Task 8 V2 settings, Task 9 topology/placement, benchmark status.
- Produces: immutable experiment/condition rows, APIs, paired sliders/numeric inputs, pure progress view.

- [ ] Test split/0.40/depth-two defaults, slider↔numeric synchronization, server validation, and a pure ETA/progress mapper.
- [ ] Migration 008 stores immutable topology, placement checksum, dataset/model/vector/request/feedback/hardware/resource identities, status, safety receipt, artifact SHA, and progress snapshots.
- [ ] Add controls for topology, model, dataset, backend, walk probability/depth/cap, sync/training, warmup/duration/RPS/safety. Add paired slider+numeric input for seeded articles, target created Babels, concurrent simulated users; valid custom numbers may exceed slider range.
- [ ] `trial-progress.js` polls persisted status only and imports no trainer/Kafka/serving/benchmark code. Show phase, condition `n/9`, seeded/created/indexed/requested/completed, elapsed, recent rate, ETA, draining.
- [ ] Show graph/walk/cache, placement/resources, Kafka/trainer, trainer-vs-serving staleness, activation spikes, and artifact links.
- [ ] Expose protected endpoints using existing nonce/Host/Origin checks; auto-advance false:

```text
POST /admin/api/v1/performance
GET  /admin/api/v1/performance/{experimentId}
POST /admin/api/v1/performance/{experimentId}/graceful-stop
POST /admin/api/v1/performance/{experimentId}/approve-next-scale
```
- [ ] Test and commit:

```bash
cmake --build --preset test
ctest --preset test -R "experiment|performance" --output-on-failure
node --test tests/js/trial-progress.test.js tests/js/scalability-dashboard.test.js
git add backend tests/js
git commit -m "feat: control and save scalability trials"
```

**Next-phase context:** Review truth/immutability, not polish. Progress UI failure
must not mutate or stop a run.

---

### Task 11: Add Concurrent Benchmarking and Interference Analysis

**Files:**
- Modify: `benchmark/src/babel_benchmark/contracts.py`
- Modify: `benchmark/src/babel_benchmark/runner.py`
- Modify: `benchmark/src/babel_benchmark/analysis.py`
- Modify: `benchmark/src/babel_benchmark/cli.py`
- Create: `benchmark/src/babel_benchmark/resources.py`
- Create: `benchmark/src/babel_benchmark/topology.py`
- Create: `benchmark/src/babel_benchmark/cache.py`
- Create: `benchmark/tests/test_concurrency.py`
- Create: `benchmark/tests/test_resources.py`
- Create: `benchmark/tests/test_topology.py`
- Modify: `benchmark/tests/test_analysis.py`
- Modify: `benchmark/pyproject.toml`

**Interfaces:**
- Consumes: saved condition manifest and POST endpoint.
- Produces: concurrent schedules, raw request/resource/cache rows, all interference ratios.

- [ ] Test deterministic request identity/order with `in_flight > 1` and ratios `(10,12,15) -> (1.2,1.5,1.25)`.
- [ ] Generalize condition identity to `(topology, training_enabled, activation_enabled, retrieval_backend)` while retaining Friday V1 readers.
- [ ] Use asyncio/httpx with bounded clients/semaphore for closed/open-loop schedules. Record intended/actual start, in-flight, queue delay, timings, cache, versions, snapshots, outcome; preserve timeout/error rows.
- [ ] Sample per-service CPU/RSS/threads/I/O, host memory/disk/network, optional GPU, Kafka lag, step rate, checkpoint/activation, trainer/serving versions, staleness. Missing GPU is unavailable, not zero.
- [ ] Compute and display:

```text
Itraining = p95(training no activation) / p95(serving only)
Ifull = p95(training plus activation) / p95(serving only)
IActivationIncrement = p95(training plus activation) / p95(training no activation)
percent = (ratio - 1) * 100
```

- [ ] Keep pgvector/hnswlib comparison separate: fixed topology, identical ordered IDs/vector bytes/snapshot/queries, preparation separate from steady latency, Recall@10/50 exact audit.
- [ ] Test and commit:

```bash
PYTHONPATH=benchmark/src:online/src python3 -m pytest benchmark/tests -v
git add benchmark
git commit -m "feat: benchmark concurrent topology interference"
```

**Next-phase context:** Single sequential clients are smoke evidence only.

---

### Task 12: Run Tiny 3×3 Smoke and Controlled Scale Experiments

**Files:**
- Create: `benchmark/src/babel_benchmark/matrix.py`
- Create: `benchmark/src/babel_benchmark/scale.py`
- Create: `benchmark/tests/test_matrix.py`
- Create: `benchmark/tests/test_scale.py`
- Create: `docs/experiments/scaled-performance-report.md`
- Modify: `docs/runbooks/online-experiment.md`

**Interfaces:**
- Consumes: Tasks 6-11.
- Produces: bounded smoke receipt, formal population receipt, topology/retrieval/scale/fault evidence.

- [ ] Test tiny matrix uses exactly nine conditions, at most 20 requests each/180 total, current fixture, strict timeout, and `formal_performance_claim=False`.
- [ ] Run smoke first; verify startup/cleanup, edges, progress, raw persistence, ratios, and trainer-failure availability. Never wait for full population in automated tests.
- [ ] From dashboard populate default 10,000 distinct created/indexed Babels using real model. Save manifest/checksum; formal measurement requires created=indexed≥threshold.
- [ ] At first approved cohort run all three topologies × three conditions with identical schedules. At higher cohorts compare monolith against selected split to bound matrix size.
- [ ] Inject trainer kill/restart, Kafka pause/resume, invalid state, serving restart; record availability, lag, detection/recovery, duplicates/loss, versions.
- [ ] Advance nested cohorts manually `50→100→500`, optionally `1,000→5,000→10,000`. Stop on memory >90% sustained, disk <10 GiB, errors/timeouts >5% for two windows, increasing lag at max backpressure, or process/checkpoint failure.
- [ ] After real pgvector HNSW is observed, run fixed-topology hnswlib comparison and report preparation/memory/steady latency/throughput/recall separately.
- [ ] Write report distinguishing smoke, population, topology, retrieval, scale, and faults; label same-host limitations.
- [ ] Test and commit:

```bash
PYTHONPATH=benchmark/src:online/src python3 -m pytest benchmark/tests/test_matrix.py benchmark/tests/test_scale.py -v
git add benchmark docs/experiments/scaled-performance-report.md docs/runbooks/online-experiment.md
git commit -m "feat: run controlled recommendation scale experiments"
```

**Next-phase context:** Review the smallest real complete run first. Long scale
runs generate evidence; they do not block merging working orchestration.

---

### Task 13: Publish Rolling Artifacts and Final Handoff

**Files:**
- Create: `benchmark/src/babel_benchmark/hub.py`
- Create: `benchmark/tests/test_hub.py`
- Modify: `online/src/babel_online/feedback/export.py`
- Modify: `online/src/babel_online/model/registry.py`
- Create: `prompts/scaled-experiment-handoff.md`
- Create: `docs/runbooks/scaled-experiment.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: accepted datasets/models/feedback/edges/raw measurements/reports.
- Produces: pinned rolling HF commits and dashboard-first operator handoff.

- [ ] Test each run bundle requires `manifest.json`, feedback/edges/requests/resources Parquet, `summary.json`, `report.md`, and `checksums.json`.
- [ ] Upload one-operator immutable bundles beneath `runs/{run_id}/`; include progress, topology/placement, hardware, model ledger, vector snapshots. Do not build multi-writer CAS.
- [ ] Publish children without replacing parents; record parent/original and returned commit; keep all compatible models separately selectable.
- [ ] At returned commit reload manifest/checksums/summary, one row per Parquet, and model manifest. Reject secret-bearing files and accepted-path overwrite.
- [ ] Write handoff covering external data root, secrets, dependencies, pins, population, sliders/custom inputs, topology/model selection, progress, stop, saved results, child reuse, smoke/formal labels, optional next scale.
- [ ] Run final tests:

```bash
python3 -m pytest data_pipeline/tests training/tests -q
PYTHONPATH=online/src python3 -m pytest online/tests -q
PYTHONPATH=benchmark/src:online/src python3 -m pytest benchmark/tests -q
cmake --build --preset test && ctest --preset test --output-on-failure
npm test
git diff --check
```

- [ ] Complete one real dashboard-controlled split run, confirm durable child,
zero final lag, durable edges, and remote bundle reload.
- [ ] Commit:

```bash
git add benchmark online prompts/scaled-experiment-handoff.md docs/runbooks/scaled-experiment.md README.md
git commit -m "docs: hand off scaled recommendation experiment"
```

**Next-phase context:** Add cross-host only if same-host isolation remains
insufficient; add a true parameter server only if immutable activation is the
measured bottleneck.

---

## Master Acceptance Gate

- [ ] Wikipedia processing uses a verified pinned private-HF mirror.
- [ ] Complete 2016 inventory is accounted for and streams valid text plus finite 100d teacher vectors.
- [ ] User-launched training produces a real artifact passing frozen full validation/search.
- [ ] Real 100k June/July sampled observed+hidden environments publish with exact 80k shared/20k monthly quotas, a 120k union, and no leakage.
- [ ] Serving uses pinned real Qwen+LoRA+projection and normalized 100d vectors.
- [ ] pgvector contains only real-model vectors for synthetic-created Babels; formal default threshold is 10,000 created/indexed.
- [ ] Includes durably form unique directed experiment edges; relevance and 40% continuation remain separate.
- [ ] Walk depth/cap semantics and deterministic replay hold.
- [ ] Split is dashboard default; monolith is equivalent control; isolation reports verified limits only.
- [ ] Trainer failure leaves serving available and activation preserves last valid/original state.
- [ ] Dashboard offers sliders+numeric inputs, independent progress, saved trials, topology/model selection, graph/cache/training telemetry.
- [ ] Tiny 3×3 completes quickly and makes no performance claim.
- [ ] Formal concurrent reports include latency/throughput/errors/resources/cache/lag/staleness/activation and all three interference ratios.
- [ ] pgvector/hnswlib uses one checksum-identical snapshot and stays separate from topology conclusions.
- [ ] Dataset/model/feedback/edge/measurement/report artifacts remotely reload at recorded private-HF commits.
- [ ] Original and children remain separately selectable and immutable.
