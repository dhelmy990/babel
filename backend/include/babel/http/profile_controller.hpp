#pragma once

#include <cstddef>
#include <functional>
#include <string>

#include <drogon/HttpRequest.h>
#include <drogon/HttpResponse.h>

#include "babel/application/profile_query_service.hpp"

namespace babel {

class ProfileController final {
 public:
  using Callback = std::function<void(const drogon::HttpResponsePtr&)>;
  using ListProfiles = std::function<Result<std::vector<ProfileSummaryDto>>()>;
  using LoadGraph = std::function<Result<ProfileGraphDto>(CreatorId)>;

  static constexpr std::size_t kMaxGraphJsonBytes = 64U * 1024U * 1024U;

  ProfileController(ProfileQueryService&, std::string instance_token);
  ProfileController(ListProfiles, LoadGraph, std::string instance_token = "test-instance");

  void health(const drogon::HttpRequestPtr&, Callback) const;
  void list(const drogon::HttpRequestPtr&, Callback) const;
  void graph(const drogon::HttpRequestPtr&, Callback, std::string profile_id) const;

 private:
  ListProfiles list_profiles_;
  LoadGraph load_graph_;
  std::string instance_token_;
};

}  // namespace babel
