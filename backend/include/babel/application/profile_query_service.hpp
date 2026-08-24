#pragma once

#include "babel/application/ports.hpp"

namespace babel {

class ProfileQueryService {
 public:
  ProfileQueryService(CreatorRepository& creators, GraphRepository& graphs);

  Result<std::vector<ProfileSummaryDto>> listProfiles();
  Result<ProfileGraphDto> loadGraph(CreatorId profile_id);

 private:
  CreatorRepository& creators_;
  GraphRepository& graphs_;
};

}  // namespace babel
