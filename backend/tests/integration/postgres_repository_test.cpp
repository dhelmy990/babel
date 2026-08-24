#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <future>
#include <memory>
#include <optional>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include <catch2/catch_test_macros.hpp>
#include <pqxx/pqxx>

#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"
#include "babel/adapters/postgres/postgres_repositories.hpp"
#include "babel/adapters/postgres/profile_roster_installer.hpp"
#include "babel/application/profile_manifest.hpp"
#include "babel/application/profile_query_service.hpp"

namespace {

std::string testDatabaseUrl() {
  if (const auto* configured = std::getenv("BABEL_TEST_DATABASE_URL")) {
    return configured;
  }
  return "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel";
}

std::string testSchemaName() {
  return "babel_test_postgres_repository_" + std::to_string(std::random_device{}());
}

class CurrentPathGuard {
 public:
  CurrentPathGuard() : original_(std::filesystem::current_path()) {}

  ~CurrentPathGuard() {
    std::error_code ignored;
    std::filesystem::current_path(original_, ignored);
  }

 private:
  std::filesystem::path original_;
};

class PostgresFixture {
 public:
  PostgresFixture()
      : base_url_(testDatabaseUrl()),
        schema_(testSchemaName()),
        integration_lock_(acquireIntegrationLock(base_url_)),
        database_(schemaDatabaseUrl()),
        migration_runner_(database_),
        roster_installer_(database_),
        creators_(database_),
        graphs_(database_),
        wikipedia_babels_(database_),
        seed_runs_(database_),
        legacy_migrations_(database_),
        profile_service_(creators_, graphs_) {
    resetDatabase();
  }

  ~PostgresFixture() { dropSchema(); }

  void resetDatabase() {
    pqxx::connection connection(base_url_);
    pqxx::work transaction(connection);
    transaction.exec("DROP SCHEMA IF EXISTS " + transaction.quote_name(schema_) + " CASCADE");
    transaction.exec("CREATE SCHEMA " + transaction.quote_name(schema_));
    transaction.commit();
  }

  void installSchemaAndRoster() {
    REQUIRE(migration_runner_.run().has_value());
    REQUIRE(roster_installer_.install(babel::ProfileManifest::creators()).has_value());
  }

  static babel::Babel makeBabel(const babel::CreatorId& owner_id, std::string_view name) {
    return babel::Babel{
        .id = babel::BabelId::v5("test-babel:" + std::string(name)).value(),
        .owner_id = owner_id,
        .title = std::string(name),
        .content_html = "<p>" + std::string(name) + " body</p>",
        .color = "#123ABC",
        .content_revision = 7,
        .content_hash = "sha256:" + std::string(name),
    };
  }

  static babel::BabelSource makeSource(const babel::Babel& babel, std::int64_t page_id) {
    return babel::BabelSource{
        .babel_id = babel.id,
        .owner_id = babel.owner_id,
        .provider = "wikipedia",
        .external_page_id = babel::WikipediaPageId::fromInt(page_id).value(),
        .canonical_url = "https://en.wikipedia.org/?curid=" + std::to_string(page_id),
        .source_revision_id = 42,
        .seed_assignment_id = std::nullopt,
        .declared_title = babel.title,
    };
  }

  std::int64_t countRows(std::string_view table) {
    pqxx::connection connection(schemaDatabaseUrl());
    pqxx::read_transaction transaction(connection);
    return transaction.exec("SELECT count(*) FROM " + transaction.quote_name(table))
        .one_field()
        .as<std::int64_t>();
  }

 protected:
  static std::unique_ptr<pqxx::connection> acquireIntegrationLock(const std::string& url) {
    auto connection = std::make_unique<pqxx::connection>(url);
    pqxx::nontransaction transaction(*connection);
    transaction.exec("SELECT pg_advisory_lock(621946339, 20260824)");
    return connection;
  }

  std::string schemaDatabaseUrl() const {
    const auto separator = base_url_.find('?') == std::string::npos ? '?' : '&';
    return base_url_ + separator + "options=-csearch_path%3D" + schema_;
  }

  void dropSchema() noexcept {
    try {
      pqxx::connection connection(base_url_);
      pqxx::work transaction(connection);
      transaction.exec("DROP SCHEMA IF EXISTS " + transaction.quote_name(schema_) + " CASCADE");
      transaction.commit();
    } catch (...) {
    }
  }

  std::string base_url_;
  std::string schema_;
  std::unique_ptr<pqxx::connection> integration_lock_;
  babel::PostgresDatabase database_;
  babel::MigrationRunner migration_runner_;
  babel::ProfileRosterInstaller roster_installer_;
  babel::PostgresCreatorRepository creators_;
  babel::PostgresGraphRepository graphs_;
  babel::PostgresWikipediaBabelRepository wikipedia_babels_;
  babel::PostgresSeedRunRepository seed_runs_;
  babel::PostgresLegacyMigrationRepository legacy_migrations_;
  babel::ProfileQueryService profile_service_;
};

TEST_CASE_METHOD(PostgresFixture, "migrations install a 21-profile empty roster",
                 "[postgres_repository]") {
  const auto migration_result = migration_runner_.run();
  INFO((migration_result ? "migration succeeded" : migration_result.error().message));
  REQUIRE(migration_result.has_value());
  REQUIRE(roster_installer_.install(babel::ProfileManifest::creators()).has_value());

  const auto profiles = profile_service_.listProfiles();
  REQUIRE(profiles.has_value());
  REQUIRE(profiles->size() == 21);
  REQUIRE(profiles->front().display_name == "Personal");

  const auto graph = profile_service_.loadGraph(profiles->at(1).id);
  REQUIRE(graph.has_value());
  REQUIRE(graph->babels.empty());
  REQUIRE(graph->edges.empty());
}

TEST_CASE_METHOD(PostgresFixture, "Wikipedia Babel insertion is atomic and maps HTML into graphs",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owner = babel::ProfileManifest::creators().at(1).id;
  const auto babel = makeBabel(owner, "Atomic article");
  const auto source = makeSource(babel, 101);

  REQUIRE(wikipedia_babels_.insertWikipediaBabel(babel, source).has_value());
  const auto found = wikipedia_babels_.findByPage(owner, source.external_page_id);
  REQUIRE(found.has_value());
  REQUIRE(found->has_value());
  REQUIRE(found->value().content_html == "<p>Atomic article body</p>");

  const auto graph = profile_service_.loadGraph(owner);
  REQUIRE(graph.has_value());
  REQUIRE(graph->babels.size() == 1);
  REQUIRE(graph->babels.front().content_html == "<p>Atomic article body</p>");
}

TEST_CASE_METHOD(PostgresFixture,
                 "duplicate owner provider page rejects the Babel and preserves existing rows",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owner = babel::ProfileManifest::creators().at(1).id;
  const auto first = makeBabel(owner, "First article");
  REQUIRE(wikipedia_babels_.insertWikipediaBabel(first, makeSource(first, 202)).has_value());

  const auto duplicate = makeBabel(owner, "Duplicate article");
  const auto result = wikipedia_babels_.insertWikipediaBabel(duplicate, makeSource(duplicate, 202));
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::conflict);
  REQUIRE(countRows("babels") == 1);
  REQUIRE(countRows("babel_sources") == 1);
}

TEST_CASE_METHOD(PostgresFixture, "repository maps check violations to invalid argument",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owner = babel::ProfileManifest::creators().at(1).id;
  auto invalid = makeBabel(owner, "Invalid color");
  invalid.color = "not-a-color";

  const auto result = wikipedia_babels_.insertWikipediaBabel(invalid, makeSource(invalid, 204));
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::invalid_argument);
  REQUIRE(countRows("babels") == 0);
  REQUIRE(countRows("babel_sources") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "repository maps foreign key violations to invalid argument",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto missing_owner = babel::CreatorId::v5("creator:missing-owner").value();
  const auto article = makeBabel(missing_owner, "Missing owner");

  const auto result = wikipedia_babels_.insertWikipediaBabel(article, makeSource(article, 205));
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::invalid_argument);
  REQUIRE(countRows("babels") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "repository maps non-constraint SQL errors to internal",
                 "[postgres_repository]") {
  const auto id = babel::CreatorId::v5("creator:before-migrations").value();
  const auto result = creators_.exists(id);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::internal);
}

TEST_CASE("repository maps connection failures to database unavailable", "[postgres_repository]") {
  babel::PostgresDatabase unavailable(
      "postgresql://babel:babel-local-dev@127.0.0.1:1/babel?connect_timeout=1");
  babel::PostgresCreatorRepository creators(unavailable);
  const auto id = babel::CreatorId::v5("creator:unavailable").value();

  const auto result = creators.exists(id);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::database_unavailable);
}

TEST_CASE_METHOD(PostgresFixture, "Wikipedia source must describe the Babel inserted with it",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owner = babel::ProfileManifest::creators().at(1).id;
  const auto existing = makeBabel(owner, "Existing Babel without source");
  {
    pqxx::connection connection(schemaDatabaseUrl());
    pqxx::work transaction(connection);
    transaction.exec(R"(
        INSERT INTO babels(id, owner_id, title, content_html, color, content_revision, content_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
      )",
                     pqxx::params{existing.id.value, existing.owner_id.value, existing.title,
                                  existing.content_html, existing.color, existing.content_revision,
                                  existing.content_hash});
    transaction.commit();
  }
  const auto inserted = makeBabel(owner, "New Babel");
  auto mismatched_source = makeSource(inserted, 203);
  mismatched_source.babel_id = existing.id;

  const auto result = wikipedia_babels_.insertWikipediaBabel(inserted, mismatched_source);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::invalid_argument);
  REQUIRE(countRows("babels") == 1);
  REQUIRE(countRows("babel_sources") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "seed assignments attach to existing Wikipedia sources",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto assignment = babel::ProfileManifest::seedAssignments().front();
  const auto article = makeBabel(assignment.creator_id, "Assigned article");
  REQUIRE(wikipedia_babels_.insertWikipediaBabel(article, makeSource(article, 303)).has_value());

  REQUIRE(wikipedia_babels_
              .attachSeedAssignment(article.id, assignment.id, assignment.declared_title)
              .has_value());

  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  const auto row = transaction
                       .exec("SELECT seed_assignment_id, declared_title FROM babel_sources "
                             "WHERE babel_id = $1",
                             pqxx::params{article.id.value})
                       .one_row();
  REQUIRE(row["seed_assignment_id"].as<std::string>() == assignment.id.value);
  REQUIRE(row["declared_title"].as<std::string>() == assignment.declared_title);
  REQUIRE(seed_runs_.assignmentExists(assignment.id).value());

  const auto other_assignment = babel::ProfileManifest::seedAssignments().at(1);
  const auto conflict = wikipedia_babels_.attachSeedAssignment(
      article.id, other_assignment.id, other_assignment.declared_title);
  REQUIRE_FALSE(conflict.has_value());
  REQUIRE(conflict.error().code == babel::ErrorCode::conflict);
  const auto preserved = transaction
                             .exec("SELECT seed_assignment_id, declared_title FROM babel_sources "
                                   "WHERE babel_id = $1",
                                   pqxx::params{article.id.value})
                             .one_row();
  REQUIRE(preserved["seed_assignment_id"].as<std::string>() == assignment.id.value);
  REQUIRE(preserved["declared_title"].as<std::string>() == assignment.declared_title);
  REQUIRE(seed_runs_.assignmentExists(assignment.id).value());
  REQUIRE_FALSE(seed_runs_.assignmentExists(other_assignment.id).value());
}

TEST_CASE_METHOD(PostgresFixture, "graph loading uses one repeatable read snapshot",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owner = babel::ProfileManifest::creators().at(1).id;
  const auto concurrent_babel = makeBabel(owner, "Concurrent graph insert");

  pqxx::connection blocker_connection(schemaDatabaseUrl());
  pqxx::work blocker(blocker_connection);
  blocker.exec("LOCK TABLE babels IN ACCESS EXCLUSIVE MODE");

  auto graph_future = std::async(std::launch::async, [&] { return profile_service_.loadGraph(owner); });
  pqxx::connection observer(base_url_);
  bool reader_is_blocked = false;
  for (int attempt = 0; attempt < 100 && !reader_is_blocked; ++attempt) {
    pqxx::read_transaction observation(observer);
    reader_is_blocked = observation
                            .exec(R"(
                                SELECT EXISTS(
                                  SELECT 1
                                  FROM pg_locks locks
                                  JOIN pg_class relations ON relations.oid = locks.relation
                                  JOIN pg_namespace schemas ON schemas.oid = relations.relnamespace
                                  WHERE schemas.nspname = $1
                                    AND relations.relname = 'babels'
                                    AND NOT locks.granted
                                )
                              )",
                                  pqxx::params{schema_})
                            .one_field()
                            .as<bool>();
    if (!reader_is_blocked) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
  }

  if (reader_is_blocked) {
    blocker.exec(R"(
        INSERT INTO babels(id, owner_id, title, content_html, color, content_revision, content_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
      )",
                 pqxx::params{concurrent_babel.id.value, concurrent_babel.owner_id.value,
                              concurrent_babel.title, concurrent_babel.content_html,
                              concurrent_babel.color, concurrent_babel.content_revision,
                              concurrent_babel.content_hash});
  }
  blocker.commit();
  const auto graph = graph_future.get();

  REQUIRE(reader_is_blocked);
  REQUIRE(graph.has_value());
  REQUIRE(graph->babels.empty());
  REQUIRE(graph->edges.empty());
}

TEST_CASE_METHOD(PostgresFixture, "loading an absent profile returns not found",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto missing = babel::CreatorId::v5("creator:missing-profile").value();
  const auto graph = profile_service_.loadGraph(missing);
  REQUIRE_FALSE(graph.has_value());
  REQUIRE(graph.error().code == babel::ErrorCode::not_found);
}

TEST_CASE_METHOD(PostgresFixture, "seed runs atomically snapshot pending assignments and total",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto manifest = babel::ProfileManifest::seedAssignments();
  const std::vector<babel::SeedAssignment> assignments{manifest.at(0), manifest.at(1)};

  const auto run = seed_runs_.createRun("manifest-v1", assignments);
  REQUIRE(run.has_value());
  REQUIRE_FALSE(seed_runs_.assignmentExists(assignments.front().id).value());

  const auto status = seed_runs_.status(run.value());
  REQUIRE(status.has_value());
  REQUIRE(status->kind == babel::SeedStatusKind::persisted);
  REQUIRE(status->run_id == run.value());
  REQUIRE(status->run_state == babel::SeedRunState::queued);
  REQUIRE(status->total == 2);
  REQUIRE(status->imported == 0);
  REQUIRE(status->skipped == 0);
  REQUIRE(status->failed == 0);

  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  const auto items = transaction.exec(R"(
      SELECT seed_assignment_id, creator_id, declared_title, state, attempt_count
      FROM seed_run_items
      WHERE seed_run_id = $1
      ORDER BY declared_title
    )",
                                      pqxx::params{run->value});
  REQUIRE(items.size() == 2);
  REQUIRE(items.front()["state"].as<std::string>() == "pending");
  REQUIRE(items.front()["attempt_count"].as<int>() == 0);
}

TEST_CASE_METHOD(PostgresFixture, "duplicate assignment snapshot rolls back the whole seed run",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto assignment = babel::ProfileManifest::seedAssignments().front();
  const std::vector<babel::SeedAssignment> duplicates{assignment, assignment};

  const auto result = seed_runs_.createRun("manifest-v1", duplicates);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::conflict);
  REQUIRE(countRows("seed_runs") == 0);
  REQUIRE(countRows("seed_run_items") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "seed status derives persisted outcomes and exact attempt counts",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto manifest = babel::ProfileManifest::seedAssignments();
  const std::vector<babel::SeedAssignment> assignments{manifest.at(0), manifest.at(1),
                                                       manifest.at(2)};
  const auto run = seed_runs_.createRun("manifest-v1", assignments).value();

  const auto imported_babel = makeBabel(assignments.at(0).creator_id, "Seeded article");
  REQUIRE(wikipedia_babels_
              .insertWikipediaBabel(imported_babel, makeSource(imported_babel, 404))
              .has_value());
  REQUIRE(seed_runs_
              .recordItemState(
                  run, assignments.at(0).id,
                  babel::SeedItemUpdate{
                      .state = babel::SeedItemState::imported,
                      .attempt_count = 3,
                      .resolved_page_id = babel::WikipediaPageId::fromInt(404).value(),
                      .babel_id = imported_babel.id,
                      .error = std::nullopt,
                  })
              .has_value());
  REQUIRE(seed_runs_
              .recordItemState(run, assignments.at(1).id,
                               babel::SeedItemUpdate{
                                   .state = babel::SeedItemState::skipped,
                                   .attempt_count = 0,
                                   .resolved_page_id = std::nullopt,
                                   .babel_id = std::nullopt,
                                   .error = std::nullopt,
                               })
              .has_value());
  const babel::ApplicationError failure{
      .code = babel::ErrorCode::wikipedia_unavailable,
      .message = "upstream timed out",
  };
  REQUIRE(seed_runs_
              .recordItemState(
                  run, assignments.at(2).id,
                  babel::SeedItemUpdate{
                      .state = babel::SeedItemState::failed,
                      .attempt_count = 4,
                      .resolved_page_id = babel::WikipediaPageId::fromInt(405).value(),
                      .babel_id = std::nullopt,
                      .error = failure,
                  })
              .has_value());

  const auto status = seed_runs_.status(run).value();
  REQUIRE(status.total == 3);
  REQUIRE(status.imported == 1);
  REQUIRE(status.skipped == 1);
  REQUIRE(status.failed == 1);

  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  const auto failed = transaction
                          .exec(R"(
                              SELECT attempt_count, resolved_page_id, babel_id,
                                     error_code, error_detail
                              FROM seed_run_items
                              WHERE seed_run_id = $1 AND seed_assignment_id = $2
                            )",
                                pqxx::params{run.value, assignments.at(2).id.value})
                          .one_row();
  REQUIRE(failed["attempt_count"].as<int>() == 4);
  REQUIRE(failed["resolved_page_id"].as<std::int64_t>() == 405);
  REQUIRE(failed["babel_id"].is_null());
  REQUIRE(failed["error_code"].as<std::string>() == "wikipedia_unavailable");
  REQUIRE(failed["error_detail"].as<std::string>() == "upstream timed out");
}

TEST_CASE_METHOD(PostgresFixture, "seed item database constraints reject invalid outcomes",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto assignment = babel::ProfileManifest::seedAssignments().front();
  const std::vector<babel::SeedAssignment> assignments{assignment};
  const auto run = seed_runs_.createRun("manifest-v1", assignments).value();

  const auto result = seed_runs_.recordItemState(
      run, assignment.id,
      babel::SeedItemUpdate{
          .state = babel::SeedItemState::imported,
          .attempt_count = 0,
          .resolved_page_id = std::nullopt,
          .babel_id = std::nullopt,
          .error = std::nullopt,
      });
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::invalid_argument);

  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  REQUIRE(transaction
              .exec("SELECT state FROM seed_run_items WHERE seed_run_id = $1",
                    pqxx::params{run.value})
              .one_field()
              .as<std::string>() == "pending");
}

TEST_CASE_METHOD(PostgresFixture, "latest seed status handles no run and interrupts running runs",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto none = seed_runs_.latestStatus();
  REQUIRE(none.has_value());
  REQUIRE(none->kind == babel::SeedStatusKind::not_started);
  REQUIRE_FALSE(none->run_id.has_value());

  const auto assignment = babel::ProfileManifest::seedAssignments().front();
  const std::vector<babel::SeedAssignment> assignments{assignment};
  const auto run = seed_runs_.createRun("manifest-v1", assignments).value();
  REQUIRE(seed_runs_.setRunState(run, babel::SeedRunState::running).has_value());
  REQUIRE(seed_runs_.markRunningAsInterrupted().has_value());

  const auto latest = seed_runs_.latestStatus().value();
  REQUIRE(latest.kind == babel::SeedStatusKind::persisted);
  REQUIRE(latest.run_id == run);
  REQUIRE(latest.run_state == babel::SeedRunState::interrupted);
  REQUIRE(latest.total == 1);
}

TEST_CASE_METHOD(PostgresFixture, "legacy Personal graph and digest commit atomically",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto personal = babel::ProfileManifest::creators().front().id;
  const auto first = makeBabel(personal, "Legacy first");
  const auto second = makeBabel(personal, "Legacy second");
  const std::vector<babel::Babel> babels{first, second};
  const std::vector<babel::Edge> edges{
      babel::Edge{
          .id = babel::EdgeId::v5("test-edge:legacy").value(),
          .owner_id = personal,
          .source_id = first.id,
          .target_id = second.id,
      },
  };
  const std::string digest(64, 'a');

  REQUIRE(legacy_migrations_.importPersonalGraph(digest, babels, edges).has_value());
  REQUIRE(legacy_migrations_.digestExists(digest).value());
  REQUIRE(countRows("legacy_migrations") == 1);
  REQUIRE(countRows("babels") == 2);
  REQUIRE(countRows("edges") == 1);

  const auto duplicate = legacy_migrations_.importPersonalGraph(digest, babels, edges);
  REQUIRE(duplicate.has_value());
  REQUIRE(countRows("babels") == 2);
}

TEST_CASE_METHOD(PostgresFixture, "legacy import rejects generated ownership without writes",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto generated = babel::ProfileManifest::creators().at(1).id;
  const std::vector<babel::Babel> babels{makeBabel(generated, "Not Personal")};
  const std::vector<babel::Edge> edges;

  const auto result = legacy_migrations_.importPersonalGraph(std::string(64, 'b'), babels, edges);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::invalid_argument);
  REQUIRE(countRows("legacy_migrations") == 0);
  REQUIRE(countRows("babels") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "legacy graph failure rolls back Babels and digest",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto personal = babel::ProfileManifest::creators().front().id;
  const auto article = makeBabel(personal, "Rollback legacy");
  const auto missing = babel::BabelId::v5("test-babel:missing-target").value();
  const std::vector<babel::Babel> babels{article};
  const std::vector<babel::Edge> edges{
      babel::Edge{
          .id = babel::EdgeId::v5("test-edge:rollback").value(),
          .owner_id = personal,
          .source_id = article.id,
          .target_id = missing,
      },
  };

  const auto result =
      legacy_migrations_.importPersonalGraph(std::string(64, 'c'), babels, edges);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(countRows("legacy_migrations") == 0);
  REQUIRE(countRows("babels") == 0);
  REQUIRE(countRows("edges") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "PostgreSQL rejects cross-owner edges without writes",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto owners = babel::ProfileManifest::creators();
  const auto first = makeBabel(owners.at(1).id, "Owner one");
  const auto second = makeBabel(owners.at(2).id, "Owner two");
  REQUIRE(wikipedia_babels_.insertWikipediaBabel(first, makeSource(first, 501)).has_value());
  REQUIRE(wikipedia_babels_.insertWikipediaBabel(second, makeSource(second, 502)).has_value());

  pqxx::connection connection(schemaDatabaseUrl());
  REQUIRE_THROWS_AS(
      [&] {
        pqxx::work transaction(connection);
        transaction.exec(R"(
            INSERT INTO edges(id, owner_id, source_babel_id, target_babel_id)
            VALUES ($1, $2, $3, $4)
          )",
                         pqxx::params{babel::EdgeId::v5("test-edge:cross-owner").value().value,
                                      first.owner_id.value, first.id.value, second.id.value});
        transaction.commit();
      }(),
      pqxx::foreign_key_violation);
  REQUIRE(countRows("edges") == 0);
}

TEST_CASE_METHOD(PostgresFixture, "roster upsert restores stable metadata and preserves unknown creators",
                 "[postgres_repository]") {
  REQUIRE(migration_runner_.run().has_value());
  auto expected = babel::ProfileManifest::creators();
  auto changed = expected.at(1);
  changed.display_name = "Stale display name";
  changed.color = "#000000";
  const babel::Creator unknown{
      .id = babel::CreatorId::v5("creator:unknown-external").value(),
      .slug = "unknown-external",
      .display_name = "Unknown External",
      .color = "#ABCDEF",
      .kind = babel::CreatorKind::generated,
      .order = 99,
  };
  const std::vector<babel::Creator> initial{changed, unknown};
  REQUIRE(roster_installer_.install(initial).has_value());
  REQUIRE(roster_installer_.install(expected).has_value());

  const auto profiles = creators_.listOrdered().value();
  REQUIRE(profiles.size() == 22);
  REQUIRE(creators_.get(changed.id).value().display_name == expected.at(1).display_name);
  REQUIRE(creators_.get(changed.id).value().color == expected.at(1).color);
  REQUIRE(creators_.get(unknown.id).value().display_name == unknown.display_name);
}

TEST_CASE_METHOD(PostgresFixture, "roster maps stable metadata collisions to conflict",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto existing = babel::ProfileManifest::creators().at(1);
  const babel::Creator conflicting{
      .id = babel::CreatorId::v5("creator:conflicting-roster-entry").value(),
      .slug = existing.slug,
      .display_name = "Conflicting Creator",
      .color = "#ABCDEF",
      .kind = babel::CreatorKind::generated,
      .order = 99,
  };
  const std::vector<babel::Creator> roster{conflicting};

  const auto result = roster_installer_.install(roster);
  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::conflict);
  REQUIRE(creators_.exists(conflicting.id).value() == false);
}

TEST_CASE_METHOD(PostgresFixture, "roster installation atomically swaps manifest identities",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto original = babel::ProfileManifest::creators();
  auto swapped = original;
  std::swap(swapped.at(1).slug, swapped.at(2).slug);
  std::swap(swapped.at(1).order, swapped.at(2).order);

  REQUIRE(roster_installer_.install(swapped).has_value());
  REQUIRE(creators_.get(swapped.at(1).id).value().slug == swapped.at(1).slug);
  REQUIRE(creators_.get(swapped.at(1).id).value().order == swapped.at(1).order);
  REQUIRE(creators_.get(swapped.at(2).id).value().slug == swapped.at(2).slug);
  REQUIRE(creators_.get(swapped.at(2).id).value().order == swapped.at(2).order);

  REQUIRE(roster_installer_.install(original).has_value());
  REQUIRE(creators_.get(original.at(1).id).value().slug == original.at(1).slug);
  REQUIRE(creators_.get(original.at(2).id).value().slug == original.at(2).slug);
}

TEST_CASE_METHOD(PostgresFixture, "roster staging avoids unknown temporary slug identities",
                 "[postgres_repository]") {
  installSchemaAndRoster();
  const auto original = babel::ProfileManifest::creators();
  const babel::Creator unknown{
      .id = babel::CreatorId::v5("creator:temporary-slug-owner").value(),
      .slug = "babel-stage-" + original.at(1).id.value,
      .display_name = "Temporary Slug Owner",
      .color = "#ABCDEF",
      .kind = babel::CreatorKind::generated,
      .order = 99,
  };
  const std::vector<babel::Creator> unknown_roster{unknown};
  REQUIRE(roster_installer_.install(unknown_roster).has_value());

  auto swapped = original;
  std::swap(swapped.at(1).slug, swapped.at(2).slug);
  std::swap(swapped.at(1).order, swapped.at(2).order);
  const auto result = roster_installer_.install(swapped);

  REQUIRE(result.has_value());
  REQUIRE(creators_.get(unknown.id).value().slug == unknown.slug);
  REQUIRE(creators_.get(swapped.at(1).id).value().slug == swapped.at(1).slug);
  REQUIRE(creators_.get(swapped.at(2).id).value().slug == swapped.at(2).slug);
}

TEST_CASE_METHOD(PostgresFixture, "migrations are idempotent and track every version once",
                 "[postgres_repository]") {
  REQUIRE(migration_runner_.run().has_value());
  REQUIRE(migration_runner_.run().has_value());
  REQUIRE(countRows("schema_migrations") == 3);
}

TEST_CASE_METHOD(PostgresFixture, "default migration path resolves outside the source tree",
                 "[postgres_repository]") {
  CurrentPathGuard restore_path;
  std::filesystem::current_path(std::filesystem::temp_directory_path());
  babel::MigrationRunner defaults(database_);
  const auto result = defaults.run();
  INFO((result ? "migration succeeded" : result.error().message));
  REQUIRE(result.has_value());
  REQUIRE(countRows("schema_migrations") == 3);
}

TEST_CASE_METHOD(PostgresFixture, "migration discovery rejects duplicate numeric versions",
                 "[postgres_repository]") {
  const auto directory = std::filesystem::temp_directory_path() /
                         ("babel-postgres-duplicate-migration-test-" + schema_);
  std::filesystem::remove_all(directory);
  std::filesystem::create_directories(directory);
  {
    std::ofstream(directory / "010_first.sql") << "SELECT 1;";
    std::ofstream(directory / "010_second.sql") << "SELECT 2;";
  }
  babel::MigrationRunner duplicates(database_, directory);
  const auto result = duplicates.run();
  std::filesystem::remove_all(directory);

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::internal);
  REQUIRE(result.error().message.find("duplicate migration version") != std::string::npos);
}

TEST_CASE_METHOD(PostgresFixture, "migration versions are canonical across filename aliases",
                 "[postgres_repository]") {
  const auto directory = std::filesystem::temp_directory_path() /
                         ("babel-postgres-version-alias-test-" + schema_);
  std::filesystem::create_directories(directory);
  const auto migration_sql =
      "CREATE TABLE canonical_migration_once(id integer); INSERT INTO canonical_migration_once "
      "VALUES (1);";
  std::ofstream(directory / "001_once.sql") << migration_sql;
  babel::MigrationRunner runner(database_, directory);
  const auto first = runner.run();
  std::filesystem::rename(directory / "001_once.sql", directory / "1_once.sql");
  const auto second = runner.run();
  std::filesystem::remove_all(directory);

  REQUIRE(first.has_value());
  REQUIRE(second.has_value());
  REQUIRE(countRows("canonical_migration_once") == 1);
  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  REQUIRE(transaction.exec("SELECT version FROM schema_migrations").one_field().as<std::string>() ==
          "1");
}

TEST_CASE_METHOD(PostgresFixture, "concurrent migration runners apply each version once",
                 "[postgres_repository]") {
  const auto directory = std::filesystem::temp_directory_path() /
                         ("babel-postgres-concurrent-migration-test-" + schema_);
  std::filesystem::create_directories(directory);
  std::ofstream(directory / "1_once.sql")
      << "SELECT pg_sleep(0.2); CREATE TABLE concurrent_migration_once(id integer); "
         "INSERT INTO concurrent_migration_once VALUES (1);";
  babel::MigrationRunner first_runner(database_, directory);
  babel::MigrationRunner second_runner(database_, directory);
  std::promise<void> release_runners;
  auto start = release_runners.get_future().share();
  auto first = std::async(std::launch::async, [&] {
    start.wait();
    return first_runner.run();
  });
  auto second = std::async(std::launch::async, [&] {
    start.wait();
    return second_runner.run();
  });
  release_runners.set_value();
  const auto first_result = first.get();
  const auto second_result = second.get();
  std::filesystem::remove_all(directory);

  INFO((first_result ? "first runner succeeded" : first_result.error().message));
  INFO((second_result ? "second runner succeeded" : second_result.error().message));
  REQUIRE(first_result.has_value());
  REQUIRE(second_result.has_value());
  REQUIRE(countRows("concurrent_migration_once") == 1);
  REQUIRE(countRows("schema_migrations") == 1);
}

TEST_CASE_METHOD(PostgresFixture, "migration filesystem failures return typed errors without throwing",
                 "[postgres_repository]") {
  const auto missing = std::filesystem::temp_directory_path() /
                       ("babel-postgres-missing-migration-test-" + schema_);
  babel::MigrationRunner missing_runner(database_, missing);
  babel::Result<void> missing_result;
  REQUIRE_NOTHROW(missing_result = missing_runner.run());
  REQUIRE_FALSE(missing_result.has_value());
  REQUIRE(missing_result.error().code == babel::ErrorCode::internal);

  const auto unreadable = std::filesystem::temp_directory_path() /
                          ("babel-postgres-unreadable-migration-test-" + schema_);
  std::filesystem::create_directories(unreadable);
  std::ofstream(unreadable / "1_unreadable.sql") << "SELECT 1;";
  std::filesystem::permissions(unreadable, std::filesystem::perms::none);
  babel::MigrationRunner unreadable_runner(database_, unreadable);
  babel::Result<void> unreadable_result;
  bool threw = false;
  try {
    unreadable_result = unreadable_runner.run();
  } catch (...) {
    threw = true;
  }
  std::filesystem::permissions(unreadable, std::filesystem::perms::owner_all);
  std::filesystem::remove_all(unreadable);

  REQUIRE_FALSE(threw);
  REQUIRE_FALSE(unreadable_result.has_value());
  REQUIRE(unreadable_result.error().code == babel::ErrorCode::internal);
}

TEST_CASE_METHOD(PostgresFixture, "migrations run numerically and stop after a rolled-back failure",
                 "[postgres_repository]") {
  const auto directory =
      std::filesystem::temp_directory_path() /
      ("babel-postgres-failed-migration-test-" + schema_);
  std::filesystem::remove_all(directory);
  std::filesystem::create_directories(directory);
  {
    std::ofstream(directory / "2_create.sql")
        << "CREATE TABLE migration_order_log(entry text NOT NULL);";
    std::ofstream(directory / "10_failure.sql")
        << "INSERT INTO migration_order_log(entry) VALUES ('middle'); SELECT missing_column;";
    std::ofstream(directory / "11_later.sql")
        << "CREATE TABLE migration_later_side_effect(id integer);";
  }
  babel::MigrationRunner failing(database_, directory);
  const auto result = failing.run();
  std::filesystem::remove_all(directory);

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == babel::ErrorCode::internal);
  pqxx::connection connection(schemaDatabaseUrl());
  pqxx::read_transaction transaction(connection);
  REQUIRE(transaction.exec("SELECT to_regclass('migration_order_log') IS NOT NULL")
              .one_field()
              .as<bool>());
  REQUIRE(transaction.exec("SELECT count(*) FROM migration_order_log").one_field().as<int>() == 0);
  REQUIRE(transaction.exec("SELECT to_regclass('migration_later_side_effect') IS NULL")
              .one_field()
              .as<bool>());
  REQUIRE(transaction
              .exec("SELECT count(*) FROM schema_migrations WHERE version = '2'")
              .one_field()
              .as<int>() == 1);
  REQUIRE(transaction
              .exec("SELECT count(*) FROM schema_migrations WHERE version IN ('10', '11')")
              .one_field()
              .as<int>() == 0);
}

}  // namespace
