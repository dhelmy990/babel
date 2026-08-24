#pragma once

#include <cstddef>
#include <filesystem>

#include "babel/application/ports.hpp"

namespace babel {

enum class LegacyMigrationStatus { imported, already_migrated };

struct LegacyMigrationResult {
  LegacyMigrationStatus status;
  std::size_t babel_count;
  std::size_t edge_count;
};

class LegacyMigrationService final {
 public:
  LegacyMigrationService(CreatorId personal_creator_id, LegacyMigrationRepository&,
                         HtmlSanitizer&, IdGenerator&);

  Result<LegacyMigrationResult> migrateFile(std::filesystem::path source_path);

 private:
  CreatorId personal_creator_id_;
  LegacyMigrationRepository& repository_;
  HtmlSanitizer& sanitizer_;
  IdGenerator& ids_;
};

}  // namespace babel
