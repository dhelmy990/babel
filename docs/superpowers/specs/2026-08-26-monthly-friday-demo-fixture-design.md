# Monthly Friday Demo Fixture Design

**Date:** 2026-08-26  
**Release scope:** `friday_demo_fixture`

## Purpose

Provide the smallest deterministic June-to-July environment that exercises the
monthly data contracts and backend seed handoff. This is a representative demo
fixture, not a historical or official June/July Wikipedia snapshot.

The observable article text is reused from the private, pinned October 2016
pilot at Hugging Face revision
`c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`. June and July are scenario-period
labels applied by the fixture compiler so temporal consumers can demonstrate
identity changes without waiting for bulk monthly dumps. Every README,
manifest, and provenance document repeats that limitation.

## Contract Boundary

Four closed JSON Schemas define the exchange rows:

- `monthly-article-v1`: observable namespace-zero article identity and safe
  prepared text. It requires stable article/page/revision identity, nullable
  QID, a sorted unique redirect list, and a SHA-256 content hash. Hidden graph,
  Clickstream, archetype, seed-weight, PPR, relevance, and random-draw fields
  are forbidden by `additionalProperties: false`.
- `monthly-edge-v1`: hidden directed canonical graph edge, with no self-loop.
- `clickstream-edge-v1`: hidden link-only behavioral edge preserving positive
  raw count `n` and a finite normalized weight in `[0, 1]`.
- `article-crosswalk-v1`: temporal lineage evidence and outcome. Continuity
  uses unique QID first and stable page ID only when QID is absent. Titles are
  descriptive evidence and never an identity join key. Conflicts are emitted
  explicitly as ambiguous.

All JSONL output is UTF-8, one compact key-sorted JSON object per line, sorted
by documented stable keys, and terminated with a newline.

## Fixture Shape

`fixtures/monthly/demo` contains:

```text
README.md
provenance.json
article-crosswalk.jsonl
ambiguities.jsonl
2016/
  articles.jsonl
june/
  articles.jsonl
  edges.jsonl
  clickstream.jsonl
  hidden-archetypes.jsonl
  resolved-catalog-v1.jsonl
  resolved-catalog-v1.jsonl.sha256
  resolved-catalog-v2.jsonl
  resolved-catalog-v2.jsonl.sha256
july/
  articles.jsonl
  edges.jsonl
  clickstream.jsonl
  hidden-archetypes.jsonl
  resolved-catalog-v1.jsonl
  resolved-catalog-v1.jsonl.sha256
  resolved-catalog-v2.jsonl
  resolved-catalog-v2.jsonl.sha256
```

Each month has exactly 80 observable articles. The crosswalk demonstrates 76
unchanged rows, one moved row, one deleted row, two created rows, and one
explicit ambiguity group formed by two June candidates and one July target.
Both monthly catalogs remain exactly 80 rows.

The graph is a deterministic directed ring plus fixed jumps over the sorted
article keys. It is duplicate-free, contains no self-loop, and is kept only in
the hidden artifacts. Clickstream rows are a deterministic subset of graph
edges, all have `type == "link"`, retain raw positive counts, and normalize
weights deterministically.

The authoritative backend roster supplies 20 archetypes and four ordered seed
assignments each. Their fixed weights are `0.4`, `0.3`, `0.2`, and `0.1`.
Because the representative 80-row text source is not guaranteed to contain the
roster's requested titles, fixture seed resolution is deterministic assignment
by catalog order and records both declared title and assigned article identity;
it does not pretend that title resolution occurred against an official monthly
snapshot. Each resolved catalog row retains its creator/assignment fields and
joins the complete prepared article identity, text, redirects, content hash,
snapshot, and revision metadata. Both periods emit exactly 80 rows sorted by
page ID plus a SHA-256 companion. Every declared title is the joined row's
canonical title or one of its real redirects and resolves uniquely across the
transport. Version 1 transport files remain byte-for-byte append-only history.

## Provenance and Readiness

`provenance.json` is the sole release input manifest. It requires these exact
top-level release fields and period keys:

- `manifest_version: 1`;
- `release_scope: friday_demo_fixture`;
- `snapshot_claim: representative_fixture_not_official_monthly_snapshot`;
- `readiness: fixture_ready`;
- `source: {repo_id, config, revision}`;
- `periods` keyed exactly `2016`, `2026-06`, and `2026-07`.

The `2016` period exposes at least the byte-faithfully derived article source
used by the fixture. June and July each expose exactly `articles`, `edges`,
`clickstream`, `hidden_archetypes`, and `backend_seed_catalog`. Every artifact
descriptor has `{path, sha256, rows}` with a path relative to the fixture root.
The seed-catalog descriptor's SHA-256 is authoritative. Each catalog also has a
dashboard-compatible checksum companion containing
`<sha256>  resolved-catalog-v2.jsonl\n`; it is a transport companion, not a
sixth JSONL period artifact, so the frozen five-artifact release manifest
remains closed.
The provenance also records:

- the pinned private pilot repository, revision, config, source snapshot date,
  and source row/file hashes;
- explicit scenario labels for June and July;
- artifact byte sizes, SHA-256 hashes, schemas, and row counts; and
- `state: fixture_ready` only after local schema and invariant verification.

The fixture is not uploaded as a real monthly dataset and must never be marked
`complete`, `pilot_ready`, or an official monthly snapshot.

## API and Validation

The `babel_data.monthly` package exposes small pure functions for canonical
JSONL, article/catalog construction, graph and Clickstream construction,
profile seed resolution, identity crosswalk construction, fixture generation,
and fixture verification. Tests cover happy paths and common invalid data:
schema closure, namespace and hash constraints, redirect ordering, hidden-field
leakage, graph duplicate/self-loop rejection, link-only Clickstream and weight
bounds, the 20-by-4 weight contract, title-only non-matching, explicit
ambiguity, deterministic bytes, artifact checksums, and exact row counts.

The release lane consumes `fixtures/monthly/demo/provenance.json` only after its
readiness state is `fixture_ready` and all artifact hashes verify. Crosswalk
and ambiguity files are deterministic expectations used by local tests; they
are deliberately not authoritative release inputs because the release lane
computes its own temporal crosswalk from the three article catalogs.
