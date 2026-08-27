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
- **Original model:** the immutable Qwen-based recommender distilled on the
  fixed 50,000-example 2016 interview subset and selected as the parent of an
  online run. The dataset release remains complete even though this model's
  pre-interview training subset is not.
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
4. After the user confirms that training is healthy, produce the real,
   deterministically sampled June and July engineering environments defined
   below.
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

## June and July Engineering Snapshot Scale

The complete 2016 distillation release remains complete because it determines
the Qwen student's training coverage. The June and July 2026 environments have
a different purpose: they provide real source material at sufficient scale for
storage, retrieval, concurrency, feedback, and topology experiments. They do
not need to reproduce every article in either monthly Wikipedia.

Each monthly environment targets 10,000 eligible namespace-zero articles,
125 times the current 80-row fixture:

- an 8,000-identity shared temporal core present in both months; and
- a 2,000-identity monthly supplement selected separately for that month.

The shared core is selected deterministically from crosswalked identities. It
first includes required dashboard seeds and their valid one-hop graph
neighborhoods, then high-traffic Clickstream identities, and finally a
seeded-hash tail for topical variety. The monthly supplements use the same
priority classes against each month's remaining eligible identities. When an
identity would enter both supplements, the deterministic crosswalk and ranking
assign it to only one supplement and backfill the other. At the target size,
the resulting union contains exactly 12,000 identities.

Task 4 must select from indexed or range-addressable, streamable data already
available at authenticated, commit-pinned Hugging Face revisions. It must not
discover the selected rows by downloading or scanning complete Wikipedia XML
or SQL dumps. Preparing or mirroring an indexed source is a prerequisite and
is outside the construction timer; if no qualifying source is available, the
task reports that blocker instead of falling back to a full-corpus scan or the
artificial fixture.

After a month's exact source/index pin is available, its expansion budget is
45 minutes. On reaching the target, the builder freezes the manifest and begins
joint finalization immediately. At 45 minutes it stops that month's expansion
regardless of target progress. Once both monthly candidate sets are frozen, the
builder packages the largest common June/July cohort supported by the valid
rows collected so far. The packaged per-month row count `N` is the
largest multiple of five no greater than 10,000 that both months can support;
the shared core contains `4N/5` identities and each disjoint monthly supplement
contains `N/5`. If `N` is below 5,000, publication fails. Thus the emergency
floor is exactly 5,000 rows per month: 4,000 shared identities and 1,000
month-specific identities, with a 6,000-identity union. Selection elapsed time
and packaging/upload elapsed time are recorded separately.

For each selected monthly article, retain only the title, lead, and first
useful section together with canonical page identity, revision and content
hashes, and resolved redirects. Do not store entire article bodies. Retain real
induced directed pagelinks and real Clickstream transitions whose endpoints
are both selected. Each monthly hidden configuration targets at least 100,000
pagelinks and 100,000 Clickstream transitions when the induced source data
contains that many, and hard-caps each relation at 250,000 rows. Below the
target, retain all valid induced rows and report the shortfall. Above the cap,
retain Clickstream rows by descending real transition count with stable
source/target tie-breaks; retain pagelinks with Clickstream-supported edges
first and then a seeded stable source/target ordering.
The observable/hidden boundary remains unchanged: graph edges and behavioral
signals never enter observable catalog configurations. All source reads use
backend authentication at exact private-Hugging-Face commit SHAs, and the
selection manifest records the policy version, seed, source revisions, ordered
identity checksums, shared/supplement membership, and exclusion counts.

These releases are named and documented as **10k time-boxed engineering
snapshots**, never complete monthly Wikipedias. A complete monthly expansion is
post-interview evidence expansion and is not a prerequisite for model
integration or topology measurement.

The source snapshots and runtime population are separate quantities. The
formal experiment still targets 10,000 distinct synthetic-created Babels and a
creator ladder of 50, 100, and 500. Only those created Babels enter pgvector or
the serving candidate set; the remaining source articles are creation material.

## Deadline Training Scope

Dataset completeness and pre-interview training size are independent. Task 3
continues reconciling and publishing the complete 2016 distillation dataset.
The user-launched Colab trainer consumes a deterministic subset of that
complete pinned release:

- smoke training: 1,000 examples;
- interview training: exactly 50,000 examples;
- validation: exactly 5,000 held-out examples;
- test: exactly 5,000 held-out examples;
- epochs: one; and
- maximum sequence length: 384 tokens by default, configurable down to 256 or
  up to 512 when measured memory permits.

Sampling preserves the release's existing train, validation, and test split
assignments. Within each split, order rows by
`SHA-256("babel-interview-2016-v1" + NUL + article_key)` and select the lowest
required ranks. The 1,000-row smoke set is the first 1,000 identities of the
same ordered 50,000-row training set. Save the seed
`babel-interview-2016-v1`, ordered article identities, per-split checksums,
dataset commit SHA, and selection-policy version with the dataset handoff,
every checkpoint, and final model artifact.

Validation uses exact search over the fixed 5,000 held-out validation vectors.
The fixed 5,000-row test set remains untouched until the 50,000-example model
checkpoint is complete. The pre-interview path has no 100,000- or 200,000-row
expansion stage and does not wait for full-corpus ANN evaluation. Larger and
complete-corpus training are post-interview experiments.

The first real 50,000-example checkpoint, its fixed exact-validation report,
and its serving-adapter manifest are the only training artifacts that unblock
real Qwen integration. A smoke checkpoint proves mechanics but cannot satisfy
that gate.

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

## Creator Recommendation Walks

Recommendation browsing is a workload setting, not a topology. It is enabled
by default and held checksum-identical across topology comparisons.

One configurable `recommendation_walk_probability`, defaulting to 40%, is used
for independent session-start and continuation rolls. After creating a Babel,
the creator receives a session-start roll. A successful roll requests
recommendations for that new Babel. The
existing hidden relevance and creator-preference calculation independently
assigns `include`, `exclude`, or `ignore` to every returned candidate. The walk
roll does not replace or modify those decisions.

Every included candidate forms a directed edge:

```text
current source Babel -> included recommended Babel
```

After the edge is recorded, the creator receives a separate 40% continuation
roll for that included Babel. A successful roll adds the included Babel to the
current session's breadth-first recommendation queue. This repeats for newly
included Babels subject to all of the following defaults:

- recommendation-walk probability: 40%;
- maximum traversal depth: two graph hops;
- maximum recommendation requests per creator session: 10;
- a Babel is requested at most once within one session; and
- an existing run/source/target edge is never duplicated.

The root Babel is depth zero. Recommendation requests are made for the root and
for successful continuations at depth one. Included nodes at depth two receive
edges but are not expanded, so “depth two” cannot be interpreted as two full
additional branching generations. The request cap remains an independent hard
limit.

All session-start and continuation draws are deterministic over their applicable
run, creator, session, source Babel, candidate Babel, and candidate-rank
identities. Reports record the root, parent request, depth, draw outcome, request
count, and cache hit/miss fields so the same traversal can be replayed and its
cache-recency effect can be measured.

An include edge is durable experiment state, not merely an activity-log count.
Because recommendations intentionally cross creator ownership, the core
personal `edges` table cannot represent them: that table requires both endpoints
to have the same owner. A separate `experiment_edges` relation records at least
the run, source Babel, target Babel, acting creator, request, feedback event, and
creation timestamp, with uniqueness on `(run_id, source_babel_id,
target_babel_id)`. `exclude` and `ignore` never create edges. Experiment exports
include these edge rows and their provenance.

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
topology receives its own serving-only baseline. The dashboard and reports show
all three calculations explicitly, as ratios and percentage increases:

```text
Itraining =
  p95(serving + training, activation disabled)
  / p95(serving only)

Ifull =
  p95(serving + training + activation)
  / p95(serving only)

IActivationIncrement =
  p95(serving + training + activation)
  / p95(serving + training, activation disabled)
```

For any displayed ratio `I`, percentage increase is `(I - 1) * 100`.

The experimental matrix remains bounded:

- topology comparisons use pgvector;
- pgvector-versus-hnswlib comparisons use one fixed topology and snapshot;
- all three same-host topologies are compared at the initial cohort;
- higher creator cohorts compare the monolith control with the selected split
  configuration; and
- cross-host execution is approved only after same-host evidence justifies its
  additional setup and cost.

Automated validation executes the full three-topology by three-condition matrix
only as a bounded smoke test over the existing tiny fixture-sized article and
Babel population. It verifies orchestration, counters, edge formation,
measurement persistence, and cleanup, but its timings are never presented as
performance conclusions. Large 3-by-3 runs are operator-initiated experiments,
not a test-suite or implementation gate.

## Scalability Dashboard

The dashboard separates ordinary online runs from saved scalability
experiments. It provides:

- topology, parent model, dataset revision, retrieval backend, creator cohort,
  concurrent clients, target request rate, population threshold, recommendation
  session-start/continuation probability, traversal limits, trainer batch size,
  synchronization interval, resource limits, warmup, and duration;
- paired slider and numeric-input controls for seeded source articles, target
  created Babels, and concurrent simulated users; sliders cover safe defaults
  while numeric inputs allow an explicitly submitted custom value;
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

A separate read-only trial-progress component consumes persisted progress data
through the dashboard status API. It does not import or directly control Kafka,
the trainer, serving, or benchmark implementation. It shows the current phase
and condition, condition number, configured totals, seeded/created/indexed/
requested/completed counts, elapsed time, recent rate, estimated time remaining,
and graceful-stop/draining state. Failure of this presentation component cannot
stop or mutate an experiment.

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
- recommendation-session roots, parent requests, traversal depths, deterministic
  draw outcomes, accepted edges, and cache hit/miss observations;
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
- include actions durably form unique directed experiment edges and recursive
  browsing uses separate relevance and continuation decisions;
- the tiny 3-by-3 smoke matrix completes without becoming a prerequisite for a
  long-running full-scale matrix;
- all three interference ratios and independent trial progress are visible and
  saved;
- comparisons use sufficiently large, checksum-identical inputs and concurrent
  request schedules; and
- cross-host claims are made only for actual cross-host execution.
