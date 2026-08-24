#pragma once

#include <string>
#include <vector>

#include "babel/domain/models.hpp"

namespace babel {

struct SeedAssignment {
  SeedAssignmentId id;
  CreatorId creator_id;
  std::string declared_title;
};

class ProfileManifest {
 public:
  [[nodiscard]] static std::vector<Creator> creators();
  [[nodiscard]] static std::vector<SeedAssignment> seedAssignments();
};

}  // namespace babel
