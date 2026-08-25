#include "babel/http/admin_controller.hpp"

#include <fstream>
#include <iterator>
#include <string_view>
#include <utility>

#include <drogon/HttpResponse.h>
#include <nlohmann/json.hpp>

namespace babel {
namespace {

using Json = nlohmann::json;
constexpr std::size_t kMaxAdminAssetBytes = 2U * 1024U * 1024U;
constexpr std::string_view kNonceMarker = "{{BABEL_ADMIN_NONCE}}";

drogon::HttpResponsePtr jsonResponse(drogon::HttpStatusCode status, Json payload) {
  auto response = drogon::HttpResponse::newHttpResponse();
  response->setStatusCode(status);
  response->setContentTypeCode(drogon::CT_APPLICATION_JSON);
  response->addHeader("Cache-Control", "no-store");
  response->setBody(payload.dump());
  return response;
}

drogon::HttpResponsePtr textResponse(drogon::HttpStatusCode status, std::string body,
                                     std::string_view content_type) {
  auto response = drogon::HttpResponse::newHttpResponse();
  response->setStatusCode(status);
  response->setContentTypeString(std::string(content_type));
  response->addHeader("Cache-Control", "no-store");
  response->setBody(std::move(body));
  return response;
}

std::string runStateName(SeedRunState state) {
  switch (state) {
    case SeedRunState::queued:
      return "queued";
    case SeedRunState::running:
      return "running";
    case SeedRunState::completed:
      return "completed";
    case SeedRunState::completed_with_errors:
      return "completed_with_errors";
    case SeedRunState::failed:
      return "failed";
    case SeedRunState::interrupted:
      return "interrupted";
  }
  return "failed";
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

Json statusJson(const SeedStatusDto& status) {
  if (status.kind == SeedStatusKind::not_started) {
    return Json{{"state", "not_started"},
                {"total", 80},
                {"completed", 0},
                {"skipped", 0},
                {"failed", 0},
                {"errors", Json::array()}};
  }
  Json errors = Json::array();
  for (const auto& error : status.errors) {
    errors.push_back(Json{{"article", error.article},
                          {"code", errorCodeName(error.code)},
                          {"message", error.message}});
  }
  return Json{{"runId", status.run_id ? Json(status.run_id->value) : Json(nullptr)},
              {"state", status.run_state ? runStateName(*status.run_state) : "failed"},
              {"total", status.total},
              {"completed", status.imported},
              {"skipped", status.skipped},
              {"failed", status.failed},
              {"currentProfile", status.current_profile ? Json(*status.current_profile) : Json(nullptr)},
              {"currentArticle", status.current_article ? Json(*status.current_article) : Json(nullptr)},
              {"errors", std::move(errors)}};
}

drogon::HttpResponsePtr genericFailure(drogon::HttpStatusCode status,
                                       std::string_view code,
                                       std::string_view message) {
  return jsonResponse(status,
                      Json{{"error", Json{{"code", code}, {"message", message}}}});
}

}  // namespace

AdminController::AdminController(AdminSecurity& security,
                                 std::filesystem::path asset_directory,
                                 SeedJobRunner& runner)
    : AdminController(security, std::move(asset_directory),
                      [&runner] { return runner.currentStatus(); },
                      [&runner] { return runner.start(); }) {}

AdminController::AdminController(AdminSecurity& security,
                                 std::filesystem::path asset_directory,
                                 CurrentStatus current_status, StartSeed start_seed)
    : security_(security),
      asset_directory_(std::move(asset_directory)),
      current_status_(std::move(current_status)),
      start_seed_(std::move(start_seed)) {}

void AdminController::index(const drogon::HttpRequestPtr&, Callback callback) const {
  asset("index.html", "text/html; charset=utf-8", true, std::move(callback));
}

void AdminController::dashboardCss(const drogon::HttpRequestPtr&, Callback callback) const {
  asset("dashboard.css", "text/css; charset=utf-8", false, std::move(callback));
}

void AdminController::dashboardJs(const drogon::HttpRequestPtr&, Callback callback) const {
  asset("dashboard.js", "application/javascript; charset=utf-8", false,
        std::move(callback));
}

void AdminController::seedStatusJs(const drogon::HttpRequestPtr&, Callback callback) const {
  asset("seed-status.js", "application/javascript; charset=utf-8", false,
        std::move(callback));
}

void AdminController::experimentStatusJs(const drogon::HttpRequestPtr&,
                                         Callback callback) const {
  asset("experiment-status.js", "application/javascript; charset=utf-8", false,
        std::move(callback));
}

void AdminController::experimentDashboardJs(const drogon::HttpRequestPtr&,
                                            Callback callback) const {
  asset("experiment-dashboard.js", "application/javascript; charset=utf-8", false,
        std::move(callback));
}

void AdminController::asset(std::string_view filename, std::string_view content_type,
                            bool inject_nonce, Callback callback) const {
  if (filename.find('/') != std::string_view::npos ||
      filename.find('\\') != std::string_view::npos) {
    callback(genericFailure(drogon::k404NotFound, "not_found", "Asset not found"));
    return;
  }
  std::error_code error;
  const auto root = std::filesystem::weakly_canonical(asset_directory_, error);
  if (error) {
    callback(genericFailure(drogon::k500InternalServerError, "internal", "Asset unavailable"));
    return;
  }
  const auto candidate = std::filesystem::weakly_canonical(root / filename, error);
  const bool regular = !error && std::filesystem::is_regular_file(candidate, error);
  if (error || candidate.parent_path() != root || !regular) {
    callback(genericFailure(drogon::k404NotFound, "not_found", "Asset not found"));
    return;
  }
  const auto size = std::filesystem::file_size(candidate, error);
  if (error || size > kMaxAdminAssetBytes) {
    callback(genericFailure(drogon::k500InternalServerError, "internal", "Asset unavailable"));
    return;
  }
  std::ifstream input(candidate, std::ios::binary);
  std::string content{std::istreambuf_iterator<char>{input}, std::istreambuf_iterator<char>{}};
  if (!input || content.size() != size) {
    callback(genericFailure(drogon::k500InternalServerError, "internal", "Asset unavailable"));
    return;
  }
  if (inject_nonce) {
    const auto marker = content.find(kNonceMarker);
    if (marker == std::string::npos || content.find(kNonceMarker, marker + 1U) != std::string::npos) {
      callback(genericFailure(drogon::k500InternalServerError, "internal", "Asset unavailable"));
      return;
    }
    content.replace(marker, kNonceMarker.size(), security_.nonce());
  }
  callback(textResponse(drogon::k200OK, std::move(content), content_type));
}

void AdminController::seedStatus(const drogon::HttpRequestPtr&, Callback callback) const {
  try {
    auto status = current_status_();
    if (!status) {
      const auto code = status.error().code == ErrorCode::database_unavailable
                            ? drogon::k503ServiceUnavailable
                            : drogon::k500InternalServerError;
      callback(genericFailure(code, "seed_status_unavailable", "Seed status unavailable"));
      return;
    }
    callback(jsonResponse(drogon::k200OK, Json{{"status", statusJson(*status)}}));
  } catch (...) {
    callback(genericFailure(drogon::k500InternalServerError, "internal", "Seed status unavailable"));
  }
}

void AdminController::startSeed(const drogon::HttpRequestPtr& request,
                                Callback callback) const {
  if (!security_.authorizeMutation(request)) {
    callback(genericFailure(drogon::k403Forbidden, "forbidden", "Seed request rejected"));
    return;
  }
  try {
    auto started = start_seed_();
    if (started) {
      auto status = current_status_();
      Json payload{{"runId", started->value}};
      if (status) payload["status"] = statusJson(*status);
      callback(jsonResponse(drogon::k202Accepted, std::move(payload)));
      return;
    }
    if (started.error().code == ErrorCode::conflict) {
      auto status = current_status_();
      if (status) {
        callback(jsonResponse(drogon::k409Conflict, Json{{"status", statusJson(*status)}}));
      } else {
        callback(genericFailure(drogon::k409Conflict, "conflict", "A seed run is active"));
      }
      return;
    }
    const auto code = started.error().code == ErrorCode::database_unavailable
                          ? drogon::k503ServiceUnavailable
                          : drogon::k500InternalServerError;
    callback(genericFailure(code, "seed_start_failed", "Seed run could not be started"));
  } catch (...) {
    callback(genericFailure(drogon::k500InternalServerError, "internal",
                            "Seed run could not be started"));
  }
}

}  // namespace babel
