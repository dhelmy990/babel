# Babel

Babel is a local-first knowledge graph application. A C++ modular monolith owns
profiles, Babels, edges, snapshot-backed Wikipedia ingestion, and administrative commands in
PostgreSQL. Electron is the supported user application. A separate, narrow web
dashboard starts and monitors population from a commit-pinned private Hugging
Face dataset and is the production operator surface for representative online
experiments.

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
export BABEL_HF_REVISION='e1acc648fcace8820dd5ee70bae9216ea4334555'
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

## Run The Online Experiment

The same `/admin` dashboard offers an online experiment panel. Choose the
original immutable model or an immutable child, keep the default `pgvector`
retrieval backend, select June only or the representative June → July scenario,
and start a run. The defaults are 50 creators, 100 events per month, and run
seed 0. A second active run is rejected. **Graceful stop** records stop intent;
it never kills the worker or overwrites, deletes, or promotes the original
model.

The dashboard polls durable run status and typed observable activity: created
Babels, candidate/include/exclude/ignore decisions, accepted edges, event rate,
Kafka offset/lag, trainer step and rolling rank loss, checkpoint state, serving
sync, and active model version. Hidden graph/PPR/clickstream/profile/random
inputs and Colab losses are not part of the backend DTOs.

The backend owns persistence and calls the authenticated loopback
`babel-online serve` worker with only the run UUID after the immutable launch
JSON is committed. The Python worker reloads and verifies the immutable launch
from PostgreSQL, then acquires the full June/July bundle from the private Hub at
the exact recorded commit. It has no live-Wikipedia or arbitrary-local-data
fallback. See [the experiment runbook](docs/runbooks/online-experiment.md).

Migrations 005 through 008 are schema-only: they never insert placeholder model
provenance. The real-model mode downloads and verifies the pinned trained Qwen
adapter/projection, attaches it to the exact upstream base revision, and serves
normalized 100-dimensional vectors. The deterministic fixture remains available
only for bounded smoke tests.

## Run The Scaled Architecture Experiment

The dashboard's separate **Scalability trial** panel saves immutable experiment
and nine-condition identities. Its default is a same-host split between the
recommendation server and Kafka-consuming trainer, with pgvector, 50 creators,
10,000 synthetic-created Babels, 50 concurrent users, interleaved creation and
recommendation, independent 0.40 walk start/continuation draws, depth 2, and a
ten-request cap. Paired sliders and numeric inputs allow safe defaults or
explicit custom populations.

Formal measurement cannot begin merely because population reaches its target.
The dashboard requires matching model, dataset, 100-dimensional vector count,
and checksum evidence plus explicit operator approval. It then displays
independent persisted progress, topology and resource placement, request and
walk/cache telemetry, Kafka/trainer health, model staleness, activation spikes,
saved results, and all three interference ratios. Graceful stop drains work and
preserves the last valid serving model. The immutable original and compatible
post-run children remain separately selectable.

Accepted runs can be packaged beneath an immutable private-Hub
`runs/<run-id>/` path. Publication rejects secrets and existing accepted paths,
then reloads JSON evidence, the reusable child descriptor and serving state,
and one row from every Parquet file at the returned commit. See [the scaled
experiment runbook](docs/runbooks/scaled-experiment.md)
and [operator handoff](prompts/scaled-experiment-handoff.md). A tiny 3-by-3 is
smoke evidence only; complete a controlled concurrent run before publishing or
claiming a formal result.

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

Training-time conversion from sanitized HTML into model input is derived
transiently. Training, model serving, simulation, and parameter synchronization
remain a separate Python worker boundary. The backend stores experiment control
state and exposes only the dashboard operator contract; Python is never embedded
into the C++ process.

Personalized PageRank (PPR), production-scale vector-indexing jobs, and
non-representative monitoring remain deferred. The representative simulator,
retrieval, serving, and training path stays behind the separate Python worker
boundary rather than entering the modular monolith.

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
