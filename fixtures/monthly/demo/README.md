# Monthly Friday Demo Fixture

Release scope: `friday_demo_fixture`.

This is a representative deterministic fixture, not an official June or July Wikipedia snapshot.
The labels `2026-06` and `2026-07` are scenario periods only. Article text and
revision IDs were derived byte-faithfully from the pinned October 2016 pilot
`dhelmy990/babel-wikipedia-experiment` / `distillation_2016` at `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`.

Representative input SHA-256: `72709f50989269e63694eaf9a66429d413dce6595c9ac93b02088c43a1171e4b`.
Profile catalog input SHA-256: `0d1ace3326c62dc6f68b148687ba2ce6002d6c2512fd4a133cc1823d04e71e74`.

The last four identities carry explicit simulated titles, page IDs, and QID
transition metadata solely to exercise moved, deleted, created, and ambiguous
cross-period behavior. All other page IDs preserve the representative source.
The
observable catalogs contain no graph, Clickstream, archetype, seed-weight, PPR,
hidden-relevance, or random-draw fields. `provenance.json` is the sole release
input manifest; the crosswalk and ambiguity files are local expectations only.
Each `resolved-catalog-v3.jsonl` contains 78 unique real October 2016 articles
that resolve all 80 backend-owned `ProfileManifest` creator assignments. The
two repeated requested titles reuse the same source article across creators;
the assignment ledger remains authoritative in the backend and is not copied
into this source catalog. These source articles were extracted with the
production parser from strict byte ranges of the official `enwiki-20161001`
multistream dump; no live Wikipedia API was used. `Corporate finance` is the
only row for which the production lead heuristic returned empty, so its
nonempty lead is deterministically the first nonempty prepared article
paragraph (`lead_derivation=first_nonempty_paragraph_fallback`).
