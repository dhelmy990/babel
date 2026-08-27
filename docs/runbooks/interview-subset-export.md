# Frozen 2016 Interview Subset Export

This runbook publishes `distillation_2016_interview` without interrupting the
active complete-corpus extractor. The configuration contains exactly 50,000
train, 5,000 validation, and 5,000 test rows. Its 1,000-row smoke population is
the prefix of the ordered train selection, not another split.

## Safety boundary

- Never signal, pause, restart, or reconfigure `build-complete-2016`.
- Never back up, copy, checkpoint, or write to its reconciliation database.
- The exporter uses `mode=ro`, connection-local `query_only`, page-ID keyset
  reads of at most 5,000 identities, and a fresh closed connection per batch.
- A frozen frontier is accepted only at a committed selected-text journal
  boundary. A recount at or below its maximum page ID must remain exact.
- Prepared bulk data belongs under `/home/dhelmy990/Data/babel-data`, not in the
  repository.
- Publication credentials are read only from
  `/home/dhelmy990/Code/babel/.env`; do not pass a token on the command line.

## Export

Run from the committed implementation worktree and pin that exact commit:

```bash
data_pipeline/.venv/bin/python -m babel_data.cli export-interview-2016 \
  --database /home/dhelmy990/Data/babel-data/full-2016-work/1a319328641844e29537/reconcile.sqlite3 \
  --output-root /home/dhelmy990/Data/babel-data/prepared/2016-interview-50k \
  --code-commit "$(git rev-parse HEAD)"
```

The status JSON contains only paths, counts, checksums, and frontier evidence.
It never contains row text, vectors, or credentials. Preserve the emitted
frontier count, maximum page ID, selected-text journal row, database identity,
and ordered selection checksums in the training handoff.

## Publish and verify

```bash
data_pipeline/.venv/bin/python -m babel_data.cli publish-interview-2016 \
  --repo dhelmy990/babel-wikipedia-experiment \
  --input-root /home/dhelmy990/Data/babel-data/prepared/2016-interview-50k \
  --revision-out /home/dhelmy990/Data/babel-data/receipts/interview-2016-revision.txt
```

Publication is one parent-pinned commit to `main`. Interview configuration
paths are immutable: an existing nonidentical path aborts publication. The
existing `distillation_2016/manifest.json` must be byte-identical before and
after the commit. The publisher then authenticates at the returned exact SHA,
checks every published byte, and streams one schema-validated row from each
split with `name="distillation_2016_interview"`.

The revision file is created atomically with mode `0600` and never overwritten.
The dataset card declares both `distillation_2016` and
`distillation_2016_interview`; later complete-release activation upgrades a
legacy locally rendered card before publication so the interview declaration
survives.

## Training boundary

Do not launch Qwen training from this runbook. Hand off the exact Hub SHA,
configuration, seed `babel-interview-2016-v1`, selection checksums, and frozen
frontier evidence to the independent training agent. Use max length 384, a
1,000-row smoke run, one epoch over the fixed 50,000 train rows, exact fixed
5,000-row validation, and untouched 5,000-row test.
