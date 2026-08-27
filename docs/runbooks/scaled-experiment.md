# Scaled Recommendation Architecture Experiment

This runbook operates the dashboard-first engineering experiment. It compares
recommendation serving while online training runs in the same process, in a
separate same-host service, and in a resource-isolated same-host service. A
server here means an independently running service; separate physical machines
are not required.

The formal path uses real Wikipedia-derived June/July inputs, the real distilled
Qwen adapter and 100-dimensional projection, pgvector, synchronous
recommendation POSTs, Kafka feedback, asynchronous training, and immutable
model activation. Fixture or smoke results can prove wiring but cannot support
the formal performance claims.

> **Active-run warning:** trial
> `ce8e54ff-e317-4a89-b7db-90327e02dc43` is already building its population.
> Do not run the preparation/launch commands against it, regenerate its worker
> token, or restart its backend/worker. Use the launch section only for a fresh
> process or later trial.

## Frozen inputs

| Input | Immutable identity |
|---|---|
| Current 50-creator trial | `ce8e54ff-e317-4a89-b7db-90327e02dc43` |
| Starting model ID | `2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67` |
| Trained adapter/projection | `dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e` |
| Artifact directory | `artifacts/3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8` |
| Base model/tokenizer | `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| June/July environment | `dhelmy990/babel-wikipedia-experiment@0d1ab2c7f0e2295682288fcf10077d2d776bf559` |
| Dataset configuration | `crosswalk_2026_06_07` |

The monthly release supplies 5,000 real observable articles per month: 4,000
identities shared across June and July and 1,000 month-specific identities per
month. Hidden pagelinks and Clickstream remain simulator-only. The formal
population has 10,000 synthetic-created Babels; only created Babels enter the
candidate universe and pgvector.

Keep bulk data, Hugging Face snapshots, Qwen files, and run state outside Git.
The paths below reuse the existing caches:

```bash
export BABEL_PERFORMANCE_STATE_ROOT='/home/dhelmy990/Data/babel-data/state/performance'
export BABEL_ONLINE_HF_CACHE='/home/dhelmy990/Data/babel-data/hf-cache/monthly-2026'
export BABEL_ONLINE_MODEL_ARTIFACT_CACHE='/home/dhelmy990/.cache/huggingface/hub'
export BABEL_ONLINE_QWEN_CACHE='/home/dhelmy990/.cache/huggingface/hub'
```

`HF_TOKEN` remains backend/worker-side. Never print it, put it in a command
argument, send it to the browser, or commit it.

## Prepare and launch

From the integration worktree:

```bash
cd /home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
docker compose up -d postgres kafka
uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
uv pip install --python online/.venv/bin/python -e benchmark

set -a
source /home/dhelmy990/Code/babel/.env
set +a

export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
export BABEL_KAFKA_BOOTSTRAP_SERVERS='127.0.0.1:29092'
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_CONFIG='crosswalk_2026_06_07'
export BABEL_ONLINE_DATASET_REVISION='0d1ab2c7f0e2295682288fcf10077d2d776bf559'
export BABEL_ONLINE_MODEL_MODE='real_qwen'
export BABEL_ONLINE_QWEN_DEVICE='cpu'
export BABEL_RUNTIME_TOPOLOGY='same_host_split'
export BABEL_PERFORMANCE_STATE_ROOT='/home/dhelmy990/Data/babel-data/state/performance'
export BABEL_ONLINE_HF_CACHE='/home/dhelmy990/Data/babel-data/hf-cache/monthly-2026'
export BABEL_ONLINE_MODEL_ARTIFACT_CACHE='/home/dhelmy990/.cache/huggingface/hub'
export BABEL_ONLINE_QWEN_CACHE='/home/dhelmy990/.cache/huggingface/hub'
export BABEL_PERFORMANCE_WORKER_TOKEN="$(openssl rand -hex 32)"
export PATH="$PWD/online/.venv/bin:$PATH"
```

Use CPU for the comparable same-host experiment unless CUDA is deliberately the
resource under test. If using CUDA, verify the runtime first and record the
device in the result. The backend and performance worker must inherit the same
64-lowercase-hex `BABEL_PERFORMANCE_WORKER_TOKEN`; do not independently
regenerate it per terminal.

Start the real population/matrix worker in terminal 1:

```bash
babel-online performance-worker
```

Start the backend/dashboard with the same environment in terminal 2:

```bash
just start
```

The worker is loopback-only on `127.0.0.1:8792`. The dashboard at
`http://127.0.0.1:8787/admin` is the normal launch, approval, stop, and review
surface. `babel-online supervise` belongs to the older online vertical-slice
runner and is not the formal scale-matrix command.

If a dashboard mutation times out but the worker is demonstrably healthy,
retry that one idempotent action without creating another saved trial:

```bash
babel-online performance-command \
  --experiment-id '<saved-trial-uuid>' \
  --action approve-next-scale
```

The accepted actions are `start`, `approve-next-scale`, and `graceful-stop`.
This retry helper is not a formal-matrix resume facility.

## Population gate

The default saved configuration is:

- 50 creators;
- 5,000 June plus 5,000 July source identities;
- 10,000 created and indexed Babels;
- 50 concurrent users;
- `pgvector` retrieval;
- independent recommendation-start and continuation probabilities of 0.40;
- maximum traversal depth 2 and maximum 10 requests per traversal;
- creation/recommendation interleaving enabled;
- training micro-batch 8 and synchronization every 10 steps;
- 30-second warmup, 120-second measured duration, and target 5 RPS.

The sliders are convenience controls; the adjacent numeric fields allow
server-validated custom values. Save the trial before starting it so every
control and pin becomes part of its immutable launch identity.

Population is separate from measurement. Wait for `population_ready`, then
verify all of the following before approving:

- exactly 10,000 distinct created Babel rows and exactly 10,000 pgvector rows;
- every vector is finite and exactly 100-dimensional;
- the model repository/revision/artifact and dataset repository/revision match
  the frozen identities above;
- the population, assignment, used-source, ordered vector, and vector snapshot
  checksums are present and consistent;
- no duplicate `(creator, source article)` population rows;
- zero unresolved population batches or failures.

The threshold never auto-advances. Review the receipt in the dashboard and
press **Approve formal measurements** exactly once. The worker then captures
one frozen reference workload and reuses it for all nine conditions.

## Formal 3x3 matrix

Conditions execute sequentially; requests inside each condition are concurrent.
The exact order is:

| Index | Topology | Training | Activation |
|---:|---|:---:|:---:|
| 1 | `same_process` | no | no |
| 2 | `same_process` | yes | no |
| 3 | `same_process` | yes | yes |
| 4 | `same_host_split` | no | no |
| 5 | `same_host_split` | yes | no |
| 6 | `same_host_split` | yes | yes |
| 7 | `same_host_isolated` | no | no |
| 8 | `same_host_isolated` | yes | no |
| 9 | `same_host_isolated` | yes | yes |

Condition 6 is the default published child: it represents the requested split
service architecture with training and immutable activation. Conditions 3 or 9
may be selected explicitly for a different comparison, but serving-only and
no-activation conditions cannot supply a post-run child.

For each topology the dashboard displays all three calculations:

```text
Itraining = p95(training, no activation) / p95(serving only)
Ifull = p95(training + activation) / p95(serving only)
IActivationIncrement = p95(training + activation) / p95(training, no activation)
```

Also inspect p50/p95/p99/max, request rate, error/timeout rate, per-stage server
timings, process placement, CPU/memory use, trainer throughput, Kafka lag,
model-version staleness, and activation-window spikes. A progress-panel failure
must not alter the worker; the persisted trial remains authoritative.

The original model is never replaced. Each completed compatible child remains
separately selectable. A later run can select the original to reset the lineage
or select condition 6's child to continue adaptation.

## Optional retrieval-only pgvector/hnswlib comparison

Do not run this comparison before at least one formal serving-only pgvector
condition has completed successfully. It is deliberately gated by that
condition's `live-evidence.json`; a smoke receipt, training condition, failed
request, or another vector snapshot is rejected before the command opens a
database connection.

Install the optional local index dependency into the existing environment:

```bash
uv pip install --python online/.venv/bin/python -e 'benchmark[hnswlib]'
```

Then select one completed serving-only condition and run:

```bash
babel-friday-benchmark retrieval-compare \
  --population "$PERF_ROOT/$TRIAL_ID/population" \
  --formal-pgvector-evidence '<SERVING_ONLY_CONDITION>/live-evidence.json' \
  --dsn "$BABEL_DATABASE_URL" \
  --query-count 100 \
  --warmup-passes 1 \
  --measurement-passes 3 \
  --output "$RUN_ROOT/retrieval-comparison.json"
```

The command selects a deterministic ordered set of query vectors from the
checksum-verified `vectors.f32le` population. Both backends consume those same
ordered IDs, exact float32 bytes, snapshot checksum, creator exclusions, and
queries. Exact cosine search audits Recall@10 and Recall@50. Warmups and index
preparation are excluded from p50/p95/p99 and throughput; their pass count,
request count, and duration remain separately reported. The PostgreSQL
preparation evidence retains the full `EXPLAIN (ANALYZE, BUFFERS)` plan for the
first measured limit-50 query, including its real creator exclusion, and the
command refuses the comparison unless that plan names the configured HNSW
index.

The JSON result is explicitly `retrieval_only` with
`topologyConclusionEligible=false`. PostgreSQL memory is labelled as total
shared relation storage (table/index), while hnswlib reports its serialized
index footprint and observed signed net local RSS delta. The JSON carries explicit
`shared_database_relation_all_runs` and
`current_process_net_rss_and_serialized_index` scopes; those differently scoped values
must not be presented as an apples-to-apples RAM comparison. This microbenchmark
does not change pgvector as the formal/default backend and cannot support a
serving/training topology conclusion.

## Export feedback and build the accepted bundle

Do not begin this phase until the trial's complete formal matrix is durably
`completed` (nine conditions at cohort 50; six at cohorts 100/500), training
conditions report zero final Kafka lag, and the selected child is present in
the model registry.

```bash
TRIAL_ID='ce8e54ff-e317-4a89-b7db-90327e02dc43'
PERF_ROOT="$BABEL_PERFORMANCE_STATE_ROOT"
RUN_ROOT="/home/dhelmy990/Data/babel-data/runs/$TRIAL_ID"
EXPORT_ROOT="$RUN_ROOT/export"
HANDOFF_ROOT="$RUN_ROOT/handoff"
ACCEPTED_ROOT="$RUN_ROOT/accepted"

babel-online performance-export \
  --experiment-id "$TRIAL_ID" \
  --evidence-root "$PERF_ROOT/$TRIAL_ID/conditions" \
  --output-root "$EXPORT_ROOT" \
  --selected-condition-index 6 \
  --bundle-inputs "$HANDOFF_ROOT/trial-bundle-inputs.json"
```

The export replays only the exact acknowledged Kafka ranges for the trial's
bound condition/run IDs. It validates the cohort-specific order (the full 3×3
matrix at 50; `same_process` plus `same_host_split` at 100/500), Kafka
high-watermarks, checkpoint coverage and
zero final lag for training conditions, reconstructs accepted directed edges,
compares those edges with PostgreSQL, and writes `feedback.parquet`,
`edges.parquet`, and a checksum/count manifest. It then resolves condition 6's
registered immutable artifact and writes canonical `selected-child.json`,
`model-manifest.json`, and `trial-bundle-inputs.json`. Its JSON receipt contains
paths and counts, not database or Kafka credentials.

Keep the export, handoff, and accepted roots distinct. Each retry should use new
empty roots so partial output cannot be mistaken for accepted evidence.

Build the accepted bundle solely from the generated inputs:

```bash
babel-friday-benchmark trial-bundle-build \
  --output-root "$ACCEPTED_ROOT" \
  --inputs "$HANDOFF_ROOT/trial-bundle-inputs.json"
```

The builder validates the exact trial ID, cohort, ordered condition/run
bindings, formal pins, selected child lineage, population and feedback manifests, required
Parquet rows, and referenced model state. The resulting bundle is
`$ACCEPTED_ROOT/runs/$TRIAL_ID` with `manifest.json`, `checksums.json`, summary,
report, requests/resources/feedback/edges Parquet, population evidence, and the
complete reusable model artifact.

## Publish and attach

Publish the built bundle with the already loaded private token:

```bash
babel-friday-benchmark trial-bundle-publish \
  --bundle-root "$ACCEPTED_ROOT/runs/$TRIAL_ID" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  > "$HANDOFF_ROOT/publication-receipt.json"
```

Publication is a single-operator commit beneath `runs/<TRIAL_ID>/`. It rejects
an existing remote run path and scans candidate files for credential markers.
At the returned immutable commit it reloads JSON evidence, checksums, model
state, and at least one row from every required Parquet file. Do not treat an
upload as accepted until that remote verification returns successfully.

The attachment endpoint uses the per-process nonce embedded in the currently
running local `/admin` page. Capture it into `BABEL_ADMIN_NONCE` without
printing it, then run:

```bash
babel-friday-benchmark trial-bundle-attach \
  --receipt "$HANDOFF_ROOT/publication-receipt.json" \
  --trial-id "$TRIAL_ID" \
  --base-url 'http://127.0.0.1:8787'
```

Attachment succeeds only for the completed matching trial and exact remote path
`runs/<TRIAL_ID>`. It is idempotent for the same verified receipt. Reload the
dashboard and confirm the artifact commit/path, condition-6 child, original
parent, and the available topology ratio sets (three at cohort 50; two at
cohorts 100/500).

The backend serving the already-running trial was started before the final
artifact-attachment route landed. Wait for the trial to become durably
`completed`, then stop it, rebuild/restart the backend from this branch, and
capture the restarted process's new admin nonce before attachment. Never do
that rebuild/restart while population or matrix work is active.

## Smoke labels and scaling

A tiny 3x3 run is only a wiring smoke. It must be marked nonformal and cannot be
used for latency, topology, HNSW, or scaling conclusions.

The first formal result is the accepted 50-creator, 10,000-vector matrix. Move
to 100 and then 500 creators only after the first result is complete and healthy
and the operator explicitly approves another saved trial. Keep an optional
pgvector/HNSW comparison separate from topology conclusions. `cross_host` is a
future evidence expansion, not a prerequisite for the same-host split result.

For each higher cohort, choose **100 creators** or **500 creators** in the
dashboard and click **Save and launch cohort population**. Each click creates a
new durable trial; it does not mutate or advance the 50-creator trial. Wait for
the exact 10,000 created/indexed vectors and pinned provenance gate, then click
**Approve this cohort's measurements**. The worker freezes one workload and
replays it across exactly six conditions:

- `same_process`: serving only, training without activation, training with activation;
- `same_host_split`: serving only, training without activation, training with activation.

Higher cohorts set concurrent users equal to the creator cohort and remain
manual: completion never starts the next cohort. Use the immutable original
Qwen model for 100/500. Existing post-run children are bound to their producing
50-creator population and are intentionally rejected until cross-cohort child
population remapping exists. After either higher trial completes, run the same
export/build/publish/attach commands above: the generated handoff records the
100/500 cohort, exact six-condition order, and condition 6
(`same_host_split` with training and activation) as the default immutable child.
It publishes beneath that higher trial's own `runs/<TRIAL_ID>` path and attaches
only to that exact dashboard trial. The selected trial view retains every persisted
condition row, its condition p95, and all three interference ratios; reopening a
saved trial reloads those database-backed results.

## Shutdown and recovery boundary

Use **Graceful stop** to stop new work and retain evidence and the last valid
serving model. Do not kill the trainer as an ordinary stop mechanism. After a
trial reaches a terminal state, stop the backend and performance worker with
Ctrl-C; PostgreSQL and Kafka may remain running for the next trial.

The formal matrix is intentionally a first-run sequential workflow today. It
does **not** support interruption/resume in place. Once any matrix condition has
started:

- do not restart the performance worker or backend;
- do not stop PostgreSQL or Kafka;
- do not rebind or reuse the trial's condition IDs;
- do not publish a partial matrix;
- if interrupted or failed, preserve its evidence and create a new trial.

This limitation is distinct from an individual trainer retaining committed
Kafka offsets. Trainer checkpointing does not make the matrix orchestrator
restartable. A true condition-level resume/re-clone workflow is post-interview
work.

Only introduce a real parameter server if immutable checkpoint distribution and
activation become the measured bottleneck. The current split correctly uses a
small model-state distributor while keeping serving and training independent.
