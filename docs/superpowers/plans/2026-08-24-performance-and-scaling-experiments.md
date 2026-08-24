# Performance and Scaling Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quantify recommendation latency, online-training interference, synchronization cost, adaptation, and resource limits across deterministic nested creator cohorts from 50 through 10,000 without changing the recommender or simulator semantics.

**Architecture:** A separate benchmark package drives the stable Slice 3 APIs in two modes: fixed observable replay isolates systems interference, while live nested-cohort runs measure learning and temporal adaptation. A sidecar resource sampler and existing per-request/online telemetry produce raw Parquet records; deterministic analysis creates paired reports, freezes the 50-creator quality target, and applies latency-aware trainer backpressure without giving the recommender hidden data.

**Tech Stack:** Python 3.10+, asyncio/httpx, PyArrow/Parquet, NumPy, SciPy, pandas, psutil, pynvml, Hugging Face Hub, PostgreSQL, Kafka, pytest, existing C++/Drogon dashboard and `babel_online` package.

## Global Constraints

- Requires Slice 3 acceptance, a known-good original/child model pair, a frozen request/feedback replay corpus, and stable online schemas.
- Do not change model architecture, hidden decision formulas, creator construction, labels, Qwen weights, or model lineage in this slice.
- The 2016 Colab job runs elsewhere and is excluded from local interference measurements.
- Conditions are: serving only; serving plus training with synchronization disabled; serving plus training and synchronization; then increasing request rates and creator cohorts.
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
 condition runner + backpressure                   nested scale runner
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
| Agent A | `benchmark/loadgen.py`, `conditions.py`, `backpressure.py` | Load timing and control loops never use wall-clock durations |
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
- Produces: `BenchmarkManifestV1`, `RequestMeasurementV1`, `ResourceSampleV1`, `AdaptationWindowV1`, `PerformanceSummaryV1`, and exact condition names.

- [ ] **Step 1: Write failing schema and paired-input tests**

```python
def test_every_system_condition_uses_the_same_replay_inputs(manifest):
    conditions = {c.name: c for c in manifest.conditions}
    assert {c.request_corpus_sha256 for c in conditions.values()} == {manifest.request_corpus_sha256}
    assert {c.feedback_range for c in conditions.values()} == {manifest.feedback_range}

def test_raw_measurement_preserves_stage_and_model_identity():
    row = RequestMeasurementV1.model_validate(fixture_request())
    assert row.clientTotalNs >= row.serverTotalNs
    assert row.servingModelId and row.servingModelVersion >= 0
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_contracts.py -v`

Expected: collection fails because benchmark contracts are absent.

- [ ] **Step 3: Define the exact conditions and comparison keys**

Use stable names:

```text
serving_only
serving_plus_training_no_sync
serving_plus_training_and_sync
live_scale
```

The manifest pins dataset/model SHAs, request corpus checksum, Kafka topic and
offset range, warmup/sample counts, concurrency/RPS schedule, sync interval,
trainer micro-batch bounds, resource interval, timeouts, safety limits, host
description, and code commit. Comparisons join by request ID and schedule slot,
not timestamp proximity alone.

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
network/serialization. Mark warmup rows explicitly; never mix them into reported
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
- Create: `benchmark/src/babel_benchmark/backpressure.py`
- Create: `benchmark/src/babel_benchmark/conditions.py`
- Create: `benchmark/tests/test_backpressure.py`
- Create: `benchmark/tests/test_conditions.py`
- Modify: `online/src/babel_online/training/consumer.py`
- Modify: `online/tests/training/test_consumer.py`

**Interfaces:**
- Consumes: Tasks 1–3 and Slice 3 trainer control socket/status.
- Produces: `LatencyBackpressureController`, `run_condition_matrix`, and paired raw condition directories.

- [ ] **Step 1: Write the backpressure state-machine tests**

```python
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

Run: `python3 -m pytest benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py -v`

Expected: FAIL because condition orchestration is missing.

- [ ] **Step 3: Establish the serving-only baseline first**

For each request schedule, warm the model/index, disable trainer and sync,
replay the fixed request corpus, and compute baseline p95. The latency threshold
for matching training conditions is `1.25 * baseline_p95`; record the numeric
value before enabling training.

- [ ] **Step 4: Run the two training interference conditions**

Reset to the same immutable starting artifact before each condition. Replay the
same Kafka feedback range at a controlled rate. In
`serving_plus_training_no_sync`, train/checkpoint but hold serving version fixed.
In `serving_plus_training_and_sync`, apply the configured sync cadence. Use the
same request arrival schedule and resource sampler in both.

- [ ] **Step 5: Apply hysteretic training backpressure**

Calculate rolling p95 over the latest max(200 requests, 60 seconds). If it
exceeds threshold for two consecutive windows, halve the micro-batch down to 2,
then add 25 ms inter-batch delay up to 500 ms. After five consecutive windows
below `0.90 * threshold`, remove delay in 25 ms steps, then double batch up to
its configured maximum. Persist every transition and its observed latency/lag.

- [ ] **Step 6: Test and commit**

Run: `python3 -m pytest benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py online/tests/training/test_consumer.py -v`

```bash
git add benchmark/src/babel_benchmark/backpressure.py benchmark/src/babel_benchmark/conditions.py benchmark/tests/test_backpressure.py benchmark/tests/test_conditions.py online/src/babel_online/training/consumer.py online/tests/training/test_consumer.py
git commit -m "feat: measure and bound training interference"
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
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest benchmark/tests/test_scale.py -v`

Expected: FAIL because scale orchestration is absent.

- [ ] **Step 3: Build explicit one-run-at-a-time scale plans**

Each scale gets a fresh run ID, the same selected starting model, dataset SHA,
simulator parameters, frozen quality target, per-creator event budget, and
request schedule normalized both by total RPS and RPS/creator. Record creator
manifest checksum and assert each smaller manifest is the exact prefix.

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
```

```javascript
test('dashboard defaults to 50 and never auto-advances', () => {
  const view = benchmarkForm(defaults());
  assert.equal(view.creatorCount, 50);
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
`p95(condition)/p95(serving_only)`. For scale runs report quality versus total
events, events/creator, wall/training time, throughput, and June/July target
attainment. Include sample counts and confidence intervals from deterministic
bootstrap resampling; never report a percentile without its denominator.

- [ ] **Step 4: Add explicit dashboard benchmark launch/status**

Allow selection of starting immutable model, one cohort or maximum ladder size,
request schedule, sync interval, and safety limits. Default creator count/max is
50 and auto-advance is false. Show the current condition, raw sample counts,
resource/safety state, backpressure, graceful-stop progress, and links to the
accepted report/artifact. Keep these controls separate from ordinary online-run
Start.

Persist `performance_experiments` and `performance_conditions` in migration
006 with immutable manifest/checksum, state, current condition/cohort, safety
receipt, artifact SHA, and timestamps. `PerformanceJobRunner` launches exactly
one `babel-benchmark run --benchmark-id <uuid>` process after the dashboard POST.
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

Expected: a tiny replay produces all three paired system conditions, a backpressure
transition, resource rows, a frozen target, 50⊂100 creator manifests, and a
summary whose IDs/checksums join exactly.

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

- [ ] **Step 4: Run the paired interference matrix**

For each approved RPS/concurrency point, run serving-only first, derive its
threshold, then run training/no-sync and training/sync from the same model with
identical inputs. Remotely publish and verify raw artifacts after every condition
so later failures do not lose completed measurements.

- [ ] **Step 5: Advance the scale ladder only by explicit approval**

Run 50, review safety/quality, then offer 100; repeat for 500, 1,000, 5,000,
and 10,000. A stopped ladder is valid partial evidence. Every completed scale
must have a graceful checkpoint/export and independent immutable child; no
larger scale continues from the smaller scale's learned model.

- [ ] **Step 6: Write the runbook and final comparative report**

Document hardware capture, dashboard controls, safe sequencing, calibration,
condition reset, interpreting latency/lag/backpressure, recovery, scale approval,
Hub paths, and how to regenerate every table from raw Parquet. Clearly distinguish
systems replay results from live adaptation results.

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/performance-and-scaling.md benchmark/tests/e2e/test_performance_fixture.py README.md documentation.md
git commit -m "docs: verify performance and scaling experiments"
```

## Slice Acceptance Gate

- [ ] Serving-only, training/no-sync, and training/sync use identical paired inputs.
- [ ] Every request has client total, server total, stage timings, model version, and outcome.
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
contention, Kafka lag, or synchronization, and whether quality reaches the
frozen target before changing infrastructure. Only evidence from the paired raw
records should justify a later GPU split, multiple consumers/partitions, remote
serving, a parameter server, or other Monolith-like scaling. Preserve the
accepted manifests, original/child artifacts, dataset/model SHAs, raw Parquet,
and code commit so any future architecture proposal can be compared against
this reproducible local baseline.
