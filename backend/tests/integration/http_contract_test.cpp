#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <functional>
#include <string>

#include <drogon/drogon.h>
#include <nlohmann/json.hpp>

#include "babel/application/profile_manifest.hpp"
#include "babel/http/admin_controller.hpp"
#include "babel/http/admin_security.hpp"
#include "babel/http/profile_controller.hpp"
#include "babel/runtime/application.hpp"
#include "babel/runtime/config.hpp"

namespace {

using namespace babel;
using Json = nlohmann::json;

drogon::HttpRequestPtr request(drogon::HttpMethod method, std::string path) {
  auto result = drogon::HttpRequest::newHttpRequest();
  result->setMethod(method);
  result->setPath(std::move(path));
  return result;
}

drogon::HttpResponsePtr invoke(
    const std::function<void(const drogon::HttpRequestPtr&,
                             std::function<void(const drogon::HttpResponsePtr&)>)>& handler,
    const drogon::HttpRequestPtr& req) {
  drogon::HttpResponsePtr response;
  handler(req, [&](const auto& value) { response = value; });
  REQUIRE(response != nullptr);
  return response;
}

Json body(const drogon::HttpResponsePtr& response) {
  return Json::parse(response->getBody());
}

ProfileGraphDto graphWithContent(std::size_t content_size) {
  const auto creators = ProfileManifest::creators();
  return ProfileGraphDto{
      .profile = ProfileSummaryDto{
          .id = creators.at(1).id,
          .display_name = creators.at(1).display_name,
          .color = creators.at(1).color,
          .order = creators.at(1).order,
      },
      .babels = {BabelDto{
          .id = BabelId::v5("http-contract-babel").value(),
          .title = "A title",
          .content_html = std::string(content_size, 'x'),
          .color = creators.at(1).color,
          .content_revision = 1,
      }},
      .edges = {},
  };
}

TEST_CASE("profile routes use camelCase JSON and preserve empty graphs") {
  const auto creators = ProfileManifest::creators();
  ProfileController controller{
      [creators] {
        std::vector<ProfileSummaryDto> profiles;
        for (const auto& creator : creators) {
          profiles.push_back({creator.id, creator.display_name, creator.color, creator.order});
        }
        return Result<std::vector<ProfileSummaryDto>>{std::move(profiles)};
      },
      [graph = graphWithContent(0)](CreatorId) mutable {
        graph.babels.clear();
        return Result<ProfileGraphDto>{graph};
      },
      "test-instance-token"};

  const auto health = invoke(
      [&](const auto& req, auto callback) { controller.health(req, std::move(callback)); },
      request(drogon::Get, "/health"));
  REQUIRE(health->getStatusCode() == drogon::k200OK);
  CHECK(body(health).at("status") == "ok");
  CHECK(body(health).at("instanceToken") == "test-instance-token");

  const auto profiles = invoke(
      [&](const auto& req, auto callback) { controller.list(req, std::move(callback)); },
      request(drogon::Get, "/api/v1/profiles"));
  REQUIRE(profiles->getStatusCode() == drogon::k200OK);
  const auto profiles_json = body(profiles);
  REQUIRE(profiles_json.at("profiles").size() == 21);
  CHECK(profiles_json["profiles"][0]["displayName"] == "Personal");
  CHECK(profiles_json["profiles"][0].contains("display_name") == false);

  const auto graph = invoke(
      [&](const auto& req, auto callback) {
        controller.graph(req, std::move(callback), creators.at(1).id.value);
      },
      request(drogon::Get, "/api/v1/profiles/id/graph"));
  REQUIRE(graph->getStatusCode() == drogon::k200OK);
  const auto graph_json = body(graph);
  CHECK(graph_json.at("babels").empty());
  CHECK(graph_json.at("edges").empty());
  CHECK(graph_json.at("profile").at("displayName") == creators.at(1).display_name);
}

TEST_CASE("profile graph rejects invalid IDs and oversized complete JSON") {
  ProfileController invalid_controller{
      [] { return Result<std::vector<ProfileSummaryDto>>{std::vector<ProfileSummaryDto>{}}; },
      [](CreatorId) { return Result<ProfileGraphDto>{graphWithContent(0)}; }};
  const auto invalid = invoke(
      [&](const auto& req, auto callback) {
        invalid_controller.graph(req, std::move(callback), "not-a-uuid");
      },
      request(drogon::Get, "/api/v1/profiles/not-a-uuid/graph"));
  REQUIRE(invalid->getStatusCode() == drogon::k400BadRequest);
  CHECK(body(invalid).at("error").at("code") == "invalid_argument");

  ProfileController oversized_controller{
      [] { return Result<std::vector<ProfileSummaryDto>>{std::vector<ProfileSummaryDto>{}}; },
      [](CreatorId) {
        return Result<ProfileGraphDto>{graphWithContent(ProfileController::kMaxGraphJsonBytes)};
      }};
  const auto oversized = invoke(
      [&](const auto& req, auto callback) {
        oversized_controller.graph(req, std::move(callback),
                                   ProfileManifest::creators().at(1).id.value);
      },
      request(drogon::Get, "/api/v1/profiles/id/graph"));
  REQUIRE(oversized->getStatusCode() == drogon::k413RequestEntityTooLarge);
  CHECK(body(oversized).at("error").at("code") == "response_too_large");
  CHECK(oversized->getBody().size() < 1024);
}

TEST_CASE("profile errors map to stable statuses without leaking internal details") {
  ProfileController controller{
      [] {
        return Result<std::vector<ProfileSummaryDto>>{tl::make_unexpected(
            ApplicationError{ErrorCode::database_unavailable, "password=secret"})};
      },
      [](CreatorId) {
        return Result<ProfileGraphDto>{tl::make_unexpected(
            ApplicationError{ErrorCode::internal, "SELECT * FROM private_table"})};
      }};
  const auto unavailable = invoke(
      [&](const auto& req, auto callback) { controller.list(req, std::move(callback)); },
      request(drogon::Get, "/api/v1/profiles"));
  CHECK(unavailable->getStatusCode() == drogon::k503ServiceUnavailable);
  CHECK(unavailable->getBody().find("secret") == std::string::npos);

  const auto internal = invoke(
      [&](const auto& req, auto callback) {
        controller.graph(req, std::move(callback),
                         ProfileManifest::creators().at(1).id.value);
      },
      request(drogon::Get, "/api/v1/profiles/id/graph"));
  CHECK(internal->getStatusCode() == drogon::k500InternalServerError);
  CHECK(internal->getBody().find("private_table") == std::string::npos);
}

TEST_CASE("admin mutation requires exact local host origin and constant-time nonce check") {
  AdminSecurity security{"fixed-process-nonce"};
  auto valid = request(drogon::Post, "/admin/api/v1/seed");
  valid->addHeader("host", "127.0.0.1:8787");
  valid->addHeader("origin", "http://127.0.0.1:8787");
  valid->addHeader("x-babel-admin-nonce", "fixed-process-nonce");
  CHECK(security.authorizeMutation(valid));

  auto localhost = request(drogon::Post, "/admin/api/v1/seed");
  localhost->addHeader("host", "localhost:8787");
  localhost->addHeader("origin", "http://localhost:8787");
  localhost->addHeader("x-babel-admin-nonce", "fixed-process-nonce");
  CHECK(security.authorizeMutation(localhost));

  for (const auto& [header, value] : std::vector<std::pair<std::string, std::string>>{
           {"host", "evil.example:8787"},
           {"origin", "http://evil.example:8787"},
           {"x-babel-admin-nonce", "wrong-process-nonce"}}) {
    auto rejected = request(drogon::Post, "/admin/api/v1/seed");
    rejected->addHeader("host", "127.0.0.1:8787");
    rejected->addHeader("origin", "http://127.0.0.1:8787");
    rejected->addHeader("x-babel-admin-nonce", "fixed-process-nonce");
    rejected->addHeader(header, value);
    CHECK_FALSE(security.authorizeMutation(rejected));
  }
}

TEST_CASE("admin serves nonce-injected no-store assets and maps seed state") {
  const auto temp = std::filesystem::temp_directory_path() / "babel-http-contract-assets";
  std::filesystem::create_directories(temp);
  {
    std::ofstream(temp / "index.html") << "nonce={{BABEL_ADMIN_NONCE}}";
    std::ofstream(temp / "dashboard.css") << "body{}";
    std::ofstream(temp / "dashboard.js") << "void 0;";
    std::ofstream(temp / "seed-status.js") << "void 0;";
    std::ofstream(temp / "experiment-status.js") << "void 0;";
    std::ofstream(temp / "experiment-dashboard.js") << "void 0;";
  }
  const auto run_id = SeedRunId::v5("http-active-run").value();
  AdminSecurity security{"fixed-process-nonce"};
  AdminController controller{
      security,
      temp,
      [run_id] {
        return Result<SeedStatusDto>{SeedStatusDto{
            .kind = SeedStatusKind::persisted,
            .run_id = run_id,
            .run_state = SeedRunState::running,
            .total = 80,
            .imported = 3,
            .skipped = 1,
            .failed = 2,
            .current_profile = std::nullopt,
            .current_article = std::nullopt,
            .errors = {},
        }};
      },
      [run_id] { return Result<SeedRunId>{run_id}; }};

  const auto index = invoke(
      [&](const auto& req, auto callback) { controller.index(req, std::move(callback)); },
      request(drogon::Get, "/admin"));
  REQUIRE(index->getStatusCode() == drogon::k200OK);
  CHECK(index->getBody().find("fixed-process-nonce") != std::string::npos);
  CHECK(index->getBody().find("{{BABEL_ADMIN_NONCE}}") == std::string::npos);
  CHECK(index->getHeader("cache-control") == "no-store");

  const auto css = invoke(
      [&](const auto& req, auto callback) {
        controller.dashboardCss(req, std::move(callback));
      },
      request(drogon::Get, "/admin/dashboard.css"));
  const auto dashboard_js = invoke(
      [&](const auto& req, auto callback) {
        controller.dashboardJs(req, std::move(callback));
      },
      request(drogon::Get, "/admin/dashboard.js"));
  const auto status_js = invoke(
      [&](const auto& req, auto callback) {
        controller.seedStatusJs(req, std::move(callback));
      },
      request(drogon::Get, "/admin/seed-status.js"));
  const auto experiment_status_js = invoke(
      [&](const auto& req, auto callback) {
        controller.experimentStatusJs(req, std::move(callback));
      },
      request(drogon::Get, "/admin/experiment-status.js"));
  const auto experiment_dashboard_js = invoke(
      [&](const auto& req, auto callback) {
        controller.experimentDashboardJs(req, std::move(callback));
      },
      request(drogon::Get, "/admin/experiment-dashboard.js"));
  for (const auto& response :
       {css, dashboard_js, status_js, experiment_status_js, experiment_dashboard_js}) {
    CHECK(response->getStatusCode() == drogon::k200OK);
    CHECK(response->getHeader("cache-control") == "no-store");
  }

  const auto status = invoke(
      [&](const auto& req, auto callback) { controller.seedStatus(req, std::move(callback)); },
      request(drogon::Get, "/admin/api/v1/seed"));
  REQUIRE(status->getStatusCode() == drogon::k200OK);
  CHECK(body(status).at("status").at("state") == "running");
  CHECK(body(status).at("status").at("completed") == 3);
  CHECK(status->getHeader("access-control-allow-origin").empty());

  std::filesystem::remove_all(temp);
}

TEST_CASE("admin seed status exposes the current item and durable errors") {
  const auto run_id = SeedRunId::v5("http-status-details").value();
  AdminSecurity security{"fixed-process-nonce"};
  AdminController controller{
      security,
      std::filesystem::temp_directory_path(),
      [run_id] {
        return Result<SeedStatusDto>{SeedStatusDto{
            .kind = SeedStatusKind::persisted,
            .run_id = run_id,
            .run_state = SeedRunState::completed_with_errors,
            .total = 80,
            .imported = 78,
            .skipped = 0,
            .failed = 2,
            .current_profile = "Film and Cinema Creator",
            .current_article = "Cinematography",
            .errors = {SeedErrorDto{
                .article = "Cinematography",
                .code = ErrorCode::wikipedia_unavailable,
                .message = "MediaWiki unavailable <retry>",
            }},
        }};
      },
      [run_id] { return Result<SeedRunId>{run_id}; }};

  const auto response = invoke(
      [&](const auto& req, auto callback) { controller.seedStatus(req, std::move(callback)); },
      request(drogon::Get, "/admin/api/v1/seed"));
  const auto status = body(response).at("status");
  CHECK(status.at("currentProfile") == "Film and Cinema Creator");
  CHECK(status.at("currentArticle") == "Cinematography");
  REQUIRE(status.at("errors").size() == 1);
  CHECK(status["errors"][0]["code"] == "wikipedia_unavailable");
  CHECK(status["errors"][0]["message"] == "MediaWiki unavailable <retry>");
}

TEST_CASE("admin seed POST returns 403 202 and active-run 409") {
  const auto run_id = SeedRunId::v5("http-started-run").value();
  AdminSecurity security{"fixed-process-nonce"};
  int starts = 0;
  AdminController controller{
      security,
      std::filesystem::temp_directory_path(),
      [run_id] {
        return Result<SeedStatusDto>{SeedStatusDto{
            .kind = SeedStatusKind::persisted,
            .run_id = run_id,
            .run_state = SeedRunState::running,
            .total = 80,
            .imported = 0,
            .skipped = 0,
            .failed = 0,
            .current_profile = std::nullopt,
            .current_article = std::nullopt,
            .errors = {},
        }};
      },
      [&] {
        ++starts;
        if (starts > 1) {
          return Result<SeedRunId>{tl::make_unexpected(
              ApplicationError{ErrorCode::conflict, "active"})};
        }
        return Result<SeedRunId>{run_id};
      }};

  auto missing_nonce = request(drogon::Post, "/admin/api/v1/seed");
  const auto forbidden = invoke(
      [&](const auto& req, auto callback) { controller.startSeed(req, std::move(callback)); },
      missing_nonce);
  CHECK(forbidden->getStatusCode() == drogon::k403Forbidden);

  auto valid = request(drogon::Post, "/admin/api/v1/seed");
  valid->addHeader("host", "127.0.0.1:8787");
  valid->addHeader("origin", "http://127.0.0.1:8787");
  valid->addHeader("x-babel-admin-nonce", "fixed-process-nonce");
  const auto accepted = invoke(
      [&](const auto& req, auto callback) { controller.startSeed(req, std::move(callback)); },
      valid);
  REQUIRE(accepted->getStatusCode() == drogon::k202Accepted);
  CHECK(body(accepted).at("runId") == run_id.value);

  const auto conflict = invoke(
      [&](const auto& req, auto callback) { controller.startSeed(req, std::move(callback)); },
      valid);
  REQUIRE(conflict->getStatusCode() == drogon::k409Conflict);
  CHECK(body(conflict).at("status").at("runId") == run_id.value);
}

TEST_CASE("runtime configuration is fixed to loopback and rejects unsafe database input") {
  const auto defaults = RuntimeConfig::fromEnvironment([](std::string_view) {
    return std::optional<std::string>{};
  });
  REQUIRE(defaults.has_value());
  CHECK(defaults->bind_address == "127.0.0.1");
  CHECK(defaults->port == 8787);
  CHECK(defaults->database_url ==
        "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel");
  CHECK(defaults->seed_source.repository == "dhelmy990/babel-wikipedia-experiment");
  CHECK(defaults->seed_source.configuration == "catalog_2026_06");
  CHECK(defaults->experiment_source.repository ==
        "dhelmy990/babel-wikipedia-experiment");
  CHECK(defaults->experiment_source.configuration == "demo_crosswalk");
  CHECK(defaults->experiment_source.commit_sha ==
        "e1acc648fcace8820dd5ee70bae9216ea4334555");
  CHECK(defaults->seed_source.requested_revision ==
        "0d1ab2c7f0e2295682288fcf10077d2d776bf559");
  CHECK(defaults->seed_source.artifact_path ==
        "backend-seed/2026-06/resolved-catalog-v3.jsonl");
  CHECK(defaults->huggingface_cache_root ==
        "/home/dhelmy990/Data/babel-data/cache/backend-seed");
  CHECK_FALSE(defaults->huggingface_token.has_value());
  CHECK(defaults->online_worker_endpoint == "http://127.0.0.1:8790");
  CHECK_FALSE(defaults->online_worker_token.has_value());

  const auto nul = RuntimeConfig::fromEnvironment([](std::string_view name) {
    return name == "BABEL_DATABASE_URL"
               ? std::optional<std::string>{std::string("postgresql://local\0evil", 23)}
               : std::nullopt;
  });
  CHECK_FALSE(nul.has_value());

  const auto configured = RuntimeConfig::fromEnvironment([](std::string_view name) {
    if (name == "HF_TOKEN") return std::optional<std::string>{"server-secret"};
    if (name == "BABEL_HF_REVISION") return std::optional<std::string>{std::string(40, 'a')};
    if (name == "BABEL_HF_CONFIG") return std::optional<std::string>{"demo_catalog"};
    if (name == "BABEL_ONLINE_DATASET_REPOSITORY")
      return std::optional<std::string>{"owner/online"};
    if (name == "BABEL_ONLINE_DATASET_CONFIG")
      return std::optional<std::string>{"online_demo"};
    if (name == "BABEL_ONLINE_DATASET_REVISION")
      return std::optional<std::string>{std::string(40, 'd')};
    if (name == "BABEL_ONLINE_WORKER_ENDPOINT")
      return std::optional<std::string>{"http://127.0.0.1:9876"};
    if (name == "BABEL_ONLINE_WORKER_TOKEN")
      return std::optional<std::string>{std::string(64, 'e')};
    if (name == "BABEL_DATA_ROOT") return std::optional<std::string>{"/srv/babel-data"};
    return std::optional<std::string>{};
  });
  REQUIRE(configured.has_value());
  CHECK(configured->huggingface_token == "server-secret");
  CHECK(configured->seed_source.requested_revision == std::string(40, 'a'));
  CHECK(configured->seed_source.configuration == "demo_catalog");
  CHECK(configured->experiment_source.repository == "owner/online");
  CHECK(configured->experiment_source.configuration == "online_demo");
  CHECK(configured->experiment_source.commit_sha == std::string(40, 'd'));
  CHECK(configured->huggingface_cache_root == "/srv/babel-data/cache/backend-seed");
  CHECK(configured->online_worker_endpoint == "http://127.0.0.1:9876");
  CHECK(configured->online_worker_token == std::optional{std::string(64, 'e')});
}

TEST_CASE("runtime command parser exposes migrate serve and typed Personal migration only") {
  const auto migrate = parseRuntimeCommand(std::vector<std::string_view>{"migrate"});
  REQUIRE(migrate.has_value());
  CHECK(migrate->kind == RuntimeCommandKind::migrate);

  const auto serve = parseRuntimeCommand(std::vector<std::string_view>{"serve"});
  REQUIRE(serve.has_value());
  CHECK(serve->kind == RuntimeCommandKind::serve);

  const auto personal = parseRuntimeCommand(
      std::vector<std::string_view>{"migrate-personal", "--source", "/tmp/legacy.json"});
  REQUIRE(personal.has_value());
  CHECK(personal->kind == RuntimeCommandKind::migrate_personal);
  REQUIRE(personal->source.has_value());
  CHECK(*personal->source == "/tmp/legacy.json");

  CHECK_FALSE(parseRuntimeCommand(std::vector<std::string_view>{"seed"}).has_value());
  CHECK_FALSE(parseRuntimeCommand(
                  std::vector<std::string_view>{"migrate-personal", "/tmp/legacy.json"})
                  .has_value());
  CHECK_FALSE(parseRuntimeCommand(std::vector<std::string_view>{"serve", "extra"}).has_value());
}

}  // namespace
