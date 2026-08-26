# Interview Subset Early Export Design

**Date:** 2026-08-26

## Purpose

Publish a real, training-ready 2016 subset immediately from rows already
committed by the active complete extractor. This is a deadline accelerator, not
a replacement for the complete `distillation_2016` release. The active
`build-complete-2016` process must not be stopped, restarted, modified, or made
to wait for the export.

## Dataset Contract

Publish one additional private Hugging Face configuration in
`dhelmy990/babel-wikipedia-experiment` on `main`:

```text
distillation_2016_interview
  train:      50,000
  validation:  5,000
  test:        5,000
```

Rows retain the `distillation-example-v1` content contract, including real
Wikipedia text and finite 100-dimensional teacher vectors. The configuration
is separate from `distillation_2016`; it never overwrites the pilot, complete
release, source mirrors, readiness state, or versioned complete shards.

The release manifest records that this population is a deterministic sample of
a frozen incomplete extraction frontier, not a sample selected after the
complete corpus was known. It records the frozen selected-row count, maximum
page ID, selected-text journal boundary, extraction-source SHAs, code commit,
selection policy, and publication commit.

## Frozen Frontier and SQLite Safety

At a durable selected-text journal boundary, capture with one short read:

- committed `selected_text` count;
- maximum committed `page_id`;
- maximum completed selected-text journal row; and
- current reconciliation database identity.

Read the frozen frontier with page-ID keyset pagination and a maximum batch of
5,000 identities. Every batch opens a query-only SQLite connection, fetches the
bounded result, and closes the connection. No read transaction, cursor,
connection, backup, or database copy may span batches. The exporter never runs
a checkpoint, writes to the reconciliation database, or changes its pragmas.

After scanning the frontier, recount rows at or below the frozen maximum page
ID. The count must equal the captured count; otherwise discard the candidate
export and retry from a new frontier. This detects a non-monotonic insertion
inside the frozen boundary. Reads that encounter `busy` back off briefly and
retry without holding state in SQLite.

## Deterministic Selection

Preserve the existing `split_for(article_key)` train/validation/test assignment.
Within each split rank by:

```text
SHA-256("babel-interview-2016-v1" + NUL + article_key)
```

Maintain only the lowest required identities in bounded heaps: 50,000 train,
5,000 validation, and 5,000 test. The 1,000-row smoke set is the first 1,000
training identities in this same order; it is not another dataset split.

After identity selection, fetch only the chosen rows in short-lived bounded
SQLite reads and write deterministic Parquet shards outside the repository.
Persist ordered identities, per-split checksums, frontier evidence, shard
checksums, and the selection seed. Reject missing text, non-finite vectors,
wrong vector dimension, duplicate article keys, split drift, or count drift.

## Hugging Face Publication

Publish append-only configuration paths and a configuration-local manifest and
readiness document. Merge the dataset-card YAML without removing or changing
the existing `distillation_2016` configuration. The complete-release card
renderer and verifier must preserve `distillation_2016_interview` when the
complete versioned release later activates.

Serialize publication with the existing single-writer discipline. The active
extractor performs no remote writes, so local extraction and this publication
may run concurrently. The later complete publisher may accept the intervening
main commit only when the existing `distillation_2016/manifest.json` is byte
unchanged.

Acceptance requires loading every split by configuration name at the exact
returned commit SHA with authentication and streaming at least one validated
row. Record the exact commit in a mode-0600 handoff file and in
`prompts/interview-50k-training-handoff.md`. Tokens never enter artifacts,
logs, commits, or notebook cells.

## Handoff Gate

The independent training agent receives the exact configuration name, commit
SHA, seed, ordered-selection checksums, 1k smoke settings, 50k one-epoch
settings, max length 384, fixed 5k exact validation, and untouched 5k test.
This exporter does not launch Qwen training. The complete extractor continues
until its own full release is published and verified.

