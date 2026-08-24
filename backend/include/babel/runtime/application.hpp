#pragma once

#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string_view>

#include "babel/application/legacy_migration_service.hpp"
#include "babel/runtime/config.hpp"

namespace babel {

class PostgresDatabase;

class BackendInstanceLease final {
 public:
  static Result<std::unique_ptr<BackendInstanceLease>> acquire(PostgresDatabase&);
  ~BackendInstanceLease();

  BackendInstanceLease(const BackendInstanceLease&) = delete;
  BackendInstanceLease& operator=(const BackendInstanceLease&) = delete;

 private:
  class State;
  explicit BackendInstanceLease(std::unique_ptr<State>);

  std::unique_ptr<State> state_;
};

enum class RuntimeCommandKind { migrate, serve, migrate_personal };

struct RuntimeCommand {
  RuntimeCommandKind kind;
  std::optional<std::filesystem::path> source;
};

Result<RuntimeCommand> parseRuntimeCommand(std::span<const std::string_view> arguments);

class Application final {
 public:
  explicit Application(RuntimeConfig);

  Result<void> migrate();
  Result<LegacyMigrationResult> migratePersonal(const std::filesystem::path& source);
  Result<void> serve();

 private:
  Result<void> verifySchemaReady();

  RuntimeConfig config_;
};

}  // namespace babel
