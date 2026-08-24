# Online Recommendation Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin dashboard start and gracefully stop a deterministic 50-creator June→July experiment that synchronously serves two-tower recommendations, publishes observable feedback through one Kafka topic, trains asynchronously, synchronizes versioned state, and saves an immutable child model.

**Architecture:** The implemented C++/Drogon backend remains the dashboard control plane and PostgreSQL owner. A separately installable Python package runs the hidden simulator, loopback recommendation service, Kafka producer, single online trainer, model-state synchronization, and bounded feedback export; strict loader and process boundaries prevent hidden monthly data from entering serving or training. Each run starts from an immutable Hugging Face model artifact, works in a run-scoped local copy, and publishes a new immutable descendant without altering its parent.

**Tech Stack:** C++20, Drogon, PostgreSQL 18/pgvector, Python 3.10+, PyTorch, Transformers, Safetensors, Datasets, Hugging Face Hub, FastAPI/Uvicorn, hnswlib, SciPy, confluent-kafka, Apache Kafka 4.3.1 in KRaft mode, PyArrow, psutil, pytest, Catch2, Node test runner.

## Global Constraints

- Requires the completed backend seeding-dashboard implementation and Slice 2's pinned connected dataset release.
- Requires Slice 2's `huggingface_wikipedia` provenance migration and pinned-Hub dashboard source; online work must not restore a live MediaWiki fallback.
- Starting artifact: immutable complete-2016 recommender in private `dhelmy990/babel-two-tower-recommender`.
- Dataset: private `dhelmy990/babel-wikipedia-experiment`, loaded only at one exact commit SHA per run.
- Default creators: 50; creator IDs and all randomness derive deterministically from the run seed.
- A creator may not create two Babels from the same source article; enforce sampling without replacement and `UNIQUE(run_id, creator_id, source_article_key)`.
- Different creators may create Babels from the same source article.
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
 run/model persistence     hidden-world engine     model + serving POST
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
| Wave 1 | Agent A owns migration/C++ repositories; B owns `online/.../hidden`; C owns `online/.../model` and `serving` | C++, Python, model-compatibility, and import-boundary suites pass together |
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
      ann.py
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
- Produces: `RunConfigV1`, `RecommendationRequestV1`, `RecommendationResponseV1`, `FeedbackEventV1`, `ActivityLogV1`, `ModelManifestV1`, `validate_contract`, and the immutable field names used by every later task.

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

The response returns request/run/model IDs, integer model version, ordered
candidates, and nanosecond durations for queue, tokenization, Qwen encoding,
history lookup, context tower, ANN retrieval, filtering, and total server time.
The feedback schema matches the approved camelCase event exactly and sets
`additionalProperties: false` recursively.

- [ ] **Step 4: Define immutable run and model manifests**

`RunConfigV1` includes dataset SHA, starting model ID, 50 default creators,
environment sequence (`["2026-06"]` or `["2026-06", "2026-07"]`), per-month
event budgets, RNG seed, all simulator defaults, `top_l`, recommendation K,
Kafka topic/group, checkpoint interval, sync interval, and artifact roots.
`ModelManifestV1` includes stable ID/label, nullable parent/producing run,
encoder repo/revision, dataset revisions, environment, counts, training config,
metrics, checkpoint path/checksum, and embedding/index compatibility version.

- [ ] **Step 5: Build a fully separated tiny fixture**

Create six observable articles and text, separate June/July directed graphs,
clickstream rows, two resolved archetypes, deterministic histories, one
original model manifest, request examples, and feedback examples. Put hidden
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

### Task 2: Persist Runs, Synthetic Babels, Logs, and Immutable Models

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
- Modify: `backend/CMakeLists.txt`

**Interfaces:**
- Consumes: Task 1 run/model schemas and existing `Result<T>`/PostgreSQL adapter patterns.
- Produces: `ExperimentRepository`, `ModelRegistryRepository`, `ModelRegistryService::listCompatible`, immutable model creation, lifecycle transitions, activity pagination, and duplicate-source enforcement.

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
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "experiment_repository|model_registry" --output-on-failure`

Expected: tests do not compile because experiment repositories are absent.

- [ ] **Step 3: Add the online migration**

Create `recommender_models`, `experiment_runs`, `experiment_creators`,
`experiment_babels`, `experiment_activity_logs`, and `model_synchronizations`.
Store the exact launch JSON and its SHA-256. Add check constraints for the
approved lifecycle and immutable-parent lineage. Add:

```sql
UNIQUE (run_id, creator_id, source_article_key)
```

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
Compatibility requires equal encoder artifact, output dimension 100, schema
major version, and index-space version. Never expose a delete/update method for
model artifact rows; only labels may be added as separate metadata.

- [ ] **Step 5: Run migration/service tests and commit**

Run: `ctest --preset test -R "experiment_repository|model_registry" --output-on-failure`

```bash
git add backend/migrations/005_online_experiment.sql backend/include/babel/application/experiment_models.hpp backend/include/babel/application/experiment_ports.hpp backend/include/babel/application/model_registry_service.hpp backend/include/babel/adapters/postgres/experiment_repository.hpp backend/src/application/model_registry_service.cpp backend/src/adapters/postgres/experiment_repository.cpp backend/tests backend/CMakeLists.txt
git commit -m "feat: persist online runs and immutable models"
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

### Task 4: Build the Immutable Original, ANN State, and Recommendation POST

**Files:**
- Create: `online/src/babel_online/observable.py`
- Create: `online/src/babel_online/model/item_tower.py`
- Create: `online/src/babel_online/model/context_tower.py`
- Create: `online/src/babel_online/model/ann.py`
- Create: `online/src/babel_online/model/artifact.py`
- Create: `online/src/babel_online/model/registry.py`
- Create: `online/src/babel_online/serving/state.py`
- Create: `online/src/babel_online/serving/timings.py`
- Create: `online/src/babel_online/serving/app.py`
- Create: `online/tests/model/test_context_tower.py`
- Create: `online/tests/model/test_artifact.py`
- Create: `online/tests/serving/test_recommendations.py`

**Interfaces:**
- Consumes: observable catalog, complete distilled encoder artifact, Task 1 recommendation contracts.
- Produces: `ItemTower`, `CreatorContextTower`, `CandidateIndex`, `build_original_artifact`, `ServingState.apply_sync`, and `POST /api/v1/recommendations`.

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
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/model online/tests/serving -v`

Expected: FAIL because model and serving modules are missing.

- [ ] **Step 3: Implement deterministic representation-compatible towers**

Load the complete distilled Qwen encoder/projection by exact model revision and
freeze it. Initialize item residuals to zero. Initialize attention as normalized
scaled dot-product attention between history and new-Babel embeddings. Initialize
the fusion linear map on `[new, attended_history]` to `[0.5I, 0.5I]` with zero
bias. Leave attention/fusion and residuals trainable in the working copy.

- [ ] **Step 4: Build the run-scoped HNSW candidate index**

Precompute/cache observable catalog content vectors, then index each actually
created synthetic Babel under its Babel ID and creator ID. The same article may
therefore have several Babel labels. Support adding a new Babel, applying
touched residual updates under a write lock, querying oversampled neighbors,
and filtering the request creator's own Babels before truncating to K.

- [ ] **Step 5: Implement measured synchronous serving**

Use a bounded request semaphore and capture enqueue time before acquisition.
Time tokenization, new-note encoding, history lookup, context computation, ANN,
filtering, and total with `perf_counter_ns`. Return the complete v1 response and
`Server-Timing`; client/network overhead is computed later as client total minus
server total. Return model ID/version used by the request, even if a sync lands
immediately afterward.

- [ ] **Step 6: Build and publish the original without mutation APIs**

Write Safetensors, context initialization, content-cache/index manifest,
compatibility version, source model/dataset SHAs, and checksums into an atomic
artifact directory. Upload it as an immutable path/tag in
`dhelmy990/babel-two-tower-recommender`; register its stable ID in PostgreSQL.
`load_artifact` verifies every checksum before serving.

- [ ] **Step 7: Run tests and commit**

Run: `python3 -m pytest online/tests/model online/tests/serving -v`

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

- [ ] **Step 4: Implement synchronous POST and atomic feedback publication**

Create the new Babel once, POST observable text/history/K to loopback serving,
measure client monotonic duration, evaluate candidates privately, validate one
feedback event, and wait for Kafka acknowledgement. Only then persist the
activity record and expose include actions as accepted edges. The newly created
Babel may remain durably staged on failure, but it retains the same event number
and request ID and is retried rather than sampled again. On publish failure,
retain the RNG/event number for exact retry and pause the simulation.

- [ ] **Step 5: Implement June→July continuity**

At the month budget boundary, preserve creators, created Babels, trainer/model
state, and run ID. Load July hidden and observable data at the same dataset SHA,
map persistent identities through the crosswalk, initialize new source articles
with content-only vectors, rebuild the compatible run index, and continue event
numbering. Never reset to the original model.

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
  REQUIRE(process.launches() == 1);
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
dataset/index versions, verify creator count/event budget/ranges, persist exact
config before process launch, and pass only the new run ID plus database/runtime
environment to `babel-online run --run-id <uuid>`. One active local synthetic
run is allowed initially.

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
touched-index labels under `.partial`, fsync, rename, then commit Kafka offsets.
On restart, verify checksums, restore state, assign partitions, and seek to the
checkpoint offsets. Ignore incomplete directories.

- [ ] **Step 5: Synchronize through direct atomic model state**

At the configured interval, write only changed context tensors and touched item
residuals plus compatibility/checksum manifest into `sync-v<N>.partial`, rename
to `sync-v<N>`, and atomically update `LATEST`. Serving verifies compatibility,
applies state under its write lock, updates touched ANN labels, and reports both
training and serving versions. Failed/incompatible sync leaves serving on its
last version.

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
- Produces: model selector, launch form, Start, Graceful stop, timeline, online-health panel, and run summary.

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
```

- [ ] **Step 2: Run and verify failure**

Run: `node --test tests/js/experiment-dashboard.test.js`

Expected: FAIL because `experiment-status.js` is absent.

- [ ] **Step 3: Extend the dashboard without weakening seed controls**

Keep the existing Wikipedia seed section. Add dataset/model selectors, default
50 creators, June-only/June→July choice, event budgets, seed, advanced simulator
settings, Start, and Graceful stop. Disable incompatible models with a visible
reason. Do not add reset, overwrite, pause, resume, or Colab controls.

- [ ] **Step 4: Render the three approved views**

Timeline rows show creator/new Babel, recommended titles, actions, and derived
accepted edges. Health shows rates, produced/trained counts, offsets/lag,
rolling ranking loss, checkpoint, training/serving versions, synchronization,
and timing p50/p95/p99. Summary shows counts, action distribution, resources,
sync history, lifecycle/export progress, child artifact, and actionable errors.
Poll logs by sequence and cap DOM rows to 1,000.

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
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest online/tests/runtime/test_supervisor.py -v`

Expected: FAIL because the supervisor is missing.

- [ ] **Step 3: Implement separate serving/training processes**

The supervisor starts a loopback Uvicorn serving child and a single trainer
child, waits for both health checks, then runs the simulator. It persists
rate-limited structured logs. Every five seconds it records basic per-process
CPU/RSS, host memory, optional GPU utilization, request/event rates, rolling
p50/p95/p99, Kafka offsets/lag, training step duration/loss, and synchronization
versions/timestamps through `telemetry.py` and `resources.py`; these are the
basic health signals consumed by the Slice 3 dashboard, while controlled
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
events and produces no duplicate update.

- [ ] **Step 6: Verify real private-Hub smoke behavior**

Start from the registered original, load the connected dataset by exact SHA,
serve one real recommendation with all stage timings, complete a short 50-creator
June run, gracefully stop, remotely verify the run export and child artifact,
then start a second run selecting that child. Confirm the original checksum is
unchanged.

- [ ] **Step 7: Write the runbook and commit**

Document prerequisites/tokens, dashboard fields, model selection, expected log
flow, safe stop, Kafka recovery, artifact locations, error meanings, and how to
start from original versus child.

```bash
git add online/src/babel_online/runtime online/tests/runtime online/tests/e2e Justfile README.md documentation.md docs/runbooks/online-experiment.md
git commit -m "feat: complete online recommendation vertical slice"
```

## Slice Acceptance Gate

- [ ] Dashboard Start creates an immutable configuration and a 50-creator run.
- [ ] No creator/source duplicate can pass sampler or PostgreSQL constraints.
- [ ] Every event performs a synchronous recommendation POST with stage timings.
- [ ] Candidates are other creators' Babels and several may be included.
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
metric names, sync manifest, tiny-run evidence, and a known-good original/child
pair. Review stage timing completeness and clock units especially carefully:
Slice 4 can repair instrumentation defects, but it must not change the hidden
decision formula, creator construction, pairwise labels, model initialization,
or model lineage while measuring them. Preserve a serving-only replay corpus
and an exact feedback-offset range from the 50-creator smoke run; those become
the paired inputs for interference comparisons.
