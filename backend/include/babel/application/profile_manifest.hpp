#pragma once

#include <vector>

#include "babel/application/dtos.hpp"

namespace babel {

class ProfileManifest {
 public:
  [[nodiscard]] static std::vector<Creator> creators();
  [[nodiscard]] static std::vector<SeedAssignment> seedAssignments();
};

}  // namespace babel
