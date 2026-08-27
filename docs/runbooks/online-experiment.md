# Online Recommendation Experiment Handoff

This is the dashboard-operated recommendation architecture experiment. The browser may start and
gracefully stop runs; it never receives the private Hugging Face token or hidden
simulator data. PostgreSQL/pgvector is the default durable vector store, Kafka
is the asynchronous feedback path, and every completed run registers a new
immutable child without modifying its selected parent.

Formal runs use the immutable distilled Qwen artifact at
`dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e`
and artifact directory
`artifacts/3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8`.
The deterministic fixture remains smoke-only and must never be reported as a
Qwen performance result.

## Prerequisites

Start the two local services and install the worker extras:

```bash
docker compose up -d postgres kafka
UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
```

Use the same randomly generated worker token in both terminals. Keep `HF_TOKEN`
in the environment; do not paste it into the dashboard.

```bash
export BABEL_ONLINE_WORKER_TOKEN="$(openssl rand -hex 32)"
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_CONFIG='crosswalk_2026_06_07'
export BABEL_ONLINE_DATASET_REVISION='0d1ab2c7f0e2295682288fcf10077d2d776bf559'
export BABEL_ONLINE_MODEL_MODE='real_qwen'
export BABEL_ONLINE_QWEN_DEVICE='cpu'
export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
```

Run `just start` once so migrations are applied. In another terminal with the
same variables and `HF_TOKEN`, start the loopback worker:

```bash
PYTHONPATH=online UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv run --project online --extra kafka --extra parquet --extra pgvector \
  babel-online supervise
```

The worker downloads/caches only the five prepared scale configurations and
their release manifest from the private repository at the exact revision. It
requires the experiment pin `crosswalk_2026_06_07`, validates all five physical
configurations, and never falls back to live Wikipedia or an arbitrary local
source tree.

## Run from the dashboard

Open `http://127.0.0.1:8787/admin`. In **Recommendation experiment**:

1. Select the original model or any compatible immutable post-run child.
2. Keep `pgvector` for the default 50-creator run.
3. Select the pinned June/July environment and press **Start experiment**.
4. Watch recommendation decisions, Kafka lag, trainer step/rank loss, and
   serving synchronization in the activity panel.
5. Press **Graceful stop** if desired. The worker stops creating events, drains
   the captured Kafka range, checkpoints, exports feedback, synchronizes, and
   registers an immutable child.

Run output is under `artifacts/online/<run-id>` and restart state is under
`state/online/<run-id>`. These local runtime directories are intentionally not
source-controlled. A completed child remains selectable beside every ancestor;
there is no replace/promote operation.

The recommendation endpoint is `http://127.0.0.1:8791/api/v1/recommendations`.
Its response and dashboard log record monotonic end-to-end client latency plus
queue, encode, context, ANN, filtering, serialization-preparation, and server
timings. Use `docs/runbooks/friday-demo-performance.md` for the Slice 4 replay.

## Bounded 3×3 smoke before formal population

Run only the Task 12 tests first:

```bash
PYTHONPATH=benchmark/src:online/src online/.venv/bin/python -m pytest \
  benchmark/tests/test_matrix.py benchmark/tests/test_scale.py \
  benchmark/tests/test_faults.py benchmark/tests/test_backpressure.py -v
```

`tiny_smoke_plan()` fixes exactly nine condition slots, 20 requests per slot,
180 total, one suite timeout, the current fixture, and
`formal_performance_claim=false`. `run_lifecycle_tiny_smoke()` calls one suite
start, each condition callback, and one suite stop. It publishes the receipt
only after cleanup succeeds. Each condition runs in a parent-acknowledged,
worker-only Linux process group and receives a cancellation event; the callback
cannot start before that isolation handshake. The runner reserves a
cooperative-cancellation window, then terminates or kills the whole group and
reaps its worker before returning; neither the callback nor a subprocess it
launched remains active after `TimeoutError`. `SIGKILL` is sent to the verified
group after the TERM grace even when its worker leader already exited, covering
TERM-ignoring descendants. A successful condition requires
positive requests and edges, all required health observations, and an existing
nonempty raw evidence file. `DashboardPerformanceHttpClient` maps saved-trial
creation and graceful stop to the Task 10 loopback admin endpoints; its caller
supplies the admin nonce without persisting it.

This is currently a Python library entrypoint, not a live matrix CLI. The tests
use a bounded callback harness. Saved-trial creation alone does not start a
condition. Before calling the smoke live, provide a condition callback that
starts the Task 9 topology/load mode, sends the bounded requests, captures raw
evidence, and cleans up while honoring cancellation. Also verify that the V2
launch record persists the full selected topology/load configuration; that
dependency is not repaired by Task 12's receipt contracts.

Formal population is a separate manual gate. Freeze 5,000 June plus 5,000 July
Babels once, in stable Babel-ID order, and record the ordered-manifest and
vector-byte checksums. Also record checksums for the 50-creator round-robin
assignment manifest and cross-month per-creator used-source sets. Clone those
exact bytes into every condition. Freeze the request corpus, feedback,
creator-local schedule, event mix, probability-0.40 start draws, and independent
probability-0.40 continuation draws as six checksums. Do not regenerate walks
from condition-specific recommendations.

Every controlled receipt must use the cohort encoded in its condition ID and
must report exactly the frozen population's 10,000 created/indexed rows. The
formal threshold is owned by that frozen receipt and cannot be lowered per
validation call.

`FrozenPopulationReceipt` and `FrozenWorkloadReceipt` validate these artifacts;
they do not create them. The exporter/checksum/clone driver remains required
Task 12 work after operator approval. Task 13 publishes completed evidence; it
does not replace this driver.

## Backpressure and fault adapters

`BackpressureOrchestrator` applies each persisted controller state at a window
boundary. For an in-process trainer, wrap the actual `OnlineTrainer` in
`OnlineTrainerPacingAdapter`; it calls `process_available(max_records=...)` and
applies the configured inter-batch delay. A split trainer uses the same adapter
inside its consumer loop. The state file is the service-neutral handoff: do not
change offsets, event order, or action labels while changing pace.

The fault runner maps to Task 9 as follows:

| Fault | Runnable seam |
|---|---|
| Trainer kill | authenticated `POST /v1/topology/trainer/stop` through `HttpTask9TopologyControl` |
| Trainer restart | supplied service callback starts `babel-online-trainer --run-id <run> --activation-enabled true` with the same pins/state root |
| Kafka pause/resume | `CallbackKafkaControl` wraps the consumer/admin client's pause and resume actions |
| Invalid model state | supplied injector writes one invalid activation request and returns whether serving rejected it |
| Serving restart | separate stop and start callbacks around an availability/version probe |

Use independently managed role commands for a live restart campaign so the
callbacks own and can reap the restarted PIDs. The probe must read serving
health, Kafka lag, duplicate/loss counters, and trainer/serving versions. A
serving restart is stop → probe outage → start → probe recovery. Invalid state
passes only when rejection is explicit and the last valid serving version
remains available. Trainer restart, Kafka resume, and serving start execute even
when the during-fault probe raises.

Advance cohorts manually: 50, then 100, then 500. At 50 run all nine
conditions. At higher cohorts run the monolith and one selected split topology
across the three load modes. Never auto-advance. The exact stop rules and the
pending/formal evidence ledger are in
`docs/experiments/scaled-performance-report.md`.

## Acceptance

The formal run must show: `datasetConfig=crosswalk_2026_06_07`, exact dataset
and Qwen revisions, 10,000 distinct created and indexed Babels, no duplicate
`(creator, source article)`, only created other-creator Babels as candidates,
100-dimensional pgvector rows, acknowledged feedback, trainer progress,
periodic serving sync, zero final Kafka lag, a checkpoint/export, and a new
child whose parent checksum remains unchanged. Fixture smoke receipts explicitly
do not satisfy this gate.
