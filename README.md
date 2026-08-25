# Babel

Babel is a local-first knowledge graph application. A C++ modular monolith owns
profiles, Babels, edges, snapshot-backed Wikipedia ingestion, and administrative commands in
PostgreSQL. Electron is the supported user application. A separate, narrow web
dashboard starts and monitors population from a commit-pinned private Hugging
Face dataset.

The initial roster is fixed at 21 profiles: `Personal` followed by 20 generated
creator profiles. Profiles exist after migration even when they have no Babels.
There is no end-user authentication in this local release. The backend alone
uses a private-dataset read token for dashboard seeding.

## Prerequisites

- Linux with a C++20 compiler
- CMake 3.25 or newer
- vcpkg; `just` defaults to `$HOME/.cache/vcpkg`, or honors `VCPKG_ROOT`
- Docker with the Compose plugin
- Node.js and npm
- `just`, `curl`, and `openssl`
- A graphical session for Electron; `xvfb-run` is sufficient for headless smoke
  testing

Install JavaScript dependencies once:

```bash
npm install
```

The CMake manifest installs the native dependencies through vcpkg during
configuration.

## Start Locally

Start PostgreSQL explicitly, then start the application:

```bash
just db-up
export HF_TOKEN='<private dataset read token>'
export BABEL_HF_REVISION='3fc8c1a2d84d6c8d069876ed27f91d6ead7fab2b'
just start
```

`just start` builds the C++ backend, applies migrations, starts the loopback
service at `http://127.0.0.1:8787`, waits for that exact child to become healthy,
prints the dashboard URL, and launches Electron. Interrupting the command stops
the backend and Electron. PostgreSQL remains running.

`just start` deliberately does **not** run `db-up` and does **not** seed. This
makes database lifecycle and authenticated snapshot population explicit. The
backend consumes `HF_TOKEN`; the token is never returned to the browser, logged,
or stored in PostgreSQL.

## Seed From The Dashboard

Open [http://127.0.0.1:8787/admin](http://127.0.0.1:8787/admin) while `just start`
is running, then press **Seed 80 Babels**. This rendered dashboard button is the
only supported way to start operational Wikipedia population. The backend pins
the configured Hugging Face revision once, verifies and caches the small seed
catalog, and only then begins item work. It never falls back to live MediaWiki.
Do not seed from a CLI, Electron, or a direct API call.

The dashboard starts a background job and shows persisted progress, the current
profile/article, and live errors. It remains usable if individual imports fail:

- A successful assignment is durable and is not imported again.
- A later button press retries missing assignments and marks existing ones
  skipped for that run.
- A second dashboard request while a run is active attaches to the active run.
- Unavailable Hugging Face acquisition or checksum failures leave the run failed
  before any item begins; correct the source or connectivity and press retry.
- Profiles with failed assignments remain selectable and display every durable
  success they have, which may still be an empty graph.

The dashboard never populates `Personal`.

## Migrate Personal Data

Stop `just start` before running an administrative command, then migrate a copy
of a legacy graph into the Personal profile:

```bash
just migrate-personal /absolute/path/to/legacy-graph.json
```

The command reads but never rewrites the source. It hashes the exact bytes,
sanitizes legacy rich text into the same Quill-compatible HTML representation,
and atomically imports the Personal Babels and edges. Reciprocal edge pairs are
valid, while other directed cycles and complete profile JSON over 64 MiB are
rejected before database writes so Electron can always load an imported graph.
Running the command again with identical bytes reports `already_migrated` and
creates no duplicates.

Only an explicit `just migrate-personal` command can populate Personal. The
dashboard and generated-profile seed job cannot target it.

## Electron Scope

Electron opens on a mostly black profile wheel. Mouse wheel, arrow keys, hover,
or click select one of the 21 color-coded profiles; Enter or click loads it.
Switch Profile returns to the selector.

The application reads profile lists and graphs from the loopback backend. Loaded
graphs are intentionally read-only: creation, deletion, editing, edge mutation,
and persistence are disabled. There is no localStorage fallback. If the backend
is unavailable, Electron shows a connection error and retry action instead of
loading stale local data.

## Tests

With PostgreSQL already running:

```bash
just test
```

This configures and builds the test preset, runs all Catch2 tests through CTest,
then runs the Node test suite. Automated dashboard-source tests use a local JSONL
fixture and fake Hub transport; they do not contact Hugging Face or MediaWiki.
Live population and its operational retry check
remain dashboard-only.

## Storage Contract

PostgreSQL stores all profiles, Babels, edges, pinned Hugging Face/Wikipedia
provenance, seed state, and Personal migration digests. Babel content is stored only as sanitized,
Quill-compatible HTML. The application does not store a normalized/plain-text
copy for embeddings.

Training-time conversion from sanitized HTML into model input belongs to a
future training boundary and must be derived transiently. Training, model
serving, and parameter synchronization are separate architectural boundaries
from day one and are not deployed by this repository yet. A GPU is not required
for this application slice.

Also deferred are recommendations, personalized PageRank (PPR), FAISS or other
vector-indexing jobs, simulator behavior, and metrics/monitoring beyond the seed
status dashboard. None belongs in the current modular monolith.

## Troubleshooting

- `connection refused` from the backend: run `just db-up`, wait for PostgreSQL to
  become healthy, then retry `just start`.
- `another backend instance is active`: stop the existing `just start` or backend
  process before running migrations or Personal import.
- backend reports `HF_TOKEN is required`: export a private-dataset read token in
  the shell that launches `just start`; never put it in browser configuration.
- dashboard source acquisition fails: verify `BABEL_HF_REVISION`, repository,
  configuration, artifact checksum, and token access, then retry. There is no
  live MediaWiki fallback.
- Electron shows `Unable to connect`: confirm `http://127.0.0.1:8787/health`
  responds, then use the profile selector retry action.
- Docker Snap cannot read a checkout under a hidden directory: use a visible
  worktree or set `COMPOSE_FILE` to an equivalent visible `compose.yaml`.

See [documentation.md](documentation.md) for the codebase map, runtime flows,
HTTP contracts, and architectural boundaries.
