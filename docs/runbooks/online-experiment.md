# Friday Online Experiment Handoff

This is the dashboard-operated Slice 3 demo. The browser may start and
gracefully stop runs; it never receives the private Hugging Face token or hidden
simulator data. PostgreSQL/pgvector is the default durable vector store, Kafka
is the asynchronous feedback path, and every completed run registers a new
immutable child without modifying its selected parent.

The checked-in model under `fixtures/online/demo-model` is a
**deterministic Friday demo stand-in**, not the completed 2016 distilled Qwen
encoder. Its manifest and working-state bytes are checksum verified before it
can enter the model registry. The Colab distillation artifact does not yet use
the online runtime's `ModelManifestV1`/working-state format, and this Friday
runtime still uses the deterministic item-tower stand-in. A later integration
slice must adapt the artifact and wire the real encoder before anyone points
`BABEL_ONLINE_MODEL_ARTIFACT` at it or claims that the local demo serves Qwen.

## Prerequisites

Start the two local services and install the worker extras:

```bash
docker compose up -d postgres kafka
UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector
```

Use the same randomly generated worker token in both terminals. Keep `HF_TOKEN`
in the environment; do not paste it into the dashboard.

```bash
export BABEL_ONLINE_WORKER_TOKEN="$(openssl rand -hex 32)"
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_REVISION='e1acc648fcace8820dd5ee70bae9216ea4334555'
export BABEL_ONLINE_MODEL_ARTIFACT="$PWD/fixtures/online/demo-model"
export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
```

Run `just start` once so migrations are applied. In another terminal with the
same variables and `HF_TOKEN`, start the loopback worker:

```bash
PYTHONPATH=online UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv run --project online --extra kafka --extra parquet --extra pgvector \
  babel-online serve
```

The worker downloads/caches only the five prepared demo configurations and
their release manifest from the private repository at the exact revision. It
requires the experiment pin `demo_crosswalk`, validates all five physical
configurations, and never falls back to live Wikipedia or an arbitrary local
source tree.

## Run from the dashboard

Open `http://127.0.0.1:8787/admin`. In **Recommendation experiment**:

1. Select the original model or any compatible immutable post-run child.
2. Keep `pgvector` for the default 50-creator demo.
3. Select June → July and press **Start experiment**.
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

## Demo acceptance

The accepted run must show: `datasetConfig=demo_crosswalk`, exact dataset
revision, 50 creators, no duplicate `(creator, source article)`, only created
other-creator Babels as candidates, 100-dimensional pgvector rows, acknowledged
feedback, trainer progress, periodic serving sync, zero final Kafka lag, a
checkpoint/export, and a new child whose parent checksum remains unchanged.
