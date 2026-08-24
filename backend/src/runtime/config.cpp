#include "babel/runtime/config.hpp"

#include <cstdlib>

namespace babel {
namespace {

constexpr std::string_view kDefaultDatabaseUrl =
    "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel";

bool invalidEnvironmentValue(std::string_view value) {
  return value.empty() || value.find('\0') != std::string_view::npos ||
         value.find('\n') != std::string_view::npos || value.find('\r') != std::string_view::npos;
}

}  // namespace

Result<RuntimeConfig> RuntimeConfig::fromEnvironment() {
  return fromEnvironment([](std::string_view name) -> std::optional<std::string> {
    const std::string terminated(name);
    if (const auto* value = std::getenv(terminated.c_str())) return std::string(value);
    return std::nullopt;
  });
}

Result<RuntimeConfig> RuntimeConfig::fromEnvironment(const Environment& environment) {
  RuntimeConfig config;
  const auto database_url = environment("BABEL_DATABASE_URL");
  config.database_url = database_url.value_or(std::string{kDefaultDatabaseUrl});
  if (invalidEnvironmentValue(config.database_url)) {
    return invalidArgument("BABEL_DATABASE_URL must be a non-empty single-line value");
  }

#ifdef BABEL_MIGRATION_DIRECTORY
  config.migration_directory = BABEL_MIGRATION_DIRECTORY;
#else
  config.migration_directory = "backend/migrations";
#endif
#ifdef BABEL_ADMIN_ASSET_DIRECTORY
  config.admin_asset_directory = BABEL_ADMIN_ASSET_DIRECTORY;
#else
  config.admin_asset_directory = "backend/admin";
#endif
  return config;
}

}  // namespace babel
