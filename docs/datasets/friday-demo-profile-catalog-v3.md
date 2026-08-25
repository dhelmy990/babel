# Friday demo ProfileManifest catalog V3

Release scope: `friday_demo_fixture`.

This is a representative demo fixture, not an official June or July Wikipedia
snapshot. The `2026-06` and `2026-07` labels are scenario periods. The article
identities and text in the V3 profile catalogs are real namespace-zero pages
from the official English Wikipedia `2016-10-01` dump. No live Wikipedia API,
fuzzy title matching, or invented identity/text was used.

## Independent assignment contract

The authoritative source is `ProfileManifest::seedAssignments()` in
`backend/src/application/profile_manifest.cpp`: 80 creator/title assignments,
78 distinct backend-normalized titles. `Artificial neural network` and
`Regulation` each occur for two different creators. V3 is therefore a
78-article source catalog; the backend retains the independent 80-row
assignment ledger and may legitimately reuse either source article across two
creators.

An integration test extracts the C++ title literals independently and proves:

- 80 creator/title pairs and 78 normalized lookup keys;
- every lookup key resolves unambiguously through canonical titles or real
  redirect titles;
- the 80 assignments cover all 78 unique positive page IDs; and
- the two repeated titles map to one page each.

## Bounded official acquisition

The verified local multistream index was
`/home/dhelmy990/Data/babel-data/sources/2016/enwiki-20161001-pages-articles-multistream-index.txt.bz2`.
Exact title selection produced 76 complete shared-offset ranges totaling
33,215,931 compressed bytes. Each HTTP request to the official dump URL
required status 206 and the exact `Content-Range`; the production
`babel_data.wikipedia.iter_wikipedia_pages` parser then resolved 78/78 titles
directly, with zero missing, redirected, or ambiguous titles.

The external, resumable acquisition evidence is under
`/home/dhelmy990/Data/babel-data/work/monthly-profile-v3/`:

- `profile-articles-2016.jsonl`: 78 rows, SHA-256
  `0d1ace3326c62dc6f68b148687ba2ce6002d6c2512fd4a133cc1823d04e71e74`;
- `resolution-ledger.jsonl`: 78 resolved rows, SHA-256
  `99f2e09d3511fd5f8f1cb31f74a4e132dbeffd82f32791c787ad207938da1b37`;
- `range-ledger-initial.json`: 76 ranges, SHA-256
  `e87829de75eab8cdd3cba7c0217aeb5c11ce083e41a367150d904c759e070d43`.
- `provenance.json`: source/readiness evidence including the lead fallback,
  SHA-256
  `60b5897a99d81a6684105d04c01187fbca2043f036c3ace196df6698548f377e`.

All 78 articles have nonempty production-prepared article text and a positive
source revision ID. The production lead heuristic returned empty only for
`Corporate finance`; its lead is deterministically derived from the first
nonempty paragraph of that same prepared article text and is marked
`lead_derivation=first_nonempty_paragraph_fallback`.

## Frozen fixture artifacts

`fixtures/monthly/demo/provenance.json` remains the sole release input
manifest and keeps the frozen `friday_demo_fixture` provenance semantics. Its
monthly `backend_seed_catalog` descriptors point to append-only V3 files:

- `june/resolved-catalog-v3.jsonl` and `.sha256`;
- `july/resolved-catalog-v3.jsonl` and `.sha256`.

Rebuild from the checked source inputs with:

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python -m babel_data.monthly.fixture \
  --source /home/dhelmy990/Data/babel-data/work/2016-pilot/accepted.jsonl \
  --profile-source /home/dhelmy990/Data/babel-data/work/monthly-profile-v3/profile-articles-2016.jsonl \
  --profile-manifest backend/src/application/profile_manifest.cpp \
  --output fixtures/monthly/demo
```
