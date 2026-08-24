# Backend Seeding Dashboard Design

**Date:** 2026-08-24

## Purpose

This milestone establishes a narrow backend foundation for Babel. It provides a
PostgreSQL-backed set of synthetic creator profiles plus a `Personal` profile,
a local C++ administration dashboard that seeds the generated profiles from
Wikipedia, and an Electron profile selector that can load each profile's
current graph.

The dashboard is the only operator-facing entry point for generated Wikipedia
population and operational ingestion checks. Before the dashboard seed action
runs, all generated profiles exist but contain no Babels. After a successful
seed, each generated profile owns four Wikipedia-derived Babels. `Personal` is
never populated by the dashboard; it has a separate, explicit legacy-migration
command.

This milestone does not implement recommendation, training, model serving,
embeddings, FAISS, PPR, or mutable graph editing.

## Scope

### Included

- A local C++20 modular-monolith backend.
- PostgreSQL with the pgvector extension available through Docker Compose.
- Twenty generated creator profiles defined by
  `prompts/wikipedia_user_profiles.md`.
- One independently provisioned `Personal` profile and a non-destructive
  `just migrate-personal <source-json>` command.
- Four fixed Wikipedia seed articles per generated profile, for 80 expected
  profile/Babel assignments.
- Full rendered Wikipedia article HTML converted to a sanitized,
  Quill-compatible representation before storage.
- A small, dark administration dashboard served by the C++ backend.
- One dashboard action that starts an asynchronous, idempotent seed job.
- Persistent seed progress and article-specific error reporting.
- An Electron profile-selection screen with a vertical wheel, a mostly black
  visual treatment, and a stable accent color per profile.
- Read-only loading of a selected profile's Babels into the existing graph.
- Empty graph behavior when a profile has not been seeded.
- Automated C++ and integration tests run outside the dashboard.

### Deferred

- Creating, editing, deleting, or linking database-backed Babels in Electron.
- Authentication and authorization between profiles.
- Embedding generation or storage, normalized-text storage, FAISS, and vector
  indexing jobs.
- Recommendation request/result endpoints.
- PPR, simulator behavior, training, model serving, and parameter
  synchronization.
- Metrics collection and monitoring panels beyond seed status and errors.

## Architectural Boundaries

The modular monolith contains only the application backend and its
administrative behavior. Training, model serving, parameter synchronization,
and derived vector indexing are separate architectural boundaries from day one
and will be deployed only when needed.

```text
Electron renderer
  profile selector + read-only graph
          |
          | preload/IPC
          v
Electron main adapter
          |
          | localhost HTTP/JSON
          v
+------------------------------------------------------+
| C++ application backend                              |
|                                                      |
| Profile queries | graph queries | seed administration|
| Wikipedia import service | Personal legacy migration |
|                                                      |
| Domain -> application services -> ports -> adapters  |
+---------------------------+--------------------------+
                            |
                            v
                  PostgreSQL + pgvector

Future separate boundaries, not deployed in this milestone:

  training pipeline <-> parameter synchronization <-> model serving
                                                   |
                                                   v
                                          derived vector index
```

The backend does not contain placeholder model logic. Future integrations must
cross explicit ports or process APIs rather than link training or serving code
into the application executable.

## Backend Modules

Dependency direction points inward toward domain types and application
services:

```text
domain
  Creator | Babel | Edge | SourceMetadata | SeedRun | SeedRunItem
       ^
application
  ProfileQueryService | GraphQueryService | SeedService
  WikipediaImportService | LegacyMigrationService
       ^
ports
  CreatorRepository | BabelRepository | EdgeRepository
  SeedRunRepository | ArticleSource | HtmlSanitizer
       ^
adapters
  PostgreSQL | Wikipedia HTTP | libxml2 sanitizer
  Drogon HTTP/API | static admin dashboard
```

Drogon, PostgreSQL, Wikipedia, and HTML parser types must not appear in domain
or application interfaces. Controllers translate HTTP DTOs into application
calls. Repository adapters own SQL and transaction details.

## Persistence Model

PostgreSQL is the source of truth. The pgvector extension is enabled, but no
embedding table or vector data is introduced until an embedding model and
dimension are selected.

### `creators`

- `id`: stable UUID primary key.
- `slug`: stable unique machine name.
- `display_name`: archetype name shown by Electron.
- `profile_color`: validated display color.
- `profile_kind`: `generated` or `personal`.
- `selector_order`: stable unique wheel position.
- `created_at`, `updated_at`.

All 21 creator rows are installed independently of Wikipedia seeding as part of
database initialization. Therefore the profile selector works against a fresh,
unseeded database. `Personal` has a stable UUID and sorts before the 20
generated profiles.

### `babels`

- `id`: UUID primary key.
- `owner_id`: creator UUID.
- `title`: canonical article title at ingestion time.
- `content_html`: sanitized Quill-compatible HTML.
- `color`: initialized from the owning profile color.
- `content_revision`: monotonically increasing local revision.
- `content_hash`: digest used later to detect stale derived artifacts.
- `created_at`, `updated_at`.

No normalized plain text and no Quill Delta are stored. A future training or
embedding adapter will use a `ContentTextExtractor` to produce ephemeral plain
text from `content_html` for the duration of a job.

### `babel_sources`

- `babel_id` and `owner_id`.
- `provider`: `wikipedia` in this milestone.
- `external_page_id`: canonical Wikipedia page ID.
- `canonical_url`.
- `source_revision_id` when supplied by Wikipedia.
- `fetched_at`.
- `seed_assignment_id`: nullable stable UUID from the versioned seed manifest.
- `declared_title`: the manifest title used to initiate resolution.

Unique keys cover both `(owner_id, provider, external_page_id)` and non-null
`seed_assignment_id`. The first permits the same Wikipedia page to belong to
multiple profiles while preventing duplicate imports for one profile. The
second lets a repeated seed run determine completed manifest assignments
without resolving or fetching their titles again.

Wikipedia provenance remains attached if editing is added later. A future
refresh must not overwrite local edits automatically.

### `edges`

- `id`: UUID primary key.
- `owner_id`.
- `source_babel_id`, `target_babel_id`.
- `created_at`.

Composite foreign keys ensure both endpoints belong to `owner_id`. No edges are
created by this milestone, but graph responses include an empty `edges` array
and the table establishes PostgreSQL ownership for later graph editing.

### `seed_runs`

- Run UUID and manifest version.
- State: `queued`, `running`, `completed`, `completed_with_errors`, `failed`, or
  `interrupted`.
- Total, completed, skipped, and failed counts.
- Start and finish timestamps.

### `seed_run_items`

- Run UUID, creator UUID, and declared Wikipedia title.
- Resolved page ID and Babel UUID when successful.
- State and attempt count.
- Stable error code and escaped human-readable detail when unsuccessful.
- Start and finish timestamps.

These rows make dashboard status durable across refreshes and backend restarts.

### `legacy_migrations`

- Source-file SHA-256 digest as the idempotency key.
- Target creator UUID, constrained to `Personal` by the application service.
- Imported Babel and edge counts.
- Completion timestamp.

The legacy source file is never modified or deleted.

## Profile and Seed Manifest

The backend owns a structured, versioned manifest derived from
`prompts/wikipedia_user_profiles.md`. Runtime code does not parse the Markdown.

The manifest contains exactly 20 profiles in the documented order:

- Five computer-science profiles.
- Seven art and creative-media profiles.
- Two neuroscience profiles.
- Three finance profiles.
- Three politics profiles.

All four declared seed titles are used for every profile. PPR weights, PPR
computation, archetype expansion, and simulated users are ignored. Profile
names are display metadata and are not future recommender features.

Stable UUIDs, slugs, ordering, seed-assignment UUIDs, and 21 distinct fixed hex
colors are checked by automated tests. Every profile color must remain legible
against the black selector background. The same Wikipedia page may appear under
more than one profile.

## Wikipedia Ingestion

All Wikipedia-to-Babel ingestion passes through one application service:

```cpp
ImportWikipediaBabelResult importWikipediaBabel(
    CreatorId profileId,
    WikipediaPageId pageId
);
```

The result distinguishes `Imported` from `AlreadyExists` and returns the Babel
ID and canonical title. Fetch, resolution, sanitization, and persistence
failures use typed application errors. The service validates the profile,
fetches the canonical article by page ID, sanitizes its HTML, and atomically
stores the owned Babel and Wikipedia provenance.

The seed job first resolves each manifest title to a canonical page ID and then
calls `importWikipediaBabel`. Automated tests use the same service through fake
ports. A future manual importer may reuse it, but this milestone exposes no
standalone HTTP endpoint or dashboard control for arbitrary page IDs.

The seed job processes each missing profile/title assignment through this flow:

```text
manifest entry
      |
      v
resolve Wikipedia redirect and canonical page identity
      |
      v
fetch rendered article HTML and source revision metadata
      |
      v
parse and rebuild through a strict allowlist
      |
      v
rewrite relative Wikipedia/Wikimedia links as absolute URLs
      |
      v
persist Babel and source provenance in one transaction
```

The sanitizer keeps content-oriented elements supported by Quill: headings,
paragraphs, lists, quotations, code, emphasis, links, and remote article images.
It removes scripts, styles, event handlers, edit controls, navigation, metadata
panels, infoboxes, tables, reference lists, unsupported attributes, and unsafe
URL schemes. Remote images are not downloaded into local storage.

No plain-text derivative is persisted. Future model code will convert the
stored sanitized HTML to temporary plain text at training or embedding time.

## Seed Job Behavior

The dashboard is the only operator-facing way to start generated Wikipedia
population. There is no `seed` CLI command and normal backend startup never
contacts Wikipedia.

`POST /admin/api/v1/seed` creates a background run and returns immediately. The
dashboard polls its status. Only one run may be active at a time.

The job performs schema and manifest preflight checks, finds missing stable
seed-assignment UUIDs, and fetches with bounded concurrency, request
timeouts, a descriptive user agent, and limited retry with backoff. Each
article is committed independently so one failure does not discard successful
work.

Existing profile/page assignments are skipped and never overwritten. Pressing
the button again creates a new run that retries only missing assignments. If
the process stops during a run, startup marks that run `interrupted`; resumption
requires another dashboard action.

An article is not persisted if canonical resolution, HTML retrieval, or
sanitization fails. The run records an article-specific error and continues.

## Personal Legacy Migration

`Personal` is visible in Electron from the first database migration and renders
an empty graph until explicitly populated. It is never affected by the
dashboard seed action.

```text
just migrate-personal <source-json>
        |
        v
validate legacy babel-graph JSON
        |
        v
map legacy Babel IDs to stable UUIDs
        |
        v
insert Personal Babels, then valid edges, in one transaction
        |
        v
record source-file digest without modifying the source
```

Repeating a completed migration for the same file digest is a no-op. Invalid
input fails before database writes. This command is the explicit exception to
the rule that generated-data population originates from the dashboard.

## Dashboard

The C++ backend serves a local, mostly black dashboard at `/admin`. It uses plain
HTML, CSS, and JavaScript and has no frontend framework.

The initial dashboard contains:

- Current population state.
- One `Seed 80 Babels` action.
- A progress bar and completed/expected counts.
- The current profile/article when available.
- A concise list of article-specific errors.
- A retry state after partial completion.

There is no separate operational-test control in this milestone. Schema,
manifest, Wikipedia, sanitizer, and persistence checks occur as seed preflight
or seed stages and surface through the same progress/error model. Later metrics
and monitoring panels can extend this control plane without changing seed
services. Any future generated-data population or operational-test action must
also be initiated from this dashboard; deterministic automated tests and the
explicit `Personal` legacy migration remain external.

## HTTP Contracts

### Electron-facing API

`GET /api/v1/profiles`

Returns stable profile selector DTOs containing ID, display name, color, and
order.

`GET /api/v1/profiles/{profileId}/graph`

Returns the selected profile plus `babels` and `edges`. An unseeded profile
returns HTTP 200 with empty arrays.

### Admin API

`GET /admin`

Returns the dashboard shell and a per-process administration nonce.

`GET /admin/api/v1/seed`

Returns the active or latest seed status, aggregate counts, current item, and
errors.

`POST /admin/api/v1/seed`

Starts a seed run and returns HTTP 202 with its run ID. If a run is already
active, the API returns that conflict and enough status for the dashboard to
attach to the existing run.

DTOs are stable application contracts. Database rows, Wikipedia responses, and
Drogon types are not exposed.

## Electron Integration

Electron remains the supported application. Browser/localStorage mode is not a
fallback for database-backed profiles.

```text
renderer
  window.electronAPI.listProfiles()
  window.electronAPI.loadProfileGraph(profileId)
          |
          v
preload allowlist
          |
          v
Electron main process
          |
          v
localhost backend API
```

Every app launch starts on the profile selector. The selector is a vertical
wheel on a mostly black screen. It lists `Personal` first followed by the 20
generated profiles in stable order and uses each profile's color as its accent.
There is no authentication or persisted session.

Selecting a profile replaces `State.babels` and `State.edges` with the graph
response and opens the existing visualization. Before seeding, that response is
empty and the existing empty state is shown. After seeding, each profile shows
its four imported Babels. `Personal` remains empty until its explicit migration
command succeeds.

Creation, editing, deletion, and edge mutation are disabled for database-loaded
profiles in this milestone. Navigation and read-only viewing remain available.
If the backend is unavailable, Electron shows an explicit connection error and
does not fall back to localStorage.

## Local Operation

The repository adds a `Justfile` with these primary recipes:

```text
just db-up
    start PostgreSQL/pgvector through Docker Compose

just start
    build the C++ backend
    apply database migrations and install the profile roster
    start the local backend/dashboard
    wait for backend readiness
    start Electron

just test
    run deterministic automated tests outside the dashboard

just migrate-personal <source-json>
    validate and import one legacy graph into Personal
    leave the source file untouched
```

`just start` does not seed and does not contact Wikipedia. The dashboard seed
button is the population boundary.

## Reliability and Local Security

- Bind the backend only to `127.0.0.1`.
- Do not enable CORS.
- Require the dashboard's per-process nonce in a custom header for seed
  mutations.
- Validate `Host` and `Origin` on administration mutations.
- Escape seed status and error text before rendering.
- Reject unsafe URLs and markup during HTML reconstruction.
- Disable the seed action while a run is active.
- Use database uniqueness and transactions as the final idempotency boundary.
- Fail `just start` clearly when PostgreSQL or migrations are unavailable.
- Report backend unavailability explicitly in Electron.

## Technology Choices

- C++20 and CMake.
- A `vcpkg.json` manifest with a pinned vcpkg baseline for reproducible local
  builds.
- Drogon as the thin HTTP server/static asset adapter.
- libpqxx behind PostgreSQL repository adapters.
- libcurl for Wikipedia HTTPS requests.
- libxml2 for HTML parsing and allowlist reconstruction.
- Catch2 registered with CTest.
- Plain HTML/CSS/JavaScript for the administration dashboard.
- Existing Electron, Three.js, and Quill dependencies for the desktop app.

## Verification

Automated tests do not run through the dashboard.

### Unit tests

- The database roster has `Personal` plus exactly 20 stable generated profiles,
  and the seed manifest has 80 profile/title assignments.
- Every seed assignment has a stable UUID and every one of the 21 profiles has
  a distinct, black-background-safe color.
- `WikipediaImportService` validates ownership, returns `Imported` or
  `AlreadyExists`, and persists no partial record after a typed failure.
- Sanitization preserves supported content and removes scripts, event handlers,
  unsafe URLs, unsupported structures, and unwanted Wikipedia chrome.
- Seed behavior imports missing assignments, skips existing assignments, and
  retries partial failures.
- Background-job state transitions and single-run exclusion are correct.
- DTO serialization does not expose database or framework types.

### PostgreSQL integration tests

- Migrations install `Personal` plus all 20 generated profiles in an otherwise
  empty database.
- A fresh profile graph contains empty Babel and edge arrays.
- Source uniqueness prevents duplicate profile/page imports.
- Edge ownership constraints reject cross-profile endpoints.
- Babel and source provenance commit atomically.
- Seed progress and errors survive repository reloads.
- Personal migration is transactional, idempotent by source digest, and never
  changes its source file.

### HTTP contract tests

- Profile list and empty/populated graph responses are stable.
- Seed creation returns immediately and status advances through valid states.
- Concurrent seed requests do not start duplicate jobs.
- Admin nonce, host, and origin validation reject invalid mutations.

### Electron adapter tests

- Profile DTOs populate `Personal` first and the 20 generated profiles in stable
  order.
- Selecting an unseeded profile replaces local state with an empty graph.
- Selecting a seeded profile loads only that profile's four Babels.
- Mutation controls are unavailable in read-only profile mode.

### Acceptance flow

```text
just db-up
just start
    -> dashboard is available
    -> Electron lists Personal plus 20 generated profiles

select any profile before seeding
    -> empty graph

press the dashboard seed button
    -> background progress and article errors are visible

select profiles after seeding
    -> four read-only Babels per completed profile

press the seed button again
    -> existing assignments are skipped
    -> only missing articles are retried

just migrate-personal /path/to/babel-graph.json
    -> Personal loads the migrated graph
    -> the source file remains unchanged
```

Live Wikipedia is not part of deterministic automated tests. Dashboard-driven
seeding is the operational path that verifies live Wikipedia access.

## Implementation Planning Constraint

The implementation plan must begin with a subagent orchestration map. It must
identify workstreams that can be dispatched concurrently without editing the
same files or depending on unfinished shared contracts. Likely independent
tracks include backend domain/schema, Wikipedia ingestion/sanitization, admin
dashboard, Electron profile selection, and verification, but the plan must
sequence shared contracts before parallel dispatch and call out integration
points explicitly.
