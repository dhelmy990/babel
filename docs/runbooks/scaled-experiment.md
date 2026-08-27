# Scaled Recommendation Architecture Experiment

This runbook covers the dashboard-first engineering experiment. Its purpose is
to compare recommendation serving while online training is colocated,
process-separated, and resource-isolated on the same host. A server means an
independently running service; separate physical machines are not required.

## Accepted inputs

| Input | Immutable identity |
|---|---|
| Trained Qwen adapter/projection | `dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e` |
| Artifact directory | `artifacts/3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8` |
| Qwen base/tokenizer | `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| June/July environment | `dhelmy990/babel-wikipedia-experiment@0d1ab2c7f0e2295682288fcf10077d2d776bf559` |
| Environment configuration | `crosswalk_2026_06_07` |

The monthly release contains 5,000 real observable articles per month: 4,000
shared identities and 1,000 month-specific identities. Hidden pagelinks and
Clickstream remain simulator-only. The formal population is 10,000
synthetic-created Babels; only those Babels enter pgvector and the candidate
universe.

Use `/home/dhelmy990/Data/babel-data` for caches and bulk outputs. Keep only
code, manifests, reports, and small receipts in the repository. `HF_TOKEN`
stays in process memory and is never sent to the dashboard.

## Start the default split

Install the real-model and systems dependencies:

```bash
docker compose up -d postgres kafka
uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
uv pip install --python online/.venv/bin/python -e benchmark
```

Source the private token without printing it and set the exact pins:

```bash
set -a
source /home/dhelmy990/Code/babel/.env
set +a
export BABEL_DATA_ROOT='/home/dhelmy990/Data/babel-data'
export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
export BABEL_KAFKA_BOOTSTRAP_SERVERS='127.0.0.1:29092'
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_CONFIG='crosswalk_2026_06_07'
export BABEL_ONLINE_DATASET_REVISION='0d1ab2c7f0e2295682288fcf10077d2d776bf559'
export BABEL_ONLINE_MODEL_MODE='real_qwen'
export BABEL_ONLINE_QWEN_DEVICE='cpu'
export BABEL_RUNTIME_TOPOLOGY='same_host_split'
export BABEL_ONLINE_WORKER_TOKEN="$(openssl rand -hex 32)"
export PATH="$PWD/online/.venv/bin:$PATH"
```

Start `babel-online supervise` and `just start` in separate terminals with the
same environment and worker token. The supervisor exposes only a loopback
control boundary. The dashboard at `http://127.0.0.1:8787/admin` remains the
only supported operator launch surface.

## Controls and evidence

The scalability panel defaults to split services, real Qwen, pgvector, 50
creators, 10,000 created/indexed Babels, 50 concurrent users, 0.40 independent
start and continuation probabilities, depth 2, cap 10, and interleaving on.
Seeded articles, created Babels, and concurrent users have linked sliders and
numeric custom inputs. Warmup, duration, RPS, p95 safety threshold, trainer
micro-batch, and synchronization interval are also frozen into each saved
trial.

Population is a separate phase. The formal gate requires the exact target row
count, real model repository/revision/checksum, dataset
repository/revision/checksum, and vector-snapshot checksum. The dashboard never
auto-starts the nine conditions. Review the receipt and press **Approve formal
measurements**.

The persisted progress view shows phase, condition, configured totals,
seeded/created/indexed/requested/completed counts, elapsed time, rate, ETA, and
draining. Trial detail also retains topology/placement, resource limits,
request stages, graph/walk/cache evidence, Kafka/trainer health, model-version
staleness, synchronization, activation spikes, all three interference ratios,
and immutable artifact identity.

Use **Graceful stop** to stop new requests, drain captured Kafka offsets,
checkpoint, and preserve serving. The original model and every compatible child
remain immutable and separately selectable. A later run may start from either
the original or a selected child.

## Result labels

A fixture-sized three-topology by three-condition run is a smoke test. It proves
orchestration, counters, edges, persistence, and cleanup only. Never report its
latencies as scaling results.

A formal result uses the accepted 10,000-vector population and concurrent
schedule. For each topology it records serving-only, serving plus training, and
serving plus training and activation. The dashboard reports:

```text
Itraining = p95(training, no activation) / p95(serving only)
Ifull = p95(training + activation) / p95(serving only)
IActivationIncrement = p95(training + activation) / p95(training, no activation)
```

Run 50 creators first. Advance to 100 and then 500 only with explicit operator
approval. Keep the pgvector-versus-hnswlib snapshot comparison separate from
topology conclusions.

## Build and publish an accepted run bundle

The controlled run must first produce nonempty `feedback.parquet`,
`edges.parquet`, `requests.parquet`, and `resources.parquet`, plus
`summary.json`, `report.md`, the selected `model-manifest.json`, and the child's
complete reusable `model-artifact/` directory. The model directory must contain
`state-descriptor.json`, its referenced online serving state/checkpoint, and
matching checksums. The bundle builder adds `manifest.json` and
`checksums.json`, including persisted progress, topology/placement, hardware,
original/child model ledger, and 100-dimensional vector-snapshot evidence.

Use `FeedbackExport.publication_files()` for the canonical feedback and edge
inputs. The bundle's model ledger records the selected immutable original,
child lineage, and any already-returned model commits. Then call
`babel_benchmark.hub.build_run_bundle(...)` with the accepted measurement
paths. Do not substitute smoke output for formal evidence.

Publish only after the run's final checks have passed:

```python
import os
from huggingface_hub import HfApi
from babel_benchmark.hub import publish_run_bundle

receipt = publish_run_bundle(
    HfApi(),
    bundle,
    repo_id="dhelmy990/babel-wikipedia-experiment",
    token=os.environ["HF_TOKEN"],
)
print(receipt.commit_sha, receipt.bundle_path)
```

Publication performs one operator commit beneath `runs/<run-id>/`; it does not
implement multi-writer compare-and-swap. It refuses a pre-existing remote run
path and scans candidate files for credential markers. At the returned commit
it reloads `manifest.json`, `checksums.json`, `summary.json`,
`model-manifest.json`, the child descriptor and every referenced serving-state
file, and one row from every required Parquet file, validating all recorded
checksums. Record the returned commit/path in the saved dashboard trial
atomically only after this reload succeeds.

After the verified receipt returns, record the child with
`ModelRegistry.record_publication(...)`, using the receipt repository, commit,
and `runs/<run-id>/model-manifest.json`. `publication_ledger()` then retains the
returned original/child commit lineage without replacing either selectable
manifest.

Do not publish a final bundle merely because these helpers pass unit tests. A
real controlled run, durable child, durable directed edges, and zero final
Kafka lag are still required evidence.

## Shutdown and recovery

Stop from the dashboard first. After the trial reaches a terminal state, stop
the backend and supervisor with Ctrl-C. Kafka and PostgreSQL may remain running
for the next scale. A trainer failure must leave serving available on its last
valid model; restarting the trainer resumes from committed offsets. A failed
activation keeps the prior child and original intact.

Only consider `cross_host` after same-host isolation evidence shows unresolved
interference. Add a true parameter server only if immutable checkpoint
activation itself becomes the measured bottleneck.
