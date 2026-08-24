# Babel Codebase Documentation

## System Shape

Babel currently has three deployed local processes:

1. PostgreSQL with pgvector, started by Docker Compose.
2. A C++ modular-monolith backend, bound to `127.0.0.1:8787`.
3. Electron, the supported profile-selection and graph-viewing application.

The backend also serves a small local operations dashboard at `/admin`. The
dashboard is not part of Electron and has one operational responsibility:
starting and monitoring generated-profile Wikipedia population.

There is no authentication in this release. Loopback binding, strict local
request checks, an instance-local admin nonce, and a single-backend database
lease define the local security boundary.

## Architectural Boundaries

The modular monolith contains only application-backend behavior and its
administrative commands:

- creator/profile manifest installation
- profile and graph queries
- Wikipedia resolution, fetch, sanitization, and import
- background seed orchestration and status
- explicit legacy migration into Personal
- database migrations and local HTTP composition

Training, model serving, and parameter synchronization are separate boundaries
from day one. They are not linked into the backend, represented as backend
modules, or deployed yet. The current slice requires no GPU.

Recommendations, personalized PageRank (PPR), FAISS or other vector-indexing
jobs, simulator behavior, and metrics/monitoring beyond the current seed status
are explicit non-goals for this slice. They must not be folded into the
application modular monolith as incidental extensions.

PostgreSQL stores sanitized Quill-compatible HTML as the only content
representation. It does not store normalized text or embedding input. A future
training component must derive its model input from that HTML at training time
without adding a second persisted content copy to this application schema.

## Codebase Map

```text
CMakeLists.txt / CMakePresets.json    C++20 build and dev/test presets
vcpkg.json                            pinned native dependency manifest
compose.yaml                          local pgvector PostgreSQL service
Justfile                              db, build, test, start, migration workflows

backend/
  include/babel/domain/               typed IDs and domain models
  include/babel/application/          ports, DTOs, services, manifest
  include/babel/adapters/             PostgreSQL, MediaWiki, HTML adapters
  include/babel/http/                 profile/admin controllers and security
  include/babel/runtime/              command parsing and composition root
  src/application/                    use-case implementations
  src/adapters/postgres/              migrations and repositories
  src/adapters/wikipedia/             MediaWiki HTTP adapter
  src/adapters/html/                  libxml2 allowlist sanitizer
  src/http/                           JSON/static dashboard controllers
  src/runtime/                        process lease, server, seed runner
  migrations/                         ordered PostgreSQL migrations
  admin/                              dark dashboard HTML, CSS, and JavaScript
  tests/unit/                         service and domain tests
  tests/integration/                  PostgreSQL and HTTP contract tests
  tests/fixtures/                     fixed Wikipedia and legacy inputs

main.js                               secure Electron main process and IPC
preload.js                            two-method read-only renderer bridge
index.html / styles.css               profile selector and graph shell
js/profile-selector.js                backend DTO validation and graph mapping
js/ui.js                              profile wheel, loading, switching, overlays
js/state.js                           renderer graph and mutation guards
js/app.js                             Three.js graph coordinator
js/rendering.js                       Three.js objects, materials, shaders
js/graph-utils.js                     DAG and reciprocal-edge algorithms
js/editor.js                          legacy editor UI, disabled for loaded profiles
js/persistence.js                     disabled persistence facade (all methods are no-ops)
tests/js/                             dashboard, Electron, Justfile, selector tests
```

## Domain Vocabulary

**Creator / profile:** An owner and selector entry. The fixed manifest contains
Personal at order 0 and 20 generated creator profiles at orders 1 through 20.

**Babel:** An owned graph node with a title, color, revision, content hash, and
sanitized Quill-compatible HTML body.

**Edge:** An owned directed relationship between two Babels of the same creator.
Cross-owner edges and self-loops are rejected. Reciprocal pairs represent an
association and are excluded from DAG layout.

**Seed assignment:** A stable manifest association among one generated creator,
one declared Wikipedia title, and one assignment UUID. There are 80 assignments,
four per generated creator. Personal has none.

**Seed run:** A durable snapshot of all assignments and their outcomes. A run can
be queued, running, completed, completed with errors, failed, or interrupted.

**Wikipedia source:** Provenance attached to a Babel: numeric page ID, canonical
HTTPS URL, optional source revision, fetch time, declared title, and optional
seed assignment.

**Personal migration:** A digest-addressed, atomic conversion of one legacy JSON
file into Babels and edges owned only by Personal.

## Local Lifecycle

`just db-up` is an explicit prerequisite. It starts only the Compose `postgres`
service and its dedicated `babel_postgres_data` volume.

`just start` performs this sequence:

1. Configure and build the development preset.
2. Run the backend `migrate` command.
3. Generate a random 64-hex instance token.
4. Start `babel_backend serve` as a child.
5. Poll `/health` until the response contains that exact child token and the
   child is still alive.
6. Print `http://127.0.0.1:8787/admin`.
7. Launch Electron through `npm start`.
8. On interruption or Electron exit, terminate the backend child and remove its
   temporary log.

It neither starts Docker nor starts a seed run. PostgreSQL intentionally outlives
the app command.

The backend takes a PostgreSQL advisory lock before migrations, Personal import,
or serving. Concurrent application-backend instances fail before database
mutation rather than sharing mutable runtime state.

## HTTP Contract

The server is fixed to loopback and intentionally emits no CORS allow header.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | health plus per-process instance token |
| `GET` | `/api/v1/profiles` | ordered 21-profile roster |
| `GET` | `/api/v1/profiles/{uuid}/graph` | complete owned graph, including empty graphs |
| `GET` | `/admin` | nonce-injected dashboard document |
| `GET` | `/admin/dashboard.css` | dashboard stylesheet |
| `GET` | `/admin/dashboard.js` | dashboard controller |
| `GET` | `/admin/seed-status.js` | seed status view model |
| `GET` | `/admin/api/v1/seed` | latest durable seed status |
| `POST` | `/admin/api/v1/seed` | start or attach to a seed run |

Profile responses use camelCase JSON. The graph response contains `profile`,
`babels`, and `edges`; an existing profile with no content returns empty arrays,
not a not-found error.

Admin mutations require the exact local Host and Origin plus the random nonce
injected into the rendered dashboard. Assets and admin JSON use `Cache-Control:
no-store`. A missing or incorrect nonce returns 403. Starting while another run
is active returns 409 with the active status, which the dashboard treats as an
attachment rather than a second job.

The admin mutation endpoint exists to support the served dashboard. Operational
practice is stricter than HTTP reachability: population is started only by
pressing the visible dashboard button, never by scripts, CLI commands, Electron,
or direct API requests.

## Wikipedia Population

The manifest gives the seed service 80 stable assignments. The dashboard button
creates a run that snapshots all 80 items, then the background runner processes
at most four assignments concurrently.

For each missing assignment the application:

1. Resolves the declared title through the MediaWiki API.
2. Fetches the rendered article by numeric page ID.
3. Verifies that the fetched identity matches the resolved page.
4. Sanitizes the rendered HTML through the Quill allowlist.
5. Inserts the owned Babel and Wikipedia source atomically.
6. Attaches the stable assignment and declared title.

The canonical reusable application operation is
`WikipediaImportService::importWikipediaBabel(creatorId, pageId, context)`. Its
page-ID overload is the predefined path for importing a known Wikipedia ID into
a profile. The seed path resolves manifest titles first and then calls this
numeric-ID operation with seed context. There is no public Electron or dashboard
form for arbitrary page IDs yet.

Unavailable MediaWiki requests are retried twice inside one assignment with a
fixed backoff. Permanent failures are recorded immediately. Assignment failures
do not discard successful imports; the run reaches `completed_with_errors` and
the dashboard shows every durable error.

A later dashboard button press snapshots all 80 assignments again. Already
attached assignments become skipped without a network request, while missing
assignments are retried. PostgreSQL uniqueness constraints prevent duplicate
owner/page and seed-assignment rows.

## HTML Storage And Sanitization

The libxml2 adapter reconstructs content from an allowlist instead of trusting
the source tree. It removes executable markup, event handlers, unsafe URL
schemes, Wikipedia UI/citation subtrees, and unsupported containers while
preserving safe prose, headings, lists, code, links, and HTTPS images in a
Quill-compatible representation. Table subtrees are dropped rather than stored.

`babels.content_html` stores only the sanitized result. `babel_sources` stores
provenance separately. There is no normalized-text column. Renderer helpers may
derive plain text for presentation, but they are not a training implementation
or persisted embedding pipeline.

## Personal Migration

`just migrate-personal /absolute/path.json` builds, verifies the schema, and runs
the explicit migration command. The service accepts a bounded regular file,
rejects symlinks and malformed graph invariants, hashes its exact bytes, and does
not rewrite the source or its mtime.

Legacy Babel and edge UUIDs are deterministic UUIDv5 values derived from the
Personal creator identity and length-prefixed legacy identities. Reordering the
legacy JSON arrays therefore preserves entity identity and connectivity.

Blank legacy descriptions become canonical empty Quill HTML. Nonblank content
passes through the same sanitizer as Wikipedia content. The repository claims
the SHA-256 digest and writes the complete Personal graph in one transaction. A
second import of the same bytes returns `already_migrated`; a failed graph write
rolls back both graph rows and the digest claim.

Personal cannot be populated by the dashboard. Generated seed assignments
cannot be attached to Personal. Stop the serving backend before invoking the
command because the application permits only one active backend instance.

## Electron Profile Flow

Electron is the supported application. The main process uses context isolation,
sandboxing, no renderer Node integration, guarded IPC, blocked arbitrary
navigation, and credential-free HTTPS-only external links.

The preload bridge exposes exactly:

```text
listProfiles()
loadProfileGraph(profileId)
```

Both methods issue bounded, timeout-controlled JSON GETs to the loopback backend.
They reject redirects, unexpected origins, non-JSON bodies, oversized responses,
and malformed DTOs.

Startup loads the 21-profile roster and displays Personal first in a colored
wheel. Wheel motion, arrow keys, mouse selection, and Enter change or open the
active profile. Selecting a profile replaces renderer state with the backend
graph, including a legitimate empty graph. Switch Profile clears transient graph
selection and returns to the wheel.

Every backend-loaded graph sets `State.isReadOnlyProfile`. State mutation
methods, editor writes, edge changes, creation, deletion, and persistence paths
then reject work. The app does not call the legacy localStorage loader as an
offline fallback. A stopped backend produces a visible connection error and
retry action.

## PostgreSQL Schema

- `creators`: stable selector identities, colors, kind, and order
- `babels`: owned sanitized content and revision/hash metadata
- `babel_sources`: one-to-one Wikipedia provenance and seed attachment
- `edges`: same-owner directed graph relationships
- `seed_runs`: durable run lifecycle
- `seed_run_items`: assignment snapshot, attempts, result, and error detail
- `legacy_migrations`: exact source digest and imported Personal counts
- `schema_migrations`: applied migration versions

The pgvector extension is enabled now so the database boundary is ready for
eventual vector data, but this release creates no embeddings, runs no models, and
performs no parameter synchronization.

## Testing And Operations

`just test` builds the test preset, runs Catch2 tests through CTest, and runs the
Node test suite. Native integration tests exercise PostgreSQL transactions,
constraints, locks, repositories, and HTTP-controller contracts. Unit tests use
fixed MediaWiki and legacy fixtures; automated tests do not populate from the
live network.

Population and its operational retry check are performed through the rendered
dashboard only. This rule does not move the automated C++ or Node test suite into
the dashboard.

Useful checks while `just start` is running:

```bash
curl --fail --silent http://127.0.0.1:8787/health
curl --fail --silent http://127.0.0.1:8787/api/v1/profiles
```

For live MediaWiki 429 responses, keep the successful partial imports, wait for
the remote rate limit to cool down, then press the dashboard retry button. The
retry is idempotent and targets only assignments that are still missing.
