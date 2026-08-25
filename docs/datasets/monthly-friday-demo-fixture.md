# Monthly Friday Demo Fixture Report

**Release scope:** `friday_demo_fixture`

**Readiness:** `fixture_ready`

**Sole release input:** `fixtures/monthly/demo/provenance.json`

This is a representative deterministic fixture, not an official June or July
Wikipedia snapshot. `2026-06` and `2026-07` are scenario-period labels only.
The safe prepared article text and revision metadata come from the actual
October 2016 pilot rows in private Hugging Face dataset
`dhelmy990/babel-wikipedia-experiment`, config `distillation_2016`, pinned at
revision `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`.

The checked-in 2016 catalog preserves the source article text, lead text,
canonical title, page ID, nullable QID, and revision ID. June and July reuse
that real representative content while applying explicit simulated temporal
metadata to the last four identities. This controlled scenario demonstrates
76 unchanged lineages, one move, one deletion, two creations, and one explicit
ambiguity group. It does not assert that those changes happened on Wikipedia.

## Verified inventory

| Period | Articles | Directed graph edges | Link Clickstream rows | Hidden archetypes | Backend seed assignments |
|---|---:|---:|---:|---:|---:|
| 2016 source | 80 | — | — | — | — |
| June scenario | 80 | 160 | 160 | 20 | 80 |
| July scenario | 80 | 160 | 160 | 20 | 80 |

Every archetype has four ordered seed weights `(0.4, 0.3, 0.2, 0.1)`. Graph
edges are directed, duplicate-free, and contain no self-loop. Every behavior
row is `type == "link"`, retains a positive raw `n`, and records
`log1p(n) / max(log1p(n))` for the period. The observable article schema is
closed and contains no graph, Clickstream, archetype, seed-weight, PPR,
hidden-relevance, or simulator-randomness fields.

## Artifact checksums

The authoritative checksums are embedded beside paths and row counts in
`provenance.json`. The June backend seed catalog checksum is
`de34ed341cba99fc9464c4939a89ba72f4c564aafa48b171ccfdc1e8e53c6dc7`;
the July checksum is
`288451fcd1a8a466852218b947d084ecc4f3aaff69f8649e41a3344246dfc973`.

## Rebuild and verification

```bash
PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python \
  -m babel_data.monthly.fixture \
  --source /home/dhelmy990/Data/babel-data/work/2016-pilot/accepted.jsonl \
  --output fixtures/monthly/demo

PYTHONPATH=data_pipeline/src data_pipeline/.venv/bin/python \
  -m babel_data.monthly.fixture --verify fixtures/monthly/demo

data_pipeline/.venv/bin/python -m pytest data_pipeline/tests/monthly -v
```

The local `article-crosswalk.jsonl` and `ambiguities.jsonl` are deterministic
test expectations only. They are not authoritative release inputs; the release
lane computes its own crosswalk from the three article catalogs.
