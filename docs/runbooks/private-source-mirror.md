# Private source mirror

All semantic Wikipedia processing must use the authenticated, exact-commit
Hugging Face mirror in `dhelmy990/babel-wikipedia-experiment`. The
authoritative HTTP URLs are acquisition inputs only. Do not pass them to a
processor and do not use them as a fallback.

## One-time shell setup

Run from the repository checkout. This sources the token without printing it;
do not enable shell tracing.

```bash
cd /home/dhelmy990/Code/babel
set -a
. /home/dhelmy990/Code/babel/.env
set +a
export BABEL_DATA_ROOT=/home/dhelmy990/Data/babel-data
test -n "${HF_TOKEN:-}"
test "$(realpath "$BABEL_DATA_ROOT")" = /home/dhelmy990/Data/babel-data
```

The three stable source IDs in `data_pipeline/manifests/2016-sources.json` are:

- `wikipedia-multistream-index` — 185,177,516 bytes
- `teacher-zip` — 727,429,988 bytes
- `wikipedia-xml` — 14,178,624,372 bytes

Mirror exactly one source per command. The smallest source is the index:

```bash
data_pipeline/.venv/bin/babel-data mirror-source \
  --source-id wikipedia-multistream-index
```

The command reads `BABEL_DATA_ROOT` and `HF_TOKEN`, stages the authoritative
download only under `$BABEL_DATA_ROOT/raw-mirror-staging`, uploads it below
`sources/<source-id>/`, resolves the returned 40-character commit, downloads
that exact private revision into `$BABEL_DATA_ROOT/hf-cache/<commit>/`, and
emits a non-secret JSON result. Its closed receipt is saved at:

```text
$BABEL_DATA_ROOT/hf-cache/<commit>/.receipts/<source-id>.json
```

To save another non-secret copy for a handoff, use an absolute path:

```bash
data_pipeline/.venv/bin/babel-data mirror-source \
  --source-id wikipedia-multistream-index \
  --receipt-out /home/dhelmy990/Data/babel-data/receipts/wikipedia-multistream-index.json
```

Never put `--token` in a recorded command. The CLI redacts either an explicit
token or `HF_TOKEN` from structured errors, but the environment form also keeps
the credential out of shell history and process arguments.

## Disk preflight

Retain room for both staging and exact-revision cache copies. The following
read-only preflight prints the required two-copy bytes, free bytes, and a
boolean result for one manifest source. It does not delete or download data.

```bash
SOURCE_ID=wikipedia-multistream-index \
PYTHONPATH=data_pipeline/src \
data_pipeline/.venv/bin/python - <<'PY'
import os
from pathlib import Path

from babel_data.sources import load_source_manifest

root = Path(os.environ["BABEL_DATA_ROOT"])
source = load_source_manifest(
    Path("data_pipeline/manifests/2016-sources.json")
)[os.environ["SOURCE_ID"]]
free = os.statvfs(root)
free_bytes = free.f_bavail * free.f_frsize
required_bytes = source.size * 2
print({
    "source_id": os.environ["SOURCE_ID"],
    "source_bytes": source.size,
    "required_two_copy_bytes": required_bytes,
    "free_bytes": free_bytes,
    "fits": free_bytes >= required_bytes,
})
PY
```

Do not start a source when `fits` is false, and do not delete bulk inputs to
make it fit. Re-run the preflight separately before `teacher-zip` or
`wikipedia-xml`; the XML dump alone requires 28,357,248,744 bytes for staging
and cache copies.

## Processing handoff

Use only the repository, exact commit, and repository path recorded by the
receipt. The receipt must be present in the revision registry shown above.

```python
import os
from pathlib import Path

from babel_data.mirror import open_processing_source

source = open_processing_source(
    "dhelmy990/babel-wikipedia-experiment",
    "240a8d906c4faeafb60b877190976941148d1747",
    "sources/wikipedia-multistream-index/enwiki-20161001-pages-articles-multistream-index.txt.bz2",
    os.environ["HF_TOKEN"],
    Path(os.environ["BABEL_DATA_ROOT"]) / "hf-cache",
)
```

`open_processing_source` authenticates repository privacy, checks that the
requested revision resolves to itself, validates the closed receipt and exact
path, and checks the cached file size and SHA-256. A direct HTTP(S) source is a
policy error; there is no Wikimedia, MediaWiki, Archive.org, or Figshare
fallback.

After the command finishes, remove the credential from the current shell if it
is no longer needed:

```bash
unset HF_TOKEN
```

## Live verification evidence

On 2026-08-26, the read-only preflight reported 58,049,101,824 free bytes and a
370,355,032-byte two-copy worst case for `wikipedia-multistream-index`. The live
mirror and an independent `open_processing_source` check then completed with:

```text
repository: dhelmy990/babel-wikipedia-experiment
commit: 240a8d906c4faeafb60b877190976941148d1747
path: sources/wikipedia-multistream-index/enwiki-20161001-pages-articles-multistream-index.txt.bz2
bytes: 185177516
expected_sha256: d669ce29a96cfd306fbbb06debf4660819f6b5c35c4a0c006d2102433a616d41
remote_sha256: d669ce29a96cfd306fbbb06debf4660819f6b5c35c4a0c006d2102433a616d41
state: remote_verified
receipt: /home/dhelmy990/Data/babel-data/hf-cache/240a8d906c4faeafb60b877190976941148d1747/.receipts/wikipedia-multistream-index.json
```

The independent processing open authenticated the private exact revision and
returned a 185,177,516-byte cached file with the same SHA-256. No larger source
was started.
