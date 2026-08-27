# June/July 2026 Engineering Snapshot Runbook

This release is the real, time-boxed dataset for the recommendation
architecture experiment. It is not the 80-row Friday fixture and it does not
claim to contain complete monthly Wikipedias.

## Immutable release

- Private repository: `dhelmy990/babel-wikipedia-experiment`
- Connected release commit: `0d1ab2c7f0e2295682288fcf10077d2d776bf559`
- Manifest: `monthly-engineering-snapshots/release-manifest.json`
- Selection identity checksum:
  `62384bfa8267540ed2002340b5d6504d5a89d1ff268fbf7a730f7ef05ddec0b5`

Always load the commit above. Do not substitute `main`.

| Configuration | Rows | Visibility |
|---|---:|---|
| `catalog_2026_06` | 5,000 | observable article identity and prepared text |
| `simulator_2026_06_hidden` | 207,126 | 103,563 pagelinks plus 103,563 Clickstream transitions |
| `catalog_2026_07` | 5,000 | observable article identity and prepared text |
| `simulator_2026_07_hidden` | 217,470 | 108,735 pagelinks plus 108,735 Clickstream transitions |
| `crosswalk_2026_06_07` | 10,000 | temporal identity memberships |

The two catalogs share exactly 4,000 canonical Wikipedia page IDs and each has
1,000 disjoint month-specific IDs. All 78 dashboard seed titles resolve in
each month. Article rows retain title, lead, and the first useful section.
Hidden graph and Clickstream fields do not appear in observable rows.

## Why the release contains 5,000 rows per month

The canonical target was 10,000 rows per month. Early range-acquisition
evidence projected that the original 10,000-row plan would exceed the
independent 45-minute limit, so selection was frozen at the specified 5,000
row emergency floor before semantic processing. No fixture substitution was
used.

Recorded phase durations were:

| Phase | June | July |
|---|---:|---:|
| indexed candidate resolution | 117.54 s | 127.93 s |
| selected-range acquisition | 350.64 s | 341.51 s |
| private-Hub XML parsing | 175.91 s | 174.64 s |
| induced-relation scan | 34.87 s | 34.35 s |

## Source and processing boundary

The authoritative monthly multistream indexes and Clickstream files were
mirrored into the private repository and connected at commit
`78c6830d6703d0dd0e7e3c85f4378ddc6294a2a4`. Exact selected multistream byte
ranges were acquired from Wikimedia as source acquisition only, hashed, and
published privately before any XML/article parsing:

- June selected XML commit:
  `06f9c3225b5c7b45f0362b18fe7b177b64ca20a7`
- July selected XML commit:
  `f34421d469ffd9bd7cd102186947fc8c801d4cdf`
- required-seed supplement commit:
  `2aa34726f80bfcbe2aebdaca24e53179d68f1adc`

Semantic processing authenticated and read those exact private commits. It did
not parse a public HTTP response, use live Wikipedia APIs, silently fall back,
or scan full XML/SQL dumps for discovery. The release manifest records all
source-object SHA-256 values.

Pagelinks are real induced edges derived from monthly Clickstream rows whose
type is `link`; this type guarantees that the source-to-target pair is a real
Wikipedia link. Counts and normalized weights remain hidden from observable
loaders. Each relation kind is deterministically capped at 250,000.

## Pinned remote loading

Set `HF_TOKEN` in the process environment without printing it, then stream an
observable configuration:

```bash
python3 - <<'PY'
import os
from datasets import load_dataset

rows = load_dataset(
    "dhelmy990/babel-wikipedia-experiment",
    "catalog_2026_06",
    split="train",
    revision="0d1ab2c7f0e2295682288fcf10077d2d776bf559",
    token=os.environ["HF_TOKEN"],
    streaming=True,
)
print(next(iter(rows))["article_key"])
PY
```

The checked-in backend default now pins dashboard seeding to
`catalog_2026_06` at this exact release commit and downloads only
`backend-seed/2026-06/resolved-catalog-v3.jsonl` into the backend cache. The
backend performs authentication, checksum verification, deterministic safe
paragraph HTML conversion, canonical page-ID preservation, retries, progress
tracking, insertion, and duplicate rejection. There is no MediaWiki fallback.

The online experiment worker remains on its earlier demo bundle until the real
Qwen serving integration changes its five-configuration loader. Do not point
that older worker at `crosswalk_2026_06_07`; the serving-integration phase owns
that coordinated switch.

## Verification

The connected release was verified by streaming every row from all five
configurations at its exact commit, comparing every Parquet SHA-256 with the
manifest, checking both 78-row backend seed artifacts, confirming observable
columns contain no hidden relation payloads, and confirming the complete 2016
release remained present.

For local code verification run:

```bash
data_pipeline/.venv/bin/python -m pytest \
  data_pipeline/tests/monthly data_pipeline/tests/test_hub.py -v
cmake --build --preset test
ctest --preset test -R "huggingface|seed|wikipedia_import" --output-on-failure
```
