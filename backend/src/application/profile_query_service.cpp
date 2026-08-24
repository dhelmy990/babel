#include "babel/application/profile_query_service.hpp"

namespace babel {

ProfileQueryService::ProfileQueryService(CreatorRepository& creators, GraphRepository& graphs)
    : creators_(creators), graphs_(graphs) {}

Result<std::vector<ProfileSummaryDto>> ProfileQueryService::listProfiles() {
  auto creators = creators_.listOrdered();
  if (!creators) {
    return tl::make_unexpected(creators.error());
  }

  std::vector<ProfileSummaryDto> profiles;
  profiles.reserve(creators->size());
  for (const auto& creator : creators.value()) {
    profiles.push_back(ProfileSummaryDto{
        .id = creator.id,
        .display_name = creator.display_name,
        .color = creator.color,
        .order = creator.order,
    });
  }
  return profiles;
}

Result<ProfileGraphDto> ProfileQueryService::loadGraph(CreatorId profile_id) {
  return graphs_.loadGraph(std::move(profile_id));
}

}  // namespace babel
