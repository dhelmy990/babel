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

Slice 3 currently times a duplicate `json.dumps` for its `serialization` stage,
not the framework's actual response encoding/socket write. Use client total as
the authoritative latency number and treat `serialization` only as a server
estimate until that instrumentation is corrected by its owning lane.

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

The CLI deliberately assumes the operator has already configured the selected
condition. It never changes the online service and therefore cannot silently
enable training or synchronization.

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

Reset to the identical starting model and pgvector snapshot. Start the online
trainer while withholding all calls to `AtomicSynchronizer.publish`, replay the
same feedback offsets at the same rate, and run:

```bash
babel-friday-benchmark replay \
  --manifest artifacts/performance/friday-demo/manifest.json \
  --requests artifacts/performance/friday-demo/requests.jsonl \
  --candidate-universe artifacts/performance/friday-demo/created-babels.jsonl \
  --condition pgvector_training_no_sync \
  --measurements artifacts/performance/friday-demo/training-no-sync.jsonl
```

Reset again. Start the trainer and enable the declared synchronization cadence,
then run:

```bash
babel-friday-benchmark replay \
  --manifest artifacts/performance/friday-demo/manifest.json \
  --requests artifacts/performance/friday-demo/requests.jsonl \
  --candidate-universe artifacts/performance/friday-demo/created-babels.jsonl \
  --condition pgvector_training_and_sync \
  --measurements artifacts/performance/friday-demo/training-and-sync.jsonl
```

The sync condition must start at the frozen snapshot but may report later
snapshot checksums and model versions after atomic synchronization. Those
identities remain in every raw row; retain the runtime's sync ledger with the
artifacts and verify each change against it before accepting the report.

Any backend switch, candidate-universe violation, response identity mismatch,
or incomplete server timing invalidates the condition. HTTP failures and
timeouts are retained as raw rows.

## Connect trainer, lag, and sync telemetry

Slice 3 does not yet expose trainer-step duration, Kafka lag, and synchronization
duration through one public stream. The benchmark therefore supplies a narrow
adapter without importing or modifying `babel_online`:

```python
from time import monotonic_ns

from babel_benchmark.contracts import dump_jsonl
from babel_benchmark.runner import (
    ConditionTelemetryRecorder,
    MeasuredConditionOperations,
)

recorder = ConditionTelemetryRecorder(manifest, condition, monotonic_ns)
measured = MeasuredConditionOperations(recorder, monotonic_ns=monotonic_ns)

measured.trainer_step(
    step=next_optimizer_step,
    operation=run_one_actual_optimizer_step,
)
measured.kafka_lag(current_kafka_lag)  # omit when the broker cannot provide it
measured.synchronization(
    version=trainer.training_version,
    operation=publish_sync,
)

telemetry_path.write_text(dump_jsonl(tuple(recorder.rows)))
```

The condition driver owns when those existing public operations run. The
adapter only measures them and emits `ConditionTelemetryV1`; it does not change
trainer offsets, event order, model state, or sync semantics. Until the runtime
composition exposes a callback at the actual optimizer-step boundary, this is
the explicit integration seam. Do not label the duration of
`OnlineTrainer.process_available()` as trainer-step time: it also includes poll
and event handling and may perform no optimizer step. The HTTP CLI can collect
real POST latency now; a fully accepted training-interference report additionally
requires this condition-driver callback and a measured sync operation.

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
