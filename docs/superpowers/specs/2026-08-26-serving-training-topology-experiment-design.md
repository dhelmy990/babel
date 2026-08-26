# Serving and Training Topology Experiment Design

**Date:** 2026-08-26

## Purpose

Babel is an engineering scalability experiment. Its purpose is to measure how
recommendation serving behaves as the created-Babel catalog, request load, and
online-learning workload grow. Historical fidelity is useful, but the primary
requirement is enough real source material and enough synthetic-created Babels
to exercise storage, retrieval, serving, feedback, training, synchronization,
and failure behavior at meaningful scale.

This design makes serving/training placement a first-class experimental
variable. Separate servers initially means independently running services, not
necessarily separate physical machines.

## Terminology

- **Offline distillation trainer:** the separate Colab agent that trains the
  2016 Qwen student. It is not part of local online-interference measurements.
- **Recommendation server:** synchronously encodes a newly created Babel,
  retrieves candidates, and returns recommendations with timing and model
  identity.
- **Online trainer:** consumes include/exclude/ignore feedback from Kafka and
  produces immutable child model states.
- **Model-state distributor:** validates and registers immutable model states
  and instructs serving to activate one. It is not a distributed parameter
  server and does not send weights through Kafka.
- **Original model:** the immutable complete 2016 distilled Qwen-based
  recommender selected as the parent of an online run.
- **Child model:** an immutable post-run model descended from the original or
  another explicitly selected parent. Creating or activating a child never
  overwrites its parent.

## Execution Gates

Work proceeds in this order:

1. Mirror authoritative Wikipedia inputs into the private Hugging Face dataset
   repository. All semantic Wikipedia reads after mirroring use an
   authenticated, commit-pinned Hugging Face revision.
2. Produce and publish the sufficiently large 2016 distillation dataset and
   verify its pinned release remotely.
3. Stop. The user prepares and launches a separate training agent. No other
   agent silently assumes ownership of the full training run.
4. After the user confirms that training is healthy, produce the real June and
   July environments.
5. Prepare the serving adapter against the training artifact contract. A
   structurally correct fixture may test parsing, but it cannot satisfy real
   integration acceptance.
6. Stop before real model integration until the training agent finishes and
   publishes the trained artifact.
7. Load the real Qwen artifact, populate pgvector with its 100-dimensional
   embeddings, and pass integration checks.
8. Execute topology, retrieval, failure-isolation, and scaling experiments.
9. Publish accepted datasets, models, interactions, raw measurements, and
   reports through simple rolling Hugging Face uploads.

## Supported Topologies

Every experiment condition records exactly one topology:

| Value | Placement | Purpose |
|---|---|---|
| `same_process` | Serving and online training execute in one process. | Control condition that exposes direct scheduling, memory, and runtime contention. |
| `same_host_split` | Recommendation server and trainer are independent processes or containers on one host. | Normal operating mode and default dashboard selection. |
| `same_host_isolated` | Split services receive recorded CPU and memory assignments and any genuinely enforceable accelerator assignment. | Measures the benefit and cost of resource isolation. |
| `cross_host` | Serving and training execute on separate hosts. | Optional later condition for real network, host-failure, and separate-accelerator effects. |

`same_host_split` is the dashboard default. `same_process` remains available as
the baseline, not as the preferred deployment. `cross_host` is not a
prerequisite for the first complete experiment.

Network shaping between same-host containers, if added, is recorded as
`same_host_network_emulation`; it must never be reported as a cross-host result.

Resource-isolation reports distinguish requested limits from verified limits.
CPU affinity and memory limits can be enforced on a typical host. Sharing one
ordinary GPU does not constitute GPU isolation unless the hardware/runtime
provides and verifies an isolation mechanism such as separate devices or MIG.

## Runtime Architecture

The request path remains synchronous:

```text
Simulator
    -> Recommendation server POST
       -> Qwen encoding
       -> creator-context computation
       -> pgvector retrieval (default) or fixed hnswlib condition
       -> candidate filtering
    <- candidates, scores, model version, vector version, stage timings
```

The feedback and update path remains asynchronous:

```text
Simulator decisions
    -> babel.feedback.v1
    -> Online trainer
    -> immutable checkpoint
    -> Model-state distributor
    -> explicit serving activation
```

Kafka contains feedback, offsets, and correlation identifiers, not model
weights. The recommendation server retains its last valid model if the trainer
or distributor fails. The original model remains selectable from the dashboard
after any number of child models have been created.

PostgreSQL/pgvector is the durable default vector store. hnswlib remains an
explicit optional run-scoped index rebuilt from checksum-identical pgvector
rows; it is never a silent fallback.

Only Babels actually created by synthetic creators are eligible candidates.
Unused source-catalog articles may supply creation material but must not enter
the serving index.

## Population and Measurement Gate

Runs have a population phase before their measured phase. Synthetic creators
create Babels from the pinned source catalog until the configured created-Babel
threshold is reached and the corresponding real-model embeddings are indexed.
The initial formal-performance default is 10,000 distinct created Babels.

The dashboard shows source rows, created Babels, indexed Babels, duplicate
blocks, population rate, and readiness. A formal measurement cannot start when
the created and indexed counts differ or the population is below its declared
threshold. Smaller populations remain useful smoke tests but are labelled as
such.

## Controlled Comparisons

A topology comparison holds constant:

- dataset revision and created-Babel manifest;
- original model and vector snapshot checksums;
- requests, request arrival schedule, and warmup;
- feedback events, Kafka offset range, and replay order;
- retrieval backend and parameters;
- online-training configuration and synchronization cadence; and
- host/hardware description, except for explicitly declared resource limits.

For each of `same_process`, `same_host_split`, and `same_host_isolated`, run:

1. serving only;
2. serving plus online training with activation disabled; and
3. serving plus online training with synchronization and activation enabled.

This separates ordinary training contention from model-activation pauses. Each
topology receives its own serving-only baseline. Primary interference is
reported as the topology's training-condition p95 divided by its own baseline
p95.

The experimental matrix remains bounded:

- topology comparisons use pgvector;
- pgvector-versus-hnswlib comparisons use one fixed topology and snapshot;
- all three same-host topologies are compared at the initial cohort;
- higher creator cohorts compare the monolith control with the selected split
  configuration; and
- cross-host execution is approved only after same-host evidence justifies its
  additional setup and cost.

## Scalability Dashboard

The dashboard separates ordinary online runs from saved scalability
experiments. It provides:

- topology, parent model, dataset revision, retrieval backend, creator cohort,
  concurrent clients, target request rate, population threshold, trainer batch
  size, synchronization interval, resource limits, warmup, and duration;
- start, graceful stop, and explicit approval to advance the scale ladder;
- live service health and placement, including serving, trainer, Kafka,
  PostgreSQL, and model-state distributor;
- created/indexed population progress;
- request p50/p95/p99/max, throughput, errors, and per-stage timings;
- trainer throughput, step duration/loss, Kafka lag, backpressure, and
  checkpoint status;
- trainer model version, serving model version, version staleness, activation
  duration, and activation-related latency spikes;
- per-service CPU, RSS, host memory, disk, and available GPU telemetry; and
- links to immutable raw artifacts, summaries, model children, and Hugging Face
  commits.

Saved experiments are immutable after acceptance. A rerun receives a new
experiment/condition identifier rather than replacing prior evidence.

## Persisted Evidence

Raw observations are persisted before summaries. Every condition records:

- experiment, condition, run, request, and correlation identifiers;
- topology and a placement manifest;
- requested and verified resource limits;
- host, operating-system, container/runtime, CPU, memory, and GPU description;
- dataset commit, model artifact, vector snapshot, configuration, and code
  revisions;
- request traces and server-stage timings;
- trainer steps, Kafka offsets/lag, checkpoint and activation ledgers;
- resource samples, faults, restarts, safety stops, and invalid conditions; and
- raw-artifact checksums, summary/report checksums, and final Hugging Face
  revision.

Reports remain reproducible from raw Parquet records and the immutable manifest.
They include denominators and do not promote smoke-test measurements as scale
results.

## Failure and Recovery Behavior

The topology suite includes controlled operational tests:

- stopping the online trainer leaves serving available on its last valid model;
- Kafka retains acknowledged feedback and exposes increasing lag;
- restarting the trainer resumes from recorded offsets without duplicating
  model lineage;
- an invalid or incompatible checkpoint is rejected without changing serving;
- restarting serving loads its selected last-valid immutable model; and
- a failed activation leaves both the current model and original model intact.

The dashboard records the injected fault, detection time, visible impact,
recovery time, lost/duplicated events, and final model versions.

## Acceptance

The split architecture is accepted when:

- `same_host_split` is the default dashboard topology and launches genuinely
  independent serving and trainer services;
- the same workload can run as `same_process` without semantic changes;
- topology and placement are saved with all raw and derived results;
- a killed trainer does not make recommendation serving unavailable;
- model activation is explicit, versioned, observable, and reversible to any
  retained immutable model;
- the recommendation index contains only synthetic-created Babels embedded by
  the real trained encoder;
- comparisons use sufficiently large, checksum-identical inputs and concurrent
  request schedules; and
- cross-host claims are made only for actual cross-host execution.

