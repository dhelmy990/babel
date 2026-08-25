# Friday demo synchronous POST performance report

## Result

The real paired replay completed with 500 measured requests per condition and
no HTTP errors or timeouts. Online training alone was within 2.0% of the
serving-only p95, which is too small to distinguish from run-to-run noise in
this single trial. Training plus synchronization raised p95 end-to-end latency
by 18.6%, from 15.514 ms to 18.397 ms, and produced a 43.493 ms maximum.

All durations below are monotonic nanoseconds. Percentiles use nearest rank and
exclude 20 warmup requests.

| Condition | Samples | p50 | p95 | p99 | max | RPS | Errors | Timeouts | p95 ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `pgvector_serving_only` | 500 | 12,455,226 | 15,513,785 | 16,586,542 | 17,907,686 | 77.193 | 0 | 0 | 1.000x |
| `pgvector_training_no_sync` | 500 | 12,785,396 | 15,824,148 | 17,001,903 | 18,050,274 | 75.514 | 0 | 0 | 1.020x |
| `pgvector_training_and_sync` | 500 | 13,075,412 | 18,396,745 | 22,969,903 | 43,492,604 | 72.474 | 0 | 0 | 1.186x |

The fixed replay schedules one synchronous POST every 5 ms, but the runner has
one client and does not issue concurrent requests. RPS is therefore achieved
single-client completion throughput, not a capacity limit. Queue lateness is
preserved in each raw row.

## Server stages and client overhead

Each cell is p50 / p95 / p99 / max, with 500 samples. `ann` is the complete
pgvector candidate-query timer. At this 51-Babel demo size, the observed query
plan uses `DISTINCT` and a top-N sort; these numbers are not evidence of HNSW
speed.

| Condition | Stage | p50 / p95 / p99 / max (ns) |
|---|---|---:|
| serving only | queue | 2,164 / 4,839 / 7,384 / 10,430 |
|  | encode | 134,282 / 192,412 / 218,030 / 275,488 |
|  | context | 155,863 / 228,059 / 261,071 / 375,125 |
|  | pgvector candidate query (`ann`) | 10,107,261 / 13,035,346 / 14,012,664 / 14,821,244 |
|  | filtering | 60,113 / 86,252 / 103,614 / 113,443 |
|  | serialization | 41,588 / 69,831 / 91,292 / 108,654 |
|  | server total | 10,549,172 / 13,485,893 / 14,523,323 / 15,361,620 |
|  | client overhead | 1,839,919 / 2,269,166 / 2,576,564 / 2,964,923 |
| training, no sync | queue | 2,134 / 3,607 / 4,969 / 31,189 |
|  | encode | 138,140 / 189,967 / 209,404 / 587,905 |
|  | context | 156,093 / 213,501 / 244,260 / 454,585 |
|  | pgvector candidate query (`ann`) | 10,105,719 / 13,150,443 / 13,761,701 / 15,057,457 |
|  | filtering | 57,217 / 81,944 / 101,661 / 212,189 |
|  | serialization | 42,199 / 74,230 / 96,441 / 242,436 |
|  | server total | 10,521,610 / 13,622,270 / 14,522,903 / 15,537,350 |
|  | client overhead | 1,945,146 / 3,165,481 / 4,463,972 / 5,354,827 |
| training and sync | queue | 2,064 / 3,837 / 4,709 / 12,524 |
|  | encode | 135,846 / 204,314 / 231,986 / 259,618 |
|  | context | 154,190 / 219,914 / 253,346 / 380,485 |
|  | pgvector candidate query (`ann`) | 10,252,865 / 14,220,034 / 15,620,506 / 40,785,436 |
|  | filtering | 58,590 / 83,577 / 95,730 / 304,983 |
|  | serialization | 43,261 / 78,517 / 95,239 / 432,834 |
|  | server total | 10,644,842 / 14,679,597 / 16,219,302 / 41,283,321 |
|  | client overhead | 2,005,781 / 4,895,573 / 8,638,680 / 15,531,940 |

Client total starts before HTTP request-model dumping and stops after response
JSON parsing. Client overhead is client total minus server total, so it includes
request/response encoding, loopback transport, framework work, socket write,
and response parsing. The server `serialization` timer covers response-model
preparation but stops before the final `model_dump_json()` and socket write;
client total is the authoritative end-to-end measure.

## Online telemetry

| Condition | Trainer step p50 / p95 / p99 / max (ns) | Kafka lag p50 / p95 / p99 / max | Synchronization spike |
|---|---:|---:|---:|
| serving only | n/a | n/a | n/a |
| training, no sync | 398,308 / 761,922 / 1,619,715 / 2,646,134 (n=3,763) | 1 / 3 / 3 / 3 (n=206) | n/a |
| training and sync | 410,662 / 1,168,066 / 2,156,214 / 7,233,278 (n=3,763) | 1 / 3 / 5 / 5 (n=97) | 49,923,073 ns (62 publications) |

Both training conditions published 4,000 real feedback events to Kafka and ran
the real `OnlineTrainer`/`NumpyWorkingModel`; 3,763 events contained optimizer
pairs. Step timing wraps the exact `train_pairs` call. Kafka lag is broker high
watermark minus trainer next offset, clamped to the condition's captured start
watermark so historical shared-topic records are excluded.

The sync condition captured version, vectors, and model state together through
the corrected locked `OnlineTrainer.capture_sync_state()` API. Each measured
publication used the real `AtomicSynchronizer`: it materialized all 51 created
Babel vectors, computed the canonical snapshot, fsynced and atomically renamed
the artifact directory, and swapped an isolated serving snapshot. To protect
the completed demo run, the interference harness did not insert new vector
versions or flip the active row in PostgreSQL, and HTTP requests stayed on the
frozen version-48 pgvector snapshot. The reported sync spike therefore excludes
production database write/activation cost and likely understates full sync
interference.

## Reproduction identity and commands

- Benchmark run: `3cb917ff-409f-580e-b3c2-52956cf4eee9`
- Source run: `596e191a-957e-43bb-ad27-1a109696378d`
- Serving baseline source: `d5852b359fc58148076f008abe890be02ea403ff`
- Locked training/sync source: `f90f22ecd66fdec01c2d9b6d7135dee6e6abd434`
- Model: `6e54f425-290c-5d5f-a451-60f8260d0d96`, version 48
- Embedding space: `00000000-0000-5000-8000-000000000003`
- Starting pgvector snapshot: `5e539bef89d9cb9b1537a418a84ca73cfbd91f81179cea2ac0cc042b9b04f612`
- Request corpus SHA-256: `01459e97f8067a96d41fa4c8edc5b042bc28f60a38264e7ffe30f645e2660a54`
- Created-Babel universe SHA-256: `7542e67d5b5772cd65645bc5872002b8265f97d3803575239607cb0850904cde`
- Host: Linux 6.8, AMD Ryzen 9 8945HS, 8 cores / 16 threads

The real conditions used the checked-in `live-replay` adapter. The no-sync
command was:

```bash
PYTHONPATH=benchmark/src:online/src online/.venv/bin/python -m babel_benchmark.cli live-replay \
  --manifest artifacts/performance/friday-real/manifest.json \
  --requests artifacts/performance/friday-real/requests.jsonl \
  --candidate-universe artifacts/performance/friday-real/created-babels.jsonl \
  --condition pgvector_training_no_sync \
  --measurements artifacts/performance/friday-real/training-no-sync.jsonl \
  --telemetry artifacts/performance/friday-real/training-no-sync-telemetry.jsonl \
  --dsn postgresql://babel:babel-local-dev@127.0.0.1:54329/babel \
  --kafka-bootstrap 127.0.0.1:29092 \
  --feedback artifacts/online/596e191a-957e-43bb-ad27-1a109696378d/feedback/feedback-export/feedback.jsonl \
  --run-id 596e191a-957e-43bb-ad27-1a109696378d --model-version 48 \
  --publish-limit 4000
```

The sync command adds `--condition pgvector_training_and_sync`, its matching
output paths, `--sync-root <fresh-artifact-directory>`, and
`--sync-every-steps 50`. The serving-only run used `babel_benchmark.cli replay`
against the same real loopback POST and frozen corpus. Raw measurements,
telemetry, sync ledgers, summary, and generated report remain under
`artifacts/performance/friday-real/` and are intentionally not committed.

The 51-row candidate universe was exported from `experiment_babels`; the
benchmark rejects any candidate not created by a synthetic creator in this run,
any response identity/backend mismatch, and incomplete server timings.
