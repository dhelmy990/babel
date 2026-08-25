#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

#include "babel/application/errors.hpp"
#include "babel/domain/models.hpp"

namespace babel {

struct RuntimeConfig {
  using Environment = std::function<std::optional<std::string>(std::string_view)>;

  std::string database_url;
  std::string bind_address{"127.0.0.1"};
  std::uint16_t port{8787};
  std::optional<std::string> instance_token;
  std::filesystem::path migration_directory;
  std::filesystem::path admin_asset_directory;
  std::optional<std::string> huggingface_token;
  SourceSelection seed_source{
      .repository = "dhelmy990/babel-wikipedia-experiment",
      .configuration = "demo_catalog_2026_06",
      .requested_revision = "e1acc648fcace8820dd5ee70bae9216ea4334555",
      .artifact_path = "backend-seed/2026-06/resolved-catalog-v3.jsonl",
  };
  std::filesystem::path huggingface_cache_root{
      "/home/dhelmy990/Data/babel-data/cache/backend-seed"};

  static Result<RuntimeConfig> fromEnvironment();
  static Result<RuntimeConfig> fromEnvironment(const Environment&);
};

}  // namespace babel
