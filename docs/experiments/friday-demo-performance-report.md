# Friday demo synchronous POST performance report

## Status

The measurement lane is ready, but no real Friday environment run is checked
in. The deterministic fixture proves replay, candidate-universe enforcement,
monotonic timing, aggregation, and report rendering; it is not performance
evidence. Do not copy fixture timings into this report or draw a latency
conclusion from unit tests.

Generate the real report with the commands in
`docs/runbooks/friday-demo-performance.md`. The accepted report must retain the
raw request and telemetry JSONL beside its summary.

## Frozen comparison

All three conditions use the same request IDs, order, nanosecond schedule,
created-synthetic-Babel universe, starting model, embedding space, and pgvector
snapshot:

1. `pgvector_serving_only`: serving active; trainer and synchronization inactive.
2. `pgvector_training_no_sync`: serving and online trainer active; serving state
   held fixed.
3. `pgvector_training_and_sync`: serving and online trainer active; configured
   atomic synchronizations active.

The first replay row is warmup. It remains in raw data and is excluded from all
reported percentiles and RPS.

## End-to-end results

Populate this section only from `babel-friday-benchmark report` output.

| Condition | Samples | p50 (ns) | p95 (ns) | p99 (ns) | max (ns) | RPS | Errors | Timeouts | p95 slowdown vs serving-only |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `pgvector_serving_only` | — | — | — | — | — | — | — | — | 1.000x |
| `pgvector_training_no_sync` | — | — | — | — | — | — | — | — | — |
| `pgvector_training_and_sync` | — | — | — | — | — | — | — | — | — |

## Server stages

For each condition, report p50/p95/p99/max and sample count for `queue`,
`encode`, `context`, `ann`, `filtering`, `serialization`, and `serverTotal`.
These values come from the stable Slice 3 response contract and remain separate
from the authoritative client end-to-end total.

Current instrumentation caveat: Slice 3's `serialization` stage measures a
duplicate `json.dumps` of the response data. It does not cover FastAPI/Starlette
response encoding or the socket write. Treat it as a server-side estimate;
client total and derived client overhead include the actual HTTP serialization,
transfer, and response parse.

## Online telemetry

| Condition | Trainer step p50/p95/p99/max (ns) | Kafka lag p50/p95/p99/max | Sync spike (ns) |
|---|---:|---:|---:|
| `pgvector_serving_only` | n/a | n/a | n/a |
| `pgvector_training_no_sync` | — | — if available | n/a |
| `pgvector_training_and_sync` | — | — if available | — |

Trainer and synchronization durations use the same monotonic-nanosecond adapter
as request measurements. Kafka lag is reported when the condition driver can
read it; missing lag is `n/a`, never zero.

## Acceptance checks

- Every successful candidate belongs to `fixtures/performance/created-babels.jsonl`
  and is owned by a synthetic creator in the run.
- No response contains the request creator's own Babel.
- The three raw streams have identical request IDs and schedule offsets.
- Each successful response reports pgvector, the expected model and embedding
  space, and complete nonnegative server timing stages.
- Errors and timeouts remain rows and are never removed from throughput/error
  counts.
- Slowdown is `condition p95 / pgvector_serving_only p95` from non-warmup
  successful requests.
