# Scaled Recommendation Experiment Handoff

This handoff runs the engineering experiment from the dashboard. It uses the
real distilled Qwen encoder, real June/July engineering snapshots, pgvector,
Kafka feedback, and independently selectable immutable model children. It does
not use the old 80-row fixture or deterministic encoder.

## Fixed identities

- Model repository: `dhelmy990/babel-qwen-navigation-2016-interview`
- Model commit: `57d949cd634b920cc1a46f27c9b21df094b5240e`
- Artifact ID: `3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8`
- Base Qwen revision: `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`
- Dataset repository: `dhelmy990/babel-wikipedia-experiment`
- June/July release commit: `0d1ab2c7f0e2295682288fcf10077d2d776bf559`
- Dataset configuration: `crosswalk_2026_06_07`
- External data root: `/home/dhelmy990/Data/babel-data`

Never replace a pin with `main`. Never paste or print `HF_TOKEN`. Source the
existing `/home/dhelmy990/Code/babel/.env` only into the local process
environment.

## Prepare services and packages

```bash
cd /home/dhelmy990/.config/superpowers/worktrees/babel/slices-1-2
docker compose up -d postgres kafka
uv sync --project online --extra dev --extra kafka --extra parquet --extra pgvector --extra qwen
uv pip install --python online/.venv/bin/python -e benchmark
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

Use `BABEL_ONLINE_QWEN_DEVICE=cuda` only when the local CUDA runtime is known to
work. Keep the same randomly generated worker token in the worker and backend
shells; do not display it.

Start the supervisor in terminal 1:

```bash
babel-online supervise
```

In terminal 2, repeat the environment exports with the same worker token, then
run:

```bash
just start
```

Open `http://127.0.0.1:8787/admin`.

## Dashboard procedure

1. In **Scalability trial**, keep `same_host_split`, the immutable Qwen
   original, release `0d1ab2…`, and pgvector for the first formal trial.
2. Defaults are 50 creators, 10,000 target created Babels, 10,000 seeded source
   identities across the two monthly catalogs, 50 concurrent users, independent
   0.40 start/continuation draws, traversal depth 2, cap 10, and interleaving on.
3. Each population control has a safe slider and a numeric custom input. Custom
   values may exceed the slider range but remain server-validated.
4. Save the configuration. Population must reach exactly 10,000 created and
   indexed 100-dimensional vectors with matching model, dataset, and snapshot
   checksums. Reaching the threshold does not start measurements.
5. Inspect the population evidence, then explicitly approve the condition
   matrix. It never auto-advances.
6. Watch the independent progress panel: phase, condition `n/9`, seeded,
   created, indexed, requested, completed, elapsed time, recent rate, ETA, and
   draining state. A progress-display failure does not stop the run.
7. Watch placement/resources, request stages, walk/edge/cache counters, Kafka
   lag, trainer loss/steps, checkpoint/synchronization, model staleness, and
   activation spikes.
8. **Graceful stop** stops new work, drains captured feedback, checkpoints, and
   retains the last valid serving model. Do not kill the trainer to perform an
   ordinary stop.
9. Reload the saved trial from the dashboard and inspect `Itraining = T/S`,
   `Ifull = F/S`, and `IActivationIncrement = F/T` plus the raw-artifact link.

The original model is never replaced. A completed compatible child appears as
a separate model choice. Select it on a later run to test continued online
adaptation; select the original to reset the experimental lineage.

## Smoke, formal, and next scale

- A tiny 3-by-3 run is a wiring smoke only. Its timings are not a performance
  conclusion.
- A formal run requires the accepted 10,000-vector population, concurrent
  workload, complete resource/request evidence, explicit approval, and saved
  raw results.
- Run 50 creators first. Approve 100 or 500 only after the 50-creator result is
  complete and healthy. Cross-host is optional evidence expansion, not a
  prerequisite.

## Publication gate

Do not upload or label a final run bundle until the controlled scale run has
produced real feedback, directed accepted edges, requests, resources, summary,
report, model manifest, reusable child state descriptor/checkpoint, population
evidence, and zero-final-lag receipt. The
publication helper uploads one immutable `runs/<run-id>/` commit, reloads all
JSON evidence, every checksummed model-state file, and one row from every
Parquet file at the returned commit, and rejects secrets or an existing
accepted path.

Full operational details are in
`docs/runbooks/scaled-experiment.md`.
