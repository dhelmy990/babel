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

### 28 August isolated interview smoke on the existing GCP VM

This is a non-formal representative run only. Its three local conditions
`01`/`02`/`03` map to formal matrix positions `7`/`8`/`9`, respectively, but
`formalPerformanceClaim` remains `false`. The failed/formal source trial
`dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28` is immutable. Generate a fresh
`ISOLATED_TRIAL_ID`; never resume, restart, or mutate the source trial.

Reuse the existing imported 10,000-vector population unchanged. Do not
export, import, audit, or re-encode it. The hard
`BABEL_ONLINE_ALLOW_POPULATION_BUILD=false` guard must pass before launch.
Only Qwen encoding and new-request serving use CUDA in this smoke. The CPU
trainer, Kafka, PostgreSQL, and index work remain unchanged.

Use only the existing `babel-gpu-serving` VM in project
`chloe-tutoring-bot`, zone `asia-southeast1-b`, and connect through IAP:

```bash
gcloud compute ssh babel-gpu-serving \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  --tunnel-through-iap
```

At the VM prompt, enter an explicit root shell before accessing the protected
current release, runtime environment, or Docker daemon:

```bash
sudo -s
```

Verify the privilege boundary and capture the original IAP SSH operator's UID
and GID. Both must be positive integers so the retrieved result remains
readable by that operator rather than becoming root-owned:

```bash
set -Eeuo pipefail
test "$(id -u)" -eq 0
OPERATOR_UID="${SUDO_UID:?sudo -s must preserve the original operator UID}"
OPERATOR_GID="${SUDO_GID:?sudo -s must preserve the original operator GID}"
[[ "$OPERATOR_UID" =~ ^[1-9][0-9]*$ ]]
[[ "$OPERATOR_GID" =~ ^[1-9][0-9]*$ ]]
```

Run all remaining VM command blocks inside this same root shell. Record the
printed fresh ID for the later local copy command; do not reuse it for another
attempt.

```bash
SOURCE_TRIAL_ID='dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28'
ISOLATED_TRIAL_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
test "$ISOLATED_TRIAL_ID" != "$SOURCE_TRIAL_ID"
PERF_ROOT='/var/lib/babel-online/performance'
RUN_ROOT="/var/lib/babel-online/results/28-august-morning-run/isolated-smoke/$ISOLATED_TRIAL_ID"
STAGE_ROOT="/tmp/babel-isolated-$ISOLATED_TRIAL_ID"
test ! -e "$STAGE_ROOT"

set -a
source /opt/babel/current/release.env
set +a

compose=(
  docker compose
  --project-name babel-gcp-demo
  --profile matrix
  --env-file /opt/babel/current/release.env
  --file /opt/babel/current/compose.yaml
)

install -d -m 0770 -o "$OPERATOR_UID" -g 10001 "$RUN_ROOT"
printf 'ISOLATED_TRIAL_ID=%s\n' "$ISOLATED_TRIAL_ID"

guard="$(
  "${compose[@]}" run --rm --no-deps --entrypoint /bin/sh performance-worker \
    -c 'printf %s "$BABEL_ONLINE_ALLOW_POPULATION_BUILD"'
)"
test "$guard" = false
```

The isolated topology runner owns the per-condition serving and trainer roles,
so stop the standalone roles before the matrix. Keep exactly one performance
worker, then require both the backend and worker to answer on loopback:

```bash
"${compose[@]}" stop serving trainer
"${compose[@]}" up --detach backend performance-worker

for _ in $(seq 1 120); do
  if curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:8787/health >/dev/null \
    && curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:8792/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error --max-time 2 \
  http://127.0.0.1:8787/health >/dev/null
curl --fail --silent --show-error --max-time 2 \
  http://127.0.0.1:8792/health >/dev/null

BABEL_PERFORMANCE_WORKER_TOKEN="$(
  python3 /opt/babel/current/release.py runtime-token /etc/babel/runtime.env
)"

worker_curl() {
  printf 'header = "X-Babel-Worker-Token: %s"\n' \
    "$BABEL_PERFORMANCE_WORKER_TOKEN" \
    | curl --config - "$@"
}

BABEL_ADMIN_NONCE="$(
  curl --fail --silent --show-error --max-time 5 \
    http://127.0.0.1:8787/admin \
    | sed -n 's/.*name="babel-admin-nonce" content="\([^"]*\)".*/\1/p'
)"
test -n "$BABEL_ADMIN_NONCE"

admin_curl() {
  printf '%s\n%s\n' \
    'header = "Origin: http://127.0.0.1:8787"' \
    "header = \"X-Babel-Admin-Nonce: $BABEL_ADMIN_NONCE\"" \
    | curl --config - "$@"
}

capture_evidence() (
  trap - ERR INT TERM HUP
  set +e
  worker_curl --silent --show-error --max-time 5 \
    --url http://127.0.0.1:8792/v1/performance/status \
    > "$RUN_ROOT/worker-status-final.json"
  admin_curl --silent --show-error --max-time 5 \
    --url "http://127.0.0.1:8787/admin/api/v1/performance/$ISOLATED_TRIAL_ID" \
    > "$RUN_ROOT/trial-final.json"
  "${compose[@]}" logs --no-color --timestamps backend performance-worker \
    > "$RUN_ROOT/compose.log" 2>&1
  exit 0
)

inventory_results() (
  trap - ERR INT TERM HUP
  set -Eeuo pipefail
  inventory_tmp="$(mktemp /tmp/babel-isolated-sums.XXXXXX)"
  trap 'rm -f -- "$inventory_tmp"' EXIT
  cd "$RUN_ROOT"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 -r sha256sum > "$inventory_tmp"
  install -m 0644 "$inventory_tmp" "$RUN_ROOT/SHA256SUMS"
  rm -f -- "$inventory_tmp"
  trap - EXIT
  sha256sum --check SHA256SUMS
)

stage_results() (
  trap - ERR INT TERM HUP
  set -Eeuo pipefail
  test ! -e "$STAGE_ROOT"
  stage_tmp="${STAGE_ROOT}.partial.$$"
  test ! -e "$stage_tmp"
  trap 'test -e "$STAGE_ROOT" || rm -rf -- "$stage_tmp"' EXIT
  install -d -m 0700 "$stage_tmp"
  cp --archive "$RUN_ROOT/." "$stage_tmp/"
  chown -R "$OPERATOR_UID:$OPERATOR_GID" "$stage_tmp"
  mv -- "$stage_tmp" "$STAGE_ROOT"
  trap - EXIT
  test -d "$STAGE_ROOT"
)

fail_and_stage() {
  trap - ERR INT TERM HUP
  set +e
  local status="${1:-1}"
  local reason="${2:-unexpected isolated smoke failure}"
  if [[ ! "$status" =~ ^[1-9][0-9]*$ ]]; then
    status=1
  fi
  capture_evidence
  {
    echo '# FAILED — 28 August isolated interview smoke'
    echo
    echo 'This is incomplete, non-formal representative evidence only.'
    printf -- '- Trial: %s\n' "$ISOLATED_TRIAL_ID"
    printf -- '- Source trial: %s (immutable)\n' "$SOURCE_TRIAL_ID"
    printf -- '- Failure: %s\n' "$reason"
    printf -- '- Captured UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo '- Changed-configuration retry: forbidden'
  } > "$RUN_ROOT/FAILED.md"
  local failed_state="$RUN_ROOT/failed-condition-state"
  if [[ -d "$PERF_ROOT/$ISOLATED_TRIAL_ID/conditions" \
      && ! -e "$failed_state" ]]; then
    install -d -m 0750 "$failed_state"
    cp --archive "$PERF_ROOT/$ISOLATED_TRIAL_ID/conditions" "$failed_state/"
  fi
  if ! inventory_results; then
    echo '- SHA-256 inventory required a best-effort retry.' \
      >> "$RUN_ROOT/FAILED.md"
    inventory_results || true
  fi
  if ! stage_results; then
    echo 'failed evidence could not be staged; leave the VM running' >&2
  fi
  exit "$status"
}

trap 'fail_and_stage $? "unexpected command failure"' ERR
trap 'fail_and_stage 130 "received INT"' INT
trap 'fail_and_stage 143 "received TERM"' TERM
trap 'fail_and_stage 129 "received HUP"' HUP
```

Every error or terminal signal uses that single fail-and-stage path. It disables
its own traps, captures worker/dashboard/log evidence, writes the explicitly
non-formal `FAILED.md`, copies available condition state, inventories the
result, and stages it for IAP retrieval. After a staged failure, exit the
remaining SSH shell, retrieve with the local block below, and then stop the VM:

```bash
gcloud compute instances stop babel-gpu-serving --project chloe-tutoring-bot --zone asia-southeast1-b
```

Create the exact isolated-smoke request, prepare it through the source trial's
nonce-protected dashboard route, and save the create receipt:

```bash
python3 -c '
import json
import sys
json.dump(
    {
        "rerunId": sys.argv[1],
        "matrix": "isolated-smoke",
        "warmupSeconds": 5,
        "durationSeconds": 25,
        "targetRps": 5.0,
    },
    sys.stdout,
    separators=(",", ":"),
)
print()
' "$ISOLATED_TRIAL_ID" > "$RUN_ROOT/create-request.json"

admin_curl --fail-with-body --silent --show-error --max-time 30 \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary "@$RUN_ROOT/create-request.json" \
  --url "http://127.0.0.1:8787/admin/api/v1/performance/$SOURCE_TRIAL_ID/representative-rerun" \
  --output "$RUN_ROOT/create-receipt.json"
```

Approve only the fresh isolated trial and save the approval receipt. Do not
mutate the database directly and do not start a second performance worker.

```bash
admin_curl --fail-with-body --silent --show-error --max-time 30 \
  --request POST \
  --url "http://127.0.0.1:8787/admin/api/v1/performance/$ISOLATED_TRIAL_ID/approve-next-scale" \
  --output "$RUN_ROOT/approval-receipt.json"
```

Poll the authenticated worker status. A failed or interrupted phase, a status
for another trial, or the bounded 15-minute timeout is a hard failure. Preserve
the receipts, last status, and logs; do not retry with changed configuration.

```bash
completed=false
for _ in $(seq 1 900); do
  status_json="$(
    worker_curl --fail --silent --show-error --max-time 5 \
      --url http://127.0.0.1:8792/v1/performance/status
  )"
  printf '%s\n' "$status_json" >> "$RUN_ROOT/worker-status.jsonl"
  phase="$(
    printf '%s' "$status_json" | python3 -c '
import json
import sys
expected = sys.argv[1]
document = json.load(sys.stdin)
if document.get("experimentId") != expected:
    raise SystemExit("worker status belongs to another trial")
print(document.get("phase", ""))
' "$ISOLATED_TRIAL_ID"
  )"
  case "$phase" in
    completed)
      completed=true
      break
      ;;
    failed|interrupted)
      fail_and_stage 1 "worker entered terminal phase $phase"
      ;;
  esac
  sleep 1
done
if [[ "$completed" != true ]]; then
  fail_and_stage 1 'worker did not complete before the 15-minute timeout'
fi
capture_evidence
```

Export that exact trial from the performance-worker container. This does not
export or rebuild the frozen population:

```bash
ID="$ISOLATED_TRIAL_ID"
"${compose[@]}" exec -T performance-worker \
  babel-online performance-export \
  --representative \
  --experiment-id "$ID" \
  --evidence-root "/var/lib/babel-online/performance/$ID/conditions" \
  --output-root "$RUN_ROOT/export" \
  > "$RUN_ROOT/export-receipt.json"
```

Generate fail-closed `$RUN_ROOT/summary.json` and `$RUN_ROOT/REPORT.md` from
the exact evidence and protected release attestation before building the closed
bundle:

```bash
python3 - \
  "$ID" \
  "$PERF_ROOT/$ID/conditions" \
  "$RUN_ROOT" \
  /opt/babel/current/release.env <<'PY'
import json
import math
import re
import sys
from pathlib import Path
from statistics import mean

trial_id, evidence_arg, run_arg, release_arg = sys.argv[1:]
evidence_root = Path(evidence_arg)
run_root = Path(run_arg)
source_trial_id = "dd8c6ee6-1a4b-443d-ae2c-2a0c02792f28"

request = json.loads((run_root / "create-request.json").read_text(encoding="utf-8"))
if request != {
    "rerunId": trial_id,
    "matrix": "isolated-smoke",
    "warmupSeconds": 5,
    "durationSeconds": 25,
    "targetRps": 5.0,
}:
    raise SystemExit("isolated smoke request schedule differs")
receipt = json.loads((run_root / "create-receipt.json").read_text(encoding="utf-8"))
trial = receipt.get("trial") if isinstance(receipt, dict) else None
request_identity = trial.get("requestIdentity") if isinstance(trial, dict) else None
if (
    not isinstance(request_identity, dict)
    or trial.get("experimentId") != trial_id
    or trial.get("topology") != "same_host_isolated"
    or trial.get("retrievalBackend") != "pgvector"
    or trial.get("warmupSeconds") != 5
    or trial.get("durationSeconds") != 25
    or trial.get("targetRps") != 5.0
    or trial.get("populationReady") is not True
    or trial.get("vectorCount") != 10_000
    or trial.get("requiredVectorCount") != 10_000
    or request_identity.get("sourceTrialId") != source_trial_id
    or request_identity.get("evidenceScope") != "representative_isolated_smoke"
):
    raise SystemExit("create receipt does not attest the isolated source population")

release = {}
for number, line in enumerate(
    Path(release_arg).read_text(encoding="utf-8").splitlines(), start=1
):
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"release attestation line {number} is not KEY=value")
    key, value = line.split("=", 1)
    if key in release or not key or not value or value != value.strip():
        raise SystemExit("release attestation contains an invalid or repeated value")
    release[key] = value

image_keys = (
    "BABEL_BACKEND_IMAGE",
    "BABEL_SERVING_IMAGE",
    "BABEL_TRAINER_IMAGE",
    "BABEL_PERFORMANCE_WORKER_IMAGE",
)
required_release = {
    "BABEL_SOURCE_COMMIT",
    *image_keys,
    "BABEL_MODEL_REVISION",
    "BABEL_DATASET_REVISION",
    "BABEL_POPULATION_VECTOR_SHA256",
    "BABEL_POPULATION_SNAPSHOT_SHA256",
}
if not required_release.issubset(release):
    raise SystemExit("release attestation is incomplete")
sha40 = re.compile(r"^[a-f0-9]{40}$")
sha256 = re.compile(r"^[a-f0-9]{64}$")
image = re.compile(r"^[^\s@]+@sha256:[a-f0-9]{64}$")
if sha40.fullmatch(release["BABEL_SOURCE_COMMIT"]) is None:
    raise SystemExit("source commit is not a lowercase 40-hex revision")
if any(image.fullmatch(release[key]) is None for key in image_keys):
    raise SystemExit("one or more release images are not digest-qualified")
if any(
    sha40.fullmatch(release[key]) is None
    for key in ("BABEL_MODEL_REVISION", "BABEL_DATASET_REVISION")
):
    raise SystemExit("model or dataset revision is not a lowercase 40-hex revision")
if any(
    sha256.fullmatch(release[key]) is None
    for key in (
        "BABEL_POPULATION_VECTOR_SHA256",
        "BABEL_POPULATION_SNAPSHOT_SHA256",
    )
):
    raise SystemExit("population attestation is not a lowercase SHA-256")

expected_files = {
    Path("01/live-evidence.json"),
    Path("02/live-evidence.json"),
    Path("03/live-evidence.json"),
}
actual_files = {
    path.relative_to(evidence_root)
    for path in evidence_root.rglob("*")
    if path.is_file()
}
if actual_files != expected_files:
    raise SystemExit("condition evidence must be exactly padded 01/02/03 files")

def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

def numeric_metrics(resources, field, *, integer=False):
    values = []
    for resource in resources:
        value = resource.get(field)
        if value is None:
            continue
        valid = type(value) is int if integer else type(value) in {int, float}
        if not valid or value < 0 or not math.isfinite(float(value)):
            raise SystemExit(f"resource metric {field} is invalid")
        values.append(value)
    return values

rows = []
expected_modes = ((False, False), (True, False), (True, True))
for internal_index, (training, activation) in enumerate(expected_modes, start=1):
    path = evidence_root / f"{internal_index:02d}" / "live-evidence.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    raw = document.get("rawEvidence")
    identity = {
        "topology": "same_host_isolated",
        "trainingEnabled": training,
        "activationEnabled": activation,
        "retrievalBackend": "pgvector",
    }
    if not isinstance(raw, dict):
        raise SystemExit(f"condition {internal_index} raw evidence is missing")
    if raw.get("evidenceScope") != "representative_isolated_smoke":
        raise SystemExit(f"condition {internal_index} scope differs")
    if raw.get("conditionIdentity") != identity:
        raise SystemExit(f"condition {internal_index} identity differs")
    measurements = raw.get("measurements")
    if not isinstance(measurements, list) or len(measurements) != 150:
        raise SystemExit(f"condition {internal_index} measurements differ")
    if any(
        not isinstance(row, dict)
        or row.get("outcome") != "success"
        or type(row.get("isWarmup")) is not bool
        for row in measurements
    ):
        raise SystemExit(f"condition {internal_index} contains request failures")
    warmup = [row for row in measurements if row["isWarmup"] is True]
    measured = [row for row in measurements if row["isWarmup"] is False]
    if (
        len(warmup) != 25
        or len(measured) != 125
        or document.get("requestCount") != 125
        or raw.get("warmupCount") != 25
        or raw.get("selectedRequestCount") != 150
    ):
        raise SystemExit(f"condition {internal_index} request schedule differs")
    latencies_ns = []
    starts = []
    completions = []
    for row in measured:
        latency = row.get("clientTotalNs")
        started = row.get("actualStartMonotonicNs")
        completed = row.get("completedAtMonotonicNs")
        if (
            type(latency) is not int
            or latency <= 0
            or type(started) is not int
            or type(completed) is not int
            or completed <= started
        ):
            raise SystemExit(f"condition {internal_index} timing evidence is invalid")
        latencies_ns.append(latency)
        starts.append(started)
        completions.append(completed)
    elapsed_ns = max(completions) - min(starts)
    if elapsed_ns <= 0:
        raise SystemExit(f"condition {internal_index} elapsed time is invalid")
    latencies_ms = [value / 1_000_000 for value in latencies_ns]
    p95_ms = percentile(latencies_ms, 0.95)
    recorded_p95 = document.get("p95Ms")
    if type(recorded_p95) not in {int, float} or not math.isclose(
        float(recorded_p95), p95_ms
    ):
        raise SystemExit(f"condition {internal_index} p95 differs")

    resources = raw.get("resources")
    if (
        not isinstance(resources, list)
        or not resources
        or any(not isinstance(row, dict) for row in resources)
    ):
        raise SystemExit(f"condition {internal_index} resources are missing")
    cpu = numeric_metrics(resources, "cpuPercent")
    host_memory = numeric_metrics(resources, "hostMemoryUsedBytes", integer=True)
    gpu = numeric_metrics(resources, "gpuUtilizationPercent")
    gpu_memory = numeric_metrics(resources, "gpuMemoryUsedBytes", integer=True)
    trainer_throughput = numeric_metrics(resources, "trainingStepRate")
    activation_ns = numeric_metrics(resources, "activationDurationNs", integer=True)
    if not cpu or not host_memory or not gpu or not gpu_memory:
        raise SystemExit(f"condition {internal_index} resource arrays are incomplete")

    feedback = raw.get("feedbackKafka")
    final = feedback.get("finalTrainerState") if isinstance(feedback, dict) else None
    if (
        not isinstance(final, dict)
        or final.get("available") is not True
        or type(final.get("kafkaLag")) is not int
        or final["kafkaLag"] < 0
    ):
        raise SystemExit(f"condition {internal_index} final trainer state is missing")
    if training:
        ranges = feedback.get("offsetRanges")
        next_offsets = final.get("nextOffsets")
        if (
            final.get("kafkaLag") != 0
            or final.get("offsetsCoverPublishedRanges") is not True
            or not isinstance(ranges, list)
            or not ranges
            or not isinstance(next_offsets, list)
            or not next_offsets
            or sha256.fullmatch(str(final.get("checkpointManifestSha256", "")))
            is None
        ):
            raise SystemExit(f"condition {internal_index} Kafka drain is incomplete")
        offset_map = {}
        for offset in next_offsets:
            if (
                not isinstance(offset, dict)
                or not isinstance(offset.get("topic"), str)
                or not offset["topic"]
                or type(offset.get("partition")) is not int
                or offset["partition"] < 0
                or type(offset.get("nextOffset")) is not int
                or offset["nextOffset"] < 0
            ):
                raise SystemExit(f"condition {internal_index} offsets are invalid")
            key = (offset["topic"], offset["partition"])
            if key in offset_map:
                raise SystemExit(f"condition {internal_index} offsets repeat")
            offset_map[key] = offset["nextOffset"]
        for offset_range in ranges:
            if (
        not isinstance(offset_range, dict)
        or not isinstance(offset_range.get("topic"), str)
        or not offset_range["topic"]
        or type(offset_range.get("partition")) is not int
        or offset_range["partition"] < 0
        or type(offset_range.get("startInclusive")) is not int
        or offset_range["startInclusive"] < 0
        or type(offset_range.get("endExclusive")) is not int
        or offset_range["endExclusive"] <= offset_range["startInclusive"]
        or offset_map.get(
                    (offset_range.get("topic"), offset_range.get("partition")), -1
                )
                < offset_range["endExclusive"]
            ):
                raise SystemExit(f"condition {internal_index} offsets lack coverage")
    if activation and (
        not isinstance(raw.get("observedActivationTargets"), list)
        or not raw["observedActivationTargets"]
        or not activation_ns
    ):
        raise SystemExit("condition 3 activation evidence or timing is incomplete")

    rows.append(
        {
            "formalConditionIndex": internal_index + 6,
            "conditionIndex": internal_index,
            "conditionId": document["conditionId"],
            "runId": document["runId"],
            "trainingEnabled": training,
            "activationEnabled": activation,
            "requestCount": len(measured),
            "p50Ms": percentile(latencies_ms, 0.50),
            "p95Ms": p95_ms,
            "p99Ms": percentile(latencies_ms, 0.99),
            "maxMs": max(latencies_ms),
            "achievedRps": len(measured) / (elapsed_ns / 1_000_000_000),
            "errors": 0,
            "timeouts": 0,
            "meanCpuPercent": mean(cpu),
            "maxHostMemoryBytes": max(host_memory),
            "meanGpuUtilizationPercent": mean(gpu),
            "maxGpuMemoryBytes": max(gpu_memory),
            "maxTrainerThroughputStepsPerSecond": max(
                trainer_throughput, default=0.0
            ),
            "finalKafkaLag": final["kafkaLag"],
            "maxActivationTimingMs": max(activation_ns, default=0) / 1_000_000,
        }
    )

if rows[0]["p95Ms"] <= 0 or rows[1]["p95Ms"] <= 0:
    raise SystemExit("interference ratio denominator is not positive")
ratios = {
    "Itraining": rows[1]["p95Ms"] / rows[0]["p95Ms"],
    "Ifull": rows[2]["p95Ms"] / rows[0]["p95Ms"],
    "IActivationIncrement": rows[2]["p95Ms"] / rows[1]["p95Ms"],
}
label = "representative isolated smoke — non-formal interview evidence"
images = {
    "backend": release["BABEL_BACKEND_IMAGE"],
    "serving": release["BABEL_SERVING_IMAGE"],
    "trainer": release["BABEL_TRAINER_IMAGE"],
    "performanceWorker": release["BABEL_PERFORMANCE_WORKER_IMAGE"],
}
summary = {
    "schemaVersion": 1,
    "trialId": trial_id,
    "label": label,
    "evidenceScope": "representative_isolated_smoke",
    "formalPerformanceClaim": False,
    "sourceTrialId": source_trial_id,
    "populationVectorCount": 10_000,
    "schedule": {"warmupSeconds": 5, "durationSeconds": 25, "targetRps": 5.0},
    "sourceCommit": release["BABEL_SOURCE_COMMIT"],
    "images": images,
    "modelRevision": release["BABEL_MODEL_REVISION"],
    "datasetRevision": release["BABEL_DATASET_REVISION"],
    "populationVectorSha256": release["BABEL_POPULATION_VECTOR_SHA256"],
    "populationSnapshotSha256": release[
        "BABEL_POPULATION_SNAPSHOT_SHA256"
    ],
    "conditions": rows,
    "interference": ratios,
}
(run_root / "summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
    encoding="utf-8",
)

lines = [
    "# 28 August Morning Run — Isolated Topology Smoke",
    "",
    f"{label}.",
    "",
    f"- Trial: `{trial_id}`",
    f"- Source trial: `{source_trial_id}` (immutable)",
    "- Population vector count: 10,000",
    "- Schedule: 5 s warmup, 25 s measured, target 5.0 RPS",
    f"- Source commit: `{release['BABEL_SOURCE_COMMIT']}`",
    f"- Backend image: `{images['backend']}`",
    f"- Serving image: `{images['serving']}`",
    f"- Trainer image: `{images['trainer']}`",
    f"- Performance-worker image: `{images['performanceWorker']}`",
    f"- Model revision: `{release['BABEL_MODEL_REVISION']}`",
    f"- Dataset revision: `{release['BABEL_DATASET_REVISION']}`",
    f"- Population vector SHA-256: `{release['BABEL_POPULATION_VECTOR_SHA256']}`",
    f"- Population snapshot SHA-256: `{release['BABEL_POPULATION_SNAPSHOT_SHA256']}`",
    "",
    "| Formal # | Local # | Training | Activation | p50 ms | p95 ms | p99 ms | max ms | achieved RPS | errors | timeouts | CPU mean % | host memory max GiB | GPU mean % | GPU memory max GiB | trainer throughput max steps/s | final Kafka lag | activation max ms |",
    "|---:|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for row in rows:
    lines.append(
        f"| {row['formalConditionIndex']} | {row['conditionIndex']} | "
        f"{str(row['trainingEnabled']).lower()} | "
        f"{str(row['activationEnabled']).lower()} | "
        f"{row['p50Ms']:.3f} | {row['p95Ms']:.3f} | "
        f"{row['p99Ms']:.3f} | {row['maxMs']:.3f} | "
        f"{row['achievedRps']:.3f} | {row['errors']} | {row['timeouts']} | "
        f"{row['meanCpuPercent']:.2f} | "
        f"{row['maxHostMemoryBytes'] / 2**30:.3f} | "
        f"{row['meanGpuUtilizationPercent']:.2f} | "
        f"{row['maxGpuMemoryBytes'] / 2**30:.3f} | "
        f"{row['maxTrainerThroughputStepsPerSecond']:.3f} | "
        f"{row['finalKafkaLag']} | {row['maxActivationTimingMs']:.3f} |"
    )
lines += [
    "",
    f"- Itraining: {ratios['Itraining']:.4f}",
    f"- Ifull: {ratios['Ifull']:.4f}",
    f"- IActivationIncrement: {ratios['IActivationIncrement']:.4f}",
    "",
]
(run_root / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
PY

"${compose[@]}" exec -T performance-worker \
  babel-friday-benchmark representative-run-build \
  --trial-id "$ID" \
  --export-root "$RUN_ROOT/export/feedback-export" \
  --evidence-root "$PERF_ROOT/$ID/conditions" \
  --report "$RUN_ROOT/REPORT.md" \
  --output-root "$RUN_ROOT/representative-accepted" \
  > "$RUN_ROOT/representative-build-receipt.json"
```

The builder requires exactly three condition evidence files, complete training
offset coverage, zero final Kafka lag, and the formal-position bindings
`7`/`8`/`9`. It preserves `formalPerformanceClaim=false`.

The existing private publication command is optional. If publication is
explicitly requested, use the exact closed bundle returned by the build
receipt:

```bash
BUNDLE_ROOT="$(
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundleRoot"])' \
    "$RUN_ROOT/representative-build-receipt.json"
)"
"${compose[@]}" exec -T performance-worker \
  babel-friday-benchmark representative-run-publish \
  --trial-id "$ID" \
  --bundle-root "$BUNDLE_ROOT" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  --revision main \
  > "$RUN_ROOT/representative-publication-receipt.json"
```

That optional representative publication must never be attached or presented
as formal evidence.

Generate and check one SHA-256 inventory for every result file other than the
inventory itself, then create the fresh traversable `/tmp` stage owned by the
original SSH operator:

```bash
inventory_results
stage_results
trap - ERR INT TERM HUP
```

After success, exit the root shell and then the VM SSH shell before running a
local copy command:

```bash
exit
exit
```

On failure, `fail_and_stage` exits the root shell nonzero after staging, so exit
the remaining VM SSH shell once. In either case both the root and SSH shells
must be closed before retrieval.

On the local workstation, set the same recorded ID and retrieve only the fresh
`/tmp` stage through IAP. Refuse both possible destination names before copying,
so scp can never merge with stale evidence. The final local destination is
exactly
`results/28-august-morning-run/isolated-smoke/$ISOLATED_TRIAL_ID`:

```bash
ISOLATED_TRIAL_ID='<recorded fresh UUID>'
ID="$ISOLATED_TRIAL_ID"
LOCAL_ROOT='results/28-august-morning-run/isolated-smoke'
test ! -e "$LOCAL_ROOT/$ID"
test ! -e "$LOCAL_ROOT/babel-isolated-$ID"
mkdir -p "$LOCAL_ROOT"
gcloud compute scp --recurse --tunnel-through-iap \
  --project chloe-tutoring-bot \
  --zone asia-southeast1-b \
  "babel-gpu-serving:/tmp/babel-isolated-$ID" \
  "$LOCAL_ROOT/"
test -d "$LOCAL_ROOT/babel-isolated-$ID"
test ! -e "$LOCAL_ROOT/$ID"
mv -- "$LOCAL_ROOT/babel-isolated-$ID" "$LOCAL_ROOT/$ID"
(
  cd "$LOCAL_ROOT/$ID"
  sha256sum --check SHA256SUMS
)
```

Stop the VM after completion or after an extended or indefinite blocker. The
exact stop command is:

```bash
gcloud compute instances stop babel-gpu-serving --project chloe-tutoring-bot --zone asia-southeast1-b
```

### Formal accepted-bundle export

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
