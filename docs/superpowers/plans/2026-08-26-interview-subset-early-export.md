# Interview Subset Early Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and remotely verify a deterministic private 50k/5k/5k Hugging Face configuration from the active extractor's already committed rows without interrupting extraction or retaining its SQLite WAL.

**Architecture:** A standalone exporter freezes a durable page-ID frontier, performs bounded keyset reads with one query-only SQLite connection per batch, selects IDs with bounded hash heaps, then fetches only chosen rows into Parquet. A configuration-specific Hub publisher appends `distillation_2016_interview` to `main`, merges the dataset card, and preserves it through the later complete-release activation.

**Tech Stack:** Python 3.10+, SQLite WAL, NumPy, PyArrow/Parquet, Hugging Face Hub/Datasets, pytest.

## Global Constraints

- Do not stop, restart, signal, modify, checkpoint, or write through the active complete-2016 extractor.
- Read SQLite with query-only connections, keyset batches no larger than 5,000, and no connection or cursor spanning batches.
- Configuration is exactly `distillation_2016_interview` in private repo `dhelmy990/babel-wikipedia-experiment` on `main`.
- Counts are exactly 50,000 train, 5,000 validation, and 5,000 test; smoke is the first 1,000 selected training IDs.
- Selection rank is `SHA-256("babel-interview-2016-v1" + NUL + article_key)` within the release's existing split assignment.
- `HF_TOKEN` comes from `/home/dhelmy990/Code/babel/.env` and is never printed, logged, committed, or embedded.
- Bulk prepared output uses absolute `/home/dhelmy990/Data/babel-data`; do not write it into the worktree.
- Do not launch Qwen training. Produce a pinned handoff for the independent training agent.

---

### Task 1: Export, Publish, Verify, and Hand Off the Frozen Interview Configuration

**Files:**
- Create: `data_pipeline/src/babel_data/interview_export.py`
- Create: `data_pipeline/tests/test_interview_export.py`
- Modify: `data_pipeline/src/babel_data/release.py`
- Modify: `data_pipeline/src/babel_data/hub.py`
- Modify: `data_pipeline/src/babel_data/cli.py`
- Modify: `data_pipeline/tests/test_hub.py`
- Create: `prompts/interview-50k-training-handoff.md`
- Create: `docs/runbooks/interview-subset-export.md`

**Interfaces:**
- Consumes: absolute reconciliation database path, absolute output root, private repo ID, and token loaded by the CLI.
- Produces: `FrozenFrontier`, `InterviewSelectionV1`, prepared Parquet/config manifest, exact verified Hub commit, and mode-0600 revision handoff.

- [ ] Write failing tests that freeze count/max-page/journal evidence; paginate with a fresh query-only connection per batch; prove no connection spans batches; preserve 98/1/1 split assignment; select exact 50k/5k/5k with seed `babel-interview-2016-v1`; reject a changed frontier count; and reject duplicate, missing-text, non-finite, or non-100d rows.
- [ ] Write a WAL integration test with a concurrent writer and repeated `PRAGMA wal_checkpoint(PASSIVE)` observations proving the exporter does not leave a busy reader after each batch.
- [ ] Implement `freeze_frontier(database_path) -> FrozenFrontier`, `select_interview_ids(database_path, frontier, batch_size=5000) -> InterviewSelectionV1`, and `write_interview_release(database_path, frontier, selection, output_root)`. Use `WHERE page_id > ? AND page_id <= ? ORDER BY page_id LIMIT ?`; close every connection before heap or Parquet work; fetch chosen rows in bounded page-ID groups.
- [ ] Write publication tests proving append-only interview paths, atomic parent-commit use, exact-revision split streaming, private-repository verification, byte-preservation of the existing distillation manifest, and merged dataset-card preservation when the complete release later activates.
- [ ] Implement `publish_interview_configuration(...) -> str` and extend dataset-card rendering so both configurations remain declared. Never replace an existing nonidentical interview path. Accept an intervening main commit during later complete staging only when `distillation_2016/manifest.json` remains byte-identical.
- [ ] Add CLI `export-interview-2016` and `publish-interview-2016` commands. JSON status includes only counts, checksums, frontier, paths, and commit identity; it excludes the token and row contents.
- [ ] Run focused tests:

```bash
data_pipeline/.venv/bin/python -m pytest \
  data_pipeline/tests/test_interview_export.py \
  data_pipeline/tests/test_hub.py -q
```

- [ ] Run the real export against `/home/dhelmy990/Data/babel-data/full-2016-work/1a319328641844e29537/reconcile.sqlite3`, writing to `/home/dhelmy990/Data/babel-data/prepared/2016-interview-50k`. Confirm the active extractor PID remains alive and its selected-text journal advances.
- [ ] Publish to `dhelmy990/babel-wikipedia-experiment` on `main`; remotely load one validated row from each split using `name="distillation_2016_interview"`, the exact returned SHA, streaming, and authentication. Write the SHA to `/home/dhelmy990/Data/babel-data/receipts/interview-2016-revision.txt` with mode 0600.
- [ ] Write `prompts/interview-50k-training-handoff.md` with the pinned commit/config, selection/frontier checksums, 1k smoke, 50k one epoch, max length 384, fixed exact 5k validation, untouched 5k test, checkpoint/resume, and Colab Secrets instructions. Do not launch training.
- [ ] Run the full data-pipeline suite and commit scoped source/tests/docs only:

```bash
data_pipeline/.venv/bin/python -m pytest data_pipeline/tests -q
git diff --check
git add data_pipeline/src/babel_data data_pipeline/tests \
  prompts/interview-50k-training-handoff.md \
  docs/runbooks/interview-subset-export.md
git commit -m "feat: publish frozen 2016 interview subset"
```

**Next-phase context:** Review exact remote counts and checksums, partial-frontier disclosure, WAL safety, config/card coexistence, and pinned handoff usability. Do not require complete-corpus extraction or launch training.

