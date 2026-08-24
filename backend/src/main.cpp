#include <iostream>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

#include "babel/runtime/application.hpp"

namespace {

using namespace babel;

std::string errorCodeName(ErrorCode code) {
  switch (code) {
    case ErrorCode::invalid_argument:
      return "invalid_argument";
    case ErrorCode::not_found:
      return "not_found";
    case ErrorCode::conflict:
      return "conflict";
    case ErrorCode::database_unavailable:
      return "database_unavailable";
    case ErrorCode::wikipedia_unavailable:
      return "wikipedia_unavailable";
    case ErrorCode::wikipedia_not_found:
      return "wikipedia_not_found";
    case ErrorCode::sanitizer_rejected:
      return "sanitizer_rejected";
    case ErrorCode::invalid_legacy_file:
      return "invalid_legacy_file";
    case ErrorCode::internal:
      return "internal";
  }
  return "internal";
}

int failure(const ApplicationError& error) {
  const bool generic = error.code == ErrorCode::database_unavailable ||
                       error.code == ErrorCode::internal;
  std::cerr << nlohmann::json{{"error", {{"code", errorCodeName(error.code)},
                                          {"message", generic ? "Command failed" : error.message}}}}
                       .dump()
            << '\n';
  return 1;
}

}  // namespace

int main(int argc, char* argv[]) {
  std::vector<std::string_view> arguments;
  arguments.reserve(argc > 1 ? static_cast<std::size_t>(argc - 1) : 0U);
  for (int index = 1; index < argc; ++index) arguments.emplace_back(argv[index]);

  const auto command = parseRuntimeCommand(arguments);
  if (!command) return failure(command.error());
  const auto config = RuntimeConfig::fromEnvironment();
  if (!config) return failure(config.error());
  Application application(*config);

  if (command->kind == RuntimeCommandKind::migrate) {
    const auto migrated = application.migrate();
    if (!migrated) return failure(migrated.error());
    std::cout << nlohmann::json{{"status", "migrated"}, {"profiles", 21}}.dump() << '\n';
    return 0;
  }
  if (command->kind == RuntimeCommandKind::migrate_personal) {
    const auto migrated = application.migratePersonal(*command->source);
    if (!migrated) return failure(migrated.error());
    const auto status = migrated->status == LegacyMigrationStatus::imported
                            ? "imported"
                            : "already_migrated";
    std::cout << nlohmann::json{{"status", status},
                                {"babelCount", migrated->babel_count},
                                {"edgeCount", migrated->edge_count}}
                     .dump()
              << '\n';
    return 0;
  }

  const auto served = application.serve();
  return served ? 0 : failure(served.error());
}
