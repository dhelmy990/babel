# Scaled Recommendation Experiment Handoff

This is the operator handoff for the real engineering-scale experiment. The
dashboard controls the run; the loopback performance worker builds the real
Qwen population and executes the formal matrix. PostgreSQL/pgvector is the
durable vector store and Kafka is the feedback path. Neither the 80-row fixture
nor the deterministic encoder is valid formal evidence.

The formal training conditions must run from a revision containing the real
PyTorch online head. The head consumes frozen 100-dimensional Qwen vectors,
trains creator-context attention/fusion plus touched-item residuals in event
micro-batches, and checkpoints its optimizer/scheduler and Kafka offsets.
Serving activation must load the same context tensors together with the
materialized child vectors; a run that still constructs `NumpyWorkingModel` is
plumbing evidence only.

> **Active-run warning:** trial
> `ce8e54ff-e317-4a89-b7db-90327e02dc43` is already building its population.
> Do not rerun the launch commands, regenerate its worker token, or restart its
> backend/worker. The commands below are the reproducible launch procedure for a
> fresh process or later trial.

## Immutable identities

| Item | Pinned identity |
|---|---|
| Active 50-creator trial | `ce8e54ff-e317-4a89-b7db-90327e02dc43` |
| Starting model ID | `2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67` |
| Trained model | `dhelmy990/babel-qwen-navigation-2016-interview@57d949cd634b920cc1a46f27c9b21df094b5240e` |
| Artifact ID | `3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8` |
| Qwen base/tokenizer | `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| June/July dataset | `dhelmy990/babel-wikipedia-experiment@0d1ab2c7f0e2295682288fcf10077d2d776bf559` |
| Dataset configuration | `crosswalk_2026_06_07` |

Never replace a commit with `main`. Keep `HF_TOKEN` in process memory: do not
print it, paste it into the dashboard, or commit it. Source the existing
`/home/dhelmy990/Code/babel/.env` only into the local operator environment.

## Start the services

From the integration worktree:

```bash
cd /home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
docker compose up -d postgres kafka
uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
uv pip install --python online/.venv/bin/python -e benchmark

set -a
source /home/dhelmy990/Code/babel/.env
set +a

export BABEL_DATABASE_URL='postgresql://babel:babel-local-dev@127.0.0.1:54329/babel'
export BABEL_KAFKA_BOOTSTRAP_SERVERS='127.0.0.1:29092'
export BABEL_ONLINE_DATASET_REPOSITORY='dhelmy990/babel-wikipedia-experiment'
export BABEL_ONLINE_DATASET_CONFIG='crosswalk_2026_06_07'
export BABEL_ONLINE_DATASET_REVISION='0d1ab2c7f0e2295682288fcf10077d2d776bf559'
export BABEL_ONLINE_MODEL_MODE='real_qwen'
export BABEL_ONLINE_QWEN_DEVICE='cpu'
export BABEL_RUNTIME_TOPOLOGY='same_host_split'
export BABEL_PERFORMANCE_STATE_ROOT='/home/dhelmy990/Data/babel-data/state/performance'
export BABEL_ONLINE_HF_CACHE='/home/dhelmy990/Data/babel-data/hf-cache/monthly-2026'
export BABEL_ONLINE_MODEL_ARTIFACT_CACHE='/home/dhelmy990/.cache/huggingface/hub'
export BABEL_ONLINE_QWEN_CACHE='/home/dhelmy990/.cache/huggingface/hub'
export BABEL_PERFORMANCE_WORKER_TOKEN="$(openssl rand -hex 32)"
export PATH="$PWD/online/.venv/bin:$PATH"
```

Use `BABEL_ONLINE_QWEN_DEVICE=cuda` only after verifying the local CUDA
runtime. The backend and worker must inherit the same 64-hex performance token.
Do not regenerate it in the second terminal.

Start this in terminal 1:

```bash
babel-online performance-worker
```

Start the backend/dashboard with the same exports in terminal 2:

```bash
just start
```

Open `http://127.0.0.1:8787/admin`. The worker listens only on
`127.0.0.1:8792`.

## Operate the trial

1. Start or load the saved trial from **Scalability trial**. Keep the immutable
   Qwen original, the pinned June/July release, `same_host_split`, and
   `pgvector` selected.
2. The formal defaults are 50 creators, 5,000 June plus 5,000 July identities,
   10,000 created/indexed Babels, 50 concurrent users, independent 0.40 start
   and continuation draws, traversal depth 2, cap 10, and interleaving on.
3. Wait for population status `population_ready`. Confirm exactly 10,000
   distinct 100-dimensional vectors and matching model, dataset, population,
   and vector-snapshot checksums.
4. Population completion does **not** start measurements. Inspect its receipt,
   then press **Approve formal measurements** once.
5. Watch phase, condition `n/9`, requested/completed counts, elapsed time, rate,
   ETA, Kafka lag, trainer progress, synchronization, activation, and resource
   measurements. The independent progress panel may fail without stopping the
   trial.
6. When complete, inspect all three dashboard ratios for each topology:
   `Itraining = T/S`, `Ifull = F/S`, and `IActivationIncrement = F/T`.

The formal matrix always runs sequentially in this order:

| # | Topology | Load mode |
|---:|---|---|
| 1 | `same_process` | serving only |
| 2 | `same_process` | serving + training, no activation |
| 3 | `same_process` | serving + training + activation |
| 4 | `same_host_split` | serving only |
| 5 | `same_host_split` | serving + training, no activation |
| 6 | `same_host_split` | serving + training + activation |
| 7 | `same_host_isolated` | serving only |
| 8 | `same_host_isolated` | serving + training, no activation |
| 9 | `same_host_isolated` | serving + training + activation |

Condition 6 is the default child to publish and select for the next run. The
original remains immutable and separately selectable.

## Export, publish, and attach

For cohort 50, run this only after all nine conditions are durably `completed`:

```bash
TRIAL_ID='ce8e54ff-e317-4a89-b7db-90327e02dc43'
PERF_ROOT="$BABEL_PERFORMANCE_STATE_ROOT"
RUN_ROOT="/home/dhelmy990/Data/babel-data/runs/$TRIAL_ID"
EXPORT_ROOT="$RUN_ROOT/export"
HANDOFF_ROOT="$RUN_ROOT/handoff"
ACCEPTED_ROOT="$RUN_ROOT/accepted"

babel-online performance-export \
  --experiment-id "$TRIAL_ID" \
  --evidence-root "$PERF_ROOT/$TRIAL_ID/conditions" \
  --output-root "$EXPORT_ROOT" \
  --selected-condition-index 6 \
  --bundle-inputs "$HANDOFF_ROOT/trial-bundle-inputs.json"

babel-friday-benchmark trial-bundle-build \
  --output-root "$ACCEPTED_ROOT" \
  --inputs "$HANDOFF_ROOT/trial-bundle-inputs.json"

babel-friday-benchmark trial-bundle-publish \
  --bundle-root "$ACCEPTED_ROOT/runs/$TRIAL_ID" \
  --repo-id 'dhelmy990/babel-wikipedia-experiment' \
  > "$HANDOFF_ROOT/publication-receipt.json"
```

The publication command verifies the remote bundle at its returned immutable
commit. Do not attach an unverified or smoke receipt. Capture the dashboard
nonce from the currently running local `/admin` page into the environment as
`BABEL_ADMIN_NONCE` without printing it, then attach the verified receipt:

```bash
babel-friday-benchmark trial-bundle-attach \
  --receipt "$HANDOFF_ROOT/publication-receipt.json" \
  --trial-id "$TRIAL_ID"
```

The backend accepts only `runs/<TRIAL_ID>` for that saved trial. Reload the
dashboard and confirm that the remote commit/path and condition-6 child are
visible while the original model remains selectable.

The same commands publish cohorts 100 and 500 after their six conditions are
complete. Keep `--selected-condition-index 6`: in the higher-cohort order this
is `same_host_split` with training and activation. The generated handoff and
accepted manifest bind the creator cohort, condition count, ordered six
condition/run pairs, population/model pins, feedback/edge checksums, and the
selected child. Publication still uses `runs/<TRIAL_ID>`, and attachment still
accepts only that exact saved trial.

For the already-running trial, wait until it is durably `completed` before
stopping any process. Then rebuild/restart the backend from the current branch
so its artifact-attachment route includes the latest code, capture the new
per-process admin nonce, and run the attach command. This terminal-state restart
does not alter the completed trial or published bundle.

## Stop and recovery boundary

Use **Graceful stop** for an ordinary stop. It stops new work and preserves the
last valid serving state and evidence already written.

Do not restart or kill the performance worker, backend, PostgreSQL, or Kafka
during population or the formal matrix. The current implementation does **not**
support resuming an interrupted formal matrix in place. A stop or failure after
the matrix begins leaves that trial interrupted/failed; preserve its evidence
and create a new formal trial. Do not reuse its condition IDs or present a
partial matrix as a completed result.

See `docs/runbooks/scaled-experiment.md` for the validation and troubleshooting
details.
