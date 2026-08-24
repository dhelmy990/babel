#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

#include "babel/application/errors.hpp"

namespace babel {

struct RuntimeConfig {
  using Environment = std::function<std::optional<std::string>(std::string_view)>;

  std::string database_url;
  std::string bind_address{"127.0.0.1"};
  std::uint16_t port{8787};
  std::filesystem::path migration_directory;
  std::filesystem::path admin_asset_directory;

  static Result<RuntimeConfig> fromEnvironment();
  static Result<RuntimeConfig> fromEnvironment(const Environment&);
};

}  // namespace babel
