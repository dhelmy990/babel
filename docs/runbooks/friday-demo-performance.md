# Friday demo performance runbook

This runbook measures the existing synchronous Slice 3 recommendation POST. It
does not start a creator scale ladder, compare retrieval backends, sample every
host resource, upload to Hugging Face, or change the dashboard.

## What the lane measures

The client uses `time.monotonic_ns()` immediately before preparing the request
JSON and stops after the complete response body is parsed. Raw rows preserve
the response's `queue`, `encode`, `context`, `ann`, `filtering`,
`serialization`, and `serverTotal` nanoseconds. Client overhead is
`clientTotalNs - serverTotalNs` and therefore includes real request/response
encoding, loopback transfer, framework overhead, and response parsing.

The reporter emits end-to-end and per-stage p50/p95/p99/max with denominators,
RPS, errors, timeouts, trainer step time, Kafka lag when available, maximum sync
duration, and p95 slowdown relative to serving-only. Percentiles use nearest
rank and exclude warmup rows.

Slice 3 serializes the wire payload once and returns those exact bytes. Its
`serialization` stage measures response-model preparation; socket write time is
therefore represented only by client total/overhead. Use client total as the
authoritative end-to-end latency number.

## Install and verify

From the repository root:

```bash
online/.venv/bin/python -m pip install -e 'benchmark[dev]'
online/.venv/bin/python -m pytest benchmark/tests -q
```

Copy `fixtures/performance/manifest.json` into the run artifact directory and
replace its benchmark run ID, endpoint, model ID, embedding-space ID, starting
pgvector snapshot, request checksum, and candidate-universe checksum with the
actual frozen values. Do not edit the checked-in representative fixture for a
live run. Keep the same request corpus and schedule in all three conditions.

The checked-in fixture has six synthetic creators, six deterministic requests
at 10 RPS, and one warmup request. For a longer Friday sample, create a new
JSONL by deterministically repeating the six request shapes with unique request
IDs and frozen offsets, then update the manifest checksum before any condition
runs.

## Run the paired conditions

The plain `replay` command assumes the operator has configured the selected
condition externally. The `live-replay` command is the Friday-only adapter for
the integrated runtime: it keeps the real loopback POST active while producing
real Kafka feedback, running the real `OnlineTrainer`, and optionally publishing
locked snapshots through the real `AtomicSynchronizer`.

Create an artifact directory:

```bash
mkdir -p artifacts/performance/friday-demo
```

For serving-only, start the loopback recommendation endpoint on the manifest's
address with the trainer stopped and synchronization disabled. Then run:

```bash
babel-friday-benchmark replay \
  --manifest artifacts/performance/friday-demo/manifest.json \
  --requests artifacts/performance/friday-demo/requests.jsonl \
  --candidate-universe artifacts/performance/friday-demo/created-babels.jsonl \
  --condition pgvector_serving_only \
  --measurements artifacts/performance/friday-demo/serving-only.jsonl
```

Reset to the identical starting model and pgvector snapshot. Run the trainer
without synchronization against the frozen feedback export:

```bash
babel-friday-benchmark live-replay \
  --manifest artifacts/performance/friday-demo/manifest.json \
  --requests artifacts/performance/friday-demo/requests.jsonl \
  --candidate-universe artifacts/performance/friday-demo/created-babels.jsonl \
  --condition pgvector_training_no_sync \
  --measurements artifacts/performance/friday-demo/training-no-sync.jsonl \
  --telemetry artifacts/performance/friday-demo/training-no-sync-telemetry.jsonl \
  --dsn "$BABEL_DATABASE_URL" \
  --kafka-bootstrap "$BABEL_KAFKA_BOOTSTRAP_SERVERS" \
  --feedback "artifacts/online/$BABEL_PERF_RUN_ID/feedback/feedback-export/feedback.jsonl" \
  --run-id "$BABEL_PERF_RUN_ID" --model-version "$BABEL_PERF_MODEL_VERSION" \
  --publish-limit 4000
```

Reset again. Start the trainer and enable the declared synchronization cadence,
then run:

```bash
babel-friday-benchmark live-replay \
  --manifest artifacts/performance/friday-demo/manifest.json \
  --requests artifacts/performance/friday-demo/requests.jsonl \
  --candidate-universe artifacts/performance/friday-demo/created-babels.jsonl \
  --condition pgvector_training_and_sync \
  --measurements artifacts/performance/friday-demo/training-and-sync.jsonl \
  --telemetry artifacts/performance/friday-demo/training-and-sync-telemetry.jsonl \
  --dsn "$BABEL_DATABASE_URL" \
  --kafka-bootstrap "$BABEL_KAFKA_BOOTSTRAP_SERVERS" \
  --feedback "artifacts/online/$BABEL_PERF_RUN_ID/feedback/feedback-export/feedback.jsonl" \
  --run-id "$BABEL_PERF_RUN_ID" --model-version "$BABEL_PERF_MODEL_VERSION" \
  --publish-limit 4000 \
  --sync-root "$BABEL_PERF_SYNC_ROOT" \
  --sync-every-steps 50
```

The benchmark sync publisher uses the exact version, model state, and vectors
returned by `OnlineTrainer.capture_sync_state()` under its training lock. Give
every invocation a fresh `--sync-root`; an existing sync-version directory is a
hard error. The adapter swaps an isolated serving snapshot so it can measure
publication interference without mutating the completed run's PostgreSQL active
state. Consequently, its sync duration excludes vector-row insertion and the
active-row transaction; disclose that limitation in the report.

Any backend switch, candidate-universe violation, response identity mismatch,
or incomplete server timing invalidates the condition. HTTP failures and
timeouts are retained as raw rows.

## Trainer, lag, and sync telemetry

`live-replay` wraps the model's actual `train_pairs` call with
`time.monotonic_ns()`; it does not mislabel consumer poll/event handling as step
time. Kafka lag is sampled from real broker high watermarks and trainer offsets,
clamped to the condition's captured starting watermark so records already on a
shared topic are excluded. Sync timing covers locked capture consumption,
created-Babel materialization, canonical hashing, fsync/atomic rename, and the
isolated serving-snapshot swap. Raw rows use `ConditionTelemetryV1`.

## Generate the real report

Pass telemetry files only when collected. Missing Kafka lag remains `n/a`.

```bash
babel-friday-benchmark report \
  --measurements \
    artifacts/performance/friday-demo/serving-only.jsonl \
    artifacts/performance/friday-demo/training-no-sync.jsonl \
    artifacts/performance/friday-demo/training-and-sync.jsonl \
  --telemetry \
    artifacts/performance/friday-demo/training-no-sync-telemetry.jsonl \
    artifacts/performance/friday-demo/training-and-sync-telemetry.jsonl \
  --summary artifacts/performance/friday-demo/summary.json \
  --markdown artifacts/performance/friday-demo/report.md
```

Retain the copied manifest, request corpus, created-Babel universe, all raw
JSONL, `summary.json`, and `report.md` together. Replace the placeholder tables
in `docs/experiments/friday-demo-performance-report.md` only with real output
from a completed three-condition run. Reject a final report whose two training
conditions lack trainer-step samples or whose sync condition lacks a sync
duration. Kafka lag may remain `n/a` only when the broker cannot expose it.
