#pragma once

#include <filesystem>

#include "babel/application/errors.hpp"

namespace babel {

class PostgresDatabase;

class MigrationRunner {
 public:
  explicit MigrationRunner(PostgresDatabase& database,
                           std::filesystem::path migration_directory = "backend/migrations");

  Result<void> run();

 private:
  PostgresDatabase& database_;
  std::filesystem::path migration_directory_;
};

}  // namespace babel
