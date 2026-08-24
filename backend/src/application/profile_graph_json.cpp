#include "babel/application/profile_graph_json.hpp"

#include <utility>

#include <nlohmann/json.hpp>

namespace babel {

std::string serializeProfileGraphJson(const ProfileGraphDto& graph) {
  using Json = nlohmann::json;

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
  return Json{{"profile",
               Json{{"id", graph.profile.id.value},
                    {"displayName", graph.profile.display_name},
                    {"color", graph.profile.color},
                    {"order", graph.profile.order}}},
              {"babels", std::move(babels)},
              {"edges", std::move(edges)}}
      .dump();
}

}  // namespace babel
