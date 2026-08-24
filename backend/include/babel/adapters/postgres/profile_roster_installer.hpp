#pragma once

#include <span>

#include "babel/application/errors.hpp"
#include "babel/domain/models.hpp"

namespace babel {

class PostgresDatabase;

class ProfileRosterInstaller {
 public:
  explicit ProfileRosterInstaller(PostgresDatabase& database);

  Result<void> install(std::span<const Creator> creators);

 private:
  PostgresDatabase& database_;
};

}  // namespace babel
