# Friday demo temporal release

This release is a representative Friday-demo fixture. It is not an official
monthly Wikipedia snapshot and must not be described as one. Its embedded
claim is `representative_fixture_not_official_monthly_snapshot`.

## Pinned provenance

- Dataset repository: `dhelmy990/babel-wikipedia-experiment` (private)
- Source config: `distillation_2016`
- Source revision: `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`
- Fixture manifest: `fixtures/monthly/demo/provenance.json`
- Fixture manifest SHA-256:
  `9474f2fc242935f26c18e39299a1115d8e53ca48b9f4bbec96ae1ebe4872b0ba`
- Temporal-config revision: `ca3128c5c5c23f901d7bbf105b6818b11a971d3c`
- Final pinned revision: `3fc8c1a2d84d6c8d069876ed27f91d6ead7fab2b`

The five temporal configs were published in one append-only commit on top of
the source revision. Later append-only compatibility commits added backend
seed transport objects required by the dashboard. The initial transport paths
remain immutable but are superseded; consumers use only the resolved `v1`
paths below. No commit added, replaced, or removed any `distillation_2016`
object.

## Published configs

| Config | Rows | Visibility contract |
| --- | ---: | --- |
| `demo_catalog_2026_06` | 80 | Observable catalog only |
| `demo_simulator_2026_06_hidden` | 340 | Hidden simulator inputs |
| `demo_catalog_2026_07` | 80 | Observable catalog only |
| `demo_simulator_2026_07_hidden` | 340 | Hidden simulator inputs |
| `demo_crosswalk` | 241 | 240 memberships plus 1 explicit ambiguity |

The two catalog configs were scanned row by row after publication for hidden
simulator fields. All five configs were streamed from the exact published
revision and their counts matched the table.

The final pinned revision also contains these exact fixture-producer bytes:

| Transport object | SHA-256 |
| --- | --- |
| `backend-seed/2026-06/resolved-catalog-v1.jsonl` | `6d7c6c505cfc9ceefb67f6cd6c992cde75504390de5a0c256bb19d811e8c0a5a` |
| `backend-seed/2026-06/resolved-catalog-v1.jsonl.sha256` | `12bc7413e6162ca16ba5749a54ca57a9591b36a599340f3b305ec88f3486f30c` |
| `backend-seed/2026-07/resolved-catalog-v1.jsonl` | `061e3e2eebf67d36eabe447eb561eaca52a0171e322dabac256dba773bfc8c93` |
| `backend-seed/2026-07/resolved-catalog-v1.jsonl.sha256` | `509c630e8c6428427b294d7a7881e391f14025b179d31ee3b837dd559cb32ecb` |

Each companion contains the catalog digest, two spaces,
`resolved-catalog-v1.jsonl`, and a trailing newline, matching the dashboard
adapter's pinned artifact contract. The real C++ adapter acceptance resolved
the exact final commit, authenticated downloads, verified the companion and
all per-row article-text hashes, parsed all 80 June rows, and matched the
repository, config, and commit provenance.

## Identity rules and result

The crosswalk uses a unique Wikidata QID first. It uses stable page ID only
when at least one side lacks a QID. It never joins on title alone. Duplicate
QIDs within a period and conflicting QIDs on a reused page ID are emitted as
explicit ambiguity records.

The fixture produced 85 lineages across 240 article memberships. Membership
classifications were 228 unchanged, 3 moved, 2 created, 4 deleted, and 3
ambiguous. The crosswalk config also contains one
`qid_not_unique_within_period` ambiguity record.

## Preservation evidence

SHA-256 was computed at both the parent and published revisions. Every pair
was identical:

| Existing object | SHA-256 |
| --- | --- |
| `distillation_2016/manifest.json` | `6d99276635ec76f58c945dc3b2eb32273f113a4c9163dc926b9a6fc18300ff6a` |
| `distillation_2016/test/empty.parquet` | `d0d2b3d7e44785dbed1ce376b9c045bf7ed2bb7fcf38d8f1ee4a97c5d16647ba` |
| `distillation_2016/train/part-00000.parquet` | `1379d2f3aa3873f9fddd08d1d6d3d4182e5d0417bdbd5f427cf06f8cb6888ae1` |
| `distillation_2016/validation/part-00000.parquet` | `82eefaed7b4a7fea4980c79062e25e7efd221263a425a12f439773ee20af2680` |

## Reproduction and verification

Preparation is deliberately manifest-driven:

```python
from babel_data.demo_temporal_release import prepare_demo_temporal_release

prepared = prepare_demo_temporal_release(
    "fixtures/monthly/demo",
    "/home/dhelmy990/Data/babel-data/prepared/friday-demo-temporal-release",
)
```

Pinned remote verification uses `verify_demo_configs` with the five expected
counts above. Authentication is passed by the caller and is never read by the
library. The publication path is intentionally trusted and single-writer; it
does not claim concurrent-writer or adversarial-input hardening.
