#include <cstdlib>
#include <future>
#include <latch>
#include <memory>
#include <random>
#include <string>
#include <string_view>
#include <vector>

#include <catch2/catch_test_macros.hpp>
#include <pqxx/pqxx>

#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"
#include "babel/adapters/postgres/postgres_repositories.hpp"
#include "babel/adapters/postgres/profile_roster_installer.hpp"
#include "babel/application/profile_manifest.hpp"

namespace {

std::string testDatabaseUrl() {
  if (const auto* configured = std::getenv("BABEL_TEST_DATABASE_URL")) {
    return configured;
  }
  return "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel";
}

class LegacyPostgresFixture {
 public:
  LegacyPostgresFixture()
      : base_url_(testDatabaseUrl()),
        schema_("babel_test_legacy_migration_" + std::to_string(std::random_device{}())),
        integration_lock_(acquireIntegrationLock(base_url_)),
        database_(schemaDatabaseUrl()),
        migration_runner_(database_),
        roster_installer_(database_),
        repository_(database_),
        graphs_(database_) {
    pqxx::connection connection(base_url_);
    pqxx::work transaction(connection);
    transaction.exec("CREATE SCHEMA " + transaction.quote_name(schema_));
    transaction.commit();
    REQUIRE(migration_runner_.run().has_value());
    REQUIRE(roster_installer_.install(babel::ProfileManifest::creators()).has_value());
  }

  ~LegacyPostgresFixture() {
    try {
      pqxx::connection connection(base_url_);
      pqxx::work transaction(connection);
      transaction.exec("DROP SCHEMA IF EXISTS " + transaction.quote_name(schema_) + " CASCADE");
      transaction.commit();
    } catch (...) {
    }
  }

  static babel::Babel makeBabel(const babel::CreatorId& owner, std::string_view name) {
    return babel::Babel{
        .id = babel::BabelId::v5("legacy-integration-babel:" + std::string(name)).value(),
        .owner_id = owner,
        .title = std::string(name),
        .content_html = "<p>" + std::string(name) + "</p>",
        .color = "#12ABEF",
        .content_revision = 1,
        .content_hash = "legacy-integration-content:" + std::string(name),
    };
  }

  static babel::Edge makeEdge(const babel::CreatorId& owner, const babel::Babel& source,
                              const babel::Babel& target, std::string_view name) {
    return babel::Edge{
        .id = babel::EdgeId::v5("legacy-integration-edge:" + std::string(name)).value(),
        .owner_id = owner,
        .source_id = source.id,
        .target_id = target.id,
    };
  }

  std::int64_t countRows(std::string_view table) const {
    pqxx::connection connection(schemaDatabaseUrl());
    pqxx::read_transaction transaction(connection);
    return transaction.exec("SELECT count(*) FROM " + transaction.quote_name(table))
        .one_field()
        .as<std::int64_t>();
  }

  std::vector<std::string> migrationRecord(std::string_view digest) const {
    pqxx::connection connection(schemaDatabaseUrl());
    pqxx::read_transaction transaction(connection);
    const auto rows = transaction.exec(
        "SELECT creator_id::text, babel_count::text, edge_count::text "
        "FROM legacy_migrations WHERE source_sha256 = $1",
        pqxx::params{digest});
    if (rows.empty()) return {};
    return {rows.one_row()[0].as<std::string>(), rows.one_row()[1].as<std::string>(),
            rows.one_row()[2].as<std::string>()};
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

  std::string base_url_;
  std::string schema_;
  std::unique_ptr<pqxx::connection> integration_lock_;
  babel::PostgresDatabase database_;
  babel::MigrationRunner migration_runner_;
  babel::ProfileRosterInstaller roster_installer_;
  babel::PostgresLegacyMigrationRepository repository_;
  babel::PostgresGraphRepository graphs_;
};

TEST_CASE_METHOD(LegacyPostgresFixture,
                 "Personal graph Babels edges and digest commit without source rows",
                 "[legacy_migration][postgres_repository]") {
  const auto personal = babel::ProfileManifest::creators().front().id;
  const auto first = makeBabel(personal, "owned-first");
  const auto second = makeBabel(personal, "owned-second");
  const std::vector<babel::Babel> babels{first, second};
  const std::vector<babel::Edge> edges{makeEdge(personal, first, second, "owned")};
  const std::string digest(64, 'd');

  const auto imported = repository_.importPersonalGraph(digest, babels, edges);

  REQUIRE(imported.has_value());
  REQUIRE(*imported);
  const auto graph = graphs_.loadGraph(personal);
  REQUIRE(graph.has_value());
  CHECK(graph->profile.id == personal);
  REQUIRE(graph->babels.size() == 2);
  REQUIRE(graph->edges.size() == 1);
  CHECK(graph->edges[0].source_id == first.id);
  CHECK(graph->edges[0].target_id == second.id);
  CHECK(countRows("babel_sources") == 0);
  CHECK(migrationRecord(digest) ==
        std::vector<std::string>{personal.value, "2", "1"});
}

TEST_CASE_METHOD(LegacyPostgresFixture,
                 "concurrent duplicate Personal imports claim one digest and write one graph",
                 "[legacy_migration][postgres_repository]") {
  const auto personal = babel::ProfileManifest::creators().front().id;
  const auto first = makeBabel(personal, "race-first");
  const auto second = makeBabel(personal, "race-second");
  const std::vector<babel::Babel> babels{first, second};
  const std::vector<babel::Edge> edges{makeEdge(personal, first, second, "race")};
  const std::string digest(64, 'e');
  std::latch ready(2);
  std::latch start(1);
  const auto import = [&] {
    ready.count_down();
    start.wait();
    return repository_.importPersonalGraph(digest, babels, edges);
  };
  auto first_import = std::async(std::launch::async, import);
  auto second_import = std::async(std::launch::async, import);
  ready.wait();
  start.count_down();

  const auto first_result = first_import.get();
  const auto second_result = second_import.get();

  REQUIRE(first_result.has_value());
  REQUIRE(second_result.has_value());
  CHECK(static_cast<int>(*first_result) + static_cast<int>(*second_result) == 1);
  CHECK(countRows("legacy_migrations") == 1);
  CHECK(countRows("babels") == 2);
  CHECK(countRows("edges") == 1);
  CHECK(countRows("babel_sources") == 0);
}

TEST_CASE_METHOD(LegacyPostgresFixture,
                 "failed Personal edge insertion rolls back graph and digest claim",
                 "[legacy_migration][postgres_repository]") {
  const auto personal = babel::ProfileManifest::creators().front().id;
  const auto source = makeBabel(personal, "rollback-source");
  const auto missing = makeBabel(personal, "rollback-missing");
  const std::vector<babel::Babel> babels{source};
  const std::vector<babel::Edge> edges{makeEdge(personal, source, missing, "rollback")};
  const std::string digest(64, 'f');

  const auto imported = repository_.importPersonalGraph(digest, babels, edges);

  REQUIRE_FALSE(imported.has_value());
  CHECK(countRows("legacy_migrations") == 0);
  CHECK(countRows("babels") == 0);
  CHECK(countRows("edges") == 0);
  CHECK(countRows("babel_sources") == 0);
}

}  // namespace
