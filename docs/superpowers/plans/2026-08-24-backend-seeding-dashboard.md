# Backend Seeding Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local C++ backend that provisions 21 selectable profiles, imports 80 fixed Wikipedia articles through a dashboard-driven background job, migrates legacy data into `Personal` through an explicit command, and lets Electron load each profile read-only.

**Architecture:** A C++20 modular monolith owns application queries, Wikipedia ingestion, administrative seeding, and Personal migration behind ports. PostgreSQL is authoritative; Drogon exposes a localhost API and dark admin dashboard; Electron reaches profile DTOs only through its existing main/preload IPC boundary. Training, model serving, parameter synchronization, embeddings, FAISS, and PPR remain outside the executable.

**Tech Stack:** C++20, CMake, pinned vcpkg, Drogon, libpqxx, libcurl, libxml2, OpenSSL, tl::expected, Catch2/CTest, PostgreSQL 18 with pgvector 0.8.6, Docker Compose, Just, Electron 33, plain HTML/CSS/JavaScript.

## Global Constraints

- Bind HTTP only to `127.0.0.1:8787`; do not enable CORS.
- PostgreSQL is the source of truth. Do not restore renderer localStorage fallback.
- Install `Personal` plus exactly 20 generated profiles before any Wikipedia seed run.
- The dashboard is the sole operator surface for generated Wikipedia population and ingestion checks.
- `Personal` is never seeded from the dashboard; only `just migrate-personal <source-json>` may populate it in this milestone.
- Every Wikipedia article import must pass through `WikipediaImportService::importWikipediaBabel(CreatorId, WikipediaPageId)` or its seed-context overload.
- Store only sanitized Quill-compatible HTML. Do not store normalized text or Quill Delta.
- Dashboard seed runs import all 80 assignments from `prompts/wikipedia_user_profiles.md`, create no edges, and never overwrite an existing import.
- Electron is read-only after profile selection: no create, edit, delete, save, or edge mutation.
- Automated tests must not contact live Wikipedia and must not be launched from the dashboard.
- Do not add model, training, synchronization, vector-index, PPR, Kafka, Flink, or authentication code.
- Preserve the existing untracked `.gitignore` and `monlith.pdf` unless the user separately requests changes.

## Subagent Orchestration Map

Before implementation, the orchestrator must invoke `using-git-worktrees` and create one isolated worktree/branch per parallel lane. Parallel agents must not work in the same worktree or commit to the same branch.

```text
SEQUENTIAL FOUNDATION
  Task 1: build skeleton + immutable domain/application contracts
       |
  Task 2: SQL schema + 21-profile/80-assignment manifest
       |
       +------------------- WAVE 1 --------------------+
       |                      |                        |
       v                      v                        v
  Agent A / Task 3       Agent B / Task 4        Agent C / Task 5
  PostgreSQL adapters    Wikipedia + sanitizer   Dashboard assets
       |                      |                        |
       +----------------------+- merge/review --------+
                              |
                         Task 6 sequential
                    canonical import service
                              |
       +------------------- WAVE 2 --------------------+
       |                      |                        |
       v                      v                        v
  Agent A / Task 7       Agent B / Task 8        Agent C / Task 9
  Seed job + status      Personal migration      Electron selector
       |                      |                        |
       +----------------------+- merge/review --------+
                              |
                         Task 10 sequential
                    HTTP composition + Justfile
                              |
                         Task 11 sequential
                    full verification + docs
```

| Wave | Concurrent tasks | Safe file ownership | Integration gate |
|---|---|---|---|
| Foundation | Tasks 1 then 2 | Orchestrator owns root build files, shared headers, migrations, and manifest | Both commits must pass unit tests before branching |
| Wave 1 | Tasks 3, 4, 5 | `adapters/postgres`, `adapters/wikipedia` + `adapters/html`, and `backend/admin` are disjoint | Merge all three, configure once, run unit plus PostgreSQL tests |
| Bridge | Task 6 | `application/wikipedia_import_service.*` only | Import-service tests must pass against fakes and PostgreSQL adapter contract |
| Wave 2 | Tasks 7, 8, 9 | Seed module, legacy module, and Electron/renderer files are disjoint | Merge all three, run C++ tests and Node renderer tests |
| Final | Tasks 10 then 11 | Orchestrator owns composition root, controllers, Justfile, and documentation | Full build, contract tests, acceptance checks, review |

Each parallel agent commits only its owned files. The orchestrator reviews the diff and tests the branch before merging it. If a lane discovers a shared-contract change, it must stop and send the proposed signature to the orchestrator; it must not edit Task 1 headers independently.

## Target File Map

```text
CMakeLists.txt                         root C++ build
CMakePresets.json                     dev/test presets using vcpkg
vcpkg.json                            pinned C++ dependencies
compose.yaml                          PostgreSQL + pgvector service
Justfile                              db, build, start, test, migration recipes

backend/
  CMakeLists.txt
  admin/
    index.html                        seed-only dashboard shell
    dashboard.css                     dark operations visual system
    dashboard.js                      nonce, seed action, polling, rendering
    seed-status.js                    pure status-to-view-model function
  migrations/
    001_core.sql                      pgvector, creators, Babels, edges, sources
    002_seed_jobs.sql                 seed run/item persistence
    003_legacy_migrations.sql         Personal migration idempotency
  include/babel/
    domain/ids.hpp                    typed UUID/page identifiers
    domain/models.hpp                 domain records and enums
    application/dtos.hpp              stable Electron/admin response types
    application/errors.hpp            typed application failures
    application/ports.hpp             repository/source/sanitizer ports
    application/profile_manifest.hpp  Personal plus generated profile catalog
    application/profile_query_service.hpp
    application/wikipedia_import_service.hpp
    application/seed_service.hpp
    application/legacy_migration_service.hpp
  src/
    application/                      service implementations
    adapters/postgres/                migrations and repository adapters
    adapters/wikipedia/               MediaWiki HTTP adapter
    adapters/html/                    libxml2 allowlist sanitizer
    http/                             Drogon controllers and security filter
    runtime/                          configuration and composition root
    main.cpp                          serve, migrate, migrate-personal commands
  tests/
    unit/                             fakes and deterministic service tests
    integration/                      PostgreSQL and HTTP contract tests
    fixtures/                         Wikipedia HTML and legacy JSON fixtures

js/profile-selector.js               selector state and backend graph mapping
tests/js/profile-selector.test.js     renderer contract tests
tests/js/admin-dashboard.test.js      dashboard view-model tests
main.js                               localhost backend IPC adapter
preload.js                            renderer allowlist
js/state.js                           selected profile/read-only state
js/app.js                             profile-driven initialization and guards
js/ui.js                              selector wiring and mutation suppression
js/editor.js                          read-only save guard
js/persistence.js                     remove runtime localStorage loading
index.html                            selector markup and script order
styles.css                            selector styling and read-only states
package.json                          Node test script
README.md                             local operation and acceptance flow
documentation.md                      backend and profile-loading architecture
```

---

### Task 1: Establish the C++ Build and Immutable Contracts

**Files:**
- Create: `CMakeLists.txt`
- Create: `CMakePresets.json`
- Create: `vcpkg.json`
- Create: `backend/CMakeLists.txt`
- Create: `backend/include/babel/domain/ids.hpp`
- Create: `backend/include/babel/domain/models.hpp`
- Create: `backend/include/babel/application/errors.hpp`
- Create: `backend/include/babel/application/dtos.hpp`
- Create: `backend/include/babel/application/ports.hpp`
- Create: `backend/tests/unit/domain_contract_test.cpp`
- Modify: `package.json`

**Interfaces:**
- Consumes: Nothing; this task is the shared-contract root.
- Produces: Typed IDs, domain models, DTOs, `Result<T>`, repository ports, `ArticleSource`, `HtmlSanitizer`, and test/build targets used by every later task.

- [ ] **Step 1: Write the failing domain contract test**

```cpp
#include <catch2/catch_test_macros.hpp>
#include "babel/application/dtos.hpp"
#include "babel/domain/models.hpp"

TEST_CASE("an empty profile graph is a successful DTO") {
    babel::ProfileGraphDto graph{
        .profile = {.id = babel::CreatorId::parse("00000000-0000-5000-8000-000000000000").value(),
                    .display_name = "Personal", .color = "#F4E7D3", .order = 0},
        .babels = {},
        .edges = {}};

    REQUIRE(graph.profile.display_name == "Personal");
    REQUIRE(graph.babels.empty());
    REQUIRE(graph.edges.empty());
}

TEST_CASE("Wikipedia page IDs must be positive") {
    REQUIRE(babel::WikipediaPageId::fromInt(42).has_value());
    REQUIRE_FALSE(babel::WikipediaPageId::fromInt(0).has_value());
}
```

- [ ] **Step 2: Run the test target and verify the expected failure**

Run: `cmake --preset test && cmake --build --preset test && ctest --preset test -R domain_contract --output-on-failure`

Expected: configure or compile fails because the build and headers do not exist.

- [ ] **Step 3: Add the pinned build manifests**

Use C++20 and these vcpkg dependencies:

```json
{
  "name": "babel-backend",
  "version-string": "0.1.0",
  "builtin-baseline": "127402f1c75bb3d5ff6bce04b285faa4930a5aca",
  "dependencies": [
    "boost-uuid",
    "catch2",
    "curl",
    "drogon",
    "libpqxx",
    "libxml2",
    "nlohmann-json",
    "openssl",
    "tl-expected"
  ]
}
```

Keep this baseline unless dependency resolution proves it invalid; any baseline
change must be an explicit reviewed diff accompanied by a clean configure and
build.

Add CMake targets `babel_domain`, `babel_application`, `babel_backend`, and `babel_tests`. Register Catch2 cases with CTest. Add `npm test` as `node --test tests/js/*.test.js` without changing `npm start`.

Use `file(GLOB_RECURSE ... CONFIGURE_DEPENDS)` only inside `backend/CMakeLists.txt`
for `src/*.cpp` and `tests/*.cpp`. This deliberate monorepo-local choice keeps
the parallel lanes from editing a shared build file whenever they add an owned
source file. Enable `-Wall -Wextra -Wpedantic` for project targets without
applying those flags to third-party targets.

- [ ] **Step 4: Define typed IDs and application errors**

```cpp
namespace babel {
struct CreatorId { std::string value; static Result<CreatorId> parse(std::string_view); };
struct BabelId { std::string value; static Result<BabelId> parse(std::string_view); };
struct EdgeId { std::string value; static Result<EdgeId> parse(std::string_view); };
struct SeedRunId { std::string value; static Result<SeedRunId> parse(std::string_view); };
struct SeedAssignmentId { std::string value; static Result<SeedAssignmentId> parse(std::string_view); };
struct WikipediaPageId { std::int64_t value; static Result<WikipediaPageId> fromInt(std::int64_t); };

enum class ErrorCode {
    invalid_argument, not_found, conflict, database_unavailable,
    wikipedia_unavailable, wikipedia_not_found, sanitizer_rejected,
    invalid_legacy_file, internal
};
struct ApplicationError { ErrorCode code; std::string message; };
template <typename T> using Result = tl::expected<T, ApplicationError>;
}
```

Implement equality and hashing for typed UUID IDs. Provide UUID v5 generation using a fixed namespace UUID `6db43f2d-a1dc-5d73-9aeb-9b9d6d79d72b` and OpenSSL SHA-1 so manifest identities are deterministic.

- [ ] **Step 5: Define the shared models, DTOs, and ports**

The shared headers must define these names before parallel work begins:

```cpp
enum class CreatorKind { personal, generated };
enum class SeedRunState { queued, running, completed, completed_with_errors, failed, interrupted };
enum class SeedItemState { pending, resolving, importing, imported, skipped, failed };

struct Creator { CreatorId id; std::string slug; std::string display_name; std::string color; CreatorKind kind; int order; };
struct Babel { BabelId id; CreatorId owner_id; std::string title; std::string content_html; std::string color; std::uint64_t content_revision; std::string content_hash; };
struct Edge { EdgeId id; CreatorId owner_id; BabelId source_id; BabelId target_id; };
struct ResolvedWikipediaPage { WikipediaPageId page_id; std::string canonical_title; std::string canonical_url; };
struct RawWikipediaArticle { WikipediaPageId page_id; std::string canonical_title; std::string canonical_url; std::optional<std::int64_t> revision_id; std::string rendered_html; };
struct SanitizedHtml { std::string value; };

class CreatorRepository {
public:
    virtual Result<bool> exists(CreatorId) = 0;
    virtual Result<Creator> get(CreatorId) = 0;
    virtual Result<std::vector<Creator>> listOrdered() = 0;
};
class GraphRepository {
public:
    virtual Result<ProfileGraphDto> loadGraph(CreatorId) = 0;
};
class WikipediaBabelRepository {
public:
    virtual Result<std::optional<Babel>> findByPage(CreatorId, WikipediaPageId) = 0;
    virtual Result<void> insertWikipediaBabel(const Babel&, const BabelSource&) = 0;
    virtual Result<void> attachSeedAssignment(BabelId, SeedAssignmentId, std::string_view) = 0;
};
struct SeedItemUpdate {
    SeedItemState state;
    std::uint32_t attempt_count;
    std::optional<WikipediaPageId> resolved_page_id;
    std::optional<BabelId> babel_id;
    std::optional<ApplicationError> error;
};
class SeedRunRepository {
public:
    virtual Result<SeedRunId> createRun(
        std::string_view manifest_version,
        std::span<const SeedAssignment> assignments) = 0;
    virtual Result<bool> assignmentExists(SeedAssignmentId) = 0;
    virtual Result<void> recordItemState(
        SeedRunId,
        SeedAssignmentId,
        const SeedItemUpdate&) = 0;
    virtual Result<void> setRunState(SeedRunId, SeedRunState) = 0;
    virtual Result<SeedStatusDto> status(SeedRunId) = 0;
    virtual Result<SeedStatusDto> latestStatus() = 0;
    virtual Result<void> markRunningAsInterrupted() = 0;
};
class LegacyMigrationRepository {
public:
    virtual Result<bool> digestExists(std::string_view sha256) = 0;
    // Return true when this call imported the graph and false when the digest
    // was already claimed; graph rows and the digest commit atomically.
    virtual Result<bool> importPersonalGraph(std::string_view sha256, std::span<const Babel>, std::span<const Edge>) = 0;
};
class ArticleSource {
public:
    virtual Result<ResolvedWikipediaPage> resolveTitle(std::string_view) = 0;
    virtual Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) = 0;
};
class HtmlSanitizer {
public:
    virtual Result<SanitizedHtml> sanitize(std::string_view html, std::string_view canonical_url) = 0;
};
class IdGenerator {
public:
    virtual BabelId newBabelId() = 0;
    virtual EdgeId newEdgeId() = 0;
};
```

Also define `BabelSource`, `ProfileSummaryDto`, `BabelDto`, `EdgeDto`,
`ProfileGraphDto`, and `SeedStatusDto` in the shared headers. `SeedStatusDto`
must represent `not_started` separately from persisted run states.
`SeedItemUpdate` carries the item state, explicit attempt count, optional
resolved page ID, imported Babel ID, and application error. Pending/skipped
updates use attempt zero; each real Wikipedia resolution/import attempt uses
its one-based attempt number, including retries. Creating a run atomically snapshots every
manifest assignment as a pending item and records the immutable declared total
from that same snapshot. Mutable progress counters are derived from item rows
rather than stored independently on `seed_runs`.
`WikipediaBabelRepository::insertWikipediaBabel` must atomically insert `Babel`
and its source row. `GraphRepository::loadGraph` returns an empty successful
graph for a creator with no content.

- [ ] **Step 6: Run formatting, build, and the focused test**

Run: `cmake --preset test && cmake --build --preset test && ctest --preset test -R domain_contract --output-on-failure`

Expected: configure succeeds and `domain_contract` passes.

- [ ] **Step 7: Commit the contract foundation**

```bash
git add CMakeLists.txt CMakePresets.json vcpkg.json backend package.json
git commit -m "build: establish C++ backend contracts"
```

### Task 2: Add the Schema and Deterministic Profile Manifest

**Files:**
- Create: `compose.yaml`
- Create: `backend/migrations/001_core.sql`
- Create: `backend/migrations/002_seed_jobs.sql`
- Create: `backend/migrations/003_legacy_migrations.sql`
- Create: `backend/include/babel/application/profile_manifest.hpp`
- Create: `backend/src/application/profile_manifest.cpp`
- Create: `backend/tests/unit/profile_manifest_test.cpp`

**Interfaces:**
- Consumes: Typed IDs and domain models from Task 1.
- Produces: `ProfileManifest::creators()`, `ProfileManifest::seedAssignments()`, SQL schema, and deterministic roster data used by Tasks 3, 7, 8, and 9.

- [ ] **Step 1: Write the failing manifest test**

```cpp
TEST_CASE("profile manifest contains Personal and 20 generated archetypes") {
    const auto creators = babel::ProfileManifest::creators();
    const auto seeds = babel::ProfileManifest::seedAssignments();

    REQUIRE(creators.size() == 21);
    REQUIRE(creators.front().slug == "personal");
    REQUIRE(creators.front().kind == babel::CreatorKind::personal);
    REQUIRE(seeds.size() == 80);
    REQUIRE(std::ranges::none_of(seeds, [&](const auto& seed) {
        return seed.creator_id == creators.front().id;
    }));
    REQUIRE(seeds.front().declared_title == "Distributed computing");
    REQUIRE(seeds.back().declared_title == "Regulation");
}
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cmake --build --preset test && ctest --preset test -R profile_manifest --output-on-failure`

Expected: compile fails because `ProfileManifest` is absent.

- [ ] **Step 3: Encode the exact creator and seed catalog**

Use deterministic UUID v5 names `creator:<slug>` and `seed:<slug>:<declared-title>`. Assign `Personal` order 0 and the generated profiles orders 1 through 20. Use this exact manifest:

| Order | Slug | Display name | Color | Four Wikipedia titles |
|---:|---|---|---|---|
| 0 | `personal` | Personal | `#F4E7D3` | none |
| 1 | `distributed-systems` | Distributed Systems Creator | `#3DDC97` | `Distributed computing`; `Consensus (computer science)`; `Operating system`; `Database` |
| 2 | `machine-learning-systems` | Machine Learning Systems Creator | `#4CC9F0` | `Machine learning`; `Recommender system`; `Graphics processing unit`; `Artificial neural network` |
| 3 | `programming-languages` | Programming Languages Creator | `#F72585` | `Programming language`; `Compiler`; `Type system`; `Functional programming` |
| 4 | `cybersecurity-networks` | Cybersecurity and Networks Creator | `#FF9F1C` | `Computer security`; `Cryptography`; `Computer network`; `Malware` |
| 5 | `cpu-performance` | Low-Latency CPU and Performance Creator | `#A9DEF9` | `Central processing unit`; `CPU cache`; `Branch predictor`; `Instruction pipelining` |
| 6 | `digital-art` | Digital Art Creator | `#E4C1F9` | `Digital art`; `Computer graphics`; `Generative art`; `Animation` |
| 7 | `classical-visual-arts` | Classical Visual Arts Creator | `#FF6B6B` | `Painting`; `Renaissance art`; `Sculpture`; `Art history` |
| 8 | `film-cinema` | Film and Cinema Creator | `#FFD166` | `Film`; `Cinematography`; `Film editing`; `Screenwriting` |
| 9 | `literature-poetry` | Literature and Poetry Creator | `#06D6A0` | `Literature`; `Novel`; `Poetry`; `Literary criticism` |
| 10 | `theatre-performance` | Theatre and Performance Creator | `#EF476F` | `Theatre`; `Acting`; `Stagecraft`; `Play (theatre)` |
| 11 | `music-composition` | Music and Composition Creator | `#90BE6D` | `Music`; `Music theory`; `Musical composition`; `Electronic music` |
| 12 | `photography-design` | Photography and Graphic Design Creator | `#F9844A` | `Photography`; `Graphic design`; `Typography`; `Visual arts` |
| 13 | `computational-neuroscience` | Computational Neuroscience Creator | `#43AA8B` | `Computational neuroscience`; `Neural coding`; `Artificial neural network`; `Visual perception` |
| 14 | `cognitive-neuroscience` | Cognitive Neuroscience Creator | `#7897C5` | `Cognitive neuroscience`; `Memory`; `Attention`; `Functional magnetic resonance imaging` |
| 15 | `quantitative-finance` | Quantitative Finance Creator | `#F9C74F` | `Algorithmic trading`; `Financial market`; `Derivative (finance)`; `Portfolio (finance)` |
| 16 | `macroeconomics-markets` | Macroeconomics and Markets Creator | `#00BBF9` | `Monetary policy`; `Inflation`; `Interest rate`; `Central bank` |
| 17 | `corporate-finance` | Corporate Finance and Valuation Creator | `#F15BB5` | `Corporate finance`; `Valuation (finance)`; `Financial statement`; `Stock` |
| 18 | `public-policy` | Public Policy and Institutions Creator | `#9BDEAC` | `Public policy`; `Constitution`; `Governance`; `Regulation` |
| 19 | `international-relations` | International Relations Creator | `#F8961E` | `International relations`; `Diplomacy`; `Geopolitics`; `International trade` |
| 20 | `political-economy` | Political Economy Creator | `#B8F2E6` | `Political economy`; `Economic inequality`; `Tax`; `Regulation` |

- [ ] **Step 4: Write the core SQL migrations**

`001_core.sql` must enable `vector` and create `creators`, `babels`, `babel_sources`, and `edges`. Include these final idempotency and ownership constraints:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE babels ADD CONSTRAINT babels_owner_id_id_unique UNIQUE (owner_id, id);
ALTER TABLE babel_sources ADD CONSTRAINT babel_sources_babel_owner_fk
  FOREIGN KEY (owner_id, babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
ALTER TABLE babel_sources ADD CONSTRAINT babel_sources_owner_page_unique
  UNIQUE (owner_id, provider, external_page_id);
CREATE UNIQUE INDEX babel_sources_seed_assignment_unique
  ON babel_sources(seed_assignment_id) WHERE seed_assignment_id IS NOT NULL;
ALTER TABLE edges ADD CONSTRAINT edges_source_owner_fk
  FOREIGN KEY (owner_id, source_babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
ALTER TABLE edges ADD CONSTRAINT edges_target_owner_fk
  FOREIGN KEY (owner_id, target_babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
```

Use text columns with `CHECK` constraints for creator kind and seed states rather than PostgreSQL enums. Add `schema_migrations(version text primary key, applied_at timestamptz not null default now())` for the migration runner.

- [ ] **Step 5: Add the PostgreSQL Compose service**

Use `pgvector/pgvector:0.8.6-pg18-bookworm`, a health check with
`pg_isready`, a named volume, and localhost-only port mapping. Read credentials
from defaults suitable only for local development and expose one
`BABEL_DATABASE_URL` string through the Just recipes.

- [ ] **Step 6: Run manifest and SQL static checks**

Run: `cmake --build --preset test && ctest --preset test -R profile_manifest --output-on-failure`

Run: `docker compose config --quiet`

Expected: manifest test passes and Compose configuration is valid.

- [ ] **Step 7: Commit schema and manifest**

```bash
git add compose.yaml backend/migrations backend/include/babel/application/profile_manifest.hpp backend/src/application/profile_manifest.cpp backend/tests/unit/profile_manifest_test.cpp
git commit -m "feat: define profile roster and backend schema"
```

### Task 3: Implement PostgreSQL Migrations, Repositories, and Profile Queries

**Files:**
- Create: `backend/include/babel/adapters/postgres/postgres_database.hpp`
- Create: `backend/include/babel/adapters/postgres/migration_runner.hpp`
- Create: `backend/include/babel/adapters/postgres/profile_roster_installer.hpp`
- Create: `backend/include/babel/adapters/postgres/postgres_repositories.hpp`
- Create: `backend/src/adapters/postgres/postgres_database.cpp`
- Create: `backend/src/adapters/postgres/migration_runner.cpp`
- Create: `backend/src/adapters/postgres/profile_roster_installer.cpp`
- Create: `backend/src/adapters/postgres/postgres_repositories.cpp`
- Create: `backend/include/babel/application/profile_query_service.hpp`
- Create: `backend/src/application/profile_query_service.cpp`
- Create: `backend/tests/integration/postgres_repository_test.cpp`

**Interfaces:**
- Consumes: Task 1 ports and DTOs; Task 2 migrations and profile manifest.
- Produces: `MigrationRunner::run()`, PostgreSQL repository implementations, `ProfileQueryService::listProfiles()`, and `ProfileQueryService::loadGraph(CreatorId)`.

- [ ] **Step 1: Write the failing PostgreSQL integration test**

```cpp
TEST_CASE_METHOD(PostgresFixture, "migrations install a 21-profile empty roster") {
    resetDatabase();
    migration_runner.run();
    roster_installer.install(babel::ProfileManifest::creators());

    auto profiles = profile_service.listProfiles().value();
    REQUIRE(profiles.size() == 21);
    REQUIRE(profiles.front().display_name == "Personal");

    auto graph = profile_service.loadGraph(profiles.at(1).id).value();
    REQUIRE(graph.babels.empty());
    REQUIRE(graph.edges.empty());
}
```

- [ ] **Step 2: Run the integration test and verify failure**

Run: `docker compose up -d postgres && cmake --build --preset test && ctest --preset test -R postgres_repository --output-on-failure`

Expected: compile fails because PostgreSQL adapters are missing.

- [ ] **Step 3: Implement connection and migration boundaries**

`PostgresDatabase` owns the connection string and creates libpqxx transactions inside adapter methods. `MigrationRunner` creates `schema_migrations`, reads sorted `backend/migrations/*.sql`, and applies each unapplied file in one transaction. Reject duplicate versions and stop at the first failed migration.

- [ ] **Step 4: Implement roster installation and repositories**

Upsert creator identity metadata by stable UUID, but never delete creators that are absent from a newer binary. Implement:

```cpp
Result<std::vector<ProfileSummaryDto>> ProfileQueryService::listProfiles();
Result<ProfileGraphDto> ProfileQueryService::loadGraph(CreatorId profile_id);
```

Order profiles by `selector_order`. Map `babels.content_html` to DTO field `content_html`. Return `not_found` only when the creator UUID does not exist; an existing creator with no Babels returns empty arrays.

- [ ] **Step 5: Test ownership and uniqueness constraints**

Add integration cases that attempt a cross-owner edge and a duplicate `(owner_id, provider, external_page_id)`. Assert PostgreSQL rejects both and leaves counts unchanged.

- [ ] **Step 6: Run focused and full C++ tests**

Run: `ctest --preset test -R "postgres_repository|domain_contract|profile_manifest" --output-on-failure`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the PostgreSQL adapter**

```bash
git add backend/include/babel/adapters backend/src/adapters/postgres backend/include/babel/application/profile_query_service.hpp backend/src/application/profile_query_service.cpp backend/tests/integration/postgres_repository_test.cpp
git commit -m "feat: add PostgreSQL profile repositories"
```

### Task 4: Implement Wikipedia Resolution, Fetching, and HTML Sanitization

**Files:**
- Create: `backend/include/babel/adapters/wikipedia/mediawiki_article_source.hpp`
- Create: `backend/src/adapters/wikipedia/mediawiki_article_source.cpp`
- Create: `backend/include/babel/adapters/html/libxml_html_sanitizer.hpp`
- Create: `backend/src/adapters/html/libxml_html_sanitizer.cpp`
- Create: `backend/tests/fixtures/wikipedia_article.html`
- Create: `backend/tests/fixtures/wikipedia_article_malicious.html`
- Create: `backend/tests/unit/mediawiki_article_source_test.cpp`
- Create: `backend/tests/unit/html_sanitizer_test.cpp`

**Interfaces:**
- Consumes: `ArticleSource`, `HtmlSanitizer`, `ResolvedWikipediaPage`, and `RawWikipediaArticle` from Task 1.
- Produces: Deterministic adapters used by the import service in Task 6.

- [ ] **Step 1: Write failing sanitizer tests**

```cpp
TEST_CASE("sanitizer keeps Quill content and removes executable markup") {
    auto html = fixture("wikipedia_article_malicious.html");
    auto result = sanitizer.sanitize(html, "https://en.wikipedia.org/wiki/Film").value();

    REQUIRE(result.value.contains("<h2>History</h2>"));
    REQUIRE(result.value.contains("https://en.wikipedia.org/wiki/Cinema"));
    REQUIRE(result.value.contains("https://upload.wikimedia.org/"));
    REQUIRE_FALSE(result.value.contains("<script"));
    REQUIRE_FALSE(result.value.contains("onclick"));
    REQUIRE_FALSE(result.value.contains("javascript:"));
    REQUIRE_FALSE(result.value.contains("infobox"));
    REQUIRE_FALSE(result.value.contains("<table"));
}
```

- [ ] **Step 2: Write failing MediaWiki request-shape tests**

Inject an `HttpTransport` fake into `MediaWikiArticleSource`. Assert title resolution requests `action=query`, `redirects=1`, `prop=info`, and `inprop=url`; assert page fetching requests `action=parse`, the numeric `pageid`, `prop=text|revid|displaytitle`, `format=json`, and `formatversion=2`.

- [ ] **Step 3: Run both tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "html_sanitizer|mediawiki_article_source" --output-on-failure`

Expected: compile fails because both adapters are absent.

- [ ] **Step 4: Implement the libcurl MediaWiki adapter**

Use `https://en.wikipedia.org/w/api.php`, percent-encode query values, set connect and total timeouts, follow redirects, and send a descriptive `User-Agent` containing the project name and local-use purpose. Map missing pages to `wikipedia_not_found`, transport/5xx failures to `wikipedia_unavailable`, and malformed JSON to `internal`. Do not retry inside this adapter; Task 7 owns retry policy.

- [ ] **Step 5: Implement allowlist reconstruction with libxml2**

Parse as an HTML fragment and construct a new output tree. Keep only `p`, `br`, `h1`, `h2`, `h3`, `ul`, `ol`, `li`, `blockquote`, `pre`, `code`, `strong`, `b`, `em`, `i`, `u`, `s`, `a`, and `img`. Keep `href`, `src`, `alt`, and `title` only. Permit `https` links and images; convert `/wiki/` links against `https://en.wikipedia.org` and Wikimedia image paths against their parsed absolute source. Drop Wikipedia navigation, edit controls, infoboxes, tables, references, styles, and event attributes.

- [ ] **Step 6: Run sanitizer and source tests**

Run: `ctest --preset test -R "html_sanitizer|mediawiki_article_source" --output-on-failure`

Expected: all selected tests pass without network access.

- [ ] **Step 7: Commit Wikipedia adapters**

```bash
git add backend/include/babel/adapters/wikipedia backend/src/adapters/wikipedia backend/include/babel/adapters/html backend/src/adapters/html backend/tests/fixtures/wikipedia_article*.html backend/tests/unit/mediawiki_article_source_test.cpp backend/tests/unit/html_sanitizer_test.cpp
git commit -m "feat: add Wikipedia ingestion adapters"
```

### Task 5: Build the Seed-Only Dark Dashboard Assets

**Files:**
- Create: `backend/admin/index.html`
- Create: `backend/admin/dashboard.css`
- Create: `backend/admin/dashboard.js`
- Create: `backend/admin/seed-status.js`
- Create: `tests/js/admin-dashboard.test.js`

**Interfaces:**
- Consumes: Fixed admin contract `GET /admin/api/v1/seed` and `POST /admin/api/v1/seed`; HTML-provided nonce in `<meta name="babel-admin-nonce">`.
- Produces: Static assets consumed by Task 10 without changing backend headers.

The admin HTTP DTO deliberately serializes backend `SeedStatusDto.imported` as
the public JSON field `completed`; Task 10 owns this explicit mapping.

- [ ] **Step 1: Write failing view-model tests**

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const { seedViewModel } = require('../../backend/admin/seed-status.js');

test('unseeded state offers the initial action', () => {
  assert.deepEqual(seedViewModel({ state: 'not_started', total: 80, completed: 0, skipped: 0, failed: 0 }), {
    label: 'Seed 80 Babels',
    disabled: false,
    percent: 0,
    summary: 'No Wikipedia Babels imported'
  });
});

test('partial completion offers retry and preserves errors', () => {
  const model = seedViewModel({ state: 'completed_with_errors', total: 80, completed: 77, skipped: 0, failed: 3 });
  assert.equal(model.label, 'Retry 3 missing');
  assert.equal(model.disabled, false);
  assert.equal(model.percent, 96);
});
```

- [ ] **Step 2: Run Node tests and verify failure**

Run: `npm test`

Expected: failure because `seed-status.js` is missing.

- [ ] **Step 3: Implement the pure status mapper**

Handle `not_started`, `queued`, `running`, `completed`, `completed_with_errors`, `failed`, and `interrupted`. Disable the action for queued/running only. Calculate percentage with integer floor and guard total zero.

- [ ] **Step 4: Create the single-action dashboard**

Use CSS variables rooted in a near-black palette, a restrained monospace display face, one warm off-white text color, one status accent, a thin progress rail, and no cards unrelated to seeding. Include one button, current profile/article text, counts, and an escaped error list. Do not add a separate health, test, metric, profile, or arbitrary-page import action.

- [ ] **Step 5: Implement nonce POST and status polling**

On load, fetch status once. On button press, send JSON with `X-Babel-Admin-Nonce`; attach to an existing run on HTTP 409. Poll once per second only while state is queued/running, stop on terminal state, and render errors with `textContent` rather than `innerHTML`.

- [ ] **Step 6: Run dashboard tests**

Run: `npm test`

Expected: dashboard view-model tests pass.

- [ ] **Step 7: Commit dashboard assets**

```bash
git add backend/admin tests/js/admin-dashboard.test.js
git commit -m "feat: add seed operations dashboard"
```

### Task 6: Implement the Canonical Page-ID Import Service

**Files:**
- Create: `backend/include/babel/application/wikipedia_import_service.hpp`
- Create: `backend/src/application/wikipedia_import_service.cpp`
- Create: `backend/tests/unit/wikipedia_import_service_test.cpp`
- Create: `backend/tests/unit/fakes.hpp`

**Interfaces:**
- Consumes: Task 1 ports, Task 3 repositories, Task 4 source/sanitizer adapters.
- Produces: `WikipediaImportService::importWikipediaBabel(CreatorId, WikipediaPageId)` and seed-context overload consumed by Task 7.

- [ ] **Step 1: Write the failing canonical import test**

```cpp
TEST_CASE("page ID import creates one owned sanitized Babel") {
    FakeCreatorRepository creators = withCreator("distributed-systems");
    FakeArticleSource source = withArticle(42, "Distributed computing", "<p onclick='x()'>Safe</p>");
    FakeHtmlSanitizer sanitizer = returning("<p>Safe</p>");
    FakeWikipediaBabelRepository babels;
    WikipediaImportService service(creators, babels, source, sanitizer, fixedUuidGenerator());

    auto first = service.importWikipediaBabel(creators.onlyId(), WikipediaPageId::fromInt(42).value()).value();
    auto second = service.importWikipediaBabel(creators.onlyId(), WikipediaPageId::fromInt(42).value()).value();

    REQUIRE(first.status == ImportWikipediaStatus::imported);
    REQUIRE(second.status == ImportWikipediaStatus::already_exists);
    REQUIRE(babels.insert_count == 1);
    REQUIRE(babels.last_babel.content_html == "<p>Safe</p>");
}
```

- [ ] **Step 2: Add failing error-boundary tests**

Cover unknown creator, Wikipedia failure, sanitizer rejection, and repository failure. Assert no insert occurs for the first three and no partial Babel/source pair remains for repository failure.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R wikipedia_import_service --output-on-failure`

Expected: compile fails because the service is absent.

- [ ] **Step 4: Define the exact service API**

```cpp
enum class ImportWikipediaStatus { imported, already_exists };
struct ImportWikipediaBabelResult {
    ImportWikipediaStatus status;
    BabelId babel_id;
    std::string canonical_title;
};
struct SeedImportContext {
    SeedAssignmentId assignment_id;
    std::string declared_title;
};

Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId, WikipediaPageId);
Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId, WikipediaPageId, SeedImportContext);
```

Define a `WikipediaImporter` interface with these two overloads and make
`WikipediaImportService` implement it, so Task 7 can use a strict fake without
depending on adapters.

The two-argument function delegates to the context-free path. The seed overload persists or attaches the stable assignment ID. Both check owner/page idempotency before fetching. Hash sanitized HTML with SHA-256, initialize revision 1, and use the owner's profile color.

- [ ] **Step 5: Implement atomic persistence and duplicate recovery**

When a page already exists for the owner, return `already_exists`; if a seed context is present and not linked, attach it to the existing source row. On first import, call the repository operation that inserts Babel and source together. Convert database uniqueness races into `already_exists` after re-querying.

- [ ] **Step 6: Run import tests and PostgreSQL tests**

Run: `ctest --preset test -R "wikipedia_import_service|postgres_repository" --output-on-failure`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the import service**

```bash
git add backend/include/babel/application/wikipedia_import_service.hpp backend/src/application/wikipedia_import_service.cpp backend/tests/unit/wikipedia_import_service_test.cpp backend/tests/unit/fakes.hpp
git commit -m "feat: add canonical Wikipedia Babel import"
```

### Task 7: Implement Durable Background Seeding

**Files:**
- Create: `backend/include/babel/application/seed_service.hpp`
- Create: `backend/src/application/seed_service.cpp`
- Create: `backend/include/babel/runtime/seed_job_runner.hpp`
- Create: `backend/src/runtime/seed_job_runner.cpp`
- Create: `backend/tests/unit/seed_service_test.cpp`
- Create: `backend/tests/unit/seed_job_runner_test.cpp`

**Interfaces:**
- Consumes: Profile manifest, `ArticleSource::resolveTitle`, Task 6 import overload, and `SeedRunRepository`.
- Produces: `SeedService::run(SeedRunId)`, `SeedJobRunner::start()`, `SeedJobRunner::currentStatus()`, and durable state consumed by Task 10 admin routes.

- [ ] **Step 1: Write the failing idempotent seed test**

```cpp
TEST_CASE("seed processes only missing assignments") {
    auto manifest = manifestWithThreeAssignments();
    FakeSeedRunRepository runs = withCompletedAssignment(manifest.at(0).id);
    FakeArticleSource source = resolvingRemainingPages();
    FakeWikipediaImportService importer;
    SeedService service(manifest, runs, source, importer, noDelayRetryPolicy());

    auto run_id = runs.createRun("test-v1", manifest).value();
    REQUIRE(service.run(run_id).has_value());
    REQUIRE(importer.calls.size() == 2);
    REQUIRE(runs.status(run_id).imported == 2);
    REQUIRE(runs.status(run_id).skipped == 1);
}
```

- [ ] **Step 2: Write failing job-runner tests**

Assert a second `start()` while running returns `conflict`, terminal runs release the guard, and `markInterruptedRuns()` changes persisted `running` rows to `interrupted` at backend startup.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R "seed_service|seed_job_runner" --output-on-failure`

Expected: compile fails because seed components are missing.

- [ ] **Step 4: Implement per-assignment state transitions**

For each of 80 manifest assignments, persist `skipped` immediately when its stable assignment ID already exists. Otherwise transition `pending -> resolving -> importing -> imported` or `failed`. Store stable error codes and escaped plain messages. Derive run totals from item rows instead of maintaining unsynchronized in-memory counters.

- [ ] **Step 5: Implement bounded retry and concurrency**

Use a maximum of four concurrent article operations. Retry `wikipedia_unavailable` twice with 500 ms then 1500 ms delays. Do not retry `wikipedia_not_found`, sanitizer rejection, invalid manifest data, or database constraint failures. Keep retry timing injectable so unit tests use zero delay.

- [ ] **Step 6: Implement one active `std::jthread` job**

Expose `Result<SeedRunId> start()` and `Result<SeedStatusDto> currentStatus()`.
The runner returns a run ID immediately, owns cancellation-safe thread
lifetime, and persists every transition before publishing status. It never
starts automatically and never resumes interrupted work without a new
dashboard request.

- [ ] **Step 7: Run seed tests**

Run: `ctest --preset test -R "seed_service|seed_job_runner|wikipedia_import_service" --output-on-failure`

Expected: all selected tests pass.

- [ ] **Step 8: Commit background seeding**

```bash
git add backend/include/babel/application/seed_service.hpp backend/src/application/seed_service.cpp backend/include/babel/runtime/seed_job_runner.hpp backend/src/runtime/seed_job_runner.cpp backend/tests/unit/seed_service_test.cpp backend/tests/unit/seed_job_runner_test.cpp
git commit -m "feat: add durable background profile seeding"
```

### Task 8: Implement Explicit Personal Legacy Migration

**Files:**
- Create: `backend/include/babel/application/legacy_migration_service.hpp`
- Create: `backend/src/application/legacy_migration_service.cpp`
- Create: `backend/tests/fixtures/legacy_graph.json`
- Create: `backend/tests/fixtures/legacy_graph_invalid.json`
- Create: `backend/tests/unit/legacy_migration_service_test.cpp`
- Create: `backend/tests/integration/legacy_migration_repository_test.cpp`

**Interfaces:**
- Consumes: Personal creator ID from Task 2, `HtmlSanitizer`, and `LegacyMigrationRepository`.
- Produces: `LegacyMigrationService::migrateFile(std::filesystem::path)` and a result containing imported counts and no-op status; Task 10 wires it to the CLI.

- [ ] **Step 1: Write the failing valid migration test**

```cpp
TEST_CASE("legacy migration targets Personal and preserves edge identity") {
    FakeLegacyMigrationRepository repository;
    FakeHtmlSanitizer sanitizer = passThroughSanitizer();
    LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

    auto result = service.migrateFile(fixturePath("legacy_graph.json")).value();

    REQUIRE(result.status == LegacyMigrationStatus::imported);
    REQUIRE(result.babel_count == 2);
    REQUIRE(result.edge_count == 1);
    REQUIRE(repository.last_owner == personalCreatorId());
    REQUIRE(repository.transaction_count == 1);
}
```

- [ ] **Step 2: Write failing validation and idempotency tests**

Assert malformed JSON, duplicate legacy IDs, invalid colors, and edges referencing missing nodes fail before repository writes. Hash the complete source bytes; a second completed migration with the same SHA-256 returns `already_migrated` and performs no inserts. Verify the fixture bytes are unchanged before and after.

- [ ] **Step 3: Run migration tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R legacy_migration --output-on-failure`

Expected: compile fails because the service is absent.

- [ ] **Step 4: Implement strict legacy parsing**

Require root arrays `babels` and `edges`. Accept legacy Babel fields `id`, `title`, `description`, and `color`; ignore `contentDelta`; sanitize `description` into canonical `content_html`. Validate the entire graph before opening a write transaction.

- [ ] **Step 4a: Make migrated identities stable**

Convert every legacy Babel ID to UUIDv5 using the length-prefixed name
`legacy:<Personal UUID>:babel:<legacy-id-length>:<legacy-id>`. Derive edge IDs
from both length-prefixed endpoint IDs. This keeps Personal graph identities
stable across source ordering and repeated migrations.

- [ ] **Step 5: Implement atomic PostgreSQL import**

Atomically claim the digest with `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
Only the caller that claims it inserts all Personal Babels and valid edges in
that same transaction and returns `true`; a concurrent duplicate performs no
graph writes and returns `false`. Never create `babel_sources` rows for local
legacy content.

- [ ] **Step 6: Run unit and PostgreSQL migration tests**

Run: `ctest --preset test -R "legacy_migration|postgres_repository" --output-on-failure`

Expected: all selected tests pass and fixture checksums remain unchanged.

- [ ] **Step 7: Commit Personal migration**

```bash
git add backend/include/babel/application/legacy_migration_service.hpp backend/src/application/legacy_migration_service.cpp backend/tests/fixtures/legacy_graph*.json backend/tests/unit/legacy_migration_service_test.cpp backend/tests/integration/legacy_migration_repository_test.cpp
git commit -m "feat: add Personal legacy graph migration"
```

### Task 9: Add Electron Profile Selection and Read-Only Graph Loading

**Files:**
- Create: `js/profile-selector.js`
- Create: `tests/js/profile-selector.test.js`
- Modify: `main.js:1-158`
- Modify: `preload.js:1-12`
- Modify: `js/state.js:5-78`
- Modify: `js/persistence.js:5-97`
- Modify: `js/app.js:206-356`
- Modify: `js/ui.js:1-440`
- Modify: `js/editor.js:354-396`
- Modify: `index.html:11-126`
- Modify: `styles.css`

**Interfaces:**
- Consumes: `GET /api/v1/profiles` and `GET /api/v1/profiles/{id}/graph` DTOs fixed by Task 1.
- Produces: profile wheel, IPC methods `listProfiles()`/`loadProfileGraph(id)`, graph-to-State mapping, and read-only guards.

- [ ] **Step 1: Write failing pure mapping tests**

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const { toRendererGraph, orderedProfiles } = require('../../js/profile-selector.js');

test('Personal remains first and an empty graph clears prior state', () => {
  const profiles = orderedProfiles([
    { id: 'generated', displayName: 'Generated', color: '#3DDC97', order: 1 },
    { id: 'personal', displayName: 'Personal', color: '#F4E7D3', order: 0 }
  ]);
  assert.equal(profiles[0].displayName, 'Personal');
  assert.deepEqual(toRendererGraph({ profile: profiles[1], babels: [], edges: [] }), { babels: [], edges: [] });
});

test('backend HTML maps to the existing renderer description field', () => {
  const graph = toRendererGraph({
    profile: { id: 'p', displayName: 'P', color: '#fff', order: 1 },
    babels: [{ id: 'b', title: 'Film', contentHtml: '<p>Film</p>', color: '#fff' }],
    edges: []
  });
  assert.equal(graph.babels[0].description, '<p>Film</p>');
});
```

- [ ] **Step 2: Run Node tests and verify failure**

Run: `npm test`

Expected: failure because `profile-selector.js` is absent.

- [ ] **Step 3: Add backend IPC methods**

In `main.js`, add a `backendRequest(pathname)` helper using `BABEL_BACKEND_URL` with default `http://127.0.0.1:8787`, an abort timeout, JSON validation, and explicit error results. Add `profiles:list` and `profiles:graph` handlers. In `preload.js`, expose only `listProfiles()` and `loadProfileGraph(profileId)` for these calls; do not expose arbitrary URL fetch.

- [ ] **Step 4: Add selected-profile state and remove persistence fallback**

Add `State.currentProfile` and `State.isReadOnlyProfile`. `Persistence.load()` must no longer run during application initialization, and `Persistence.save()` must return false without writing while read-only. Retain legacy file IPC in `main.js` only so existing user data remains available to the explicit external migration command.

- [ ] **Step 5: Build the black vertical profile wheel**

Add a full-screen `#profile-selector` before the graph container. Render 21 profiles in backend order, keep the centered row active, support mouse wheel, ArrowUp/ArrowDown, and Enter/click selection, and use the profile color for the active line and glow. Show an inline backend-connection error with a retry action. Do not persist selection between launches.

- [ ] **Step 6: Load and replace graph state**

After selection, call `loadProfileGraph`, assign both arrays even when empty,
set current profile/read-only state, hide the selector, call `updateGraph()`,
and change the empty hint to `No Babels for this profile`. Add a visible
`Switch profile` control that returns to the selector and clears
selection/comparison/editor state.

- [ ] **Step 7: Disable every mutation path**

Guard `createBabel`, `handleDelete`, `toggleEdge`, editor saving, creation
hotkeys, delete keys, comparison writes, and `Persistence.save`. Keep
`UI.openEdit` available as a viewer, but call `Editor.editor.enable(false)`,
mark the title read-only, hide color/edit controls, and skip save on close.
Mark comparison fields read-only and hide edge toggles. Keep camera, hover,
selection, content viewing, and non-mutating comparison available.

- [ ] **Step 8: Run Node tests and a syntax check**

Run: `npm test`

Run: `node --check main.js && node --check preload.js && node --check js/profile-selector.js && node --check js/app.js && node --check js/ui.js`

Expected: all Node tests and syntax checks pass.

- [ ] **Step 9: Commit Electron selection**

```bash
git add main.js preload.js js/profile-selector.js js/state.js js/persistence.js js/app.js js/ui.js js/editor.js index.html styles.css tests/js/profile-selector.test.js
git commit -m "feat: load read-only creator profiles in Electron"
```

### Task 10: Compose HTTP Routes, Security, Commands, and Local Recipes

**Files:**
- Create: `backend/include/babel/http/profile_controller.hpp`
- Create: `backend/src/http/profile_controller.cpp`
- Create: `backend/include/babel/http/admin_controller.hpp`
- Create: `backend/src/http/admin_controller.cpp`
- Create: `backend/include/babel/http/admin_security.hpp`
- Create: `backend/src/http/admin_security.cpp`
- Create: `backend/include/babel/runtime/config.hpp`
- Create: `backend/src/runtime/config.cpp`
- Create: `backend/include/babel/runtime/application.hpp`
- Create: `backend/src/runtime/application.cpp`
- Create: `backend/src/main.cpp`
- Create: `backend/tests/integration/http_contract_test.cpp`
- Create: `Justfile`
- Modify: `backend/CMakeLists.txt`

**Interfaces:**
- Consumes: Tasks 3 through 9.
- Produces: runnable `babel_backend migrate`, `serve`, and `migrate-personal`; all HTTP contracts; `just db-up`, `just start`, `just test`, and `just migrate-personal`.
- Contract: profile graph responses are capped at 64 MiB in the backend and in
  Electron's bounded streaming reader. If a stored Personal graph exceeds that
  wire limit, return a typed HTTP error rather than emitting a partial graph.

- [ ] **Step 1: Write failing HTTP contract tests**

```cpp
TEST_CASE_METHOD(HttpFixture, "profile graph is empty before dashboard seeding") {
    auto profiles = getJson("/api/v1/profiles");
    REQUIRE(profiles.status == 200);
    REQUIRE(profiles.body.at("profiles").size() == 21);
    REQUIRE(profiles.body.at("profiles").at(0).at("displayName") == "Personal");

    auto graph = getJson("/api/v1/profiles/" + generatedProfileId() + "/graph");
    REQUIRE(graph.status == 200);
    REQUIRE(graph.body.at("babels").empty());
    REQUIRE(graph.body.at("edges").empty());
}

TEST_CASE_METHOD(HttpFixture, "seed POST requires the dashboard nonce") {
    REQUIRE(postJson("/admin/api/v1/seed", {}, {}).status == 403);
    REQUIRE(postJson("/admin/api/v1/seed", {}, validAdminHeaders()).status == 202);
    REQUIRE(postJson("/admin/api/v1/seed", {}, validAdminHeaders()).status == 409);
}
```

- [ ] **Step 2: Run HTTP tests and verify failure**

Run: `cmake --build --preset test && ctest --preset test -R http_contract --output-on-failure`

Expected: compile fails because controllers and composition root are absent.

- [ ] **Step 3: Implement profile routes and JSON mapping**

Expose `GET /health`, `GET /api/v1/profiles`, and `GET /api/v1/profiles/{profileId}/graph`. Use camelCase JSON fields expected by Task 9. Map `not_found` to 404, validation to 400, database unavailability to 503, and internal errors to a generic 500 body without leaking SQL.
Serialize profile graphs with the shared 64 MiB whole-response ceiling and
return a structured 413 response when the complete JSON representation would
exceed it.

- [ ] **Step 4: Implement dashboard/static and seed routes**

Serve `/admin`, `/admin/dashboard.css`, `/admin/dashboard.js`, and `/admin/seed-status.js`. Replace a fixed nonce marker in `index.html` with a cryptographically random per-process value and send `Cache-Control: no-store`. Implement seed GET/POST around Task 7. Return 202 with `runId`, 409 with the active run status, and structured escaped error DTOs.

- [ ] **Step 5: Enforce localhost admin security**

Bind only `127.0.0.1`. Reject admin mutations unless `Host` matches `127.0.0.1:8787` or `localhost:8787`, `Origin` matches the requested local origin, and `X-Babel-Admin-Nonce` matches in constant time. Send no `Access-Control-Allow-Origin` header.

- [ ] **Step 6: Implement process commands**

`babel_backend migrate` runs SQL migrations and installs the 21-profile roster. `babel_backend serve` verifies schema readiness, marks interrupted seed runs, composes services/adapters, and starts Drogon. `babel_backend migrate-personal --source <path>` verifies schema readiness and calls Task 8. Do not implement a seed CLI command.

- [ ] **Step 7: Add exact Just recipes**

```make
db-up:
    docker compose up -d postgres

build:
    cmake --preset dev
    cmake --build --preset dev

test:
    cmake --preset test
    cmake --build --preset test
    ctest --preset test --output-on-failure
    npm test

migrate-personal source: build
    ./build/dev/backend/babel_backend migrate
    ./build/dev/backend/babel_backend migrate-personal --source "{{source}}"
```

Implement `start` as a Bash recipe that builds, runs `migrate`, starts `serve` in the background, traps exit to stop it, polls `/health` with a bounded timeout, prints `http://127.0.0.1:8787/admin`, and then runs `npm start`. It must not call `docker compose up` and must not seed.

- [ ] **Step 8: Run HTTP, command, and recipe checks**

Run: `ctest --preset test -R "http_contract|seed_job|postgres_repository" --output-on-failure`

Run: `just --list && just test`

Expected: HTTP contracts pass, recipes parse, and the complete automated suite passes.

- [ ] **Step 9: Commit the composed application**

```bash
git add backend/include/babel/http backend/src/http backend/include/babel/runtime backend/src/runtime backend/src/main.cpp backend/tests/integration/http_contract_test.cpp backend/CMakeLists.txt Justfile
git commit -m "feat: compose backend dashboard and local commands"
```

### Task 11: Verify the Vertical Slice and Document Operation

**Files:**
- Modify: `README.md`
- Modify: `documentation.md`
- Modify: `docs/superpowers/specs/2026-08-24-backend-seeding-dashboard-design.md` only if implementation reveals an approved factual correction

**Interfaces:**
- Consumes: Complete application from Tasks 1 through 10.
- Produces: Verified developer workflow, acceptance evidence, and documentation for the next operator or implementation agent.

- [ ] **Step 1: Start from a fresh test database and run every automated test**

Run: `just db-up`

Run: `just test`

Expected: CMake configure/build succeeds, all Catch2/CTest cases pass, and all Node tests pass with zero failures.

- [ ] **Step 2: Verify the pre-seed invariant through HTTP**

Run `just start`, then in a separate terminal request:

```bash
curl --fail --silent http://127.0.0.1:8787/api/v1/profiles
```

Expected: 21 ordered profiles with `Personal` first. Select at least one generated profile and `Personal` in Electron; both render empty graphs on a fresh database.

- [ ] **Step 3: Verify dashboard-only live population**

Open `http://127.0.0.1:8787/admin`, press the single seed button, and observe the job reach `completed` or `completed_with_errors`. If live Wikipedia produces transient errors, press retry and confirm only missing assignments run. Do not invoke ingestion from a CLI or Electron.

- [ ] **Step 4: Verify seeded profile isolation and idempotency**

For at least three generated profiles in different categories, confirm Electron loads only four owned Babels and no edges. Press seed again and verify the database still has 80 generated Wikipedia Babel assignments with no duplicates.

- [ ] **Step 5: Verify Personal migration and source preservation**

Record the SHA-256 of a copy of a legacy graph, run `just migrate-personal /absolute/path/to/copy.json`, and verify the checksum is unchanged. Select `Personal` and confirm its migrated Babels/edges load. Run the same command again and confirm it reports `already_migrated` without duplicate rows.

- [ ] **Step 6: Verify failure and security paths**

Confirm Electron reports a backend connection error when the service is stopped. Confirm a seed POST without nonce returns 403, a second active seed request returns 409, and the profile API emits no CORS allow header.

- [ ] **Step 7: Update operator documentation**

Document prerequisites, `just db-up`, `just start`, dashboard URL, seed behavior, retry semantics, `just migrate-personal`, read-only Electron scope, `just test`, and explicit non-goals. Update the codebase map with backend directories and the new Electron profile-selection flow.

- [ ] **Step 8: Run final verification after documentation changes**

Run: `git diff --check && just test`

Expected: no whitespace errors and all automated tests pass.

- [ ] **Step 9: Commit verified documentation**

```bash
git add README.md documentation.md
git commit -m "docs: explain backend seeding workflow"
```

- [ ] **Step 10: Request code review before integration**

Invoke `requesting-code-review` against the final diff. Resolve findings with focused tests and commits, then rerun `just test` before presenting merge/PR options.
