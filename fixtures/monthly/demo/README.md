# Monthly Friday Demo Fixture

Release scope: `friday_demo_fixture`.

This is a representative deterministic fixture, not an official June or July Wikipedia snapshot.
The labels `2026-06` and `2026-07` are scenario periods only. Article text and
revision IDs were derived byte-faithfully from the pinned October 2016 pilot
`dhelmy990/babel-wikipedia-experiment` / `distillation_2016` at `c8cbb81fdb81f71a3aa5d0e5beb10348843ede6b`.

Representative input SHA-256: `72709f50989269e63694eaf9a66429d413dce6595c9ac93b02088c43a1171e4b`.

The last four identities carry explicit simulated titles, page IDs, and QID
transition metadata solely to exercise moved, deleted, created, and ambiguous
cross-period behavior. All other page IDs preserve the representative source.
The
observable catalogs contain no graph, Clickstream, archetype, seed-weight, PPR,
hidden-relevance, or random-draw fields. `provenance.json` is the sole release
input manifest; the crosswalk and ambiguity files are local expectations only.
