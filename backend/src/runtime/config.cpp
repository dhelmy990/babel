#include "babel/runtime/config.hpp"

#include <algorithm>
#include <cstdlib>

namespace babel {
namespace {

constexpr std::string_view kDefaultDatabaseUrl =
    "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel";

bool invalidEnvironmentValue(std::string_view value) {
  return value.empty() || value.find('\0') != std::string_view::npos ||
         value.find('\n') != std::string_view::npos || value.find('\r') != std::string_view::npos;
}

bool validInstanceToken(std::string_view value) {
  return value.size() == 64 &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return (character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f');
         });
}

bool validLoopbackEndpoint(std::string_view value) {
  constexpr std::string_view prefix = "http://127.0.0.1:";
  return value.starts_with(prefix) && value.size() > prefix.size() &&
         std::all_of(value.begin() + static_cast<std::ptrdiff_t>(prefix.size()), value.end(),
                     [](unsigned char character) {
                       return character >= '0' && character <= '9';
                     });
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
  config.instance_token = environment("BABEL_INSTANCE_TOKEN");
  if (config.instance_token && !validInstanceToken(*config.instance_token)) {
    return invalidArgument("BABEL_INSTANCE_TOKEN must contain exactly 64 lowercase hex digits");
  }
  if (const auto endpoint = environment("BABEL_ONLINE_WORKER_ENDPOINT")) {
    if (!validLoopbackEndpoint(*endpoint)) {
      return invalidArgument(
          "BABEL_ONLINE_WORKER_ENDPOINT must be numeric IPv4 loopback HTTP");
    }
    config.online_worker_endpoint = *endpoint;
  }
  config.online_worker_token = environment("BABEL_ONLINE_WORKER_TOKEN");
  if (config.online_worker_token && !validInstanceToken(*config.online_worker_token)) {
    return invalidArgument(
        "BABEL_ONLINE_WORKER_TOKEN must contain exactly 64 lowercase hex digits");
  }
  config.huggingface_token = environment("HF_TOKEN");
  if (config.huggingface_token && invalidEnvironmentValue(*config.huggingface_token)) {
    return invalidArgument("HF_TOKEN must be a non-empty single-line value");
  }

  const auto assignSourceValue = [&](std::string_view environment_name,
                                     std::string& destination) -> Result<void> {
    const auto value = environment(environment_name);
    if (!value) return {};
    if (invalidEnvironmentValue(*value)) {
      return invalidArgument(std::string(environment_name) +
                             " must be a non-empty single-line value");
    }
    destination = *value;
    return {};
  };
  if (auto result = assignSourceValue("BABEL_HF_REPOSITORY", config.seed_source.repository);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result = assignSourceValue("BABEL_HF_CONFIG", config.seed_source.configuration);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result =
          assignSourceValue("BABEL_HF_REVISION", config.seed_source.requested_revision);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result =
          assignSourceValue("BABEL_HF_ARTIFACT_PATH", config.seed_source.artifact_path);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result = assignSourceValue("BABEL_ONLINE_DATASET_REPOSITORY",
                                      config.experiment_source.repository);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result = assignSourceValue("BABEL_ONLINE_DATASET_CONFIG",
                                      config.experiment_source.configuration);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (auto result = assignSourceValue("BABEL_ONLINE_DATASET_REVISION",
                                      config.experiment_source.commit_sha);
      !result) {
    return tl::make_unexpected(result.error());
  }
  if (const auto data_root = environment("BABEL_DATA_ROOT")) {
    if (invalidEnvironmentValue(*data_root)) {
      return invalidArgument("BABEL_DATA_ROOT must be a non-empty single-line value");
    }
    config.huggingface_cache_root =
        std::filesystem::path(*data_root) / "cache" / "backend-seed";
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
