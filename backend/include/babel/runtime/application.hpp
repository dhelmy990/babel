#pragma once

#include <filesystem>
#include <optional>
#include <span>
#include <string_view>

#include "babel/application/legacy_migration_service.hpp"
#include "babel/runtime/config.hpp"

namespace babel {

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
