#include "babel/runtime/application.hpp"

#include <cerrno>
#include <fcntl.h>
#include <mutex>
#include <string>
#include <sys/file.h>
#include <unistd.h>
#include <utility>

#include <boost/uuid/random_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <drogon/drogon.h>
#include <pqxx/pqxx>

#include "babel/adapters/html/libxml_html_sanitizer.hpp"
#include "babel/adapters/huggingface/huggingface_article_source.hpp"
#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"
#include "babel/adapters/postgres/postgres_repositories.hpp"
#include "babel/adapters/postgres/profile_roster_installer.hpp"
#include "babel/application/profile_manifest.hpp"
#include "babel/application/profile_query_service.hpp"
#include "babel/application/seed_service.hpp"
#include "babel/application/wikipedia_import_service.hpp"
#include "babel/http/admin_controller.hpp"
#include "babel/http/admin_security.hpp"
#include "babel/http/profile_controller.hpp"
#include "babel/runtime/seed_job_runner.hpp"

namespace babel {
class BackendInstanceLease::State final {
 public:
  State(int file_descriptor, std::unique_ptr<pqxx::connection> connection)
      : file_descriptor_(file_descriptor), connection_(std::move(connection)) {}

  ~State() {
    connection_.reset();
    if (file_descriptor_ >= 0) close(file_descriptor_);
  }

 private:
  int file_descriptor_;
  std::unique_ptr<pqxx::connection> connection_;
};

namespace {

constexpr std::string_view kManifestVersion = "wikipedia-user-profiles-v1";
constexpr int kBackendAdvisoryLockNamespace = 621946339;
constexpr int kBackendAdvisoryLockId = 8787;

class FileDescriptor final {
 public:
  explicit FileDescriptor(int value) : value_(value) {}
  ~FileDescriptor() {
    if (value_ >= 0) close(value_);
  }

  FileDescriptor(const FileDescriptor&) = delete;
  FileDescriptor& operator=(const FileDescriptor&) = delete;
  FileDescriptor(FileDescriptor&& other) noexcept : value_(other.release()) {}
  FileDescriptor& operator=(FileDescriptor&& other) noexcept {
    if (this == &other) return *this;
    if (value_ >= 0) close(value_);
    value_ = other.release();
    return *this;
  }

  [[nodiscard]] int get() const noexcept { return value_; }
  int release() noexcept { return std::exchange(value_, -1); }

 private:
  int value_;
};

Result<FileDescriptor> acquireLocalBackendLock() {
  const auto path = "/tmp/babel-backend-" + std::to_string(getuid()) + "-8787.lock";
  const int descriptor = open(path.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
  if (descriptor < 0) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "could not acquire backend instance ownership",
    });
  }

  FileDescriptor file(descriptor);
  int result;
  do {
    result = flock(file.get(), LOCK_EX | LOCK_NB);
  } while (result < 0 && errno == EINTR);

  if (result == 0) return file;
  if (errno == EWOULDBLOCK || errno == EAGAIN) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::conflict,
        .message = "another Babel backend instance is already running",
    });
  }
  return tl::make_unexpected(ApplicationError{
      .code = ErrorCode::internal,
      .message = "could not acquire backend instance ownership",
  });
}

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

BackendInstanceLease::BackendInstanceLease(std::unique_ptr<State> state)
    : state_(std::move(state)) {}

BackendInstanceLease::~BackendInstanceLease() = default;

Result<std::unique_ptr<BackendInstanceLease>> BackendInstanceLease::acquire(
    PostgresDatabase& database) {
  auto local_lock = acquireLocalBackendLock();
  if (!local_lock) return tl::make_unexpected(local_lock.error());

  try {
    auto connection = database.connect();
    pqxx::nontransaction session(*connection);
    const auto acquired = session
                              .exec("SELECT pg_try_advisory_lock($1, $2)",
                                    pqxx::params{kBackendAdvisoryLockNamespace,
                                                 kBackendAdvisoryLockId})
                              .one_field()
                              .as<bool>();
    if (!acquired) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::conflict,
          .message = "another Babel backend instance owns this database",
      });
    }
    return std::unique_ptr<BackendInstanceLease>(
        new BackendInstanceLease(
            std::make_unique<State>(local_lock->release(), std::move(connection))));
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

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
  auto instance_lease = BackendInstanceLease::acquire(database);
  if (!instance_lease) return tl::make_unexpected(instance_lease.error());

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
            "SELECT count(*) = 4 FROM schema_migrations WHERE version IN ('1', '2', '3', '4')")
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
  PostgresDatabase database(config_.database_url);
  auto instance_lease = BackendInstanceLease::acquire(database);
  if (!instance_lease) return tl::make_unexpected(instance_lease.error());

  auto ready = verifySchemaReady();
  if (!ready) return tl::make_unexpected(ready.error());

  PostgresLegacyMigrationRepository migrations(database);
  LibxmlHtmlSanitizer sanitizer;
  const auto personal = ProfileManifest::creators().front().id;
  LegacyMigrationService service(personal, migrations, sanitizer);
  return service.migrateFile(source);
}

Result<void> Application::serve() {
  PostgresDatabase database(config_.database_url);
  auto instance_lease = BackendInstanceLease::acquire(database);
  if (!instance_lease) return tl::make_unexpected(instance_lease.error());

  auto ready = verifySchemaReady();
  if (!ready) return tl::make_unexpected(ready.error());
  if (!config_.huggingface_token) {
    return invalidArgument("HF_TOKEN is required to serve the dashboard seed source");
  }

  PostgresCreatorRepository creators(database);
  PostgresGraphRepository graphs(database);
  PostgresWikipediaBabelRepository wikipedia_babels(database);
  PostgresSeedRunRepository seed_runs(database);
  CurlHttpTransport transport;
  HuggingFaceArticleSourceFactory article_source_factory(
      transport, config_.huggingface_cache_root, *config_.huggingface_token);
  LibxmlHtmlSanitizer sanitizer;
  ThreadSafeIdGenerator ids;
  ProfileQueryService profiles(creators, graphs);
  const auto assignments = ProfileManifest::seedAssignments();
  SeedJobRunner seed_runner(
      std::string{kManifestVersion}, assignments, article_source_factory,
      config_.seed_source,
      [&](SeedRunId run_id, std::shared_ptr<PinnedArticleSource> article_source,
          std::stop_token stop_token) -> Result<void> {
        WikipediaImportService importer(creators, wikipedia_babels, *article_source,
                                        sanitizer, ids);
        SeedService seed_service(assignments, seed_runs, *article_source, importer,
                                 SeedRetryPolicy::withDefaultDelay());
        return seed_service.run(run_id, stop_token);
      },
      seed_runs);

  std::string instance_token;
  if (config_.instance_token) {
    instance_token = *config_.instance_token;
  } else {
    auto generated = generateAdminNonce();
    if (!generated) return tl::make_unexpected(generated.error());
    instance_token = std::move(*generated);
  }
  auto admin_nonce = generateAdminNonce();
  if (!admin_nonce) return tl::make_unexpected(admin_nonce.error());
  AdminSecurity admin_security(std::move(*admin_nonce));

  auto interrupted = seed_runner.markInterruptedRuns();
  if (!interrupted) return tl::make_unexpected(interrupted.error());

  ProfileController profile_controller(profiles, std::move(instance_token));
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
