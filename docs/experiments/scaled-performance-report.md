# Scaled Recommendation Performance Report

This document is the evidence ledger for the scaled architecture experiment.
It deliberately separates orchestration smoke evidence from formal measurements.
No fixture latency in this file is a performance claim.

## Smoke

Status: **bounded live 3×3 fixture smoke passed; formal performance claim
false**.

- Matrix: exactly three topologies by three load modes.
- Topologies: `same_process`, `same_host_split`, `same_host_isolated`.
- Load modes: serving only, training without activation, training with activation.
- Bound: at most 20 requests per condition and 180 requests total.
- Input: the current fixture only; no 10,000-Babel population was started.
- Timeout: each condition runs in a verified worker-only Linux process group.
  The callback cannot start until the parent acknowledges that isolation
  handshake. The suite then reserves a cooperative-cancellation window,
  terminates/kills the whole group, and reaps the worker before returning; a
  late condition prevents receipt publication and cannot leave a callback
  subprocess running. Group `SIGKILL` escalation occurs even if the worker
  leader already exited after `SIGTERM`.
- Receipt: positive requests/edges, startup/cleanup, progress, ratios, trainer-
  failure serving availability, and an existing nonempty raw-result file are
  mandatory, with
  `formal_performance_claim=false`.

On 27 August 2026, `babel-live-smoke` executed all nine conditions with one
request per condition (nine total) against the current fixture. Every condition
made a real loopback HTTP recommendation request, published acknowledged
feedback through the local Kafka broker, observed an accepted edge, and wrote
raw evidence. Training conditions consumed the event with the real
`OnlineTrainer` and saved a checkpoint. Activation conditions applied three
changed trainer-derived vectors before serving advanced from model version 0 to
1; the before/after vector-state checksums are recorded. Split conditions used
independent serving and trainer processes; isolated conditions additionally
verified distinct CPU affinities. In the four split training conditions, an
actually running trainer was killed and serving remained healthy. Trainer
failure isolation is explicitly `not_applicable` for serving-only and
same-process rows. No role process remained after cleanup. The schema-v2 receipt
is `state/live-smoke-actual-v5/receipt.json`.

The receipt calculates the three requested ratios from observed client p95 for
each topology: `Itraining`, `Ifull`, and `IActivationIncrement`. With only one
request per condition these are wiring checks, not stable performance estimates.

This is live systems-wiring evidence, but it remains deliberately non-formal:
the input is the small deterministic fixture, the encoder is the fixture item
tower, only nine requests were issued, and it does not use the approved 10,000-
Babel real-Qwen population. It must not be quoted as a latency, throughput, or
model-quality result. The earlier callback-harness tests remain the evidence for
strict timeout/process-group behavior.

## Population

Status: **verified 10,000-vector population complete**.

Formal measurement requires a receipt for 5,000 June plus 5,000 July
synthetic-created Babels, with 10,000 distinct created IDs and 10,000 indexed
real-Qwen vectors. The receipt freezes the ordered Babel manifest, its checksum,
the vector-byte checksum, dataset/model commits, creator/source uniqueness, a
50-creator round-robin assignment-manifest checksum, and a separate cross-month
used-source-set checksum. It requires exactly 5,000 rows per month.
Each condition must clone the same bytes. Created and indexed counts below the
declared threshold invalidate the condition.

The approved source release is
`dhelmy990/babel-wikipedia-experiment@0d1ab2c7f0e2295682288fcf10077d2d776bf559`.
On 27 August 2026, population run
`7f4ad291-e6d0-5bb9-9658-3605c634a3a9` completed and passed the independent
gate. It contains exactly 10,000 distinct created Babels and 10,000 finite,
normalized 100-dimensional vectors across 50 creators, split 5,000 June and
5,000 July. The frozen population manifest SHA-256 is
`3100687c4fe902d7afe468de141c7a08da81b267e8dd5944c427c825688ca9c7`; the
ordered vector SHA-256 is
`ae261599fa06ac3e545619d567b03cb58dc8bcb9794576bbae2d4ef65f0f6ca4`; and
the pgvector snapshot SHA-256 is
`bf8e693a8cc341724a40adedcc9da5f769547677ab97f3965ea9a2c0eedf3881`.
The population uses the pinned trained Qwen adapter and projection from
`dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e`.

## Topology

Status: **bounded live driver passed; first real-Qwen control matrix stopped at
condition 3 and is non-formal**.

At cohort 50, the controlled runner expects all nine topology/load conditions.
At cohorts 100 and 500 it compares the monolith with one operator-selected split
topology, reducing the higher-cohort matrix to six conditions. Every condition
must reuse the same frozen request, feedback, creator schedule, event mix, and
separate start/continuation draw checksums.

The creator schedule is explicitly creator-local. Start and continuation use
independent recorded draw streams, each with probability 0.40. Cohort validation
requires every expected condition exactly once and rejects duplicates, extras,
identity drift, `same_process` masquerading as the selected split, a receipt
cohort that differs from its condition IDs, and counts that differ from the
exact frozen 10,000-row population. Callers cannot weaken this with an
independent threshold.

The formal export and immutable publication contract now accepts both shapes.
It preserves the exact nine-condition cohort-50 path and validates a canonical
six-condition order for cohorts 100/500. Each accepted bundle records its
creator cohort, condition count/order, exact condition/run bindings, zero-lag
checkpoint coverage, feedback/edge checksums, model/population pins, and the
default condition-6 split-service child before publication and exact-trial
dashboard attachment.

All current topology labels are same-host. They measure process scheduling,
memory contention, model-activation pauses, Kafka lag, and failure isolation.
They do not measure physical-network jitter, NIC bandwidth, remote-machine
failure, distributed clocks, or separate physical GPUs.

### First real-Qwen control attempt

Trial `ce8e54ff-e317-4a89-b7db-90327e02dc43` froze one deterministic workload
of 2,464 recommendation requests over the accepted 10,000-Babel population.
Serving used the exact pinned Qwen LoRA plus 100-dimensional projection and
pgvector snapshot described above.

- Condition 1, `same_process` serving only: all 2,464 requests succeeded; 150
  were warm-up and 2,314 were measured; p95 was 18,607.913402 ms.
- Condition 2, `same_process` training without activation: all 2,464 requests
  succeeded; p95 was 20,574.653075 ms; all 2,464 Kafka records were consumed
  and final lag was zero. The observed `Itraining` was approximately 1.1057,
  or a 10.57% p95 increase.
- Condition 3, `same_process` training with activation, did not produce
  accepted condition evidence. The old one-event NumPy updater consumed only
  640 of 2,464 records before the fixed drain deadline while writing 68 model
  snapshots (535 MiB of runtime state). The worker failed closed with
  `online trainer did not drain to captured offsets`; conditions 4 through 9
  were not started.

This is partial control evidence, not a completed 3x3 result and not eligible
for formal publication. It proves real trained-Qwen serving and gives a valid
serving-only baseline, but the training rows use the superseded demo updater.
Commit `83d0e3c` replaces that updater with a reviewed PyTorch online head,
real configured micro-batching, trainable creator context/fusion, sparse item
residuals, complete checkpoint state, and serving activation. The corrective
run must reuse the frozen population and workload under a new trial identity.

## Retrieval

Status: **formal pgvector/HNSW evidence pending**.

pgvector remains the default. A fixed-topology hnswlib comparison may run only
after a real PostgreSQL HNSW condition has been observed. Report snapshot/index
preparation time, memory, steady latency/throughput, and Recall@K separately;
do not mix index construction with request latency.

The implemented comparison consumes the canonical frozen population and a
deterministic ordered query selection. Its formal pgvector condition gate must
match the snapshot/model identity and contain only successful measured requests.
The result records p50/p95/p99, sequential retrieval throughput, Recall@10/50,
separate warmup duration, preparation duration, index footprint, and the
exact-audit checksum. PostgreSQL preparation retains the actual HNSW
`EXPLAIN (ANALYZE, BUFFERS)` plan for a measured limit-50 query. It is
labelled `retrieval_only` and `topologyConclusionEligible=false`. PostgreSQL
relation storage and hnswlib serialized/RSS footprints retain their distinct
measurement labels rather than being treated as equivalent RAM values.

## Scale

Status: **manual gate at cohort 50; no automatic advancement**.

The required ladder is 50 → 100 → 500 creators. Cohorts 1,000 → 5,000 → 10,000
are optional. Every advance requires an explicit operator approval. Stop when:

- memory exceeds 90% for the configured safety window (30 seconds by default);
- disk free space falls below 10 GiB;
- errors plus timeouts exceed 5% for two consecutive windows;
- Kafka lag increases for two windows at verified maximum backpressure; or
- a process, checkpoint, or activation fails.

Trainer backpressure is persisted. Two high-latency windows halve micro-batch
size to 2, then add 25 ms inter-batch delay up to 500 ms. Five low windows remove
delay and then restore the configured batch. “Maximum backpressure” means
exactly micro-batch 2 plus delay 500 ms.

## Faults

Status: **bounded controller smoke passed; live fault campaign pending**.

The controller covers trainer kill/restart, Kafka pause/resume, invalid model
state, and serving stop/start. Evidence records serving availability during and
after the fault, maximum lag, detection/recovery durations, duplicate/lost
events, and trainer/serving versions. Invalid-state evidence additionally
requires explicit rejection and retention of the last valid serving version.
Serving restart uses separate stop/probe/start/probe boundaries; restart time is
not mislabeled as detection time. Trainer, Kafka, and serving recovery actions
execute in `finally`, including when the during-fault probe fails.

## Formal result table

No complete formal matrix has been collected yet. Partial control measurements
are reported above and must not be presented as a completed topology result.

| Evidence | Status | Artifact |
|---|---|---|
| Tiny 3×3 smoke | Live passed; 9 requests; non-formal | `state/live-smoke-actual-v5/receipt.json` |
| Frozen 10,000-Babel population | Passed | `state/performance/ce8e54ff-e317-4a89-b7db-90327e02dc43/population/manifest.json` |
| First cohort-50 control attempt | Failed closed at condition 3; non-formal | `state/performance/ce8e54ff-e317-4a89-b7db-90327e02dc43/conditions/` |
| Corrective cohort-50 topology run | Pending | — |
| pgvector/HNSW comparison | Pending | — |
| Cohort 100/500 scale | Pending | — |
| Live fault campaign | Pending | — |
