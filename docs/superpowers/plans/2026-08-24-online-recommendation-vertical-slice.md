# Online Recommendation Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin dashboard start and gracefully stop a deterministic 50-creator June→July experiment that synchronously serves two-tower recommendations, publishes observable feedback through one Kafka topic, trains asynchronously, synchronizes versioned state, and saves an immutable child model.

**Architecture:** The implemented C++/Drogon backend remains the dashboard control plane and PostgreSQL owner. PostgreSQL/pgvector durably materializes lazy catalog vectors and the active vectors of Babels actually created in a run; serving uses pgvector HNSW by default or an explicitly selected hnswlib snapshot rebuilt from the same pinned PostgreSQL rows. A separately installable Python package runs the hidden simulator, loopback recommendation service, Kafka producer, single online trainer, model-state synchronization, and bounded feedback export; each run freezes its retrieval backend and publishes an immutable child without altering its parent.

**Tech Stack:** C++20, Drogon, PostgreSQL 18 with pgvector 0.8.6, Python 3.10+, PyTorch, Transformers, Safetensors, Datasets, Hugging Face Hub, FastAPI/Uvicorn, psycopg 3, pgvector-python, hnswlib, SciPy, confluent-kafka, Apache Kafka 4.3.1 in KRaft mode, PyArrow, psutil, pytest, Catch2, Node test runner.

## Global Constraints

- Requires the completed backend seeding-dashboard implementation and Slice 2's pinned connected dataset release.
- Requires Slice 2's `huggingface_wikipedia` provenance migration and pinned-Hub dashboard source; online work must not restore a live MediaWiki fallback.
- Starting artifact: immutable complete-2016 recommender in private `dhelmy990/babel-two-tower-recommender`.
- Dataset: private `dhelmy990/babel-wikipedia-experiment`, loaded only at one exact commit SHA per run.
- Default creators: 50; creator IDs and all randomness derive deterministically from the run seed.
- A creator may not create two Babels from the same source article; enforce sampling without replacement and `UNIQUE(run_id, creator_id, source_article_key)`.
- Different creators may create Babels from the same source article.
- Only Babels actually created by synthetic creators in the current run are recommendation candidates; uncreated monthly catalog articles are never candidates.
- Hugging Face remains canonical for prepared data/model artifacts; pgvector is the durable local runtime materialization derived from their pinned SHAs.
- Catalog embeddings are materialized lazily or in bounded batches for source articles selected by the cohort; the 50-creator run must not encode the complete monthly catalog.
- Retrieval backend is an immutable run field: `pgvector` by default or explicit `hnswlib`.
- pgvector uses cosine HNSW over active created-Babel vectors; hnswlib is disposable and rebuilt from one checksum-bearing pgvector snapshot.
- Retrieval never switches mid-run and never silently falls back between pgvector and hnswlib.
- The dashboard is the only operator surface for starting and gracefully stopping synthetic runs.
- Offline Qwen distillation is never launched or reported by this dashboard.
- Recommendation is a synchronous HTTP POST; Kafka is outside the request path.
- Kafka contains only schema-v1 observable feedback, keyed by creator ID, on `babel.feedback.v1`.
- Initial Kafka deployment is one `apache/kafka:4.3.1` KRaft broker with replication factor 1 and one continuously running training consumer.
- Automatic offset commits are disabled; offsets commit only after the matching training checkpoint is durable.
- Kafka never carries model weights; synchronization uses atomic run-local model-state directories.
- The distilled Qwen encoder remains frozen during fast online updates.
- Include is a positive, explicit exclude is a full-weight hard negative, and ignore has negative weight 0.25.
- The original model is immutable and cannot be deleted, overwritten, or promoted in place.
- June→July canonical runs retain the same run ID and training state; only the hidden environment and observable catalog change.
- Hidden PPR, graph, Clickstream, archetype, seed, relevance, and random-draw values never appear in recommendation, feedback, trainer, dashboard, or persisted activity-log schemas.
- Bind Python control and recommendation endpoints to loopback only; preserve the existing C++ admin nonce/origin protections.
- Use monotonic clocks for durations and UTC wall time only for timestamps.
- Use test-first development and commit after every task gate.

---

## Orchestrator Fleet Map

Maximum concurrency is one orchestrator plus three workers. Begin in isolated
worktrees created with `using-git-worktrees`. Each worker owns only the paths in
its lane; shared JSON Schemas and migrations are frozen by the orchestrator.

```text
Orchestrator / Task 1
cross-process contracts, fixtures, package skeleton
          |
          +-------------------- Wave 1 --------------------+
          |                         |                       |
 Agent A / Task 2          Agent B / Task 3        Agent C / Task 4
 vectors/run persistence   hidden-world engine     model + retrieval POST
          |                         |                       |
          +------------------ integration gate ------------+
                                    |
          +-------------------- Wave 2 --------------------+
          |                         |                       |
 Agent A / Task 5          Agent B / Task 6        Agent C / Task 7
 Kafka + bounded export    simulator event loop    C++ experiment API
          |                         |                       |
          +------------------ integration gate ------------+
                                    |
          +-------------------- Wave 3 --------------------+
          |                                                 |
 Agent A / Task 8                                  Agent C / Task 9
 trainer/checkpoint/sync                           dashboard controls/logs
          |                                                 |
          +------------------ integration gate ------------+
                                    |
                         Orchestrator / Task 10
                     supervisor + June→July acceptance
```

| Wave | Safe ownership | Integration gate |
|---|---|---|
| Foundation | Orchestrator owns `schemas/online/`, fixture envelopes, shared Python contract/config files | Contract and leakage tests pass before branching |
| Wave 1 | Agent A owns migration/C++ repositories and vector tables; B owns `online/.../hidden`; C owns Python materialization/retrieval/model/serving | C++, pgvector, Python, model-compatibility, and import-boundary suites pass together |
| Wave 2 | A owns Kafka/export; B owns simulator/client; C owns C++ experiment application/HTTP | A fixture recommendation produces one schema-valid Kafka event and one durable run row |
| Wave 3 | A owns trainer/sync; C owns admin HTML/CSS/JS and Node tests | Checkpoint/offset recovery and dashboard contracts pass before lifecycle composition |
| Final | Orchestrator alone owns runtime supervisor, Justfile/Compose integration, acceptance docs | Full tiny June→July run, graceful drain, export, and immutable child verification pass |

If a lane needs to change a frozen schema, C++ DTO, or Python dataclass, it
must send the proposed diff to the orchestrator and wait. Do not let agents
independently generate competing JSON shapes. Merge and review after every
wave; do not wait until the end to integrate three process boundaries.

## Target File Map

```text
schemas/online/
  experiment-run-v1.json             immutable launch configuration
  recommendation-request-v1.json     observable synchronous request
  recommendation-response-v1.json    candidates, version, stage timings
  feedback-event-v1.json             atomic Kafka event
  activity-log-v1.json               dashboard-safe structured log
  model-manifest-v1.json              immutable lineage/compatibility
  embedding-space-v1.json            100d materialization identity
  hnsw-snapshot-v1.json               rebuildable index provenance
fixtures/online/
  tiny/                               two months, six creators, catalogs/graphs
  requests.jsonl                      serving contract examples
  feedback.jsonl                      trainer/export examples
online/
  pyproject.toml
  requirements.lock
  src/babel_online/
    config.py                         validated run/runtime settings
    contracts.py                      generated/validated v1 dataclasses
    observable.py                     catalog-only Hugging Face loader
    hidden/                            simulator-only loaders and algorithms
      loader.py
      profiles.py
      ppr.py
      relatedness.py
    model/
      item_tower.py
      context_tower.py
      candidate_index.py
      materialization.py
      pgvector_index.py
      hnswlib_index.py
      artifact.py
      registry.py
    serving/
      app.py
      timings.py
      state.py
    feedback/
      bus.py
      kafka.py
      export.py
    simulation/
      sampling.py
      decisions.py
      client.py
      engine.py
    training/
      pairs.py
      loss.py
      checkpoint.py
      synchronizer.py
      consumer.py
    runtime/
      database.py
      telemetry.py
      resources.py
      supervisor.py
      cli.py
  tests/
backend/
  migrations/005_online_experiment.sql
  include/babel/application/experiment_*.hpp
  include/babel/http/experiment_controller.hpp
  include/babel/runtime/experiment_job_runner.hpp
  src/application/experiment_*.cpp
  src/adapters/postgres/experiment_*.cpp
  src/http/experiment_controller.cpp
  src/runtime/experiment_job_runner.cpp
  tests/unit/experiment_*.cpp
  tests/integration/experiment_*.cpp
  admin/experiment-status.js
  admin/dashboard.js
  admin/index.html
  admin/dashboard.css
tests/js/experiment-dashboard.test.js
compose.yaml
Justfile
docs/runbooks/online-experiment.md
documentation.md
README.md
```

### Task 1: Freeze Cross-Process Contracts and the Tiny World

**Files:**
- Create: `schemas/online/experiment-run-v1.json`
- Create: `schemas/online/recommendation-request-v1.json`
- Create: `schemas/online/recommendation-response-v1.json`
- Create: `schemas/online/feedback-event-v1.json`
- Create: `schemas/online/activity-log-v1.json`
- Create: `schemas/online/model-manifest-v1.json`
- Create: `schemas/online/embedding-space-v1.json`
- Create: `schemas/online/hnsw-snapshot-v1.json`
- Create: `fixtures/online/tiny/*`
- Create: `fixtures/online/requests.jsonl`
- Create: `fixtures/online/feedback.jsonl`
- Create: `online/pyproject.toml`
- Create: `online/requirements.lock`
- Create: `online/src/babel_online/__init__.py`
- Create: `online/src/babel_online/config.py`
- Create: `online/src/babel_online/contracts.py`
- Create: `online/tests/test_contracts.py`
- Create: `online/tests/test_import_boundaries.py`

**Interfaces:**
- Consumes: Slice 2 monthly schemas and exact pinned dataset/model revisions.
- Produces: `RunConfigV1`, `RetrievalBackend`, `EmbeddingSpaceV1`, `HnswSnapshotV1`, `RecommendationRequestV1`, `RecommendationResponseV1`, `FeedbackEventV1`, `ActivityLogV1`, `ModelManifestV1`, `validate_contract`, and the immutable field names used by every later task.

- [ ] **Step 1: Write failing round-trip and leakage tests**

```python
def test_feedback_fixture_round_trips_without_hidden_fields():
    event = FeedbackEventV1.model_validate_json(FEEDBACK.read_text())
    assert event.schemaVersion == 1
    assert event.creatorId
    assert set(event.model_dump()) == EXPECTED_FEEDBACK_FIELDS
    assert not FORBIDDEN_HIDDEN_FIELDS & recursively_collect_keys(event.model_dump())

def test_serving_and_training_packages_cannot_import_hidden_world():
    violations = forbidden_imports("online/src/babel_online", sources=("serving", "training", "model"), forbidden="babel_online.hidden")
    assert violations == []

def test_run_defaults_to_pgvector_and_forbids_backend_switch():
    run = RunConfigV1.model_validate(fixture_run_config())
    assert run.retrievalBackend == "pgvector"
    with pytest.raises(FrozenInstanceError):
        run.retrievalBackend = "hnswlib"

def test_hnsw_snapshot_is_derived_from_pgvector_rows():
    snapshot = HnswSnapshotV1.model_validate(fixture_hnsw_manifest())
    assert snapshot.pgvectorSnapshotSha256
    assert snapshot.rowCount == len(snapshot.orderedBabelIds)
```

- [ ] **Step 2: Run tests and verify missing contracts fail**

Run: `python3 -m pytest online/tests/test_contracts.py online/tests/test_import_boundaries.py -v`

Expected: collection fails because `babel_online.contracts` does not exist.

- [ ] **Step 3: Define exact recommendation and feedback dataclasses**

```python
class RecommendationRequestV1(BaseModel):
    schemaVersion: Literal[1]
    requestId: UUID
    runId: UUID
    creatorId: UUID
    newBabelId: UUID
    newSourceArticleKey: str
    title: str
    text: str
    historyBabelIds: list[UUID]
    candidateCount: int = Field(gt=0, le=100)

class CandidateActionV1(BaseModel):
    babelId: UUID
    sourceArticleKey: str
    rank: int = Field(gt=0)
    modelScore: float
    action: Literal["include", "exclude", "ignore"]

class RecommendationCandidateV1(BaseModel):
    babelId: UUID
    creatorId: UUID
    sourceArticleKey: str
    rank: int = Field(gt=0)
    modelScore: float
```

The response returns request/run/model IDs, integer model version, fixed
`retrievalBackend`, embedding-space ID, active PostgreSQL snapshot checksum,
backend snapshot checksum (equal to the PostgreSQL checksum for pgvector and the
validated hnswlib-manifest checksum for hnswlib), SHA-256 of the normalized
little-endian float32 query vector, ordered candidates, and nanosecond durations
for queue, tokenization, Qwen encoding, history lookup, context tower, candidate
retrieval, filtering, and total server time.
The feedback schema matches the approved camelCase event exactly and sets
`additionalProperties: false` recursively.

- [ ] **Step 4: Define immutable run and model manifests**

```python
RetrievalBackend = Literal["pgvector", "hnswlib"]

class EmbeddingSpaceV1(BaseModel):
    dimension: Literal[100]
    distance: Literal["cosine"]
    distilledEncoderArtifact: str
    compatibilityVersion: str

class HnswSnapshotV1(BaseModel):
    runId: UUID
    servingModelId: UUID
    servingModelVersion: int
    embeddingSpaceId: UUID
    pgvectorSnapshotSha256: str
    orderedBabelIds: list[UUID]
    rowCount: int
    vectorSha256: str
    m: int = 16
    efConstruction: int = 200
    efSearch: int = 100
```

`RunConfigV1` includes dataset SHA, starting model ID, immutable
`retrievalBackend` defaulting to `pgvector`, 50 default creators,
environment sequence (`["2026-06"]` or `["2026-06", "2026-07"]`), per-month
event budgets, RNG seed, all simulator defaults, `top_l`, recommendation K,
Kafka topic/group, checkpoint interval, sync interval, and artifact roots.
`ModelManifestV1` includes stable ID/label, nullable parent/producing run,
encoder repo/revision, dataset revisions, environment, counts, training config,
metrics, checkpoint path/checksum, and `EmbeddingSpaceV1` identity. HNSW
parameters belong to the run/snapshot, not the immutable recommender weights.

Freeze snapshot canonicalization in `contracts.py`: sort by lowercase canonical
Babel UUID; encode each normalized vector as exactly 100 little-endian float32
values in C order; `vectorSha256` hashes their concatenation. The PostgreSQL
snapshot hash covers canonical JSON Lines containing Babel ID, creator ID, source
article key, catalog content hash, embedding-space ID, serving model ID,
materialized model version, and that row's vector SHA, with sorted keys, UTF-8,
LF delimiters, and no
insignificant whitespace. Both adapters and Slice 4 import this one function;
they may not implement parallel checksum formats.

- [ ] **Step 5: Build a fully separated tiny fixture**

Create six observable articles and text, separate June/July directed graphs,
clickstream rows, two resolved archetypes, deterministic histories, one
original model manifest, four created Babel instances, two uncreated catalog
articles, pgvector rows, an equivalent hnswlib snapshot manifest, request
examples, and feedback examples. Put hidden
rows in a physically separate fixture directory; observable fixtures contain
no graph or simulator values.

- [ ] **Step 6: Lock dependencies and pass contract tests**

Run:

```bash
python3 -m piptools compile --generate-hashes --resolver=backtracking --extra dev --output-file online/requirements.lock online/pyproject.toml
python3 -m pytest online/tests/test_contracts.py online/tests/test_import_boundaries.py -v
```

Expected: all schemas reject extra/hidden fields and every fixture round-trips.

- [ ] **Step 7: Commit**

```bash
git add schemas/online fixtures/online online/pyproject.toml online/requirements.lock online/src/babel_online/__init__.py online/src/babel_online/config.py online/src/babel_online/contracts.py online/tests/test_contracts.py online/tests/test_import_boundaries.py
git commit -m "feat: freeze online experiment contracts"
```

### Task 2: Persist Runs, Vector Materializations, Logs, and Immutable Models

**Files:**
- Create: `backend/migrations/005_online_experiment.sql`
- Create: `backend/include/babel/application/experiment_models.hpp`
- Create: `backend/include/babel/application/experiment_ports.hpp`
- Create: `backend/include/babel/application/model_registry_service.hpp`
- Create: `backend/src/application/model_registry_service.cpp`
- Create: `backend/include/babel/adapters/postgres/experiment_repository.hpp`
- Create: `backend/src/adapters/postgres/experiment_repository.cpp`
- Create: `backend/tests/unit/model_registry_service_test.cpp`
- Create: `backend/tests/integration/experiment_repository_test.cpp`
- Create: `backend/tests/integration/vector_materialization_repository_test.cpp`
- Modify: `backend/CMakeLists.txt`

**Interfaces:**
- Consumes: Task 1 run/model schemas and existing `Result<T>`/PostgreSQL adapter patterns.
- Produces: `ExperimentRepository`, `ModelRegistryRepository`, `VectorMaterializationRepository`, `ModelRegistryService::listCompatible`, immutable model creation, lifecycle transitions, activity pagination, duplicate-source enforcement, and an atomic active-vector state.

- [ ] **Step 1: Write failing persistence and lineage tests**

```cpp
TEST_CASE_METHOD(PostgresFixture, "one creator cannot reuse a source article in one run") {
  repository.insertBabel(runId, creatorId, "enwiki:42", babelA);
  auto duplicate = repository.insertBabel(runId, creatorId, "enwiki:42", babelB);
  REQUIRE_FALSE(duplicate);
  REQUIRE(duplicate.error().code == babel::ErrorCode::conflict);
}

TEST_CASE_METHOD(PostgresFixture, "a child never mutates its original parent") {
  auto original = registry.insertOriginal(originalManifest());
  auto child = registry.insertChild(childManifest(original->id));
  REQUIRE(registry.get(original->id)->checksum == original->checksum);
  REQUIRE(child->parent_model_id == original->id);
}

TEST_CASE_METHOD(PostgresFixture, "catalog vectors are reused but created Babel vectors remain distinct") {
  auto catalog = vectors.upsertCatalogEmbedding(catalogEmbedding("enwiki:42"));
  vectors.insertCreatedBabelEmbedding(runId, creatorA, babelA, catalog.id, originalModelId);
  vectors.insertCreatedBabelEmbedding(runId, creatorB, babelB, catalog.id, originalModelId);
  REQUIRE(vectors.catalogEmbeddingCount("enwiki:42") == 1);
  REQUIRE(vectors.createdBabelIds(runId) == std::vector{babelA, babelB});
}

TEST_CASE_METHOD(PostgresFixture, "retrieval backend is immutable after launch") {
  auto run = repository.insertRun(runConfig("pgvector"));
  REQUIRE_THROWS(database.execSqlSync(
      "UPDATE experiment_runs SET retrieval_backend = 'hnswlib' WHERE id = $1",
      run.id));
  REQUIRE(repository.get(run.id)->retrieval_backend == "pgvector");
}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "experiment_repository|vector_materialization|model_registry" --output-on-failure`

Expected: tests do not compile because experiment repositories are absent.

- [ ] **Step 3: Add the online migration**

Create `recommender_models`, `experiment_runs`, `experiment_creators`,
`experiment_babels`, `experiment_activity_logs`, `model_synchronizations`,
`embedding_spaces`, `catalog_embeddings`, `babel_embeddings`, and
`run_embedding_states`. Store the exact launch JSON and its SHA-256. Persist
`retrieval_backend` with `CHECK (retrieval_backend IN ('pgvector','hnswlib'))`
and an update-rejecting trigger so it cannot change after insertion. Add check
constraints for the approved lifecycle and immutable-parent lineage. Add:

```sql
UNIQUE (run_id, creator_id, source_article_key)
UNIQUE (run_id, babel_id, creator_id)
UNIQUE (id, retrieval_backend) -- on experiment_runs for the state-table FK
```

Define the vector tables concretely:

```sql
embedding_spaces (
  id uuid PRIMARY KEY,
  dimension integer NOT NULL CHECK (dimension = 100),
  distance text NOT NULL CHECK (distance = 'cosine'),
  distilled_encoder_artifact text NOT NULL,
  compatibility_version text NOT NULL,
  UNIQUE (distilled_encoder_artifact, compatibility_version)
)

catalog_embeddings (
  id uuid PRIMARY KEY,
  embedding_space_id uuid NOT NULL REFERENCES embedding_spaces(id),
  dataset_commit_sha text NOT NULL CHECK (dataset_commit_sha ~ '^[0-9a-f]{40}$'),
  article_key text NOT NULL,
  content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  embedding vector(100) NOT NULL,
  UNIQUE (embedding_space_id, dataset_commit_sha, article_key, content_hash)
)

babel_embeddings (
  run_id uuid NOT NULL,
  babel_id uuid NOT NULL,
  creator_id uuid NOT NULL,
  catalog_embedding_id uuid NOT NULL REFERENCES catalog_embeddings(id),
  serving_model_id uuid NOT NULL REFERENCES recommender_models(id),
  materialized_model_version bigint NOT NULL,
  residual vector(100) NOT NULL,
  embedding vector(100) NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (run_id, babel_id),
  FOREIGN KEY (run_id, babel_id, creator_id)
    REFERENCES experiment_babels(run_id, babel_id, creator_id)
)

run_embedding_states (
  run_id uuid PRIMARY KEY,
  embedding_space_id uuid NOT NULL REFERENCES embedding_spaces(id),
  retrieval_backend text NOT NULL CHECK (retrieval_backend IN ('pgvector','hnswlib')),
  active_model_id uuid NOT NULL REFERENCES recommender_models(id),
  active_model_version bigint NOT NULL,
  pgvector_snapshot_sha256 text NOT NULL
    CHECK (pgvector_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  hnsw_snapshot_sha256 text
    CHECK (hnsw_snapshot_sha256 IS NULL OR hnsw_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz NOT NULL,
  CHECK ((retrieval_backend = 'pgvector' AND hnsw_snapshot_sha256 IS NULL) OR
         (retrieval_backend = 'hnswlib' AND hnsw_snapshot_sha256 IS NOT NULL)),
  FOREIGN KEY (run_id, retrieval_backend)
    REFERENCES experiment_runs(id, retrieval_backend)
)
```

Add B-tree indexes for run/creator/model filtering and the default cosine index:

```sql
CREATE INDEX babel_embeddings_cosine_hnsw
ON babel_embeddings USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);
```

`catalog_embeddings` may contain only the bounded articles materialized for the
run workload; it is not the candidate universe. Only rows with a corresponding
`experiment_babels` and `babel_embeddings` row are retrievable candidates.
`babel_embeddings` stores the current materialization; immutable checkpoints and
model artifacts retain prior versions rather than duplicating every historical
vector in the serving table.

Do not reuse the existing `edges` table: it enforces same-owner application
graphs, while experiment edges may cross synthetic creators and are reconstructed
from include actions.

- [ ] **Step 4: Implement transition and compatibility rules**

Allow only:

```text
starting -> running -> stop_requested -> draining_feedback
-> checkpointing -> exporting_interactions -> completed
```

and transitions from nonterminal states to `failed` or `interrupted`.
Compatibility requires equal encoder artifact, output dimension 100, cosine
distance, schema major version, and embedding-space compatibility version.
Implement `upsertCatalogEmbedding`, `insertCreatedBabelEmbedding`,
`readActiveCreatedBabelRows`, and `activateMaterialization`; the last operation
updates touched vectors and `run_embedding_states` in one transaction. Never
expose a delete/update method for model artifact rows; only labels may be added
as separate metadata. Do not expose a normal repository operation for changing
the run retrieval backend.

- [ ] **Step 5: Run migration/service tests and commit**

Run: `ctest --preset test -R "experiment_repository|vector_materialization|model_registry" --output-on-failure`

```bash
git add backend/migrations/005_online_experiment.sql backend/include/babel/application/experiment_models.hpp backend/include/babel/application/experiment_ports.hpp backend/include/babel/application/model_registry_service.hpp backend/include/babel/adapters/postgres/experiment_repository.hpp backend/src/application/model_registry_service.cpp backend/src/adapters/postgres/experiment_repository.cpp backend/tests backend/CMakeLists.txt
git commit -m "feat: persist online runs and vector materializations"
```

### Task 3: Build the Hidden Monthly World and Deterministic Creator Cohort

**Files:**
- Create: `online/src/babel_online/hidden/loader.py`
- Create: `online/src/babel_online/hidden/profiles.py`
- Create: `online/src/babel_online/hidden/ppr.py`
- Create: `online/src/babel_online/hidden/relatedness.py`
- Create: `online/src/babel_online/simulation/sampling.py`
- Create: `online/tests/hidden/test_loader.py`
- Create: `online/tests/hidden/test_profiles.py`
- Create: `online/tests/hidden/test_ppr.py`
- Create: `online/tests/simulation/test_sampling.py`

**Interfaces:**
- Consumes: hidden June/July configurations and resolved archetype rows from Slice 2.
- Produces: `HiddenEnvironment`, `CreatorLatentState`, `build_nested_cohort`, `approximate_ppr`, `relatedness_rank`, and sampling-without-replacement.

- [ ] **Step 1: Write deterministic cohort/PPR/uniqueness tests**

```python
def test_first_50_creators_are_stable_in_larger_cohorts(hidden_fixture):
    assert build_nested_cohort(50, seed=7, env=hidden_fixture) == build_nested_cohort(100, seed=7, env=hidden_fixture)[:50]

def test_creator_sources_are_sampled_without_replacement(creator):
    sources = [creator.sample_new_source() for _ in range(creator.eligible_count)]
    assert len(sources) == len(set(sources))
    with pytest.raises(EligibleSupportExhausted):
        creator.sample_new_source()

def test_multiseed_ppr_is_normalized(graph):
    ppr = approximate_ppr(graph, restart={1: .4, 2: .3, 3: .2, 4: .1}, restart_probability=.15)
    assert ppr.sum() == pytest.approx(1.0)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/hidden online/tests/simulation/test_sampling.py -v`

Expected: FAIL because hidden modules are missing.

- [ ] **Step 3: Implement the simulator-only loader**

Require a 40-character dataset SHA and hidden config name. Load canonical
directed edges, optional Clickstream, and resolved archetypes. Construct a
SciPy CSR transition matrix with a documented uniform redistribution for
dangling nodes. The module exports opaque article keys to sampling but never
serializes PPR or graph data through Task 1 observable contracts.

- [ ] **Step 4: Generate creators and sparse PPR deterministically**

Assign archetypes round-robin after a seeded permutation. Sample weights from
`Dirichlet(50 * [0.40, 0.30, 0.20, 0.10])`. Use one multi-seed approximate PPR
per creator, retain deterministic top-L entries, and mix history/new-source
sampling with `history_noise=0.10`. Derive random generators from
`(run_seed, creator_id, event_number, candidate_id)` rather than mutable global
RNG order.

- [ ] **Step 5: Implement fixed-result percentile ranks**

`PreferenceRank` and current-note `RelatednessRank` are percentiles within
their fixed top-L sparse results, not within recommendations. Cache current-note
PPR by `(dataset_sha, month, source_article_key, top_l)`.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest online/tests/hidden online/tests/simulation/test_sampling.py -v`

```bash
git add online/src/babel_online/hidden online/src/babel_online/simulation/sampling.py online/tests/hidden online/tests/simulation/test_sampling.py
git commit -m "feat: generate deterministic hidden creator worlds"
```

### Task 4: Build the Immutable Original, Versioned Candidate Retrieval, and Recommendation POST

**Files:**
- Create: `online/src/babel_online/observable.py`
- Create: `online/src/babel_online/model/item_tower.py`
- Create: `online/src/babel_online/model/context_tower.py`
- Create: `online/src/babel_online/model/candidate_index.py`
- Create: `online/src/babel_online/model/materialization.py`
- Create: `online/src/babel_online/model/pgvector_index.py`
- Create: `online/src/babel_online/model/hnswlib_index.py`
- Create: `online/src/babel_online/model/artifact.py`
- Create: `online/src/babel_online/model/registry.py`
- Create: `online/src/babel_online/serving/state.py`
- Create: `online/src/babel_online/serving/timings.py`
- Create: `online/src/babel_online/serving/app.py`
- Create: `online/tests/model/test_context_tower.py`
- Create: `online/tests/model/test_materialization.py`
- Create: `online/tests/model/test_candidate_index.py`
- Create: `online/tests/model/test_pgvector_integration.py`
- Create: `online/tests/model/test_hnsw_snapshot.py`
- Create: `online/tests/model/test_artifact.py`
- Create: `online/tests/serving/test_recommendations.py`

**Interfaces:**
- Consumes: observable catalog, complete distilled encoder artifact, Task 1 recommendation contracts.
- Produces: `ItemTower`, `CreatorContextTower`, `EmbeddingMaterializer`, the `CandidateIndex` protocol, `PgvectorCandidateIndex`, `HnswlibCandidateIndex`, `build_original_artifact`, `ServingState.apply_sync`, and `POST /api/v1/recommendations`.

- [ ] **Step 1: Write initialization, filtering, and timing tests**

```python
def test_original_context_query_is_equal_weight_content_history():
    tower = CreatorContextTower.original(dimension=100)
    query = tower(new=unit(0), history=torch.stack([unit(1), unit(2)]))
    expected_history = scaled_dot_product_history(unit(0), torch.stack([unit(1), unit(2)]))
    assert query == pytest.approx(normalize(.5 * unit(0) + .5 * expected_history))

def test_recommendation_excludes_own_babels(client, serving_state):
    response = client.post("/api/v1/recommendations", json=request_for("creator-a"))
    assert response.status_code == 200
    assert all(c["creatorId"] != "creator-a" for c in response.json()["candidates"])
    assert set(response.json()["timingsNs"]) == REQUIRED_TIMING_STAGES

@pytest.mark.parametrize("backend", ["pgvector", "hnswlib"])
def test_uncreated_catalog_articles_are_never_candidates(index_for, backend):
    index = index_for(backend, include_uncreated_highest_score=True)
    result = index.search(query=unit(0), run_id=RUN, state=ACTIVE_STATE,
                          exclude_creator_id=CREATOR_A, k=10)
    assert UNCREATED_ARTICLE_KEY not in {candidate.article_key for candidate in result}
    assert {candidate.babel_id for candidate in result} == {CREATED_BY_B, CREATED_BY_C}

def test_hnsw_snapshot_rejects_checksum_mismatch_without_pgvector_fallback(runtime):
    runtime.configure_backend("hnswlib")
    runtime.corrupt_snapshot_vector_checksum()
    with pytest.raises(SnapshotIntegrityError):
        runtime.start_serving()
    assert runtime.pgvector_index.search_calls == 0

def test_tiny_fixture_backends_return_the_same_created_candidates(tiny_indexes):
    pg, hns = tiny_indexes
    assert ids(pg.search(**TINY_QUERY)) == ids(hns.search(**TINY_QUERY))

@pytest.mark.parametrize("backend", ["pgvector", "hnswlib"])
def test_two_creators_using_one_article_remain_two_candidates(index_for, backend):
    result = index_for(backend).search(**SAME_SOURCE_QUERY)
    same_source = [row for row in result if row.article_key == "enwiki:42"]
    assert {row.creator_id for row in same_source} == {CREATOR_B, CREATOR_C}
    assert len({row.babel_id for row in same_source}) == 2

@pytest.mark.pgvector
def test_real_pgvector_query_joins_created_babels_and_filters_owner(pgvector_fixture):
    result = pgvector_fixture.index.search(**TINY_QUERY)
    assert ids(result) == pgvector_fixture.expected_other_creator_babel_ids
    assert UNCREATED_ARTICLE_KEY not in {row.article_key for row in result}
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/model online/tests/serving -m 'not pgvector' -v`

With Compose PostgreSQL: `python3 -m pytest online/tests/model/test_pgvector_integration.py -m pgvector -v`

Expected: FAIL because model and serving modules are missing.

- [ ] **Step 3: Implement deterministic representation-compatible towers**

Load the complete distilled Qwen encoder/projection by exact model revision and
freeze it. Initialize item residuals to zero. Initialize attention as normalized
scaled dot-product attention between history and new-Babel embeddings. Initialize
the fusion linear map on `[new, attended_history]` to `[0.5I, 0.5I]` with zero
bias. Leave attention/fusion and residuals trainable in the working copy.

- [ ] **Step 4: Materialize catalog vectors lazily and created Babel vectors transactionally**

Define the retrieval boundary once:

```python
class CandidateIndex(Protocol):
    backend: RetrievalBackend

    def search(
        self,
        query: NDArray[np.float32],
        *,
        run_id: UUID,
        state: MaterializedServingState,
        exclude_creator_id: UUID,
        k: int,
    ) -> list[RetrievedCandidate]: ...

    def activate(self, state: MaterializedServingState) -> None: ...
```

When a source article is selected, look up `catalog_embeddings` by embedding
space, pinned dataset SHA, article key, and content hash. Encode with the frozen
Qwen item tower only on a cache miss, then insert idempotently. In the same Babel
creation unit of work, insert the `experiment_babels` row and its normalized
`babel_embeddings` row with a zero residual. The same catalog vector may back
several creator-owned Babel rows; those Babel IDs remain distinct candidates.
Do not precompute the full monthly catalog for the initial 50-creator run.

- [ ] **Step 5: Implement pgvector as the default candidate index**

At request start under the serving read lock, read one `run_embedding_states`
row and hold that model ID, version, and snapshot checksum for the request. Set `hnsw.ef_search = 100` and
`hnsw.iterative_scan = strict_order` in the read transaction. Query only the
active run's created Babel rows, filter the request creator in SQL, oversample
when filtering reduces results, and order by cosine distance:

```sql
SELECT eb.babel_id,
       eb.creator_id,
       xb.source_article_key,
       1 - (eb.embedding <=> $1::vector) AS score
FROM babel_embeddings AS eb
JOIN experiment_babels AS xb
  ON xb.run_id = eb.run_id AND xb.babel_id = eb.babel_id
JOIN run_embedding_states AS rs ON rs.run_id = eb.run_id
WHERE eb.run_id = $2
  AND rs.active_model_id = $3
  AND rs.active_model_version = $4
  AND rs.pgvector_snapshot_sha256 = $5
  AND eb.serving_model_id = $3
  AND eb.materialized_model_version <= $4
  AND eb.creator_id <> $6
ORDER BY eb.embedding <=> $1::vector
LIMIT $7;
```

The join to `experiment_babels` is a required invariant, not an optimization.
`catalog_embeddings` is never queried as the recommendation candidate table.

- [ ] **Step 6: Implement hnswlib as an explicit disposable adapter**

For a run launched with `retrievalBackend=hnswlib`, read the same ordered active
created-Babel rows from PostgreSQL, hash the canonical float32 vector bytes, and
validate the `HnswSnapshotV1` row count, ordered Babel IDs, vector checksum,
embedding space, model version, and PostgreSQL snapshot checksum before building
or loading the index. Maintain Babel-ID/creator/source metadata beside integer
labels. Build a shadow index and atomically swap it only after validation. A
missing, corrupt, or stale snapshot fails the run or synchronization and retains
the last valid hnswlib state; it never redirects the request to pgvector.

The backend factory has no environment-dependent auto mode:

```python
def candidate_index_for(config: RunConfig, repository: EmbeddingRepository) -> CandidateIndex:
    if config.retrievalBackend == "pgvector":
        return PgvectorCandidateIndex(repository)
    if config.retrievalBackend == "hnswlib":
        return HnswlibCandidateIndex.from_pgvector_snapshot(repository, config)
    raise UnsupportedRetrievalBackend(config.retrievalBackend)
```

- [ ] **Step 7: Implement measured synchronous serving**

Use a bounded request semaphore and capture enqueue time before acquisition.
Time tokenization, new-note encoding, history lookup, context computation,
candidate retrieval, filtering, and total with `perf_counter_ns`. Record the
fixed retrieval backend, active model version, vector snapshot checksum, and
query-vector checksum on every response/activity record. Return the complete v1 response and
`Server-Timing`; client/network overhead is computed later as client total minus
server total. Return the model ID/version captured at request start even if a
sync lands immediately afterward.

- [ ] **Step 8: Build and publish the original without mutation APIs**

Write Safetensors, context initialization, embedding-space manifest, pgvector
query parameters, optional hnswlib build parameters, compatibility version,
source model/dataset SHAs, and checksums into an atomic artifact directory.
Do not publish an hnswlib index as authoritative model state. Upload the model as
an immutable path/tag in
`dhelmy990/babel-two-tower-recommender`; register its stable ID in PostgreSQL.
`load_artifact` verifies every checksum before serving.

- [ ] **Step 9: Run tests and commit**

Run: `python3 -m pytest online/tests/model online/tests/serving -m 'not pgvector' -v`

With Compose PostgreSQL: `python3 -m pytest online/tests/model/test_pgvector_integration.py -m pgvector -v`

```bash
git add online/src/babel_online/observable.py online/src/babel_online/model online/src/babel_online/serving online/tests/model online/tests/serving
git commit -m "feat: serve immutable two-tower recommendations"
```

### Task 5: Add Minimal Kafka Transport and Bounded Interaction Export

**Files:**
- Create: `online/src/babel_online/feedback/bus.py`
- Create: `online/src/babel_online/feedback/kafka.py`
- Create: `online/src/babel_online/feedback/export.py`
- Create: `online/tests/feedback/test_bus.py`
- Create: `online/tests/feedback/test_kafka_integration.py`
- Create: `online/tests/feedback/test_export.py`
- Modify: `compose.yaml`

**Interfaces:**
- Consumes: `FeedbackEventV1` and private-Hub publisher behavior.
- Produces: `FeedbackProducer.publish`, `TrainingConsumer.poll/commit/seek`, `InMemoryFeedbackBus`, `capture_high_watermarks`, and `export_offset_range`.

- [ ] **Step 1: Write fake-bus and exact-range export tests**

```python
def test_one_recommendation_is_one_atomic_message(fake_bus, feedback_event):
    fake_bus.publish(key=feedback_event.creatorId, event=feedback_event)
    assert fake_bus.messages == [(feedback_event.creatorId, feedback_event)]

def test_export_stops_at_captured_high_watermark(fake_bus, tmp_path):
    bounds = OffsetRange(partition=0, start=2, end_exclusive=5)
    rows = export_offset_range(fake_bus, bounds, tmp_path)
    assert [row.offset for row in rows] == [2, 3, 4]
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/feedback/test_bus.py online/tests/feedback/test_export.py -v`

Expected: FAIL because feedback modules are absent.

- [ ] **Step 3: Pin one local KRaft broker**

Add `apache/kafka:4.3.1`, loopback listener, persistent volume, one broker,
replication factor 1, topic auto-creation disabled, and healthcheck. Add an
idempotent initializer for topic `babel.feedback.v1` with retention longer than
the maximum configured run plus export window.

- [ ] **Step 4: Implement acknowledged production and manual consumption**

Validate JSON before producing, key by creator ID, require broker acknowledgement,
and surface unavailability before the simulator records decisions. Configure
the single consumer group `babel-online-training-v1` with `enable.auto.commit=false`.
Expose explicit assignment, seek, position, committed offsets, and high-water
marks. Kafka payloads never contain artifact bytes.

- [ ] **Step 5: Export and remotely verify one bounded range**

Replay `[start, end)` per partition, validate every event, write Parquet plus
offset/checksum manifest to `runs/<run-id>/<month>/`, upload incrementally to the
dataset repository, and remotely read the exact Hub SHA before marking export
complete. This is a command, not an always-running consumer.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest online/tests/feedback -m 'not kafka' -v`

Run with Compose broker: `python3 -m pytest online/tests/feedback/test_kafka_integration.py -m kafka -v`

```bash
git add compose.yaml online/src/babel_online/feedback online/tests/feedback
git commit -m "feat: add minimal Kafka feedback transport"
```

### Task 6: Implement Decisions, Accepted Edges, and the Simulator Event Loop

**Files:**
- Create: `online/src/babel_online/simulation/decisions.py`
- Create: `online/src/babel_online/simulation/client.py`
- Create: `online/src/babel_online/simulation/engine.py`
- Create: `online/tests/simulation/test_decisions.py`
- Create: `online/tests/simulation/test_engine.py`

**Interfaces:**
- Consumes: Task 3 hidden ranks, Task 4 recommendation endpoint, Task 5 `FeedbackProducer`, and Task 2 run persistence.
- Produces: `decide_candidate`, `RecommendationClient`, `SimulationEngine.step`, and `reconstruct_accepted_edges`.

- [ ] **Step 1: Write probability, atomicity, and leakage tests**

```python
def test_decision_probabilities_match_approved_formula():
    p = action_probabilities(relevance=.8, epsilon=.2, exclusion_propensity=.25)
    assert p.include == pytest.approx(.74)
    assert p.exclude == pytest.approx((1 - .74) * .25 * ((.8 * .2) + .1))
    assert p.include + p.exclude + p.ignore == pytest.approx(1.0)

def test_kafka_failure_records_no_activity_or_edge(engine, unavailable_bus):
    before_actions = engine.observable_actions()
    before_edges = engine.accepted_edges()
    with pytest.raises(FeedbackUnavailable):
        engine.step()
    assert engine.observable_actions() == before_actions
    assert engine.accepted_edges() == before_edges

def test_initial_histories_and_new_notes_are_the_only_candidates(engine):
    engine.initialize_creator_histories()
    engine.step()
    assert engine.candidate_babel_ids() == engine.persisted_synthetic_babel_ids()
    assert not (engine.uncreated_catalog_article_keys() & engine.candidate_article_keys())
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/simulation/test_decisions.py online/tests/simulation/test_engine.py -v`

Expected: FAIL because decision and engine modules are missing.

- [ ] **Step 3: Implement the three-way hidden decision**

Compute `R = 0.60 * RelatednessRank + 0.40 * PreferenceRank`. With the default
`clickstream_beta=0.0`, use R unchanged. For an explicitly nonzero beta, add
`beta * normalized_clickstream(a, c)` and clamp to `[0, 1]`; the modifier remains
hidden and its configuration is recorded in the run manifest. Then
`p_include = (1-epsilon)*R + epsilon*0.5`. Conditional exclusion probability
is `propensity*((1-epsilon)*(1-R)+epsilon*0.5)`; multiply it by the remaining
mass to obtain unconditional exclusion. Ignore receives the remainder. Derive
one deterministic draw per candidate; allow multiple includes.

- [ ] **Step 4: Populate created-Babel candidates and publish feedback atomically**

Before the first event, insert each creator's observable initial-history notes as
synthetic Babels and materialize their vectors through Task 4. They form the
initial candidate set; unused monthly catalog articles do not. For each event,
create the new Babel once and commit its catalog/Babel materialization before
POSTing observable text/history/K to loopback serving. The request creator's own
new Babel is excluded by the candidate query, but it becomes eligible for other
creators immediately after the transaction commits.

Measure client monotonic duration, evaluate returned candidates privately,
validate one feedback event, and wait for Kafka acknowledgement. Only then
persist the activity record and expose include actions as accepted edges. The
newly created Babel may remain durably staged on failure, but it retains the same
event number and request ID and is retried rather than sampled again. On publish
failure, retain the RNG/event number for exact retry and pause the simulation.

- [ ] **Step 5: Implement June→July continuity**

At the month budget boundary, preserve creators, created Babels, trainer/model
state, and run ID. Load July hidden and observable data at the same dataset SHA,
map persistent identities through the crosswalk, and lazily materialize July
catalog/Babel vectors only when creators actually select them. Existing June
created-Babel candidates remain in the run. If hnswlib is selected, rebuild a
validated shadow index from the resulting committed PostgreSQL rows; pgvector
requires no separate rebuild. Continue event numbering and never reset to the
original model.

- [ ] **Step 6: Test and commit**

Run: `python3 -m pytest online/tests/simulation -v`

```bash
git add online/src/babel_online/simulation online/tests/simulation
git commit -m "feat: simulate observable creator feedback"
```

### Task 7: Extend the C++ Experiment Control API

**Files:**
- Create: `backend/include/babel/application/experiment_service.hpp`
- Create: `backend/src/application/experiment_service.cpp`
- Create: `backend/include/babel/http/experiment_controller.hpp`
- Create: `backend/src/http/experiment_controller.cpp`
- Create: `backend/include/babel/runtime/experiment_job_runner.hpp`
- Create: `backend/src/runtime/experiment_job_runner.cpp`
- Create: `backend/tests/unit/experiment_service_test.cpp`
- Create: `backend/tests/unit/experiment_job_runner_test.cpp`
- Create: `backend/tests/integration/experiment_http_contract_test.cpp`
- Modify: `backend/src/runtime/application.cpp`
- Modify: `backend/CMakeLists.txt`

**Interfaces:**
- Consumes: Task 2 repositories, existing `AdminSecurity`, Task 1 launch schema, and path to `babel-online` executable.
- Produces: model/run/status/log query endpoints, `ExperimentService::start`, `requestGracefulStop`, and one active local process runner.

- [ ] **Step 1: Write failing start/stop/API tests**

```cpp
TEST_CASE("start snapshots config and launches exactly one supervisor") {
  auto run = service.start(validLaunchRequest());
  REQUIRE(run);
  REQUIRE(repository.get(run->id)->creator_count == 50);
  REQUIRE(repository.get(run->id)->retrieval_backend == "pgvector");
  REQUIRE(process.launches() == 1);
}

TEST_CASE("launch accepts only the two explicit retrieval backends") {
  REQUIRE(service.start(launchRequestWithBackend("hnswlib")));
  REQUIRE_FALSE(service.start(launchRequestWithBackend("auto")));
}

TEST_CASE("graceful stop changes intent but does not kill the process") {
  auto result = service.requestGracefulStop(activeRunId);
  REQUIRE(result);
  REQUIRE(process.killCalls() == 0);
}
```

- [ ] **Step 2: Run and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "experiment_service|experiment_job_runner|experiment_http" --output-on-failure`

Expected: tests do not compile because experiment control is missing.

- [ ] **Step 3: Implement validated launch and compatibility checks**

Resolve the selected model from the immutable registry, reject incompatible
dataset/embedding-space versions, verify creator count/event budget/ranges, and
accept only `pgvector` or `hnswlib`, defaulting to `pgvector`. Persist the exact
config and fixed retrieval backend before process launch, and pass only the new
run ID plus database/runtime environment to `babel-online run --run-id <uuid>`.
The API exposes no operation that changes the backend of an existing run. One
active local synthetic run is allowed initially.

- [ ] **Step 4: Add nonce-protected dashboard APIs**

```text
GET  /admin/api/v1/experiment/models
GET  /admin/api/v1/experiment/runs/latest
GET  /admin/api/v1/experiment/runs/{id}
GET  /admin/api/v1/experiment/runs/{id}/logs?after=<sequence>&limit=200
POST /admin/api/v1/experiment/runs
POST /admin/api/v1/experiment/runs/{id}/graceful-stop
```

POST endpoints require the existing nonce/Host/Origin checks. Responses are
bounded, camelCase, `no-store`, and never include hidden configuration fields.

- [ ] **Step 5: Recover process state after backend restart**

On startup, mark a nonterminal run `interrupted` only if its supervisor PID and
identity token are absent. If the process still exists, reattach status polling.
Do not automatically resume or silently start a new run.

- [ ] **Step 6: Test and commit**

Run: `ctest --preset test -R "experiment_service|experiment_job_runner|experiment_http" --output-on-failure`

```bash
git add backend/include/babel/application/experiment_service.hpp backend/src/application/experiment_service.cpp backend/include/babel/http/experiment_controller.hpp backend/src/http/experiment_controller.cpp backend/include/babel/runtime/experiment_job_runner.hpp backend/src/runtime/experiment_job_runner.cpp backend/tests backend/src/runtime/application.cpp backend/CMakeLists.txt
git commit -m "feat: control online experiments from the backend"
```

### Task 8: Train Online, Checkpoint Offsets, and Synchronize Serving

**Files:**
- Create: `online/src/babel_online/training/pairs.py`
- Create: `online/src/babel_online/training/loss.py`
- Create: `online/src/babel_online/training/checkpoint.py`
- Create: `online/src/babel_online/training/synchronizer.py`
- Create: `online/src/babel_online/training/consumer.py`
- Create: `online/tests/training/test_pairs.py`
- Create: `online/tests/training/test_checkpoint.py`
- Create: `online/tests/training/test_recovery.py`
- Create: `online/tests/training/test_synchronizer.py`
- Create: `online/tests/training/test_vector_materialization.py`

**Interfaces:**
- Consumes: Task 4 working model, Task 5 manual consumer, Task 1 feedback/model schemas.
- Produces: `pairs_from_event`, `weighted_pairwise_loss`, `OnlineTrainer`, `save_online_checkpoint`, and `AtomicStateSynchronizer`.

- [ ] **Step 1: Write pair construction and recovery-order tests**

```python
def test_multiple_includes_pair_with_hard_and_soft_negatives(event):
    pairs = pairs_from_event(event)
    assert {(p.positive, p.negative, p.weight) for p in pairs} == {
        ("p1", "hard", 1.0), ("p1", "soft", .25),
        ("p2", "hard", 1.0), ("p2", "soft", .25),
    }

def test_offsets_commit_only_after_atomic_checkpoint(trainer, kafka):
    trainer.process_one_batch()
    assert kafka.committed == trainer.start_offsets
    trainer.checkpoint()
    assert kafka.committed == trainer.checkpoint_offsets

def test_pgvector_sync_activates_vectors_and_model_version_together(synchronizer, repository):
    before = repository.active_state(RUN)
    synchronizer.synchronize(changed_residuals={BABEL_B: unit(7)}, version=before.version + 1)
    after = repository.active_state(RUN)
    assert after.version == before.version + 1
    assert repository.vector(BABEL_B).materialized_model_version == after.version
    assert after.pgvector_snapshot_sha256 == repository.hash_active_created_babel_rows(RUN)

def test_failed_hnsw_shadow_build_keeps_last_complete_version(hns_runtime):
    before = hns_runtime.active_state()
    hns_runtime.fail_next_shadow_build()
    with pytest.raises(IndexBuildError):
        hns_runtime.synchronize(version=before.version + 1)
    assert hns_runtime.active_state() == before
    assert hns_runtime.pgvector_rows_checksum() == before.pgvector_snapshot_sha256
    assert hns_runtime.pgvector_index.search_calls == 0
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/training -v`

Expected: FAIL because online training modules are absent.

- [ ] **Step 3: Implement weighted pairwise ranking updates**

For every include, pair against every exclude/ignore from the same event.
Use `softplus(-(positive_score-negative_score)) * negative_weight`, average by
total pair weight, optionally add in-batch negatives, and skip direct updates
for no-positive events while advancing their processed offsets. Optimize only
context attention/fusion and item residuals; assert the Qwen encoder has no
gradients.

- [ ] **Step 4: Save complete atomic online checkpoints**

Save working model/residuals, optimizer/scheduler, RNG, global step, training
version, dataset/model manifests, per-partition next offsets, metrics, and
touched Babel IDs under `.partial`, fsync, rename, then commit Kafka offsets.
On restart, verify checksums, restore state, assign partitions, and seek to the
checkpoint offsets. Ignore incomplete directories.

- [ ] **Step 5: Synchronize model state and durable vectors as one activation**

At the configured interval, write only changed context tensors and touched item
residuals plus compatibility/checksum manifest into `sync-v<N>.partial`, fsync,
rename to `sync-v<N>`, and request activation only after the complete checkpoint
for version N is durable. Serving first verifies all files,
embedding-space compatibility, parent model, expected prior version, and
checksums without mutating active state.

Under the serving write lock, begin a PostgreSQL transaction, apply touched
residual/final-vector updates, label them with version N, and compute the
canonical checksum over all active created-Babel rows ordered by Babel ID. For a
pgvector run, update `run_embedding_states` and commit before swapping the
already validated context tensors and `LATEST`, then release the lock. Untouched
rows may retain an earlier materialization version and remain valid under the
same active model/embedding space.

For an hnswlib run, build and validate a shadow index from those same
transaction-visible rows before commit. Its manifest must name the prospective
PostgreSQL checksum, ordered IDs, row count, model version, embedding space, and
vector checksum. Only after a successful build may the transaction update both
snapshot checksums and commit; then swap the prepared context state, shadow
index, and `LATEST` before releasing the lock. Any validation, database, or
index-build failure rolls back and leaves the last complete serving version.
Never fall back to pgvector for an hnswlib run.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest online/tests/training -v`

```bash
git add online/src/babel_online/training online/tests/training
git commit -m "feat: train and synchronize online model state"
```

### Task 9: Add Dashboard Start, Stop, Activity, Health, and Model Selection

**Files:**
- Create: `backend/admin/experiment-status.js`
- Create: `tests/js/experiment-dashboard.test.js`
- Modify: `backend/admin/index.html`
- Modify: `backend/admin/dashboard.css`
- Modify: `backend/admin/dashboard.js`
- Modify: `backend/include/babel/http/admin_controller.hpp`
- Modify: `backend/src/http/admin_controller.cpp`
- Modify: `package.json`

**Interfaces:**
- Consumes: Task 7 HTTP endpoints and activity/status DTOs.
- Produces: model selector, retrieval-backend selector, launch form, Start, Graceful stop, timeline, online-health panel, and run summary.

- [ ] **Step 1: Write failing pure view-model tests**

```javascript
test('completed child and original remain independently selectable', () => {
  const options = modelOptions([originalModel(), childModel()]);
  assert.deepEqual(options.map(x => x.id), ['original-2016', 'june-child']);
});

test('distillation losses never render in online health', () => {
  const view = onlineHealth(statusFixture());
  assert.equal(JSON.stringify(view).includes('vectorLoss'), false);
  assert.equal(JSON.stringify(view).includes('relationalLoss'), false);
});

test('retrieval defaults to pgvector and becomes read-only after start', () => {
  const draft = launchView(newRunFixture());
  assert.equal(draft.retrievalBackend.value, 'pgvector');
  assert.equal(draft.retrievalBackend.disabled, false);
  const running = launchView(runningRunFixture({retrievalBackend: 'hnswlib'}));
  assert.equal(running.retrievalBackend.value, 'hnswlib');
  assert.equal(running.retrievalBackend.disabled, true);
});
```

- [ ] **Step 2: Run and verify failure**

Run: `node --test tests/js/experiment-dashboard.test.js`

Expected: FAIL because `experiment-status.js` is absent.

- [ ] **Step 3: Extend the dashboard without weakening seed controls**

Keep the existing Wikipedia seed section. Add dataset/model selectors, default
50 creators, retrieval selector (`pgvector` default, `hnswlib` explicit),
June-only/June→July choice, event budgets, seed, advanced simulator settings,
Start, and Graceful stop. Disable incompatible models with a visible reason.
Freeze and visibly label the chosen retrieval backend after Start. Do not add
auto selection, mid-run switching, reset, overwrite, pause, resume, or Colab
controls.

- [ ] **Step 4: Render the three approved views**

Timeline rows show creator/new Babel, recommended titles, actions, and derived
accepted edges. Health shows rates, produced/trained counts, offsets/lag,
rolling ranking loss, checkpoint, training/serving versions, synchronization,
retrieval backend/vector snapshot, and timing p50/p95/p99. Summary shows counts,
action distribution, resources, sync history, lifecycle/export progress, child
artifact, and actionable errors. A failed hnswlib snapshot is shown as a run or
sync error, never as a pgvector fallback. Poll logs by sequence and cap DOM rows
to 1,000.

- [ ] **Step 5: Test assets and commit**

Run: `npm test`

```bash
git add backend/admin backend/include/babel/http/admin_controller.hpp backend/src/http/admin_controller.cpp tests/js/experiment-dashboard.test.js package.json
git commit -m "feat: operate online experiments from dashboard"
```

### Task 10: Compose the Supervisor and Prove Graceful June→July Operation

**Files:**
- Create: `online/src/babel_online/runtime/database.py`
- Create: `online/src/babel_online/runtime/telemetry.py`
- Create: `online/src/babel_online/runtime/resources.py`
- Create: `online/src/babel_online/runtime/supervisor.py`
- Create: `online/src/babel_online/runtime/cli.py`
- Create: `online/tests/runtime/test_supervisor.py`
- Create: `online/tests/e2e/test_tiny_online_run.py`
- Create: `docs/runbooks/online-experiment.md`
- Modify: `Justfile`
- Modify: `README.md`
- Modify: `documentation.md`

**Interfaces:**
- Consumes: Tasks 2–9.
- Produces: `babel-online run --run-id`, child-process lifecycle, end-to-end acceptance evidence, and operator runbook.

- [ ] **Step 1: Write failing lifecycle test**

```python
def test_graceful_stop_drains_checkpoints_exports_and_saves_child(tiny_runtime):
    run = tiny_runtime.start(months=["2026-06", "2026-07"])
    tiny_runtime.request_stop(run.id)
    assert tiny_runtime.states(run.id) == [
        "starting", "running", "stop_requested", "draining_feedback",
        "checkpointing", "exporting_interactions", "completed",
    ]
    assert tiny_runtime.child_model(run.id).parent_id == tiny_runtime.original.id
    assert tiny_runtime.original.checksum == tiny_runtime.original_checksum_before

def test_default_run_serves_only_created_babels_from_pgvector(tiny_runtime):
    run = tiny_runtime.start(retrieval_backend="pgvector")
    response = tiny_runtime.recommend_once(run.id)
    assert response.retrieval_backend == "pgvector"
    assert set(response.candidate_ids) <= set(tiny_runtime.created_babel_ids(run.id))
    assert not (set(response.candidate_ids) & set(tiny_runtime.uncreated_catalog_ids(run.id)))
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/runtime/test_supervisor.py -v`

Expected: FAIL because the supervisor is missing.

- [ ] **Step 3: Implement separate serving/training processes**

The supervisor starts a loopback Uvicorn serving child and a single trainer
child, initializes the run's embedding space and pgvector materialization, and,
only for an hnswlib run, validates/builds its initial snapshot from the same
PostgreSQL created-Babel rows. It waits for both health checks before running the
simulator and persists rate-limited structured logs. Every five seconds it
records basic per-process CPU/RSS, host memory, optional GPU utilization,
request/event rates, rolling p50/p95/p99, Kafka offsets/lag, training step
duration/loss, synchronization versions/timestamps, fixed retrieval backend,
and active vector checksums through `telemetry.py` and `resources.py`; these are
the basic health signals consumed by the Slice 3 dashboard, while controlled
sampling remains Slice 4. On `stop_requested`, stop creating events,
capture Kafka high-water marks, let training drain through those exact marks,
checkpoint, export the same range, stop children, and publish/register one
immutable child artifact. A crashed trainer leaves serving on its last sync and
sets an actionable failed state; Kafka buffers acknowledged events.

- [ ] **Step 4: Add local orchestration commands**

Add `just online-deps` for Postgres/Kafka, `just online-test`, and make `just
start` locate the locked online virtual environment/executable without starting
an experiment. Dashboard Start remains the only path that launches a synthetic
run.

- [ ] **Step 5: Run the full fixture and recovery gates**

Run:

```bash
python3 -m pytest online/tests -v
cmake --build --preset test
ctest --preset test --output-on-failure
npm test
```

With Compose services, run the tiny June→July E2E twice: once normally and once
with trainer termination after an acknowledged event. Expected: the normal run
creates multiple accepted edges, at least one training update/sync, an exact
Parquet offset export, and an immutable child; recovery replays only uncommitted
events and produces no duplicate update. Run the normal fixture with pgvector,
then run a short hnswlib fixture derived from the same PostgreSQL rows; assert
identical eligible created-Babel IDs, validate the hnswlib manifest, and force a
bad snapshot to prove that no backend fallback occurs.

- [ ] **Step 6: Verify real private-Hub smoke behavior**

Start from the registered original, load the connected dataset by exact SHA,
serve one real recommendation with all stage timings, complete a short 50-creator
June run using the default pgvector backend, gracefully stop, remotely verify
the run export and child artifact, then start a second run selecting that child.
Confirm the original checksum is unchanged, every returned candidate has an
`experiment_babels` row for the run, and unused catalog rows never appear.

- [ ] **Step 7: Write the runbook and commit**

Document prerequisites/tokens, dashboard fields, model and fixed retrieval
selection, pgvector HNSW defaults, optional hnswlib snapshot provenance,
expected log flow, safe stop, Kafka recovery, artifact locations, error
meanings, and how to start from original versus child.

```bash
git add online/src/babel_online/runtime online/tests/runtime online/tests/e2e Justfile README.md documentation.md docs/runbooks/online-experiment.md
git commit -m "feat: complete online recommendation vertical slice"
```

## Slice Acceptance Gate

- [ ] Dashboard Start creates an immutable configuration and a 50-creator run.
- [ ] No creator/source duplicate can pass sampler or PostgreSQL constraints.
- [ ] Every event performs a synchronous recommendation POST with stage timings.
- [ ] Candidate rows are only synthetic creators' persisted Babels; unused catalog articles never appear.
- [ ] Candidate rows exclude the request creator; multiple other creators' Babels may be included.
- [ ] Catalog vectors are materialized lazily and reused without collapsing distinct creator-owned Babels.
- [ ] pgvector cosine HNSW is the default and queries the active 100-dimensional created-Babel materialization.
- [ ] An explicit hnswlib run rebuilds from the same checksum-bearing PostgreSQL rows and never falls back.
- [ ] Retrieval backend is visible and immutable for the life of a run.
- [ ] One schema-valid observable event is acknowledged by Kafka per recommendation.
- [ ] Hidden fields fail schema and import-boundary tests.
- [ ] One consumer trains pairwise updates and commits offsets only after checkpoint.
- [ ] Serving remains on its last valid state during trainer failure or sync rejection.
- [ ] June→July changes environment without changing run ID or resetting the model.
- [ ] Graceful stop drains captured offsets, checkpoints, exports, and saves a child.
- [ ] Original and completed child remain separately selectable and immutable.
- [ ] Dashboard logs online ranking health, never Colab distillation losses.

## Orchestrator Context for the Next Slice

Slice 4 treats this vertical slice as frozen behavior and adds controlled load,
not new recommender semantics. Before dispatching its fleet, record the accepted
recommendation request/response schemas, feedback schema, lifecycle transitions,
metric names, embedding-space manifest, active pgvector snapshot checksum,
optional hnswlib snapshot manifest, tiny-run evidence, and a known-good
original/child pair. Review stage timing completeness and clock units especially carefully:
Slice 4 can repair instrumentation defects, but it must not change the hidden
decision formula, creator construction, pairwise labels, model initialization,
model lineage, candidate-universe rule, or fixed-backend semantics while
measuring them. Preserve a serving-only replay corpus, the ordered created-Babel
IDs and float32 vector checksum for its active PostgreSQL snapshot, and an exact
feedback-offset range from the 50-creator smoke run. Slice 4 must build hnswlib
from that same snapshot and use identical request schedules for the matched
retrieval comparison before interpreting backend latency differences.
