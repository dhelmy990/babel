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

### Corrective representative 2×3 result

Trial `0367346d-98f9-4419-b2db-9194c4c868f7` completed on 27 August 2026.
It reused the checksum-identical 10,000-Babel population and the first 75
request-aligned rows of the frozen workload. It compared `same_process` with
`same_host_split` across serving-only, training-without-activation, and
training-with-activation modes. Every condition processed 75 requests (12
warm-up and 63 measured): 450 of 450 succeeded. All four training conditions
covered their published Kafka ranges and ended at lag zero.

| Topology | Serving p95 | Training p95 | Full p95 | `Itraining` | `Ifull` | `IActivationIncrement` |
|---|---:|---:|---:|---:|---:|---:|
| `same_process` | 6,320.46 ms | 81,449.70 ms | 16,842.64 ms | 12.8867 | 2.6648 | 0.2068 |
| `same_host_split` | 6,940.73 ms | 5,988.61 ms | 84,844.64 ms | 0.8628 | 12.2242 | 14.1677 |

The useful engineering result is not that every ratio must exceed one. These
are sequential, short, same-host runs, so warmed model and OS caches affect the
ordered rows. The result does show a sharp architectural distinction:

- separating the trainer kept training-without-activation p95 near the split
  serving baseline, while same-process training produced severe contention;
- split activation remained extremely expensive, demonstrating that process
  separation alone does not isolate shared pgvector/model-state materialization;
- the next optimization target is activation: batch or stage residual writes,
  reduce full snapshot materialization, and atomically switch a prepared child
  after the serving path is no longer competing with PostgreSQL insertion;
- a counterbalanced repeat is required before treating cross-topology latency
  differences as a stable performance estimate.

The activation modes produced compatible immutable children while preserving
the selectable original: monolith child
`dd56b030-a76e-5b51-b690-7790b56b140b` at version 11 and split child
`fac749ba-2395-5868-81b0-8fa7a309d376` at version 10. Raw condition evidence
is beneath
`state/performance/0367346d-98f9-4419-b2db-9194c4c868f7/conditions/`.
The representative export contains 450 feedback rows and 2,682 canonical edge
rows beneath
`runs/0367346d-98f9-4419-b2db-9194c4c868f7/representative-export/` and is
permanently labelled `formalPerformanceClaim=false`.

Publisher commits `97525f4` and `c69b433` were reviewed. The closed 17-file
bundle was remotely reloaded and checksum-verified at private dataset commit
`dc0d158ff75851a5f944aa674f9fb88221440ede`, beneath
`representative-runs/0367346d-98f9-4419-b2db-9194c4c868f7/08fcd65c2e723760e95e93dea0c48fb827de3b0702a5befece7ae9b0dc1786b1/`.
This is the first remotely verified representative evidence and remains
explicitly `formalPerformanceClaim=false`.

An earlier 150-request representative attempt
(`418a3a44-dcb8-42ee-be72-0706e4b30c35`) completed conditions 1–5 but failed
closed when one final split-activation request crossed the 120-second timeout.
It remains diagnostic load-limit evidence and is not an accepted result.

### Optimized representative rerun

Commits `b0963cd` and `0d8f6f9` changed activation to prepare the model once,
write the prepared rows with one database `executemany`, and atomically switch
the prebuilt snapshot. Experiment reviews returned PASS/APPROVED and all 79
focused tests passed.

The first attempted optimized rerun,
`7d0dbbf8-18e6-4a9b-afa1-0441ee4a300b`, failed before condition 1 because the
restarted worker did not inherit `online/.venv/bin` in `PATH` and therefore
could not find `babel-online`. It is startup diagnostic evidence only. The
launch procedure was corrected to restore the virtual-environment path before
starting either process.

Trial `72e35d2e-f04e-405d-af9a-25f873e44d5b` then completed with the same
frozen source trial, population, and workload as the accepted representative
comparison: 10,000 real-Qwen vectors, 50 creators and concurrent users, 75
requests per condition (12 warm-up and 63 measured), 2.5 target RPS, six
fixed-order conditions, and pgvector. All 450 requests succeeded with no
errors. Every training condition ended at Kafka lag zero with complete offset
coverage. This representative result is `formalPerformanceClaim=false`.

| Topology | Serving p95 | Training p95 | Full p95 | `Itraining` | `Ifull` | `IActivationIncrement` |
|---|---:|---:|---:|---:|---:|---:|
| `same_process` | 83,786.707889 ms | 13,552.654297 ms | 85,161.014859 ms | 0.1617518415 | 1.0164024462 | 6.2837148349 |
| `same_host_split` | 4,920.490575 ms | 4,940.905643 ms | 84,288.686762 ms | 1.0041489904 | 17.1301388504 | 17.0593597312 |

The same-process rows are strongly cold-cache/order distorted and cannot
support a stable topology conclusion. The same-host split training-only row is
near its serving baseline, but split full p95 moved from 84,844.64 ms in the
prior successful trial to 84,288.69 ms here, only about 0.66% lower. This is not
a material end-to-end speedup claim.

The architectural result is attribution. The split activation receipt for
version 10 records `stageDurationNs=29851507054` (about 29.85 seconds) and
`switchDurationNs=41016510` (about 41 milliseconds), with about 30.02 seconds
from publication to activation. Atomic switching is small; preparing and
materializing the snapshot remains the contention. The activated serving child
is `79db828b-c2ed-53fa-9f7c-555d6cf5610e` at version 10. The final trainer
version is 17: child `c22c42b7-2cf5-5600-8c25-4d73df5f036c` is registered and
selectable, but its final activation request remained pending after the
required successful version-10 activation. Final Kafka lag is zero.

The export again contains 450 feedback rows and 2,682 accepted edges. Its
feedback SHA-256 is
`adfea5b3b939aabe4a6478fc9c560ec71e6081b8eff5ef468ea59c97a31400fc`; its
edge SHA-256 is
`3004a3d1ab53a80be0e349d3d38d1ac5b08f883fee44750212e3ec6d8b13d069`.
Remote evidence: **pending local closed-bundle publication**. Until that pin is
recorded, the earlier trial remains the remotely verified representative.

The next experiment should move, throttle, or batch preparation at the
trainer/distributor resource boundary, then repeat the comparison in reverse
or counterbalanced order. Atomic switch work is no longer the primary target.

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
| First representative cohort-50 2×3 | Passed; 450/450 requests; remotely verified; non-formal | `dhelmy990/babel-wikipedia-experiment@dc0d158ff75851a5f944aa674f9fb88221440ede` |
| Optimized representative cohort-50 2×3 | Passed; 450/450 requests; publication pending; non-formal | pending local closed-bundle publication |
| pgvector/HNSW comparison | Pending | — |
| Cohort 100/500 scale | Pending | — |
| Live fault campaign | Pending | — |
