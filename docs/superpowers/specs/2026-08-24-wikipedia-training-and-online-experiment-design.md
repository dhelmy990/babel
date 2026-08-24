# Wikipedia Training and Online Experiment Design

**Date:** 2026-08-24

**Status:** Approved in conversation; awaiting written-spec review

**Hugging Face namespace:** `dhelmy990`

## Purpose

This design turns Babel's recommendation research documents into four bounded
implementation slices:

1. an early Google Colab handoff for distilling a Qwen content encoder from
   September 2016 Wikipedia Navigation Vectors;
2. completion of the 2016, June 2026, and July 2026 datasets in one private
   Hugging Face repository;
3. a dashboard-controlled online recommendation experiment with a minimal
   Kafka feedback path; and
4. controlled serving/training interference and scaling experiments.

The immediate priority is to make the Qwen student genuinely trainable in
Colab as soon as a verified pilot dataset shard is available. Full dataset
preparation continues independently after that handoff.

This document also resolves gaps and contradictions between:

- `prompts/Refactored Agent Instruction Set — Content Encoder, PPR Simulator,
  Clickstream, and Distillation.md`;
- `prompts/two_towers.md`;
- `prompts/wikipedia_user_profiles.md`;
- `prompts/architecture.md`;
- the backend seeding dashboard design and plan; and
- the current frontend-only implementation.

The refactored instruction set remains authoritative unless this approved
design explicitly refines or supersedes one of its underspecified decisions.

## Fixed Experimental Semantics

Babel is a creator-centric recommendation experiment.

For each event:

1. a synthetic creator creates a new Babel;
2. the serving recommender synchronously receives the creator, the new Babel,
   and observable creator history;
3. the recommender proposes candidate Babels created by other creators;
4. the hidden simulator independently includes, explicitly excludes, or
   ignores each candidate;
5. included candidates become accepted outgoing edges from the new Babel;
6. one observable feedback event is appended to Kafka; and
7. the online trainer asynchronously consumes feedback and periodically
   synchronizes compatible state to serving.

The hidden simulator may use monthly Wikipedia graphs, Personalized PageRank,
creator archetypes, graph relatedness, and optional Clickstream information.
The recommender must never observe those values.

Multiple candidates may be included for one new Babel. A creator may not
create two Babels from the same source Wikipedia article.

## Program Architecture

```text
Authoritative sources
  Figshare | archived Wikimedia | 2026 Wikimedia dumps
                         |
                         v
              Acquisition adapters
                         |
                         v
/home/dhelmy990/Data/babel-data
  raw -> staging -> prepared -> validation reports
                         |
                         v
      dhelmy990/babel-wikipedia-experiment
            private Hugging Face dataset
                         |
          immutable revision/tag per release
                         |
       +-----------------+-----------------+
       |                                   |
       v                                   v
Colab/offline training              Local online experiment
remote HF streaming                 remote HF streaming
2016 distillation                   June/July environments
                                           |
                                  synchronous recommendation
                                           |
                                     hidden simulator
                                           |
                                           v
                                  babel.feedback.v1
                                           |
                                           v
                                  online-training consumer
```

Architectural boundaries:

- Acquisition code may temporarily read authoritative upstream sources.
- All downstream Wikipedia reads use the private Hugging Face repository at a
  pinned commit SHA.
- Hugging Face is authoritative for prepared datasets and archived experiment
  runs.
- Kafka is transient transport for observable online feedback, not a dataset
  store or a model-transfer mechanism.
- Colab does not connect to Kafka or local services.
- The simulator alone may load hidden dataset configurations.
- Recommender and training loaders accept only observable schemas.
- The repository contains code, manifests, schemas, notebooks, documentation,
  and tiny fixtures, never raw dumps or bulk prepared shards.
- The existing Electron app, C++ backend, model serving, and training remain
  separate processes or modules connected through explicit contracts.

## Local Bulk-Data Workspace

Bulk working data lives outside Git at:

```text
/home/dhelmy990/Data/babel-data/
  raw/
  staging/
  prepared/
  cache/
  checkpoints/
  reports/
```

Deletion policy:

- Raw archives and prepared shards may be removed only after their derived
  data, checksums, provenance, and remote load behavior have been verified
  against a pinned Hugging Face commit.
- Small manifests, reports, logs, and debug samples remain locally.
- Every deleted source must be reacquirable from a recorded authoritative URL
  and checksum.

## Private Hugging Face Dataset

Use one private repository because all releases form one temporal experiment:

`dhelmy990/babel-wikipedia-experiment`

Initial configurations:

- `distillation_2016`
- `catalog_2026_06`
- `simulator_2026_06_hidden`
- `catalog_2026_07`
- `simulator_2026_07_hidden`
- `debug_fixture`

Run outputs use paths rather than always-present configurations:

```text
runs/<run-id>/<month>/
  interactions-*.parquet
  run-manifest.yaml
  metrics.json
```

The repository is private during development. Authentication is supplied by
environment variables or Colab Secrets and never committed.

Model artifacts use separate private Hugging Face model repositories because
dataset revisions and model lineage have different schemas and lifecycles:

- `dhelmy990/babel-qwen-navigation-2016` for pilot and complete distilled
  encoder artifacts; and
- `dhelmy990/babel-two-tower-recommender` for the immutable original
  recommender and its online-trained descendants.

### Publication Protocol

1. Build resumable Parquet shards locally.
2. Validate schemas, IDs, checksums, split assignment, and hidden/observable
   separation.
3. Upload verified shards incrementally to `main`.
4. Load the uploaded configuration remotely and repeat smoke tests.
5. Record the resulting commit SHA.
6. Create an immutable release tag when a stage is complete.
7. Delete eligible local bulk files only after remote verification.

Colab may resolve `main` when a run begins, but it must immediately record and
pin the resolved commit SHA. A training run must never continue reading a
moving `main` revision.

Parquet is the canonical prepared format. Target shard sizes are approximately
256–512 MB so Colab can stream, shuffle, and resume without downloading one
monolithic object.

### Rolling Readiness

The 2016 configuration includes a machine-readable readiness manifest:

```yaml
state: building | pilot_ready | complete
schema_version: 1
teacher_dimension: 100
available_examples: 0
verified_shards: []
source_checksums: {}
```

Rules:

- Uploaded shards are append-only within a dataset version.
- Replacing a published shard requires a new version.
- Split assignment is deterministic before the complete corpus is known.
- The first pilot is a deterministic, diverse sample across the teacher
  inventory, not the first records encountered in dump order.
- A pilot run may use a `pilot_ready` commit for pipeline verification.
- The final reported distillation run begins cleanly from the pinned Qwen base
  after the configuration becomes `complete`.
- Exploratory resume against an expanded dataset is allowed only when the run
  records its full dataset-revision history and is marked non-final.

## 2016 Distillation Dataset

### Authoritative Sources

Teacher representation:

```text
Dataset: Wikipedia Navigation Vectors
DOI: 10.6084/m9.figshare.3146878.v6
File: 2016-09-01_2016-09-30_en_100.zip
Dimension: 100
Size: 727,429,988 bytes
MD5: ac70acfc41aff7a23cc9439e3bb1771f
URL: https://ndownloader.figshare.com/files/7455673
```

The current Figshare manifest exposes a 100-dimensional September English
release, not a 300-dimensional one. The experiment therefore uses 100.

Corresponding text:

```text
Snapshot: enwiki-20161001
File: enwiki-20161001-pages-articles-multistream.xml.bz2
Size: 14,178,624,372 bytes
MD5: 5df8e610829c336138dcb9191071b283
SHA1: 86ba305ecc41dafcf03ba3e67c2eacb95724d5ca
Archive: https://archive.org/details/enwiki-20161001
```

Multistream index:

```text
File: enwiki-20161001-pages-articles-multistream-index.txt.bz2
Size: 185,177,516 bytes
MD5: 7c9486cde3f9c43ff4e23443dd2323f3
SHA1: f13aebe90c8bea2157d826659e0320157a1978d9
```

October 1 is the closest clean snapshot boundary immediately following the
September 1–30 teacher period.

### Reconciliation Pipeline

```text
verify source manifests and checksums
       |
parse teacher titles and 100d vectors
       |
parse October 1 pages and redirects
       |
normalize titles without fuzzy matching
       |
resolve redirect chains to 2016 canonical page IDs
       |
extract title and lead/first useful section
       |
join text to teacher representation
       |
assign deterministic split
       |
write and validate Parquet shards
       |
incrementally upload to Hugging Face
```

Rules:

- Normalize Unicode, spaces/underscores, and MediaWiki title conventions.
- Resolve identity only with snapshot-derived pages and redirects.
- Never silently fuzzy-match an unmatched title.
- Detect redirect cycles, duplicate vectors, missing text, invalid dimensions,
  non-finite values, and ambiguous identities.
- Preserve every exclusion reason in a reconciliation report.
- Store raw float32 teacher vectors; normalize them in the training path.
- Use stable 2016 page identity as the initial `article_key` and add explicit
  cross-period mappings later.
- Assign splits by a stable hash: 98% train, 1% validation, 1% test.
- Routine tuning cannot access the test split.

Minimum row schema:

```text
article_key
page_id
canonical_title
wikidata_id
lead_text
article_text
teacher_vector[100]
teacher_norm
source_revision_id
snapshot_date
split
reconciliation_status
```

The dataset card and provenance manifest report row counts, match rate,
exclusions, text-length distributions, vector statistics, source URLs, and
checksums.

## Qwen Student Definition

Base model and tokenizer:

```text
Repository: Qwen/Qwen3-Embedding-0.6B
Revision: 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
Hidden dimension: 1024
Teacher/output dimension: 100
```

Model behavior:

- Tokenizer and model use the same pinned revision.
- Input format is `<title>`, one blank line, then the lead or first useful
  section.
- Pilot maximum length defaults to 512 tokens and supports 1024 or higher by
  configuration.
- Use the official last non-padding token pooling behavior with left padding.
- Apply `Linear(1024, 100)` as a trainable projection.
- L2-normalize after projection with a safe epsilon.
- Freeze all base parameters except selected LoRA adapters.
- Disable model caching during training.
- Enable the input-gradient behavior required by frozen-base gradient
  checkpointing.

Initial LoRA defaults:

```yaml
target_modules: [q_proj, v_proj]
rank: 16
alpha: 32
dropout: 0.05
bias: none
```

The target modules remain configurable, but expanding them is a later
experiment rather than a silent default change.

PyTorch SDPA is the default attention backend. Flash Attention is optional.

## Distillation Objective

For a batch:

```text
article text -> Qwen -> last-token pooling -> projection -> normalize
teacher vector                                      -> normalize
```

Losses:

```text
L_vector = 1 - cosine(student, teacher)

S_student = student @ student.T
S_teacher = teacher @ teacher.T
L_relational = MSE(S_student, S_teacher)

L_total = L_vector + lambda_rel * L_relational
```

Default `lambda_rel` is 0.5 and remains configurable.

Normalization, cosine loss, and similarity matrices are computed in FP32 even
when the encoder forward pass uses BF16 or FP16. NaN and invalid-norm checks
fail a batch with diagnostics rather than allowing corrupted training.

## Installable Training Package

```text
training/
  pyproject.toml
  src/babel_training/
    config.py
    data.py
    collator.py
    model.py
    pooling.py
    losses.py
    trainer.py
    checkpointing.py
    validation.py
    hub.py
  notebooks/
    train_distillation_colab.ipynb
```

Core dependencies:

- PyTorch;
- Transformers;
- PEFT;
- Accelerate;
- Datasets;
- Hugging Face Hub;
- Safetensors;
- NumPy; and
- TorchData's stateful data loader; and
- a nearest-neighbor validation dependency when exact search is no longer
  practical.

The repository will create a pinned, isolated Python environment. It will not
rely on the currently incomplete system Hugging Face CLI installation.

## Colab Runtime

The notebook is an orchestration layer, not the implementation.

It must:

1. install the repository training package and dependencies;
2. read a Hugging Face token from Colab Secrets;
3. resolve the selected dataset revision and pin its commit SHA;
4. stream paired 2016 data remotely;
5. support `debug`, `pilot`, and `complete` readiness states;
6. detect the GPU and select BF16 or FP16 safely;
7. enable gradient accumulation and gradient checkpointing;
8. expose batch size, learning rate, maximum length, training steps/epochs,
   LoRA rank, `lambda_rel`, checkpoint interval, runtime budget, and subset
   size;
9. save to Google Drive or local Colab storage;
10. resume model and streaming state; and
11. export the final distilled artifact.

T4-safe defaults use FP16, 512-token inputs, a small micro-batch, gradient
accumulation, and gradient checkpointing.

Colab termination cannot be predicted perfectly. The notebook therefore uses
frequent durable checkpoints and a configurable runtime budget rather than
claiming guaranteed checkpointing immediately before a forced termination.

## Complete Distillation Checkpoints

A restartable checkpoint contains:

- projection weights;
- LoRA adapter weights and configuration;
- optimizer and scheduler state;
- mixed-precision scaler state when applicable;
- global step and epoch/step budget;
- Python, NumPy, Torch, and CUDA RNG states;
- exact Qwen revision;
- exact Hugging Face dataset commit SHA;
- readiness state and processed shard/example position;
- stateful data-loader state;
- complete training configuration; and
- current validation metrics.

Only saving adapter weights is not a restartable training checkpoint.

## Distillation Validation

Use a fixed, leakage-free held-out validation split.

Report:

- Recall@10 and Recall@50;
- NDCG@10 and NDCG@50;
- mean cosine similarity to the teacher;
- invalid/NaN vector counts;
- embedding norm statistics; and
- structured teacher-versus-student nearest-neighbor examples.

Metric definitions:

- Exclude the query article itself.
- Recall@K is the student/teacher top-K set overlap divided by K.
- NDCG uses nonnegative teacher-cosine relevance and the student ordering.
- Exact cosine search is used for debug and pilot validation pools.
- FAISS is introduced only when exact full validation becomes impractical.
- Save/reload equivalence uses a documented numeric tolerance on the same
  runtime; cross-GPU bitwise identity is not required.

## Colab Handoff Milestone

The first formal delivery event occurs as soon as the student and a pilot shard
are training-ready.

The handoff bundle contains:

- a top-to-bottom Colab notebook;
- the installable `babel_training` package;
- exact Qwen and pilot dataset revisions;
- T4-safe defaults;
- Colab Secrets and Google Drive instructions;
- complete checkpoint/resume support;
- a smoke checkpoint produced by a real forward/backward step;
- save/reload validation evidence; and
- a concise runbook covering launch, monitoring, recovery, and outputs.

Acceptance flow:

```text
open notebook
-> select GPU runtime
-> add HF token through Colab Secrets
-> run all setup/data/model cells
-> train
-> checkpoint to Drive
-> restart or reload state
-> resume from the recorded step
```

After handoff, pilot training and the main preparation workstream proceed
independently. The main workstream does not wait for pilot completion before
finishing 2016 or preparing June and July.

A second handoff supplies the complete 2016 dataset revision and full-run
configuration. The final training run starts cleanly from the pinned Qwen base.

## June and July 2026 Environments

Use:

```text
June graph/text snapshot: enwiki-20260601
June behavior: clickstream-enwiki-2026-06.tsv.gz

July graph/text snapshot: enwiki-20260701
July behavior: clickstream-enwiki-2026-07.tsv.gz
```

The first-of-month graph paired with that month's Clickstream creates a clear
environment switch on July 1.

Monthly construction:

```text
page + redirect + page_props
          |
linktarget + pagelinks
          |
pages-articles text
          |
monthly Clickstream
          v
canonical monthly identity table
          |
     +----+----------------+
     |                     |
observable catalog     hidden environment
article IDs/text       canonical graph edges
content hashes         filtered clickstream
revision metadata      resolved archetype seeds
```

Identity rules:

- Preserve monthly English Wikipedia page ID.
- Record Wikidata QID where available.
- Build an explicit 2016 ↔ June ↔ July crosswalk.
- Detect creations, deletions, title moves, redirect changes, and ambiguous
  QID mappings.
- Never join months using raw title alone.
- Resolve redirect targets before graph and Clickstream construction.

Graph rules:

- Namespace-zero articles only.
- Resolve `pagelinks` through `linktarget`.
- Remove invalid endpoints, duplicate canonical edges, and redirect-induced
  self-loops.
- Preserve directed edges.
- Record dangling-node and transition-normalization policies.
- Keep graph data exclusively in hidden configurations.

Clickstream rules:

- Retain `type == "link"` records whose endpoints resolve to valid monthly
  articles.
- Aggregate records that canonicalize to the same edge.
- Preserve raw `n` and a documented normalized value.
- Never interpret a missing record as dislike.
- Prepare the data even though the initial simulator default is `beta = 0`.

June is uploaded and remotely verified before July preparation begins. Local
June bulk sources may then be removed.

## Synthetic Creators

`prompts/wikipedia_user_profiles.md` remains authoritative for the 20
archetypes and their four seeds.

Initial defaults:

```yaml
creators: 50
ppr_restart_probability: 0.15
dirichlet_kappa: 50
relatedness_weight_alpha: 0.60
behavioral_noise_epsilon: 0.20
history_noise: 0.10
explicit_exclusion_propensity: 0.25
clickstream_beta: 0.0
```

Creator construction:

1. Assign each creator to an authoritative archetype.
2. Sample restart weights from
   `Dirichlet(kappa * [0.40, 0.30, 0.20, 0.10])`.
3. Compute one multi-seed approximate PPR distribution.
4. Sample historical and new Babel source articles primarily from that
   distribution, mixed with configurable history noise.
5. Assign each created Babel its own ID and retain its source article key.
6. Exclude the creator's own Babels from recommendations.

A creator cannot create two Babels from the same source article. Enforce this
with sampling without replacement and a persistence uniqueness constraint on
`(creator_id, source_article_key)`. Different creators may use the same source
article. Exhausted eligible support is an explicit failure, not permission to
duplicate.

### Scaling Cohorts

Use deterministic nested creator cohorts:

```text
50 ⊂ 100 ⊂ 500 ⊂ 1,000 ⊂ 5,000 ⊂ 10,000
```

The first 50 creators remain identical in every larger cohort. Each scale run
starts from the same selected immutable model and receives its own run ID.

Compare:

- quality versus total interactions;
- quality versus interactions per creator;
- simulation and training throughput;
- Kafka consumer lag;
- CPU, GPU, memory, disk, and network use; and
- interactions and time required to reach frozen June/July quality targets.

A 50-creator calibration run selects the canonical adaptation threshold before
larger comparisons. The threshold is then frozen in all scale-run manifests.

## Hidden Relatedness and Decisions

PPR outputs retain configurable top-L sparse results. Scores are rank
percentiles within a fixed hidden result, never within the recommender's
proposed list. Current-note PPR results may be cached by monthly article ID.

For creator `u`, current Babel `a`, and candidate `c`:

```text
R(u,a,c) = 0.60 * RelatednessRank(a,c)
         + 0.40 * PreferenceRank(u,c)
```

Inclusion preserves the noise rule from `two_towers.md`:

```text
p_include = (1 - epsilon) * R + epsilon * 0.5
```

Conditional on not including:

```text
p_explicit_exclude =
  explicit_exclusion_propensity
  * ((1 - epsilon) * (1 - R) + epsilon * 0.5)
```

The remaining action is ignore. Each candidate receives exactly one action,
and several candidates in one recommendation event may be included.

All random draws derive from recorded run, creator, event, and candidate seeds.

## Hidden/Observable Boundary

Observable:

- opaque creator and Babel IDs;
- source article IDs needed to retrieve permitted text;
- creator history at recommendation time;
- new Babel text;
- candidate IDs;
- recommendation ranks and model scores;
- include/exclude/ignore actions; and
- serving model ID/version.

Hidden:

- archetype names and assignments;
- seed articles and weights;
- Dirichlet parameters and draws;
- PPR values and ranks;
- hyperlink edges and graph proximity;
- hidden relevance;
- Clickstream values; and
- simulator random draws.

Because all configurations share one private Hub repository, separation is an
application and schema boundary rather than a permission boundary. Dedicated
loader APIs and tests enforce it.

## Minimal Kafka Feedback Path

Kafka is limited to observable online feedback.

```text
simulator
    |
    v
babel.feedback.v1
    |
    v
online-training consumer
```

Initial deployment:

- one Apache Kafka KRaft container;
- one continuously running consumer;
- replication factor 1 locally;
- versioned JSON validated by a repository-owned JSON Schema;
- manual offset management; and
- an in-memory fake bus for deterministic tests.

Additional consumers are deferred. Accepted edges and metrics are reconstructed
from feedback. An explicit periodic or end-of-run export command replays a
bounded Kafka offset range into Parquet and uploads it to Hugging Face.

Kafka retention must exceed the maximum run duration plus the export safety
window.

### Feedback Event

Use one Kafka message per recommendation event so query context and all
candidate actions remain atomic.

Topic: `babel.feedback.v1`

Key: `creator_id`

Minimum schema:

```json
{
  "schemaVersion": 1,
  "eventId": "...",
  "runId": "...",
  "recommendationEventId": "...",
  "environmentMonth": "2026-06",
  "creatorId": "...",
  "newBabelId": "...",
  "newSourceArticleKey": "...",
  "historyBabelIds": ["..."],
  "servingModelId": "...",
  "servingModelVersion": 14,
  "candidates": [
    {
      "babelId": "...",
      "sourceArticleKey": "...",
      "rank": 1,
      "modelScore": 0.73,
      "action": "include"
    }
  ],
  "simulatedAt": "...",
  "simulationStep": 18400
}
```

The history snapshot is included because asynchronous training must reproduce
the context that existed when serving generated the candidates.

Forbidden event fields include hidden relevance, PPR values/ranks, archetype
identity, seed articles, graph proximity, Clickstream values, and random draws.

### Online Ranking Updates

- Pair included candidates against explicitly excluded and ignored candidates
  from the same event.
- Explicit exclusions receive full negative weight.
- Ignores default to negative weight 0.25.
- Multiple includes produce multiple valid pairs.
- Events with no included candidate remain archived and visible but do not
  produce a direct pairwise update.
- In-batch negatives may supplement events with valid positives.
- The distilled content encoder remains frozen during fast online updates.

### Kafka and Trainer Recovery

- Disable automatic offset commits.
- Save model, optimizer, scheduler, RNG, data, and processed-offset state
  atomically.
- Commit Kafka offsets only after the corresponding checkpoint is durable.
- Restart by restoring the checkpoint and seeking to its offsets.
- Serving synchronization uses a direct atomic model-state interface.
- Kafka never transports model weights.
- Graceful stop captures partition high-water marks, drains through them,
  checkpoints, and exports exactly that offset range.

## Dashboard Control Plane

Offline Qwen distillation is launched manually in Colab. Every synthetic
read/write/recommend experiment is launched from the admin dashboard.

```text
Admin dashboard
      |
      v
Experiment control API
      |
      v
persist immutable run configuration
      |
      +--> construct selected creator cohort
      +--> start simulator/recommender loop
      +--> publish observable feedback to Kafka
      +--> start online trainer
      |
      v
status and logs returned to dashboard
```

Launch parameters:

- pinned Hugging Face dataset revision;
- selected immutable starting model;
- creator count, default 50;
- June-only or June→July run;
- event budget;
- RNG seed;
- PPR, noise, Clickstream, and synchronization settings; and
- Kafka run/topic information.

Initial controls are **Start** and **Graceful stop**. Pause/resume is deferred.

Lifecycle:

```text
starting
-> running
-> stop_requested
-> draining_feedback
-> checkpointing
-> exporting_interactions
-> completed
```

A failure produces `failed` or `interrupted` state with an actionable error and
the last restartable checkpoint.

### Dashboard Logs

The primary dashboard log explains the synthetic online experiment:

```text
Creator 017 created "Virtual memory"
Recommended: Paging, Linux, Memory management
Included: Paging, Memory management
Excluded: Linux
Edges created: 2
Feedback trained: 3 | Kafka lag: 12 | serving model: v14
```

Views:

1. synthetic activity timeline: creator, new Babel, candidates, actions, and
   accepted edges;
2. online health: event counts/rates, offsets, lag, rolling ranking loss,
   checkpoint, training version, and serving version; and
3. run summary: unique Babels, action distribution, accepted edges, resource
   use, synchronization history, stop/export progress, and errors.

Accepted edges require no graph consumer. The live view derives them from
include actions, and durable evaluation reconstructs them from archived
feedback.

Vector and relational distillation losses never appear here. Those belong to
the separate Colab job.

Structured dashboard records are rate-limited and persisted. Hidden simulator
values are never logged.

## Immutable Model Registry

The original model is the immutable recommender initialized with the complete
2016-distilled Qwen encoder. It is never overwritten or deleted by an online
run.

Every run creates a child artifact:

```text
Original 2016 baseline
    |
    +-- June run 001 final
    |       |
    |       +-- July continuation final
    |
    +-- 50-creator experiment 002 final
    |
    +-- 500-creator experiment 003 final
```

The dashboard can start from the original or any compatible completed child.
Selecting a model creates a run-scoped working copy. Training-serving
synchronization modifies only that working state. Graceful stop saves a new
immutable child.

Model metadata:

- stable ID and label;
- parent model ID;
- producing run ID;
- creation time;
- exact distilled encoder artifact;
- dataset revisions and environment month;
- creator and event counts;
- training configuration and final metrics;
- checkpoint location and checksum; and
- embedding/index compatibility version.

Incompatible models remain visible but cannot be selected for an unsafe
configuration. There is no destructive promotion operation.

### Original Recommender Initialization

The immutable original must be useful before it receives online feedback; its
creator-context path cannot begin as an unconstrained random mapping.

Its item tower contains:

- the complete 2016-distilled Qwen encoder and 1024→100 projection;
- zero-initialized item residuals; and
- a candidate ANN index built from compatible 100-dimensional catalog
  embeddings.

Its creator-context tower starts in a deterministic, representation-compatible
state:

- history attention initially behaves as scaled dot-product attention between
  normalized historical Babel embeddings and the new-Babel embedding;
- the fusion from `[new_babel, attended_history]` to the 100-dimensional query
  is initialized to an equal-weight average with zero bias; and
- the attention and fusion parameters remain trainable through online ranking
  feedback.

This initialization gives the original a meaningful content/history ranking
without exposing hidden graph structure or pretraining it on future behavior.
The exact initialization tensors, context-tower architecture, and ANN build
manifest are stored with the original artifact.

## Synchronous Recommendation Request

Every synthetic event makes a synchronous request to the serving recommender.

```text
simulator
   |
   | POST /api/v1/recommendations
   | creator + new Babel + observable history + K
   v
serving recommender
   |
   | Qwen encoding
   | creator-context tower
   | ANN retrieval
   | candidate filtering
   v
candidate Babels + scores + model version + timings
   |
   v
simulator decisions -> Kafka feedback
```

Kafka is outside the serving critical path.

Request measurements:

- request and run IDs;
- serving model ID/version;
- client-observed end-to-end latency;
- server queue time;
- tokenization time;
- new-Babel Qwen encoding time;
- history lookup time;
- context-tower time;
- ANN retrieval time;
- candidate-filtering time;
- inferred serialization/network overhead;
- status and timeout; and
- candidate count.

Durations use monotonic clocks. Server timing is returned as structured
metadata or a `Server-Timing` header.

## Performance Experiment

Instrumentation is built into the online vertical slice. Controlled load and
resource-interference work is a later slice.

Conditions:

1. serving only;
2. serving while the online trainer consumes Kafka;
3. serving during training and synchronization; and
4. increasing request rates and nested creator cohorts.

Report:

- p50, p95, p99, and maximum recommendation latency;
- requests per second;
- timeout/error rate;
- per-stage timing;
- online-training step time;
- Kafka lag;
- synchronization spikes;
- CPU/GPU, memory, disk, and network use; and
- `p95(serving + training) / p95(serving only)`.

Serving and training run as separate processes. Serving has priority. Training
uses bounded micro-batches and may be rate-limited when a configured serving
latency threshold is exceeded.

The separate 2016 Colab job runs elsewhere and does not count as local online
training interference.

## Reliability and Error Handling

- A source checksum mismatch stops parsing that source.
- Invalid/unmatched records enter explicit reports and are never silently
  repaired.
- A missing private-Hub token fails before work begins.
- Kafka unavailability pauses simulation; decisions must not be created if
  they cannot be durably published.
- Trainer failure leaves serving at the last synchronized version while Kafka
  buffers feedback.
- Graceful stop drains only through captured high-water marks.
- Creator/source duplicates fail at both sampling and persistence boundaries.
- Incompatible embedding/index versions cannot be synchronized or served.
- Remote deletion and overwrite operations are outside automated data
  publication; releases are additive and immutable once tagged.

## Verification Strategy

Unit tests:

- title and redirect reconciliation;
- deterministic split assignment;
- teacher-vector parsing and validation;
- Qwen pooling and projection dimensions;
- direct and relational losses;
- creator source-article uniqueness;
- deterministic profile/PPR/action sampling;
- hidden/observable schemas;
- feedback event validation; and
- immutable model lineage.

Training tests:

- finite forward/backward pass;
- tiny overfit with decreasing loss;
- projection output exactly 100d;
- gradients only on projection and LoRA parameters;
- checkpoint round trip with validation equivalence; and
- streamed resume from the recorded example.

Integration tests:

- remote loading from a pinned private-Hub commit;
- incremental upload followed by checksum verification;
- Kafka produce, train, checkpoint, restart, replay, and drain;
- dashboard model selection, start, logs, and graceful stop;
- accepted-edge reconstruction; and
- synchronous recommendation timing fields.

End-to-end fixture:

- tiny 2016 distillation set;
- two monthly hidden graphs and catalogs;
- a small creator cohort;
- one June→July run;
- Kafka feedback and online update;
- serving synchronization;
- immutable child artifact; and
- metrics and graph reconstruction.

## Provenance

One machine-readable manifest records:

- exact URLs, sizes, checksums, and dates for every source;
- Qwen revision and tokenizer revision;
- Hugging Face dataset commit and tag;
- schema and preprocessing-code versions;
- reconciliation and exclusion counts;
- split rules;
- monthly graph and Clickstream choices;
- simulator configuration;
- run RNG seeds;
- starting/ending model IDs;
- Kafka partition offset ranges; and
- exported artifact checksums.

## Resolved Document and Codebase Gaps

1. All requested data, training, Kafka, simulator, and serving components are
   absent from the current implementation.
2. The backend seeding dashboard exists only as a design and plan and
   explicitly defers training, Kafka, PPR, and recommendation.
3. Its 20 generated profiles are archetypes, while online experiments launch
   50 creator instances by default.
4. The dashboard design must be extended so synthetic experiments always
   launch through its control plane.
5. Kafka is no longer a blanket non-goal; its only initial role is observable
   feedback transport.
6. The verified teacher dimension is 100.
7. `architecture.md` and the refactored instructions use different alpha
   conventions. This design fixes alpha at 0.60 on current-note relatedness.
8. `two_towers.md` specifies only binary inclusion noise. This design defines
   include, explicit exclude, and ignore probabilities.
9. The mandatory-baseline language conflicts with the no-ablation instruction.
   Model-component ablations are not part of the initial implementation;
   serving/training load conditions remain required systems comparisons.
10. Exact snapshots, cross-period identity, duplicate prevention, model
    lineage, remote-only downstream reads, and Kafka recovery were previously
    underspecified.
11. Section 27 of the refactored instruction has a broken Markdown fence.
12. The repository has no Python package or tests, and the installed Hugging
    Face CLI is incomplete because a dependency is missing.
13. `documentation.md` says root `app.js` is empty although the legacy file is
    populated.

These conflicts must be corrected or explicitly superseded as the relevant
slice is implemented. The implementation must not leave several contradictory
documents appearing equally authoritative.

## Implementation Slices

### Slice 1 — Immediate Colab Handoff

Deliver:

- acquisition manifests and 2016 pilot preparation;
- incrementally uploaded `pilot_ready` revision;
- installable training package;
- frozen Qwen student definition;
- loss and validation packages;
- complete checkpoint/resume;
- Colab notebook and runbook; and
- handoff acceptance evidence.

This is the first implementation plan and the first execution target.

### Slice 2 — Dataset Completion

Deliver:

- complete 2016 reconciliation and release;
- complete-run Colab handoff;
- June observable catalog and hidden environment;
- July observable catalog and hidden environment;
- cross-period identity tables; and
- full provenance and validation reports.

### Slice 3 — Online Vertical Slice

Deliver:

- extended admin dashboard and experiment control API;
- default 50-creator deterministic cohort;
- synchronous recommendation POST endpoint;
- serving two-tower model and ANN retrieval;
- simulator decisions and accepted-edge view;
- minimal Kafka feedback path;
- one online-training consumer;
- training-serving synchronization;
- immutable model registry; and
- basic latency and health instrumentation.

### Slice 4 — Performance and Scaling

Deliver:

- controlled serving-only and serving-plus-training experiments;
- nested creator scale ladder;
- configurable request-load generation;
- latency-aware training backpressure;
- resource and synchronization analysis; and
- reproducible comparative reports.

After this specification is reviewed, create one detailed implementation plan
for each slice. The plans must state their dependencies and stable contracts so
each can later be executed with `subagent-driven-development` without reopening
the architecture.

## Non-Goals

- Full-model Qwen fine-tuning for the pilot.
- Raw Wikipedia reads in downstream training or simulation.
- Kafka in offline distillation, synchronous serving, or model transfer.
- Multiple always-running Kafka consumers in the initial online slice.
- A graph, metrics, or archival daemon in the initial online slice.
- A production-high-availability Kafka cluster.
- Exposing hidden simulator state to the recommender or dashboard logs.
- Mutating or replacing the original 2016-initialized recommender.
- Model-component ablations in the initial experiment.
- Full Monolith-scale infrastructure, Flink, or a distributed parameter server.
- Performance load generation before the online vertical slice works.

## Success Criteria

The program succeeds when:

1. a user can open the handed-off Colab notebook, authenticate through Colab
   Secrets, train the pinned Qwen student on a pinned pilot dataset, save a
   complete checkpoint, and resume it;
2. the complete 2016 representation and June/July environments are available
   as validated configurations in the private Hugging Face repository;
3. the admin dashboard can launch and gracefully stop a reproducible synthetic
   experiment from either the immutable original model or a compatible saved
   child;
4. each synchronous serving request returns candidates and measurable stage
   latency before feedback is published asynchronously;
5. Kafka feedback can be replayed from model-checkpoint offsets without hidden
   data leakage;
6. included recommendations reconstruct accepted graph edges;
7. June training continues into July without resetting the canonical temporal
   run; and
8. controlled scale experiments quantify quality, adaptation time, throughput,
   Kafka lag, synchronization effects, and serving-latency degradation.
