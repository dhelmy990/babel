#include "babel/runtime/application.hpp"

#include <mutex>
#include <string>
#include <utility>

#include <boost/uuid/random_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <drogon/drogon.h>
#include <pqxx/pqxx>

#include "babel/adapters/html/libxml_html_sanitizer.hpp"
#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"
#include "babel/adapters/postgres/postgres_repositories.hpp"
#include "babel/adapters/postgres/profile_roster_installer.hpp"
#include "babel/adapters/wikipedia/mediawiki_article_source.hpp"
#include "babel/application/profile_manifest.hpp"
#include "babel/application/profile_query_service.hpp"
#include "babel/application/seed_service.hpp"
#include "babel/application/wikipedia_import_service.hpp"
#include "babel/http/admin_controller.hpp"
#include "babel/http/admin_security.hpp"
#include "babel/http/profile_controller.hpp"
#include "babel/runtime/seed_job_runner.hpp"

namespace babel {
namespace {

constexpr std::string_view kManifestVersion = "wikipedia-user-profiles-v1";

class ThreadSafeIdGenerator final : public IdGenerator {
 public:
  BabelId newBabelId() override {
    std::scoped_lock lock(mutex_);
    return BabelId::parse(boost::uuids::to_string(generator_())).value();
  }

  EdgeId newEdgeId() override {
    std::scoped_lock lock(mutex_);
    return EdgeId::parse(boost::uuids::to_string(generator_())).value();
  }

 private:
  std::mutex mutex_;
  boost::uuids::random_generator generator_;
};

ApplicationError commandError(std::string message) {
  return ApplicationError{.code = ErrorCode::invalid_argument, .message = std::move(message)};
}

}  // namespace

Result<RuntimeCommand> parseRuntimeCommand(std::span<const std::string_view> arguments) {
  if (arguments.size() == 1 && arguments[0] == "migrate") {
    return RuntimeCommand{.kind = RuntimeCommandKind::migrate, .source = std::nullopt};
  }
  if (arguments.size() == 1 && arguments[0] == "serve") {
    return RuntimeCommand{.kind = RuntimeCommandKind::serve, .source = std::nullopt};
  }
  if (arguments.size() == 3 && arguments[0] == "migrate-personal" &&
      arguments[1] == "--source" && !arguments[2].empty()) {
    return RuntimeCommand{.kind = RuntimeCommandKind::migrate_personal,
                          .source = std::filesystem::path(arguments[2])};
  }
  return tl::make_unexpected(commandError(
      "usage: babel_backend <migrate|serve|migrate-personal --source <path>>"));
}

Application::Application(RuntimeConfig config) : config_(std::move(config)) {}

Result<void> Application::migrate() {
  PostgresDatabase database(config_.database_url);
  MigrationRunner migrations(database, config_.migration_directory);
  auto migrated = migrations.run();
  if (!migrated) return tl::make_unexpected(migrated.error());
  ProfileRosterInstaller roster(database);
  const auto creators = ProfileManifest::creators();
  return roster.install(creators);
}

Result<void> Application::verifySchemaReady() {
  try {
    PostgresDatabase database(config_.database_url);
    auto connection = database.connect();
    pqxx::read_transaction transaction(*connection);
    const auto tables_ready = transaction.exec(R"(
        SELECT
          to_regclass('public.schema_migrations') IS NOT NULL AND
          to_regclass('public.creators') IS NOT NULL AND
          to_regclass('public.babels') IS NOT NULL AND
          to_regclass('public.edges') IS NOT NULL AND
          to_regclass('public.seed_runs') IS NOT NULL AND
          to_regclass('public.seed_run_items') IS NOT NULL AND
          to_regclass('public.legacy_migrations') IS NOT NULL
      )");
    if (!tables_ready.one_field().as<bool>()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::internal,
          .message = "database schema is not ready; run babel_backend migrate",
      });
    }
    if (transaction.exec(
            "SELECT count(*) = 3 FROM schema_migrations WHERE version IN ('1', '2', '3')")
            .one_field()
            .as<bool>() == false) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::internal,
          .message = "database schema is not ready; run babel_backend migrate",
      });
    }
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<LegacyMigrationResult> Application::migratePersonal(
    const std::filesystem::path& source) {
  auto ready = verifySchemaReady();
  if (!ready) return tl::make_unexpected(ready.error());

  PostgresDatabase database(config_.database_url);
  PostgresLegacyMigrationRepository migrations(database);
  LibxmlHtmlSanitizer sanitizer;
  const auto personal = ProfileManifest::creators().front().id;
  LegacyMigrationService service(personal, migrations, sanitizer);
  return service.migrateFile(source);
}

Result<void> Application::serve() {
  auto ready = verifySchemaReady();
  if (!ready) return tl::make_unexpected(ready.error());

  PostgresDatabase database(config_.database_url);
  PostgresCreatorRepository creators(database);
  PostgresGraphRepository graphs(database);
  PostgresWikipediaBabelRepository wikipedia_babels(database);
  PostgresSeedRunRepository seed_runs(database);
  CurlHttpTransport transport;
  MediaWikiArticleSource article_source(transport);
  LibxmlHtmlSanitizer sanitizer;
  ThreadSafeIdGenerator ids;
  ProfileQueryService profiles(creators, graphs);
  WikipediaImportService importer(creators, wikipedia_babels, article_source, sanitizer, ids);
  SeedService seed_service(ProfileManifest::seedAssignments(), seed_runs, article_source,
                           importer, SeedRetryPolicy::withDefaultDelay());
  SeedJobRunner seed_runner(std::string{kManifestVersion}, seed_service, seed_runs);
  auto interrupted = seed_runner.markInterruptedRuns();
  if (!interrupted) return tl::make_unexpected(interrupted.error());

  AdminSecurity admin_security;
  ProfileController profile_controller(profiles);
  AdminController admin_controller(admin_security, config_.admin_asset_directory, seed_runner);

  auto& server = drogon::app();
  server.registerHandler(
      "/health",
      [&profile_controller](const drogon::HttpRequestPtr& request,
                            ProfileController::Callback&& callback) {
        profile_controller.health(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/api/v1/profiles",
      [&profile_controller](const drogon::HttpRequestPtr& request,
                            ProfileController::Callback&& callback) {
        profile_controller.list(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/api/v1/profiles/{1}/graph",
      [&profile_controller](const drogon::HttpRequestPtr& request,
                            ProfileController::Callback&& callback,
                            std::string profile_id) {
        profile_controller.graph(request, std::move(callback), std::move(profile_id));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.index(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin/dashboard.css",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.dashboardCss(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin/dashboard.js",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.dashboardJs(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin/seed-status.js",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.seedStatusJs(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin/api/v1/seed",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.seedStatus(request, std::move(callback));
      },
      {drogon::Get});
  server.registerHandler(
      "/admin/api/v1/seed",
      [&admin_controller](const drogon::HttpRequestPtr& request,
                          AdminController::Callback&& callback) {
        admin_controller.startSeed(request, std::move(callback));
      },
      {drogon::Post});

  try {
    server.setThreadNum(2)
        .setClientMaxBodySize(1024U * 1024U)
        .setServerHeaderField("Babel")
        .addListener(config_.bind_address, config_.port)
        .run();
  } catch (const std::exception&) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "HTTP server failed",
    });
  }
  return {};
}

}  // namespace babel
