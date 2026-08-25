# Friday Demo Handoff

Use the implemented branch at:

```text
/home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
branch: codex/slices-1-2
verified implementation baseline: 95a78120a682b4cc1647b57304800e41db5a7f95
```

The private dataset token remains in `/home/dhelmy990/Code/babel/.env`. Source
that file without printing it. Do not paste the token into the dashboard,
notebook cells, logs, screenshots, or commits.

## What is ready

- The private Hugging Face Friday bundle is pinned at
  `e1acc648fcace8820dd5ee70bae9216ea4334555` and entered through
  `demo_crosswalk`.
- Dashboard seeding still uses the separate prepared June catalog and never
  falls back to live Wikipedia.
- The dashboard launches and gracefully stops the 50-creator experiment.
- PostgreSQL/pgvector is the default candidate store; Kafka is the asynchronous
  feedback path.
- Online updates remain hidden until a configured serving synchronization.
- Every completed run creates an immutable child; the original remains
  selectable and unchanged.
- The dashboard activity log now reads every persisted typed event.
- A real three-condition latency report is checked in at
  `docs/experiments/friday-demo-performance-report.md`.

The checked-in online model is deliberately labeled **Deterministic Friday demo
stand-in (replace after Colab)**. It proves the complete systems path, but it is
not the trained 2016 Qwen encoder. The Colab artifact and the online
`ModelManifestV1`/serving implementation are not yet directly interchangeable;
do not claim that the local demo is serving trained Qwen until that adapter and
real encoder path are implemented.

## Start the local demo

Open a shell and start the already-created local services:

```bash
docker start babel-postgres-1 babel-slices-kafka-1
docker ps --format '{{.Names}} {{.Status}}' | rg 'babel-(postgres|slices-kafka)-1'
```

Create one local worker credential for both terminals without displaying it:

```bash
umask 077
openssl rand -hex 32 > "/tmp/babel-online-worker-token-$UID"
```

In terminal 1, install/start the Python worker:

```bash
cd /home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
set -a
source /home/dhelmy990/Code/babel/.env
set +a
export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
export BABEL_KAFKA_BOOTSTRAP_SERVERS='127.0.0.1:29092'
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_REVISION='e1acc648fcace8820dd5ee70bae9216ea4334555'
export BABEL_ONLINE_MODEL_ARTIFACT="$PWD/fixtures/online/demo-model"
export BABEL_ONLINE_WORKER_TOKEN="$(<"/tmp/babel-online-worker-token-$UID")"
UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv sync --project online --extra kafka --extra parquet --extra pgvector
PYTHONPATH=online/src UV_PROJECT_ENVIRONMENT=/tmp/babel-online-demo-venv \
  uv run --project online --extra kafka --extra parquet --extra pgvector \
  babel-online serve
```

Wait until the worker reports Uvicorn on `127.0.0.1:8790`.

In terminal 2, start the C++ backend/dashboard and Electron with the same
credential and frozen dataset identities:

```bash
cd /home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
set -a
source /home/dhelmy990/Code/babel/.env
set +a
export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
export BABEL_HF_REVISION='e1acc648fcace8820dd5ee70bae9216ea4334555'
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_CONFIG='demo_crosswalk'
export BABEL_ONLINE_DATASET_REVISION='e1acc648fcace8820dd5ee70bae9216ea4334555'
export BABEL_ONLINE_WORKER_TOKEN="$(<"/tmp/babel-online-worker-token-$UID")"
just start
```

Open `http://127.0.0.1:8787/admin`. In **Recommendation experiment**:

1. Select the explicitly labeled demo stand-in original, or a prior immutable
   post-run child.
2. Keep `pgvector`, 50 creators, June → July, and run seed 0.
3. Press **Start experiment**.
4. Watch created Babels, candidate decisions, Kafka lag, trainer loss/steps,
   checkpointing, and serving synchronization in the activity log.
5. Optionally press **Graceful stop**; otherwise let the 100-event run finish.
6. Confirm the new post-run child appears without replacing its parent. Select
   that child on a later run to demonstrate continuation.

Expected full-run evidence: 50 creators, 100 Babels, 100 feedback events, zero
duplicate creator/source pairs, zero final Kafka lag, ten synchronization
events, a checkpoint, a 100-row feedback export, and a new immutable child.

## Colab pilot handoff

Use `training/notebooks/train_distillation_colab.ipynb` with
`prompts/colab-distillation-pilot-handoff.md`. The notebook checks out public
source commit `92f3ac697d78eb827d75b033df92dcbed887def7`; a fresh unauthenticated clone
and exact checkout were verified. The private 2016 pilot dataset is pinned at
`c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`.

The Colab job is independent of the local online demo. Completing it produces
the distillation artifact; integrating that artifact into the local online
encoder remains a distinct post-handoff task.

## Accepted performance result

Each condition used 500 measured synchronous POSTs and had zero errors or
timeouts:

| Condition | p50 | p95 | p99 | max | p95 ratio |
|---|---:|---:|---:|---:|---:|
| Serving only | 12.455 ms | 15.514 ms | 16.587 ms | 17.908 ms | 1.000x |
| Serving + training, no sync | 12.785 ms | 15.824 ms | 17.002 ms | 18.050 ms | 1.020x |
| Serving + training + sync | 13.075 ms | 18.397 ms | 22.970 ms | 43.493 ms | 1.186x |

Interpret the no-sync 2% difference as noise in a single sequential-client
trial. Synchronization produced the meaningful observed slowdown. The report
documents that the small pgvector query used `DISTINCT`/top-N rather than HNSW,
and isolated sync timing excludes PostgreSQL vector insertion/activation.

## Verified reference run

The final post-fix dashboard run was
`fd435049-1848-4824-b833-c72be72220e9`: 50 creators, 100 Babels/feedback,
97 online updates, ten syncs, 945 valid candidate rows, no duplicate/uncreated/
same-creator candidates, and Kafka lag 0. Its parent checksum remained
`2d6cae7…`; child `c7a5f914-050d-59de-8d3a-e444907a80c8` was registered with
checksum `4836ba9…`. Dashboard latency was p50 14.892 ms, p95 17.955 ms,
p99 22.170 ms, and max 42.690 ms.

## Stop

Use Ctrl-C in terminal 2, then terminal 1. PostgreSQL and Kafka may remain up
for another demo. The runtime outputs under `artifacts/` and `state/` are local,
untracked evidence; do not add them to Git.
