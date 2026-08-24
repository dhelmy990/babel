#include "babel/adapters/postgres/migration_runner.hpp"

#include <algorithm>
#include <fstream>
#include <iterator>
#include <map>
#include <regex>
#include <string>
#include <utility>
#include <vector>

#include <pqxx/pqxx>

#include "babel/adapters/postgres/postgres_database.hpp"

namespace babel {
namespace {

struct MigrationFile {
  std::string version;
  std::filesystem::path path;
};

std::filesystem::path resolveMigrationDirectory(const std::filesystem::path& requested) {
  if (requested.is_absolute() || std::filesystem::is_directory(requested)) {
    return requested;
  }

  auto current = std::filesystem::current_path();
  while (true) {
    const auto candidate = current / requested;
    if (std::filesystem::is_directory(candidate)) {
      return candidate;
    }
    if (current == current.root_path()) {
      return requested;
    }
    current = current.parent_path();
  }
}

Result<std::vector<MigrationFile>> discoverMigrations(const std::filesystem::path& directory) {
  if (!std::filesystem::is_directory(directory)) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "migration directory does not exist: " + directory.string(),
    });
  }

  const std::regex migration_name{R"(^([0-9]+)(?:_[A-Za-z0-9][A-Za-z0-9_-]*)?\.sql$)"};
  std::map<unsigned long long, MigrationFile> by_version;
  for (const auto& entry : std::filesystem::directory_iterator(directory)) {
    if (!entry.is_regular_file() || entry.path().extension() != ".sql") {
      continue;
    }

    std::smatch match;
    const auto filename = entry.path().filename().string();
    if (!std::regex_match(filename, match, migration_name)) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::internal,
          .message = "invalid migration filename: " + filename,
      });
    }

    try {
      const auto numeric_version = std::stoull(match[1].str());
      const auto [position, inserted] = by_version.emplace(
          numeric_version, MigrationFile{.version = match[1].str(), .path = entry.path()});
      if (!inserted) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "duplicate migration version " + position->second.version + ": " +
                       position->second.path.filename().string() + " and " + filename,
        });
      }
    } catch (const std::exception&) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::internal,
          .message = "invalid migration version: " + filename,
      });
    }
  }

  std::vector<MigrationFile> migrations;
  migrations.reserve(by_version.size());
  for (auto& [numeric_version, migration] : by_version) {
    static_cast<void>(numeric_version);
    migrations.push_back(std::move(migration));
  }
  return migrations;
}

Result<std::string> readMigration(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "could not open migration: " + path.string(),
    });
  }
  std::string sql{std::istreambuf_iterator<char>{input}, std::istreambuf_iterator<char>{}};
  if (input.bad() || sql.empty()) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "could not read migration: " + path.string(),
    });
  }
  return sql;
}

ApplicationError databaseError(const std::exception& exception) {
  return ApplicationError{
      .code = ErrorCode::database_unavailable,
      .message = exception.what(),
  };
}

}  // namespace

MigrationRunner::MigrationRunner(PostgresDatabase& database,
                                 std::filesystem::path migration_directory)
    : database_(database), migration_directory_(std::move(migration_directory)) {}

Result<void> MigrationRunner::run() {
  auto migrations = discoverMigrations(resolveMigrationDirectory(migration_directory_));
  if (!migrations) {
    return tl::make_unexpected(migrations.error());
  }

  try {
    auto connection = database_.connect();
    {
      pqxx::work bootstrap(*connection);
      bootstrap.exec(R"(
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version text PRIMARY KEY,
          applied_at timestamptz NOT NULL DEFAULT now()
        )
      )");
      bootstrap.commit();
    }

    for (const auto& migration : migrations.value()) {
      pqxx::work transaction(*connection);
      const auto already_applied = transaction.exec(
          "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)",
          pqxx::params{migration.version});
      if (already_applied.one_field().as<bool>()) {
        transaction.commit();
        continue;
      }

      auto sql = readMigration(migration.path);
      if (!sql) {
        return tl::make_unexpected(sql.error());
      }
      transaction.exec(sql.value());
      transaction.exec("INSERT INTO schema_migrations(version) VALUES ($1)",
                       pqxx::params{migration.version});
      transaction.commit();
    }
  } catch (const std::exception& exception) {
    return tl::make_unexpected(databaseError(exception));
  }
  return {};
}

}  // namespace babel
