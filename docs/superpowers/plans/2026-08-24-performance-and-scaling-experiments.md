# Performance and Scaling Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quantify pgvector-versus-hnswlib retrieval behavior, recommendation latency, online-training interference, synchronization cost, adaptation, and resource limits across deterministic nested creator cohorts from 50 through 10,000 without changing the recommender or simulator semantics.

**Architecture:** A separate benchmark package drives the stable Slice 3 APIs in two modes: fixed observable replay first compares pgvector and hnswlib against one checksum-identical created-Babel vector snapshot, then isolates online-training interference on the default pgvector path; live nested-cohort runs measure learning and temporal adaptation with pgvector unless the operator explicitly selects another fixed backend. A sidecar resource sampler and existing per-request/online telemetry produce raw Parquet records; deterministic analysis creates paired reports, freezes the 50-creator quality target, and applies latency-aware trainer backpressure without giving the recommender hidden data.

**Tech Stack:** Python 3.10+, asyncio/httpx, PyArrow/Parquet, NumPy, SciPy, pandas, psutil, pynvml, Hugging Face Hub, PostgreSQL 18 with pgvector 0.8.6, hnswlib, psycopg 3, Kafka, pytest, existing C++/Drogon dashboard and `babel_online` package.

## Global Constraints

- Requires Slice 3 acceptance, a known-good original/child model pair, a frozen request/feedback replay corpus, and stable online schemas.
- Do not change model architecture, hidden decision formulas, creator construction, labels, Qwen weights, or model lineage in this slice.
- The candidate universe remains only Babels created by synthetic creators in the run; a benchmark fails if an unused catalog article is returned.
- Hugging Face remains canonical for bulk artifacts; the benchmarked vectors are the pinned local PostgreSQL materialization derived from those artifacts.
- pgvector cosine HNSW is the default serving backend. hnswlib is an explicit fixed run/condition backend and never a fallback.
- The matched backend comparison uses one PostgreSQL snapshot: identical ordered Babel IDs, creator IDs, source keys, float32 vector bytes, embedding space, model ID/version, and snapshot checksum.
- hnswlib must be rebuilt from that snapshot; backend build/load time is measured separately from steady-state request latency.
- Retrieval backend never changes within a condition. A backend failure invalidates the condition rather than redirecting requests.
- The 2016 Colab job runs elsewhere and is excluded from local interference measurements.
- Conditions are: matched pgvector serving-only; matched hnswlib serving-only; pgvector serving plus training with synchronization disabled; pgvector serving plus training and synchronization; then pgvector increasing request rates and creator cohorts.
- Fixed replay conditions use identical observable requests and feedback offset ranges in identical order.
- Live adaptation conditions use deterministic nested cohorts `50 ⊂ 100 ⊂ 500 ⊂ 1,000 ⊂ 5,000 ⊂ 10,000`.
- The first 50 creators are byte-for-byte identical in every larger cohort.
- Each scale run starts from the same selected immutable model and receives a fresh run ID and working copy.
- Default dashboard maximum cohort is 50; larger cohorts require explicit operator selection and run one at a time.
- Every benchmark suite and every approval to advance the scale ladder is initiated from the admin dashboard; CLI entry points are test/debug plumbing, not the operator workflow.
- The canonical quality threshold is selected once from the 50-creator calibration and then copied unchanged into all scale manifests.
- Serving and training remain separate processes; serving receives scheduling priority.
- Backpressure may reduce trainer batch size or add delay; it may not drop, reorder, or relabel feedback.
- All durations use monotonic nanoseconds; UTC is retained only for cross-process correlation timestamps.
- Persist raw measurements before summaries; reports must be reproducible from raw Parquet and a manifest.
- Upload accepted benchmark artifacts to `runs/<run-id>/performance/` in the private dataset repository at a recorded commit SHA.
- Hidden metrics are available only to offline evaluation and never enter serving, feedback, online trainer, or dashboard activity logs.
- Do not automatically launch the next scale when the current run exceeds configured CPU, memory, disk, Kafka lag, timeout, or error safety limits.
- Use test-first development and commit after every task gate.

---

## Orchestrator Fleet Map

Maximum concurrency is one orchestrator plus three workers. Start with the
contract task, then branch isolated worktrees. Analysis code must consume raw
schemas rather than importing load-generation internals.

```text
Orchestrator / Task 1
benchmark contracts, paired-design manifest, fixture
          |
          +-------------------- Wave 1 --------------------+
          |                         |                       |
 Agent A / Task 2          Agent B / Task 3        Agent C / Task 4
 request load generator    resource sampler        quality/adaptation eval
          |                         |                       |
          +------------------ integration gate ------------+
                                    |
          +-------------------- Wave 2 --------------------+
          |                                                 |
 Agent A / Task 5                                  Agent B / Task 6
 retrieval pair + interference/backpressure        nested scale runner
          |                                                 |
          +------------------ integration gate ------------+
                                    |
                         Agent C / Task 7
                    dashboard + reports interface
                                    |
                         Orchestrator / Task 8
                 calibrated matrix + remote acceptance
```

| Lane | Owned paths | Review concern |
|---|---|---|
| Orchestrator | `schemas/performance/`, frozen manifests, final runbook | Paired inputs and stopping rules are immutable before work starts |
| Agent A | `benchmark/loadgen.py`, `retrieval.py`, `conditions.py`, `backpressure.py` | Backend comparisons use checksum-identical rows and load timing/control loops never use wall-clock durations |
| Agent B | `benchmark/resources.py`, `scale.py` | Sampler overhead is measured; cohorts remain nested |
| Agent C | `benchmark/evaluation.py`, `analysis.py`, performance C++ control files, dashboard assets | Hidden evaluation data cannot flow back into online services |

At each integration gate, the orchestrator runs the same fixture through all
merged lanes and checks event/request IDs join one-to-one. If an agent proposes
a Slice 3 schema change, stop and review it as an instrumentation defect; do
not let a benchmark lane silently redefine production contracts.

## Target File Map

```text
schemas/performance/
  benchmark-manifest-v1.json          paired conditions and safety limits
  request-measurement-v1.json         client/server latency record
  resource-sample-v1.json             host/process/device sample
  adaptation-window-v1.json           quality/event/time window
  retrieval-comparison-v1.json        paired backend inputs and exact-recall output
  performance-summary-v1.json         comparative output
fixtures/performance/
  requests.jsonl
  feedback.jsonl
  resources.jsonl
  hidden-evaluation/                   evaluator-only relevance fixture
benchmark/
  pyproject.toml
  requirements.lock
  src/babel_benchmark/
    contracts.py
    loadgen.py
    retrieval.py
    resources.py
    evaluation.py
    backpressure.py
    conditions.py
    scale.py
    analysis.py
    hub.py
    cli.py
  tests/
backend/admin/
  performance-status.js
  dashboard.js
  index.html
  dashboard.css
tests/js/performance-dashboard.test.js
backend/
  migrations/006_performance_experiments.sql
  include/babel/application/performance_experiment_service.hpp
  include/babel/http/performance_controller.hpp
  include/babel/runtime/performance_job_runner.hpp
  src/application/performance_experiment_service.cpp
  src/http/performance_controller.cpp
  src/runtime/performance_job_runner.cpp
  tests/unit/performance_experiment_service_test.cpp
  tests/integration/performance_http_contract_test.cpp
docs/runbooks/performance-and-scaling.md
docs/experiments/performance-report-template.md
README.md
documentation.md
```

### Task 1: Freeze Benchmark Contracts and the Paired Experimental Design

**Files:**
- Create: `schemas/performance/benchmark-manifest-v1.json`
- Create: `schemas/performance/request-measurement-v1.json`
- Create: `schemas/performance/resource-sample-v1.json`
- Create: `schemas/performance/adaptation-window-v1.json`
- Create: `schemas/performance/retrieval-comparison-v1.json`
- Create: `schemas/performance/performance-summary-v1.json`
- Create: `fixtures/performance/requests.jsonl`
- Create: `fixtures/performance/feedback.jsonl`
- Create: `fixtures/performance/resources.jsonl`
- Create: `fixtures/performance/hidden-evaluation/*`
- Create: `benchmark/pyproject.toml`
- Create: `benchmark/requirements.lock`
- Create: `benchmark/src/babel_benchmark/__init__.py`
- Create: `benchmark/src/babel_benchmark/contracts.py`
- Create: `benchmark/tests/test_contracts.py`

**Interfaces:**
- Consumes: frozen Slice 3 request/response/feedback/run/model schemas.
- Produces: `BenchmarkManifestV1`, `RequestMeasurementV1`, `ResourceSampleV1`, `AdaptationWindowV1`, `RetrievalComparisonV1`, `PerformanceSummaryV1`, and exact condition names.

- [ ] **Step 1: Write failing schema and paired-input tests**

```python
def test_every_system_condition_uses_the_same_replay_inputs(manifest):
    conditions = {c.name: c for c in manifest.replay_conditions}
    assert {c.request_corpus_sha256 for c in conditions.values()} == {manifest.request_corpus_sha256}
    assert all(c.feedback_range == manifest.feedback_range for c in conditions.values())

def test_raw_measurement_preserves_stage_and_model_identity():
    row = RequestMeasurementV1.model_validate(fixture_request())
    assert row.clientTotalNs >= row.serverTotalNs
    assert row.servingModelId and row.servingModelVersion >= 0
    assert row.retrievalBackend in {"pgvector", "hnswlib"}
    assert row.pgvectorSnapshotSha256
    assert row.backendSnapshotSha256
    assert row.queryVectorSha256

def test_backend_pair_pins_identical_created_babel_vectors(manifest):
    pg = manifest.condition("pgvector_serving_only")
    hns = manifest.condition("hnswlib_serving_only")
    assert pg.pgvectorSnapshotSha256 == hns.pgvectorSnapshotSha256
    assert pg.orderedBabelIdsSha256 == hns.orderedBabelIdsSha256
    assert pg.vectorBytesSha256 == hns.vectorBytesSha256
    assert pg.createdBabelRowCount == hns.createdBabelRowCount
    assert pg.servingModelId == hns.servingModelId
    assert pg.servingModelVersion == hns.servingModelVersion

def test_backend_pair_uses_byte_identical_queries_by_request_id(pg_rows, hns_rows):
    assert {r.requestId: r.queryVectorSha256 for r in pg_rows} == {
        r.requestId: r.queryVectorSha256 for r in hns_rows
    }
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_contracts.py -v`

Expected: collection fails because benchmark contracts are absent.

- [ ] **Step 3: Define the exact conditions and comparison keys**

Use stable names:

```text
pgvector_serving_only
hnswlib_serving_only
pgvector_training_no_sync
pgvector_training_and_sync
live_scale_pgvector
```

The manifest pins dataset/model SHAs, embedding-space ID, active PostgreSQL
snapshot checksum, ordered created-Babel-ID checksum, canonical float32 vector
checksum, created-Babel row count, model ID/version, request corpus checksum,
Kafka topic and offset range, warmup/sample counts, concurrency/RPS schedule,
pgvector `ef_search`/iterative-scan settings, hnswlib M/ef-construction/ef-search,
sync interval, trainer micro-batch bounds, resource interval, timeouts, safety
limits, host description, and code commit. Comparisons join by request ID and
schedule slot, not timestamp proximity alone. Validate that every snapshot Babel
ID belongs to a synthetic creator in that run and that no catalog-only row is
present.

`RetrievalComparisonV1` records backend build/load time, steady-state latency,
throughput, timeout/error counts, memory, candidate-set violations, and Recall@K
against exact cosine results. Record pgvector and hnswlib parameters separately;
do not hide them behind a generic tuning profile.

- [ ] **Step 4: Define the calibration threshold algorithm**

For the 50-creator June calibration, calculate fixed-evaluation NDCG@10 every
`evaluation_window_events`. Smooth with a trailing five-window mean. Let the
plateau be the maximum smoothed value observed after at least 50% of the event
budget. Set `quality_target = 0.95 * plateau`; attainment requires three
consecutive smoothed windows at or above the target. Persist the numeric target,
window size, plateau, selection event, and manifest checksum. All larger June
runs and the July re-adaptation measurement use that unchanged numeric target.

- [ ] **Step 5: Lock dependencies and commit**

Run:

```bash
python3 -m piptools compile --generate-hashes --resolver=backtracking --extra dev --output-file benchmark/requirements.lock benchmark/pyproject.toml
python3 -m pytest benchmark/tests/test_contracts.py -v
```

```bash
git add schemas/performance fixtures/performance benchmark/pyproject.toml benchmark/requirements.lock benchmark/src/babel_benchmark/__init__.py benchmark/src/babel_benchmark/contracts.py benchmark/tests/test_contracts.py
git commit -m "feat: freeze performance experiment contracts"
```

### Task 2: Implement the Observable Request Load Generator

**Files:**
- Create: `benchmark/src/babel_benchmark/loadgen.py`
- Create: `benchmark/tests/test_loadgen.py`

**Interfaces:**
- Consumes: Task 1 manifests and Slice 3 recommendation endpoint.
- Produces: `LoadSchedule`, `ReplayCorpus`, `run_load`, and raw `RequestMeasurementV1` rows.

- [ ] **Step 1: Write deterministic schedule and latency tests**

```python
def test_schedule_is_independent_of_response_time():
    assert LoadSchedule(rate=10, seconds=1, seed=3).planned_offsets_ns() == [i * 100_000_000 for i in range(10)]

def test_client_latency_wraps_the_entire_http_request(fake_server):
    row = run_one(fake_server, fixture_request())
    assert row.clientTotalNs >= row.serverTotalNs
    assert row.inferredNetworkSerializationNs == row.clientTotalNs - row.serverTotalNs

def test_replay_records_fixed_backend_and_snapshot(fake_server):
    row = run_one(fake_server, fixture_request())
    assert row.retrievalBackend == fake_server.fixed_backend
    assert row.pgvectorSnapshotSha256 == fake_server.pgvector_snapshot_sha256
    assert row.backendSnapshotSha256 == fake_server.backend_snapshot_sha256
    assert row.queryVectorSha256 == fake_server.query_vector_sha256
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_loadgen.py -v`

Expected: FAIL because the load generator is absent.

- [ ] **Step 3: Build closed- and open-loop schedules**

Open-loop mode schedules arrivals independently at configured RPS and records
client queue lateness; closed-loop mode holds fixed concurrency. Use one
`httpx.AsyncClient`, bounded connection pools, explicit connect/read/total
timeouts, deterministic request ordering, and no automatic retries. Record
timeouts/errors as rows rather than omitting them.

- [ ] **Step 4: Capture raw timing and warmup boundaries**

Time from immediately before request serialization through complete response
read with `perf_counter_ns`. Validate server stage timings and calculate inferred
network/serialization. Require the response's retrieval backend, embedding-space
ID, and serving model ID to equal the condition manifest on every sampled
request. Before the first sync, model version, PostgreSQL snapshot, and backend
snapshot must equal the starting manifest; afterward they must join exactly to
the condition's immutable synchronization ledger. Any unmatched version or
checksum invalidates the condition. The matched retrieval pair must also report
the same query-vector checksum for each request ID; otherwise it is not a valid
backend comparison. Mark warmup rows explicitly; never mix them into reported
percentiles. Stream records to atomic Parquet row groups to bound memory.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_loadgen.py -v`

```bash
git add benchmark/src/babel_benchmark/loadgen.py benchmark/tests/test_loadgen.py
git commit -m "feat: generate reproducible recommendation load"
```

### Task 3: Sample Host, Process, GPU, Disk, and Network Resources

**Files:**
- Create: `benchmark/src/babel_benchmark/resources.py`
- Create: `benchmark/tests/test_resources.py`

**Interfaces:**
- Consumes: process IDs/identity tokens from Slice 3 supervisor.
- Produces: `ResourceSampler`, `ProcessIdentity`, and `ResourceSampleV1` Parquet stream.

- [ ] **Step 1: Write process-reuse and counter-delta tests**

```python
def test_pid_reuse_is_rejected(fake_proc):
    identity = ProcessIdentity(pid=100, create_time=10.0)
    fake_proc.set_create_time(11.0)
    assert ResourceSampler.matches(identity, fake_proc) is False

def test_disk_and_network_rates_use_counter_deltas(samples):
    rates = calculate_rates(samples[0], samples[1])
    assert rates.diskReadBytesPerSecond == 1000
    assert rates.networkSentBytesPerSecond == 2000
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_resources.py -v`

Expected: FAIL because resource sampling is missing.

- [ ] **Step 3: Implement one-hertz sampling with graceful capability gaps**

Collect host and per-process CPU, RSS/VMS, threads, I/O counters, disk counters,
network counters, load average, and available memory. When NVML is available,
collect GPU utilization, memory, power, and process memory; otherwise record
`gpuAvailable=false` rather than failing. Identify processes by PID plus creation
time and role (`serving`, `trainer`, `simulator`, `kafka`, `postgres`).

- [ ] **Step 4: Measure sampler overhead**

Run an idle control with and without the sampler for five minutes. Store sampler
CPU/RSS and scheduling drift. Reject a configuration whose sampler exceeds 1%
of one CPU core or whose p95 wakeup drift exceeds half the sampling interval.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_resources.py -v`

```bash
git add benchmark/src/babel_benchmark/resources.py benchmark/tests/test_resources.py
git commit -m "feat: record benchmark resource usage"
```

### Task 4: Compute Hidden Quality and Adaptation Without Leakage

**Files:**
- Create: `benchmark/src/babel_benchmark/evaluation.py`
- Create: `benchmark/tests/test_evaluation.py`
- Create: `benchmark/tests/test_evaluation_boundary.py`

**Interfaces:**
- Consumes: archived observable interactions plus evaluator-only hidden monthly environment.
- Produces: `evaluate_window`, `select_calibration_target`, user-preference/graph metrics, and `AdaptationWindowV1`.

- [ ] **Step 1: Write exact metric and boundary tests**

```python
def test_target_requires_three_sustained_windows():
    result = first_attainment([.7, .81, .82, .83, .4], target=.8, sustained=3)
    assert result.window_index == 3

def test_evaluator_is_not_importable_by_online_packages():
    assert forbidden_imports(ONLINE_ROOT, forbidden="babel_benchmark.evaluation") == []
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_evaluation.py benchmark/tests/test_evaluation_boundary.py -v`

Expected: FAIL because the evaluator is absent.

- [ ] **Step 3: Implement preference and graph recovery metrics**

At fixed event windows compute NDCG@K, Recall@K, and rank correlation against
hidden creator PPR; separately compute neighbor overlap, graph Recall/NDCG,
edge-prediction AUC, average precision, and graph-proximity correlation from
reconstructed include edges. Never compare embedding coordinates. Report total
interactions and interactions per creator on every window.

- [ ] **Step 4: Implement June and July adaptation clocks**

Measure event count and monotonic elapsed training time from June start to the
frozen target. For July, start both counters at the environment switch without
resetting model/global interactions. If a run does not attain target, report
censored outcome with its final event/time instead of inventing a duration.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_evaluation.py benchmark/tests/test_evaluation_boundary.py -v`

```bash
git add benchmark/src/babel_benchmark/evaluation.py benchmark/tests/test_evaluation.py benchmark/tests/test_evaluation_boundary.py
git commit -m "feat: evaluate temporal recommender adaptation"
```

### Task 5: Run Paired Interference Conditions and Apply Latency Backpressure

**Files:**
- Create: `benchmark/src/babel_benchmark/retrieval.py`
- Create: `benchmark/src/babel_benchmark/backpressure.py`
- Create: `benchmark/src/babel_benchmark/conditions.py`
- Create: `benchmark/tests/test_retrieval.py`
- Create: `benchmark/tests/test_backpressure.py`
- Create: `benchmark/tests/test_conditions.py`
- Modify: `online/src/babel_online/training/consumer.py`
- Modify: `online/tests/training/test_consumer.py`

**Interfaces:**
- Consumes: Tasks 1–3 and Slice 3 trainer control socket/status.
- Produces: `FrozenVectorSnapshot`, `prepare_backend_condition`, `exact_cosine_top_k`, `compare_retrieval_backends`, `LatencyBackpressureController`, `run_condition_matrix`, and paired raw condition directories.

- [ ] **Step 1: Write backend-pair and backpressure state-machine tests**

```python
def test_backend_conditions_clone_identical_created_babel_rows(condition_factory):
    pg = condition_factory.prepare("pgvector_serving_only")
    hns = condition_factory.prepare("hnswlib_serving_only")
    assert pg.run_id != hns.run_id
    assert pg.snapshot.pgvector_sha256 == hns.snapshot.pgvector_sha256
    assert pg.snapshot.ordered_babel_ids == hns.snapshot.ordered_babel_ids
    assert pg.snapshot.vector_bytes_sha256 == hns.snapshot.vector_bytes_sha256
    assert all(row.is_created_synthetic_babel for row in pg.snapshot.rows)

def test_backend_failure_invalidates_condition_without_fallback(condition_factory):
    condition = condition_factory.prepare("hnswlib_serving_only")
    condition.corrupt_hnsw_snapshot()
    result = condition.run()
    assert result.status == "invalid"
    assert result.pgvector_request_count == 0

def test_controller_reduces_batch_then_adds_delay():
    ctl = controller(batch=16, minimum=2, threshold_ms=100)
    assert ctl.observe_p95(130).micro_batch == 8
    assert ctl.observe_p95(130).micro_batch == 4
    assert ctl.observe_p95(130).micro_batch == 2
    assert ctl.observe_p95(130).delay_ms > 0

def test_backpressure_never_changes_offsets_or_event_order(controller, events):
    output = controller.process(events)
    assert output.event_ids == events.event_ids
    assert output.offsets == events.offsets
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_retrieval.py benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py -v`

Expected: FAIL because condition orchestration is missing.

- [ ] **Step 3: Freeze one created-Babel vector snapshot and exact ground truth**

Read the accepted Slice 3 materialization in stable Babel-ID order and reject any
row without matching `experiment_babels`/synthetic-creator ownership. Canonically
hash the Babel ID, creator ID, source key, normalized float32 vector bytes,
catalog content hash, embedding-space ID, and model ID/version with Slice 3's
canonicalization function; exclude run ID so the exact rows can be cloned into
condition-specific runs. Create a fresh run for each condition with
its backend immutable from launch, then insert byte-identical experiment Babel
and vector rows. Assert the destination checksum before starting its server.

For evaluation queries only, compute exact filtered cosine top-K from the same
condition table with PostgreSQL index and bitmap scans disabled in a read-only
transaction. Exclude the request creator exactly as serving does. This offline
query is ground truth for Recall@K; it is never substituted into the serving
path.

- [ ] **Step 4: Run the matched pgvector/hnswlib serving comparison**

Start `pgvector_serving_only` with the frozen PostgreSQL HNSW parameters, warm it
with the manifest's warmup requests, and replay the measured schedule. Start
`hnswlib_serving_only` from a different run ID but the checksum-identical rows,
build/validate its shadow snapshot using the frozen hnswlib parameters, and
replay the identical warmup and measured schedule. Both conditions use the same
model artifact, model version, process placement, request IDs, schedule slots,
and resource sampler. Record separately:

```text
snapshot preparation / index build / index load time
steady-state p50 / p95 / p99 / max and throughput
process and index memory
Recall@10 and Recall@50 versus exact cosine top-K
candidate-set violations and timeout/error counts
```

For pgvector, preparation time covers snapshot clone/activation, database index
maintenance, and warmup; for hnswlib it additionally exposes export/build/load
times. Do not present these unequal phases as request latency.

- [ ] **Step 5: Establish the pgvector serving-only interference baseline**

Use `pgvector_serving_only` for each request schedule, disable trainer and sync,
and compute its baseline p95 after warmup. The latency threshold for matching
training conditions is `1.25 * baseline_p95`; record the numeric value and source
condition checksum before enabling training. hnswlib results do not define this
threshold because the online interference and scale ladder remain pgvector-first.

- [ ] **Step 6: Run the two pgvector training interference conditions**

Reset to the same immutable starting artifact and clone the same frozen created-
Babel snapshot before each condition. Replay the same Kafka feedback range at a
controlled rate. In `pgvector_training_no_sync`, train/checkpoint but hold
serving model/vector version fixed. In `pgvector_training_and_sync`, apply the
configured atomic pgvector synchronization cadence. Use the same request arrival
schedule and resource sampler in both; verify every response still reports
`retrievalBackend=pgvector` and a model-version/vector-checksum pair present in
that condition's synchronization ledger.

- [ ] **Step 7: Apply hysteretic training backpressure**

Calculate rolling p95 over the latest max(200 requests, 60 seconds). If it
exceeds threshold for two consecutive windows, halve the micro-batch down to 2,
then add 25 ms inter-batch delay up to 500 ms. After five consecutive windows
below `0.90 * threshold`, remove delay in 25 ms steps, then double batch up to
its configured maximum. Persist every transition and its observed latency/lag.

- [ ] **Step 8: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_retrieval.py benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py online/tests/training/test_consumer.py -v`

```bash
git add benchmark/src/babel_benchmark/retrieval.py benchmark/src/babel_benchmark/backpressure.py benchmark/src/babel_benchmark/conditions.py benchmark/tests/test_retrieval.py benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py online/src/babel_online/training/consumer.py online/tests/training/test_consumer.py
git commit -m "feat: compare retrieval and bound training interference"
```

### Task 6: Execute Safe Nested Creator Scaling

**Files:**
- Create: `benchmark/src/babel_benchmark/scale.py`
- Create: `benchmark/tests/test_scale.py`

**Interfaces:**
- Consumes: live Slice 3 runs, Task 1 frozen quality target, Task 2 load schedules, Task 3 resource sampler, Task 4 evaluator.
- Produces: `nested_cohort_ids`, `ScaleRunPlan`, `run_scale_ladder`, and safety-stop receipts.

- [ ] **Step 1: Write cohort nesting and stop-policy tests**

```python
@pytest.mark.parametrize("small,large", [(50,100), (100,500), (500,1000), (1000,5000), (5000,10000)])
def test_creator_cohorts_are_prefix_nested(small, large):
    assert nested_cohort_ids(small, seed=19) == nested_cohort_ids(large, seed=19)[:small]

def test_safety_breach_stops_before_next_scale(runner):
    runner.complete(50, peak_memory_fraction=.95)
    assert runner.next_scale() is None
    assert runner.receipt.reason == "memory_safety_limit"

def test_scale_plan_defaults_to_fixed_pgvector_backend():
    plan = ScaleRunPlan.for_creators(50)
    assert plan.retrieval_backend == "pgvector"
    assert plan.allow_backend_switch is False
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_scale.py -v`

Expected: FAIL because scale orchestration is absent.

- [ ] **Step 3: Build explicit one-run-at-a-time scale plans**

Each scale gets a fresh run ID, the same selected starting model, dataset SHA,
embedding space, `retrievalBackend=pgvector` by default, simulator parameters,
frozen quality target, per-creator event budget, and request schedule normalized
both by total RPS and RPS/creator. Record the creator manifest checksum and
assert each smaller manifest is the exact prefix. If a later operator explicitly
requests an hnswlib scale study, it is a separate fully labeled ladder; never mix
backends within a ladder or compare its points to the pgvector ladder as though
only creator count changed.

At every scale, assert that the retrievable row count equals the distinct
created synthetic Babel count, that every row has its owning creator, and that
no unused catalog article is returned. Record pgvector table/index sizes and
created-Babel insert/materialization time alongside request and trainer metrics.

- [ ] **Step 4: Enforce resource safety before advancing**

Default stop limits: host memory 90% for 60 seconds, disk free below 10 GiB,
timeout/error rate above 5% for two windows, Kafka lag increasing for ten
windows after maximum backpressure, or any process crash/checkpoint failure.
Gracefully stop/export the current run and require explicit dashboard approval
before retrying or advancing. Never auto-launch 100+ creators from the default
50 selection.

- [ ] **Step 5: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_scale.py -v`

```bash
git add benchmark/src/babel_benchmark/scale.py benchmark/tests/test_scale.py
git commit -m "feat: run deterministic creator scale ladder"
```

### Task 7: Produce Comparative Reports and Dashboard Benchmark Controls

**Files:**
- Create: `benchmark/src/babel_benchmark/analysis.py`
- Create: `benchmark/src/babel_benchmark/hub.py`
- Create: `benchmark/src/babel_benchmark/cli.py`
- Create: `benchmark/tests/test_analysis.py`
- Create: `benchmark/tests/test_hub.py`
- Create: `backend/admin/performance-status.js`
- Create: `tests/js/performance-dashboard.test.js`
- Create: `backend/migrations/006_performance_experiments.sql`
- Create: `backend/include/babel/application/performance_experiment_service.hpp`
- Create: `backend/src/application/performance_experiment_service.cpp`
- Create: `backend/include/babel/http/performance_controller.hpp`
- Create: `backend/src/http/performance_controller.cpp`
- Create: `backend/include/babel/runtime/performance_job_runner.hpp`
- Create: `backend/src/runtime/performance_job_runner.cpp`
- Create: `backend/tests/unit/performance_experiment_service_test.cpp`
- Create: `backend/tests/integration/performance_http_contract_test.cpp`
- Create: `docs/experiments/performance-report-template.md`
- Modify: `backend/admin/index.html`
- Modify: `backend/admin/dashboard.css`
- Modify: `backend/admin/dashboard.js`
- Modify: `backend/src/runtime/application.cpp`
- Modify: `backend/CMakeLists.txt`

**Interfaces:**
- Consumes: Tasks 1–6 raw Parquet/manifest outputs and existing dashboard experiment APIs.
- Produces: `analyze_benchmark`, remote artifact publication, `PerformanceExperimentService`, nonce-protected benchmark control API, benchmark selection/status/summary views, and JSON/Markdown reports.

- [ ] **Step 1: Write percentile, slowdown, and dashboard tests**

```python
def test_slowdown_uses_paired_condition_p95():
    report = analyze_fixture(baseline_ms=[10, 20, 30], trained_ms=[20, 40, 60])
    assert report.p95SlowdownRatio == pytest.approx(2.0)

def test_retrieval_report_rejects_nonidentical_snapshots():
    with pytest.raises(UnpairedRetrievalComparison):
        analyze_retrieval(pg_fixture(snapshot="a"), hns_fixture(snapshot="b"))

def test_retrieval_report_includes_accuracy_and_preparation_costs():
    report = analyze_retrieval(pg_fixture(), hns_fixture())
    assert report.pgvector.recallAt10 is not None
    assert report.hnswlib.recallAt10 is not None
    assert report.hnswlib.indexBuildNs > 0
    assert report.candidateSetViolations == 0
```

```javascript
test('dashboard defaults to 50 and never auto-advances', () => {
  const view = benchmarkForm(defaults());
  assert.equal(view.creatorCount, 50);
  assert.equal(view.scaleRetrievalBackend, 'pgvector');
  assert.equal(view.autoAdvance, false);
});
```

```cpp
TEST_CASE("scale approval advances exactly one safe cohort") {
  auto benchmark = service.start(validManifest(/*max_creators=*/500));
  repository.markCohortSafe(benchmark->id, 50);
  auto next = service.approveNextScale(benchmark->id);
  REQUIRE(next->creator_count == 100);
  REQUIRE(process.launchesFor(benchmark->id) == 2);
}
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_analysis.py benchmark/tests/test_hub.py -v && cmake --build --preset test && ctest --preset test -R "performance_experiment|performance_http" --output-on-failure && node --test tests/js/performance-dashboard.test.js`

Expected: FAIL because analysis, performance service/controller, and dashboard
modules are absent.

- [ ] **Step 3: Compute exact requested reports from raw data**

Report p50/p95/p99/max, RPS, timeout/error rate, all server stages, client
overhead, trainer step time, Kafka lag, sync duration/spikes, version staleness,
CPU/GPU/memory/disk/network, backpressure actions, and
`p95(condition)/p95(pgvector_serving_only)`. Give the matched retrieval pair its
own table: preparation/build/load time, steady-state latency/throughput, memory,
Recall@10/50 against exact cosine, pgvector and hnswlib parameters, snapshot and
vector checksums, row count, and candidate-set violations. Do not claim hnswlib
is faster or more accurate unless the snapshot/model/request equality checks
pass. For scale runs report quality versus total events, events/creator,
wall/training time, throughput, pgvector table/index growth, materialization
cost, and June/July target attainment. Include sample counts and confidence
intervals from deterministic bootstrap resampling; never report a percentile
without its denominator.

- [ ] **Step 4: Add explicit dashboard benchmark launch/status**

Allow selection of starting immutable model, one cohort or maximum ladder size,
scale retrieval backend, request schedule, sync interval, and safety limits.
The systems suite always includes the matched pgvector/hnswlib serving-only pair;
the training-interference conditions default to pgvector. Default scale backend
is pgvector, creator count/max is 50, and auto-advance is false. The selected
backend is fixed and visible for every condition/run. Show vector snapshot
checksum, current condition/backend, raw sample counts, resource/safety state,
backpressure, graceful-stop progress, and links to the accepted report/artifact.
Keep these controls separate from ordinary online-run Start.

Persist `performance_experiments` and `performance_conditions` in migration
006 with immutable manifest/checksum, retrieval backend, PostgreSQL snapshot
checksum, model/vector identity, state, current condition/cohort, safety receipt,
artifact SHA, and timestamps. Reject backend/snapshot updates after condition
creation. `PerformanceJobRunner` launches exactly one `babel-benchmark run
--benchmark-id <uuid>` process after the dashboard POST.
Expose:

```text
GET  /admin/api/v1/performance/latest
GET  /admin/api/v1/performance/{id}
POST /admin/api/v1/performance
POST /admin/api/v1/performance/{id}/graceful-stop
POST /admin/api/v1/performance/{id}/approve-next-scale
```

All POSTs use existing admin nonce/Host/Origin protection. The approval endpoint
advances only from a completed, safe cohort to the single next ladder value and
never skips sizes or auto-approves. CLI commands remain available for automated
fixture tests but are not documented as the operator launch path.

- [ ] **Step 5: Publish immutable raw and derived artifacts**

Write manifest, raw requests/resources/adaptation windows, summary JSON,
Markdown report, plots as data-backed SVG/PNG, and checksums. Upload under
`runs/<run-id>/performance/`, resolve/record the resulting Hub SHA, remotely
reload summary plus one row from every Parquet file, and never overwrite an
accepted report path.

- [ ] **Step 6: Test and commit**

Run: `python3 -m pytest benchmark/tests -v && cmake --build --preset test && ctest --preset test -R "performance_experiment|performance_http" --output-on-failure && npm test`

```bash
git add benchmark/src/babel_benchmark/analysis.py benchmark/src/babel_benchmark/hub.py benchmark/src/babel_benchmark/cli.py benchmark/tests/test_analysis.py benchmark/tests/test_hub.py backend/migrations/006_performance_experiments.sql backend/include/babel/application/performance_experiment_service.hpp backend/src/application/performance_experiment_service.cpp backend/include/babel/http/performance_controller.hpp backend/src/http/performance_controller.cpp backend/include/babel/runtime/performance_job_runner.hpp backend/src/runtime/performance_job_runner.cpp backend/tests/unit/performance_experiment_service_test.cpp backend/tests/integration/performance_http_contract_test.cpp backend/src/runtime/application.cpp backend/CMakeLists.txt backend/admin/performance-status.js backend/admin/index.html backend/admin/dashboard.css backend/admin/dashboard.js tests/js/performance-dashboard.test.js docs/experiments/performance-report-template.md
git commit -m "feat: report online performance experiments"
```

### Task 8: Calibrate, Run the Matrix, and Record Reproducible Acceptance

**Files:**
- Create: `docs/runbooks/performance-and-scaling.md`
- Create: `benchmark/tests/e2e/test_performance_fixture.py`
- Modify: `README.md`
- Modify: `documentation.md`

**Interfaces:**
- Consumes: Tasks 1–7 and accepted Slice 3 services.
- Produces: frozen calibration manifest, controlled interference results, opt-in scale results, remote acceptance evidence, and runbook.

- [ ] **Step 1: Run the deterministic fixture E2E**

Run: `python3 -m pytest benchmark/tests/e2e/test_performance_fixture.py -v`

Expected: a tiny replay produces both checksum-matched serving backends and both
pgvector training-interference conditions, a backpressure transition, resource
rows, a frozen target, 50⊂100 creator manifests, and a summary whose
IDs/checksums join exactly. The fixture includes a deliberately higher-scoring
unused catalog vector and proves neither backend returns it.

- [ ] **Step 2: Run full repository regression gates**

Run:

```bash
python3 -m pytest online/tests benchmark/tests -v
cmake --build --preset test
ctest --preset test --output-on-failure
npm test
```

Expected: all Slice 3 behavior remains unchanged except the explicit trainer
rate-control interface.

- [ ] **Step 3: Calibrate 50 creators and freeze the target**

From the chosen immutable starting model, run the live 50-creator June→July
calibration, calculate the Task 1 target, write its immutable manifest/checksum,
and obtain operator review. If the target is unattainable within calibration
budget or quality is invalid, stop; do not fabricate a threshold or launch
larger cohorts.

- [ ] **Step 4: Run the matched retrieval pair, then the interference matrix**

For each approved RPS/concurrency point, first run pgvector and hnswlib
serving-only from checksum-identical created-Babel rows with identical requests.
Verify candidate universe, Recall@K, and snapshot equality before accepting the
backend comparison. Then use pgvector serving-only to derive the interference
threshold and run pgvector training/no-sync and training/sync from the same model
and cloned vector snapshot with identical inputs. Remotely publish and verify raw
artifacts after every condition so later failures do not lose completed
measurements.

- [ ] **Step 5: Advance the scale ladder only by explicit approval**

Run 50, review safety/quality, then offer 100; repeat for 500, 1,000, 5,000,
and 10,000. A stopped ladder is valid partial evidence. Every completed scale
must have a graceful checkpoint/export and independent immutable child; no
larger scale continues from the smaller scale's learned model. Use pgvector for
the approved ladder unless the manifest explicitly defines a separate hnswlib
ladder; never switch a running condition or ladder point.

- [ ] **Step 6: Write the runbook and final comparative report**

Document hardware capture, dashboard controls, safe sequencing, calibration,
condition reset, matched snapshot construction, exact-cosine ground truth,
interpreting retrieval latency/recall/build cost and training
latency/lag/backpressure, recovery, scale approval, Hub paths, and how to
regenerate every table from raw Parquet. Clearly distinguish backend replay,
training-interference replay, and live adaptation results.

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/performance-and-scaling.md benchmark/tests/e2e/test_performance_fixture.py README.md documentation.md
git commit -m "docs: verify performance and scaling experiments"
```

## Slice Acceptance Gate

- [ ] pgvector serving-only, hnswlib serving-only, pgvector training/no-sync, and pgvector training/sync use the declared paired inputs.
- [ ] The retrieval pair proves identical created-Babel IDs, vectors, embedding space, model version, request schedule, per-request query-vector checksums, and PostgreSQL snapshot checksum.
- [ ] No condition returns an unused catalog article or the request creator's own Babel.
- [ ] Every request has client total, server total, stage timings, retrieval backend, vector snapshot, model version, and outcome.
- [ ] Retrieval reports include preparation/build/load cost, memory, Recall@10/50 versus exact cosine, and fixed backend parameters.
- [ ] A backend error invalidates its condition; no request silently falls back.
- [ ] Reports contain p50/p95/p99/max, RPS, errors, lag, step time, sync spikes, and resource use.
- [ ] Slowdown ratio uses matching schedule/model/hardware conditions.
- [ ] Backpressure protects latency without dropping/reordering feedback.
- [ ] The 50-creator calibration produces one immutable numeric quality target.
- [ ] Nested cohort manifests prove exact prefix identity.
- [ ] June and July report both elapsed training time and interaction count to target.
- [ ] Larger cohorts start from the same selected immutable model, not prior scale children.
- [ ] Default execution remains 50 creators; larger scales require explicit approval.
- [ ] Raw and derived artifacts remotely reload at one recorded Hub commit.
- [ ] All quality evaluation remains outside serving and online training.

## Orchestrator Context for the Next Phase

This is the last approved implementation slice. The next phase is an evidence
review and architecture decision, not automatic expansion. Review which resource
first saturates, whether p95 degradation comes from encoder inference, trainer
contention, PostgreSQL retrieval/index maintenance, hnswlib memory/build cost,
Kafka lag, or synchronization, and whether quality reaches the frozen target
before changing infrastructure. Treat pgvector as the default after this slice
unless the matched evidence justifies expanding hnswlib's role; a win at one
snapshot size is not permission to replace durable PostgreSQL state or enable
automatic fallback. Only evidence from the paired raw records should justify a
later GPU split, multiple consumers/partitions, remote serving, a parameter
server, or other Monolith-like scaling. Preserve the accepted manifests,
original/child artifacts, embedding-space/model/dataset SHAs, PostgreSQL and
hnswlib snapshot manifests, ordered IDs/vector checksums, raw Parquet, and code
commit so any future architecture proposal can be compared against this
reproducible local baseline.
