#include "babel/http/profile_controller.hpp"

#include <utility>

#include <drogon/HttpResponse.h>
#include <nlohmann/json.hpp>

namespace babel {
namespace {

using Json = nlohmann::json;

Json profileJson(const ProfileSummaryDto& profile) {
  return Json{{"id", profile.id.value},
              {"displayName", profile.display_name},
              {"color", profile.color},
              {"order", profile.order}};
}

Json graphJson(const ProfileGraphDto& graph) {
  Json babels = Json::array();
  for (const auto& babel : graph.babels) {
    babels.push_back(Json{{"id", babel.id.value},
                         {"title", babel.title},
                         {"contentHtml", babel.content_html},
                         {"color", babel.color},
                         {"contentRevision", babel.content_revision}});
  }
  Json edges = Json::array();
  for (const auto& edge : graph.edges) {
    edges.push_back(Json{{"id", edge.id.value},
                        {"sourceId", edge.source_id.value},
                        {"targetId", edge.target_id.value}});
  }
  return Json{{"profile", profileJson(graph.profile)},
              {"babels", std::move(babels)},
              {"edges", std::move(edges)}};
}

drogon::HttpResponsePtr response(drogon::HttpStatusCode status, Json payload) {
  auto result = drogon::HttpResponse::newHttpResponse();
  result->setStatusCode(status);
  result->setContentTypeCode(drogon::CT_APPLICATION_JSON);
  result->setBody(payload.dump());
  return result;
}

std::string errorCodeName(ErrorCode code) {
  switch (code) {
    case ErrorCode::invalid_argument:
      return "invalid_argument";
    case ErrorCode::not_found:
      return "not_found";
    case ErrorCode::conflict:
      return "conflict";
    case ErrorCode::database_unavailable:
      return "database_unavailable";
    case ErrorCode::wikipedia_unavailable:
      return "wikipedia_unavailable";
    case ErrorCode::wikipedia_not_found:
      return "wikipedia_not_found";
    case ErrorCode::sanitizer_rejected:
      return "sanitizer_rejected";
    case ErrorCode::invalid_legacy_file:
      return "invalid_legacy_file";
    case ErrorCode::internal:
      return "internal";
  }
  return "internal";
}

drogon::HttpResponsePtr applicationError(const ApplicationError& error) {
  drogon::HttpStatusCode status = drogon::k500InternalServerError;
  switch (error.code) {
    case ErrorCode::invalid_argument:
    case ErrorCode::invalid_legacy_file:
      status = drogon::k400BadRequest;
      break;
    case ErrorCode::not_found:
      status = drogon::k404NotFound;
      break;
    case ErrorCode::conflict:
      status = drogon::k409Conflict;
      break;
    case ErrorCode::database_unavailable:
      status = drogon::k503ServiceUnavailable;
      break;
    default:
      break;
  }
  const bool generic = status == drogon::k500InternalServerError ||
                       status == drogon::k503ServiceUnavailable;
  return response(status, Json{{"error", Json{{"code", errorCodeName(error.code)},
                                               {"message", generic ? "Request failed" : error.message}}}});
}

}  // namespace

ProfileController::ProfileController(ProfileQueryService& profiles)
    : ProfileController([&profiles] { return profiles.listProfiles(); },
                        [&profiles](CreatorId id) { return profiles.loadGraph(std::move(id)); }) {}

ProfileController::ProfileController(ListProfiles list_profiles, LoadGraph load_graph)
    : list_profiles_(std::move(list_profiles)), load_graph_(std::move(load_graph)) {}

void ProfileController::health(const drogon::HttpRequestPtr&, Callback callback) const {
  callback(response(drogon::k200OK, Json{{"status", "ok"}}));
}

void ProfileController::list(const drogon::HttpRequestPtr&, Callback callback) const {
  auto profiles = list_profiles_();
  if (!profiles) {
    callback(applicationError(profiles.error()));
    return;
  }
  Json values = Json::array();
  for (const auto& profile : *profiles) values.push_back(profileJson(profile));
  callback(response(drogon::k200OK, Json{{"profiles", std::move(values)}}));
}

void ProfileController::graph(const drogon::HttpRequestPtr&, Callback callback,
                              std::string profile_id) const {
  auto parsed = CreatorId::parse(profile_id);
  if (!parsed) {
    callback(applicationError(parsed.error()));
    return;
  }
  auto graph = load_graph_(std::move(*parsed));
  if (!graph) {
    callback(applicationError(graph.error()));
    return;
  }
  const auto serialized = graphJson(*graph).dump();
  if (serialized.size() > kMaxGraphJsonBytes) {
    callback(response(drogon::k413RequestEntityTooLarge,
                      Json{{"error", Json{{"code", "response_too_large"},
                                           {"message", "Profile graph exceeds the 64 MiB response limit"}}}}));
    return;
  }
  auto result = drogon::HttpResponse::newHttpResponse();
  result->setStatusCode(drogon::k200OK);
  result->setContentTypeCode(drogon::CT_APPLICATION_JSON);
  result->setBody(serialized);
  callback(result);
}

}  // namespace babel
