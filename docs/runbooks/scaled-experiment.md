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

For a post-remediation formal run, online adaptation uses the small PyTorch
creator-context head, not `NumpyWorkingModel`. The frozen 100-dimensional Qwen
vectors remain unchanged; scaled feedback trains attention/fusion tensors and
residuals only for touched candidate Babels. `trainingMicroBatchSize` controls
the number of feedback events per optimizer step (default 8). Checkpoints bind
the complete context, residual, Adam, scheduler, offset, step, and version
state. Activation swaps both materialized pgvector rows and the matching
context tensors, while the original Qwen model remains immutable/selectable.

> **Completed-run boundary:** preserve optimized representative trial
> `72e35d2e-f04e-405d-af9a-25f873e44d5b`, its source trial, and its failed
> predecessor unchanged. Use the launch section only for a fresh trial; the
> formal matrix cannot resume in place.

## Frozen inputs

| Input | Immutable identity |
|---|---|
| Frozen source trial | `ce8e54ff-e317-4a89-b7db-90327e02dc43` |
| Latest completed representative trial | `72e35d2e-f04e-405d-af9a-25f873e44d5b` |
| First remotely verified representative | `dhelmy990/babel-wikipedia-experiment@dc0d158ff75851a5f944aa674f9fb88221440ede` |
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

Keep the `PATH` export when restarting either process and verify
`command -v babel-online` resolves to `online/.venv/bin/babel-online` before
launch. Attempt `7d0dbbf8-18e6-4a9b-afa1-0441ee4a300b` omitted that path on a
worker restart, failed with `babel-online` not found before condition 1, and is
startup diagnostic evidence only.

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

### Deadline corrective rerun from the frozen 10k trial

The dashboard can reuse the completed, checksum-verified population and captured
request corpus from trial `ce8e54ff-e317-4a89-b7db-90327e02dc43`. Select that
saved trial, leave **2×3 · monolith vs split** selected, set the bounded warmup,
duration, and RPS (the defaults are 5 seconds, 25 seconds, and 5 RPS), and click
**Prepare corrective rerun**. This creates a fresh saved trial in
`population_ready`; it does not encode vectors or mutate the source. Inspect the
new trial and then click **Approve this cohort's measurements** to start its six
conditions. The optional split-only selection is a three-condition smoke test.

The dashboard and database retain progress and condition results. Raw condition
evidence is stored at:

```text
$BABEL_PERFORMANCE_STATE_ROOT/<new-trial-id>/conditions/<condition-index>/live-evidence.json
```

After all six representative conditions complete, export their exact feedback
and accepted-edge evidence with an unmistakably non-formal manifest:

```bash
RERUN_ID='<new-trial-id>'
RUN_ROOT="/home/dhelmy990/Data/babel-data/runs/$RERUN_ID"

babel-online performance-export \
  --representative \
  --experiment-id "$RERUN_ID" \
  --evidence-root "$BABEL_PERFORMANCE_STATE_ROOT/$RERUN_ID/conditions" \
  --output-root "$RUN_ROOT/representative-export"
```

The resulting manifest records `formalPerformanceClaim=false` and the exact
representative evidence scope. It cannot enter the formal accepted-model bundle
or be attached through the formal publisher. Build the closed representative
bundle from the completed export, all six condition evidence files, and the
reviewed report:

```bash
TRIAL_ID='0367346d-98f9-4419-b2db-9194c4c868f7'
RUN_ROOT="/home/dhelmy990/Data/babel-data/runs/$TRIAL_ID"
PERF_ROOT='/home/dhelmy990/Data/babel-data/state/performance'
REPRESENTATIVE_ROOT="$RUN_ROOT/representative-accepted"
BUILD_RECEIPT="$RUN_ROOT/representative-build-receipt.json"

babel-friday-benchmark representative-run-build \
  --trial-id "$TRIAL_ID" \
  --export-root "$RUN_ROOT/representative-export/feedback-export" \
  --evidence-root "$PERF_ROOT/$TRIAL_ID/conditions" \
  --report 'docs/experiments/scaled-performance-report.md' \
  --output-root "$REPRESENTATIVE_ROOT" \
  > "$BUILD_RECEIPT"

BUNDLE_ROOT="$(jq -r '.bundleRoot' "$BUILD_RECEIPT")"
```

The builder rechecks the trial ID, exact `formalPerformanceClaim=false` value,
the `representative_` evidence scope, both declared Parquet checksums and row
counts, the exact ordered 2×3 condition identities, successful measurements,
complete training-offset coverage, and zero final Kafka lag. It stages the raw
export and condition evidence with generated trial summary/results and
model-lineage documents,
report markdown, and a complete checksum inventory. Its content-addressed local
path is always
`representative-runs/<TRIAL_ID>/<checksum-inventory-sha256>/`; it never writes
under `runs/<TRIAL_ID>/`.

Publish that exact closed bundle to the private dataset repository with the
already loaded token:

```bash
babel-friday-benchmark representative-run-publish \
  --trial-id "$TRIAL_ID" \
  --bundle-root "$BUNDLE_ROOT" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  --revision main \
  > "$RUN_ROOT/representative-publication-receipt.json"
```

Publication proves the target dataset repository is private, rejects an
existing immutable path, uploads only beneath
`representative-runs/<TRIAL_ID>/<checksum-inventory-sha256>/`, and reloads the
complete inventory at the returned commit to verify every checksum. The printed
receipt contains no token. This representative receipt must not be passed to
`trial-bundle-attach` and cannot claim formal performance evidence.

The first successful representative bundle above contains 17 files and was
remotely verified at dataset commit
`dc0d158ff75851a5f944aa674f9fb88221440ede`, path
`representative-runs/0367346d-98f9-4419-b2db-9194c4c868f7/08fcd65c2e723760e95e93dea0c48fb827de3b0702a5befece7ae9b0dc1786b1/`.
It remains `formalPerformanceClaim=false`.

### Optimized representative publication — complete

Optimized trial `72e35d2e-f04e-405d-af9a-25f873e44d5b` completed the same
frozen 2×3 workload with 450/450 successful requests, complete training-offset
coverage, and zero final lag. Its export contains 450 feedback rows and 2,682
accepted edges with these verified SHA-256 values:

```text
feedback: adfea5b3b939aabe4a6478fc9c560ec71e6081b8eff5ef468ea59c97a31400fc
edges:    3004a3d1ab53a80be0e349d3d38d1ac5b08f883fee44750212e3ec6d8b13d069
```

The optimized publication completed with 17 remotely verified files and
artifact SHA-256
`53c3835487a07a1241d4b12664c05477decc4293e1f3bd496d30a74acb44585c`.
The immutable private dataset pin is
`dhelmy990/babel-wikipedia-experiment@0076949251709c6eec71f231dc096eb0589f2f6b`,
path
`representative-runs/72e35d2e-f04e-405d-af9a-25f873e44d5b/53c3835487a07a1241d4b12664c05477decc4293e1f3bd496d30a74acb44585c/`.
The verified manifest remains `formalPerformanceClaim=false`. Do not attach
this receipt to the formal saved-trial route. This immutable path is complete:
do not rerun the representative builder or publisher for this trial or path.

### Representative publication template for a future new trial

Use this workflow only for a newly completed representative trial with a fresh
trial ID and a not-yet-published content-addressed path. Never substitute
`72e35d2e-f04e-405d-af9a-25f873e44d5b` or its existing immutable path.

```bash
TRIAL_ID='<new-representative-trial-id>'
RUN_ROOT="/home/dhelmy990/Data/babel-data/runs/$TRIAL_ID"
PERF_ROOT='/home/dhelmy990/Data/babel-data/state/performance'
REPRESENTATIVE_ROOT="$RUN_ROOT/representative-accepted"
BUILD_RECEIPT="$RUN_ROOT/representative-build-receipt.json"

babel-friday-benchmark representative-run-build \
  --trial-id "$TRIAL_ID" \
  --export-root "$RUN_ROOT/representative-export/feedback-export" \
  --evidence-root "$PERF_ROOT/$TRIAL_ID/conditions" \
  --report 'docs/experiments/scaled-performance-report.md' \
  --output-root "$REPRESENTATIVE_ROOT" \
  > "$BUILD_RECEIPT"

BUNDLE_ROOT="$(jq -r '.bundleRoot' "$BUILD_RECEIPT")"

babel-friday-benchmark representative-run-publish \
  --trial-id "$TRIAL_ID" \
  --bundle-root "$BUNDLE_ROOT" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  --revision main \
  > "$RUN_ROOT/representative-publication-receipt.json"
```

### Build the formal accepted bundle

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

For a future formal trial, wait until it is durably `completed`, then stop it,
rebuild/restart the backend from this branch if required, and capture the
restarted process's new admin nonce before attachment. Never rebuild or restart
while population or matrix work is active.

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

## Bounded same-host fault campaign

Run faults only after the selected formal population has completed and the
operator has accepted its measurements. This is a separate, explicitly invoked
campaign: it does not run during population or the topology matrix and it does
not support topology-performance conclusions.

The command owns fresh serving and trainer processes for condition 6 (the real
same-host split, training-plus-activation condition), probes the already-running
backend, and pauses the explicitly named local Kafka container. It starts a
bounded background stream of real recommendation POSTs and Kafka feedback over
that condition's rebound frozen workload. Availability therefore reflects real
requests rather than process liveness. Credentials remain inherited environment
variables and are never copied into the receipt. Cleanup resumes Kafka, verifies
the roles recovered, stops probe traffic, terminates every process it started,
and removes the rejected activation request.

```bash
babel-friday-benchmark fault-campaign \
  --trial "$SAVED_TRIAL_JSON" \
  --population-manifest "$POPULATION_MANIFEST" \
  --probe-workload "$CONDITION_06_EVIDENCE_ROOT/workload" \
  --kafka-container babel-slices-kafka-1 \
  --receipt "$RUN_ROOT/faults/fault-campaign.json"
```

Run from the same Python environment that installs both `babel-online` and
`babel-friday-benchmark`, with `BABEL_DATABASE_URL` and `HF_TOKEN` set. The
receipt and its CLI-printed SHA are a separate artifact at
`$RUN_ROOT/faults/fault-campaign.json`; they are deliberately not inputs to the
six- or nine-condition topology bundle.

The command first verifies the exact completed, operator-approved 50-, 100-, or
500-creator trial and its frozen 10,000-vector population. It then runs four
bounded windows: trainer kill/restart while serving remains available; Kafka
pause/resume with lag recovery; invalid child/checkpoint rejection while the
last valid serving version remains active; and serving restart detection and
recovery. Recovery is attempted after each window and `cleanup` always runs in
an outer `finally` block.

The atomic JSON receipt records the accepted trial and population SHAs, fault and campaign
windows, sampled availability, Kafka lag, detection/recovery durations,
duplicate/lost counts, model versions, invalid-state retention, cleanup status,
and any failed fault. The CLI prints the receipt SHA-256. Treat the artifact as
`deploymentScope=same_host` and
`evidenceUse=fault_only_not_topology_performance`; it must not be mixed into the
six- or nine-condition latency ratios.

### Publish the completed fault evidence separately

Only publish a campaign whose CLI returned `status=completed`. Copy the printed
receipt SHA-256 exactly and use condition 6's immutable child model manifest:

```bash
FAULT_RECEIPT_SHA256='<sha256 printed by fault-campaign>'
FAULT_MODEL_MANIFEST="$RUN_ROOT/handoff/model-manifest.json"
FAULT_ACCEPTED_ROOT="$RUN_ROOT/fault-accepted"

babel-friday-benchmark fault-evidence-publish \
  --receipt "$RUN_ROOT/faults/fault-campaign.json" \
  --receipt-sha256 "$FAULT_RECEIPT_SHA256" \
  --trial-id "$TRIAL_ID" \
  --model-manifest "$FAULT_MODEL_MANIFEST" \
  --output-root "$FAULT_ACCEPTED_ROOT" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  > "$RUN_ROOT/faults/publication-receipt.json"
```

The command validates the exact receipt SHA, schema and fault-only labels,
formal cohort, condition 6 run, completed four-fault order, cleanup, and the
immutable model manifest whose `producingRunId` matches the fault run. It builds
four small files: `fault-receipt.json`, `manifest.json`, `report.md`, and
`checksums.json`.

The campaign ID is the receipt SHA-256. Local and remote artifacts therefore
live at `fault-runs/<TRIAL_ID>/<CAMPAIGN_ID>/`, never beneath the accepted formal
`runs/<TRIAL_ID>/` directory. Publication refuses an existing campaign path,
scans every candidate file for credential markers, uploads in one operator
commit, then reloads and checksum-verifies every file at the returned immutable
commit. Its printed publication receipt contains paths, IDs, checksums, and the
remote commit only; it never contains `HF_TOKEN`. Do not send this receipt to
the formal dashboard artifact-attachment endpoint—the topology bundle and its
dashboard attachment remain unchanged.

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
