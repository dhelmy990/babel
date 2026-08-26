# Complete 2016 Distillation Release

This runbook builds the complete English Wikipedia 2016 navigation-teacher
release from authenticated, exact-commit private Hugging Face mirror objects.
It never uses an authoritative URL, `raw-mirror-staging`, or a direct
Wikimedia/Archive.org/Figshare object for semantic processing. It does not run
Qwen training.

## Frozen identities

```text
DATASET_REPO=dhelmy990/babel-wikipedia-experiment
INDEX_COMMIT=240a8d906c4faeafb60b877190976941148d1747
INDEX_SHA256=d669ce29a96cfd306fbbb06debf4660819f6b5c35c4a0c006d2102433a616d41
TEACHER_COMMIT=ee01785fc4cf3d7f25c90917f41e3962f93e9370
TEACHER_SHA256=5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56
WIKIPEDIA_COMMIT=PENDING_REQUIRED_MIRROR
WIKIPEDIA_SHA256=PENDING_REQUIRED_MIRROR
PILOT_BASELINE_COMMIT=PENDING_REQUIRED_MIRROR
BASE_MODEL=Qwen/Qwen3-Embedding-0.6B
BASE_MODEL_REVISION=97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

The pending Wikipedia values are a hard launch gate. Replace them only with
the `remote_verified` receipt produced by the command below. A branch name,
latest revision, byte-range sample, pilot file, or direct URL is not valid.
The Wikipedia mirror commit is also the initial pilot-compatible main baseline;
record it before any inactive complete-release shard is staged.

## Safe environment and disk preflight

Run from the isolated Babel checkout without shell tracing:

```bash
set -a
. /home/dhelmy990/Code/babel/.env
set +a
export BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data
test -n "${HF_TOKEN:-}"
test "$(realpath "$BABEL_DATA_ROOT")" = /home/dhelmy990/Data/babel-data
```

Before each mirror, require two-copy headroom. The full XML requires exactly
28,357,248,744 bytes for ordinary authoritative staging and exact-revision
cache copies:

```bash
SOURCE_ID=wikipedia-xml PYTHONPATH=data_pipeline/src \
data_pipeline/.venv/bin/python - <<'PY'
import os
from pathlib import Path
from babel_data.sources import load_source_manifest

root = Path(os.environ["BABEL_DATA_ROOT"])
source = load_source_manifest(
    Path("data_pipeline/manifests/2016-sources.json")
)[os.environ["SOURCE_ID"]]
stat = os.statvfs(root)
free = stat.f_bavail * stat.f_frsize
required = source.size * 2
print({"source_bytes": source.size, "required_bytes": required,
       "free_bytes": free, "fits": free >= required})
raise SystemExit(0 if free >= required else 1)
PY
```

## Mirror and verify sources

Use Task 2's CLI only. Commands are resumable and preserve partial state:

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli \
  mirror-source --source-id teacher-zip \
  --receipt-out /home/dhelmy990/Data/babel-data/receipts/teacher-zip.json

PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli \
  mirror-source --source-id wikipedia-xml \
  --receipt-out /home/dhelmy990/Data/babel-data/receipts/wikipedia-xml.json
```

Both receipts must say `state: remote_verified`, contain equal expected and
remote SHA-256 values, and name 40-character commits. Do not delete staging,
cache, journal, spool, accepted JSONL, report, or prepared files.

## Build or resume

Use only receipt values in this exact command. Re-running it resumes completed
teacher, Wikipedia-identity, reconciliation, selected-text, and spool ranges
without duplicate rows:

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli \
  build-complete-2016 \
  --teacher-revision ee01785fc4cf3d7f25c90917f41e3962f93e9370 \
  --teacher-sha256 5508a20088e0c5a2af4128f9aa80c675230c43b4538d42f89fb79ec324caaf56 \
  --wikipedia-revision PENDING_REQUIRED_MIRROR \
  --wikipedia-sha256 PENDING_REQUIRED_MIRROR \
  --output-root /home/dhelmy990/Data/babel-data/prepared/2016-complete
```

The builder holds text/vector batches only to the configured Parquet target,
spills global identities and hash ordering to SQLite, and retains its range
journal under `$BABEL_DATA_ROOT/full-2016-work/`. It opens both sources through
`open_processing_source`, then independently checks the pinned SHA-256 values.

Acceptance before publication:

- `teacher_total == matched + excluded`;
- `rows_written == matched`;
- every exclusion has a ledger row and reason;
- every vector is finite float32 with exactly 100 values and a positive norm;
- article keys and page IDs are unique;
- split is the existing deterministic SHA-256 98/1/1 split;
- manifest inventory/count/row digests validate; and
- the generated full-release proof validates against the exact manifest.

## Publish and remote acceptance

The local release begins in `building`. Promotion to `complete` requires the
generated full-release proof and exact remote verification:

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli \
  publish-2016 --state complete \
  --input-root /home/dhelmy990/Data/babel-data/prepared/2016-complete \
  --full-release-proof \
  /home/dhelmy990/Data/babel-data/full-2016-work/WORK_KEY/full-release-proof.json \
  --revision-out /home/dhelmy990/Data/babel-data/receipts/complete-2016.revision

DATASET_REVISION_SHA="$(tr -d '\n' </home/dhelmy990/Data/babel-data/receipts/complete-2016.revision)"
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.cli \
  verify-remote --revision "$DATASET_REVISION_SHA" \
  --input-root /home/dhelmy990/Data/babel-data/prepared/2016-complete
```

Record every rolling publication commit if the final corpus is uploaded in
more than one append-only batch. `publish-2016` uploads one inactive shard per
commit beneath the deterministic
`distillation_2016/releases/{release_id}/{split}/` root and remotely streams a
row from that exact shard commit. It then binds `supersedes_commit_sha` to the
last staging commit and performs one atomic metadata activation commit.

The root manifest, root readiness, and README config must all name only this
`active_release_root`. Historical pilot Parquet paths remain immutable in the
repository but are outside the active `data_files` globs and must not appear in
the active counts or stream. This is a one-time, single-operator transition:
the exact predecessor must still advertise `pilot_ready`, and publication must
reject a false predecessor or any attempt to supersede an already-active
complete release.

The release is not complete until the remote
manifest inventory, counts, shard SHA-256 values, aggregate row digest,
readiness bytes, and streamed row proofs agree. Remotely load at least one row
from every newly uploaded shard at its returned commit. Preserve the JSON CLI
output because its `publication_commits` sequence is the rolling upload audit.
The same per-shard evidence is durably appended to
`publication-commits.jsonl` in the prepared output root, including the shard
checksum and successful exact-commit stream proof; preserve that journal.

## Training boundary

Stop after remote acceptance. Give the separate user-launched training agent
`prompts/full-2016-training-handoff.md`. Do not run the notebook or any Qwen
forward/backward operation in this data-release session.

After all commands, remove the credential from the shell:

```bash
unset HF_TOKEN
```
