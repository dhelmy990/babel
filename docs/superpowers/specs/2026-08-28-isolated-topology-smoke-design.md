# Isolated Topology Interview Smoke Design

## Objective

Produce interview-grade evidence for the three `same_host_isolated` load modes
that correspond to formal topology conditions 7, 8, and 9. The run is a fresh,
explicitly non-formal representative trial. It must not rewrite or resume the
failed formal trial `dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28`.

The prior evidence is retained under
`results/28-august-morning-run/formal-partial-failed/`. The isolated-smoke
evidence will be stored beside it under
`results/28-august-morning-run/isolated-smoke/`.

## Approach

Add one supported representative matrix selector, `isolated-smoke`, alongside
the existing `2x3` and `split-smoke` selectors. It creates exactly three fresh
conditions using `same_host_isolated`:

1. Serving only.
2. Training without activation.
3. Training with activation.

The representative conditions use internal indices 1–3, while the report maps
them to formal matrix positions 7–9. This preserves the repository's existing
three-condition representative contracts without mutating historical IDs.

## Reused Immutable Inputs

- Source trial: `dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28`.
- Existing imported 10,000-vector population and its exact manifest.
- Existing frozen workload and workload identity.
- Pinned Qwen model and dataset revisions.
- Existing GCP VM `babel-gpu-serving` in `asia-southeast1-b`.

The no-reencoding guard remains mandatory. The rerun clones the validated
pgvector population into fresh condition run IDs; it does not export, import,
or encode the 10,000 vectors again.

## Representative Load Contract

- Evidence scope: `representative_isolated_smoke`.
- Warmup: 5 seconds.
- Measurement: 25 seconds.
- Target: 5 RPS.
- Concurrent users: 50.
- Training micro-batch: 8.
- Synchronize every: 10 steps.
- Retrieval: pgvector.
- Safety threshold: 5,000 ms.

Compute placement remains unchanged:

- CUDA: Qwen encoding for recommendation requests and serving.
- CPU: online head, Kafka, PostgreSQL/pgvector, checkpoints, controller, and
  index maintenance.

## Code Boundaries

1. `performance_rerun.py` defines the isolated representative evidence scope
   and accepts it in immutable input validation.
2. `database.py` maps that scope to exactly one `same_host_isolated` topology
   trio.
3. `performance_worker.py` accepts `matrix=isolated-smoke` and selects the new
   scope.
4. `performance_export.py` recognizes an exact completed isolated trio as
   representative, never formal, evidence.
5. The backend rerun request accepts and forwards `isolated-smoke`.
6. The dashboard labels the resulting trial as a non-formal isolated smoke.

No formal-matrix resume, retry tolerance, Kafka redesign, or training change is
included.

## Failure Behavior

- Fresh UUIDs are required for the trial and all condition runs.
- Any request error fails that condition and the representative run closed.
- Completed and failed evidence remains immutable.
- There is no automatic fallback to re-encoding or another topology.
- A failed smoke produces a clearly labeled failed report and the VM is
  stopped.

## Test Strategy

Use red-green-refactor tests for:

- scope validation and exact immutable-input reuse;
- database creation of only the three isolated conditions;
- worker matrix-selector routing;
- backend request acceptance and forwarding;
- dashboard evidence-scope labeling;
- representative export acceptance for a completed isolated trio and
  rejection of topology/scope drift.

Run the focused Python, C++, and JavaScript suites, then the existing runtime,
topology, checkpoint, Kafka-drain, and publication regressions affected by the
change.

## Deployment and Acceptance

Deploy one reviewed SHA through the existing GitHub Actions workflow. Before
starting the smoke, verify CUDA, the pinned model, pgvector population count,
and `BABEL_ONLINE_ALLOW_POPULATION_BUILD=false`.

Acceptance requires:

- all three isolated conditions finish;
- conditions with training finish with zero Kafka lag;
- activation succeeds in the third condition;
- p50, p95, p99, maximum latency, RPS, errors, CPU/memory/GPU metrics, trainer
  throughput, Kafka lag, and activation timing are saved;
- source SHA, image digests, model revision, and population identity are saved;
- the report is labeled interview-grade and non-formal;
- the evidence is copied to `results/28-august-morning-run/isolated-smoke/`;
- the VM is stopped after success or a terminal failure.
