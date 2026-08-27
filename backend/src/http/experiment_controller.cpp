#include "babel/http/experiment_controller.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cctype>
#include <limits>
#include <set>
#include <string_view>
#include <type_traits>
#include <utility>

#include <nlohmann/json.hpp>

namespace babel {
namespace {

using Json = nlohmann::json;

drogon::HttpResponsePtr jsonResponse(drogon::HttpStatusCode status, Json payload) {
  auto response = drogon::HttpResponse::newHttpResponse();
  response->setStatusCode(status);
  response->setContentTypeCode(drogon::CT_APPLICATION_JSON);
  response->addHeader("Cache-Control", "no-store");
  response->setBody(payload.dump());
  return response;
}

Json nullable(const std::optional<std::string>& value) {
  return value ? Json(*value) : Json(nullptr);
}

template <typename Id>
Json nullableId(const std::optional<Id>& value) {
  return value ? Json(value->value) : Json(nullptr);
}

Json embeddingJson(const EmbeddingSpaceDto& value) {
  return Json{{"schemaVersion", value.schema_version},
              {"embeddingSpaceId", value.embedding_space_id},
              {"dimension", value.dimension},
              {"distance", value.distance},
              {"distilledEncoderArtifact", value.distilled_encoder_artifact},
              {"datasetRevision", value.dataset_revision},
              {"compatibilityVersion", value.compatibility_version}};
}

Json modelJson(const RecommenderModelDto& value) {
  return Json{{"schemaVersion", 1},
              {"modelId", value.model_id.value},
              {"label", value.label},
              {"parentModelId", nullableId(value.parent_model_id)},
              {"producingRunId", nullableId(value.producing_run_id)},
              {"encoderRepo", value.encoder_repo},
              {"encoderRevision", value.encoder_revision},
              {"datasetRepo", value.dataset_repo},
              {"datasetRevision", value.dataset_revision},
              {"environmentSequence", value.environment_sequence},
              {"trainingExamples", value.training_examples},
              {"checkpointPath", value.checkpoint_path},
              {"checkpointSha256", value.checkpoint_sha256},
              {"embeddingSpace", embeddingJson(value.embedding_space)},
              {"immutable", value.immutable},
              {"createdAt", value.created_at},
              {"compatible", value.compatible},
              {"incompatibilityReason", nullable(value.incompatibility_reason)}};
}

Json runJson(const ExperimentRunStatusDto& value) {
  return Json{{"runId", value.run_id.value},
              {"status", experimentStatusName(value.status)},
              {"retrievalBackend", retrievalBackendName(value.retrieval_backend)},
              {"creatorCount", value.creator_count},
              {"environmentSequence", value.environment_sequence},
              {"startingModelId", value.starting_model_id.value},
              {"activeModelId", value.active_model_id.value},
              {"activeModelVersion", value.active_model_version},
              {"createdBabelCount", value.created_babel_count},
              {"feedbackCount", value.feedback_count},
              {"eventRate", value.event_rate},
              {"kafkaOffset", value.kafka_offset},
              {"kafkaLag", value.kafka_lag},
              {"trainerSteps", value.trainer_steps},
              {"rollingRankLoss", value.rolling_rank_loss ? Json(*value.rolling_rank_loss)
                                                           : Json(nullptr)},
              {"checkpointPath", nullable(value.checkpoint_path)},
              {"checkpointSha256", nullable(value.checkpoint_sha256)},
              {"servingSynced", value.serving_synced},
              {"startedAt", nullable(value.started_at)},
              {"completedAt", nullable(value.completed_at)},
              {"failure", nullable(value.failure)}};
}

Json idArray(const std::vector<BabelId>& values) {
  Json result = Json::array();
  for (const auto& value : values) result.push_back(value.value);
  return result;
}

Json detailsJson(const ExperimentActivityDetails& details) {
  return std::visit(
      [](const auto& value) -> Json {
        using Detail = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<Detail, ExperimentRecommendationActivityDto>) {
          Json result{{"kind", "recommendation"},
                      {"creatorId", value.creator_id.value},
                      {"newBabelId", value.new_babel_id.value},
                      {"newBabelTitle", value.new_babel_title},
                      {"candidateBabelIds", idArray(value.candidate_babel_ids)},
                      {"includeBabelIds", idArray(value.include_babel_ids)},
                      {"excludeBabelIds", idArray(value.exclude_babel_ids)},
                      {"ignoreBabelIds", idArray(value.ignore_babel_ids)},
                      {"acceptedEdgeCount", value.accepted_edge_count},
                      {"modelId", value.model_id.value},
                      {"modelVersion", value.model_version}};
          if (value.request_id) result["requestId"] = *value.request_id;
          if (value.traversal_session_id) {
            result["traversalSessionId"] = *value.traversal_session_id;
          }
          if (value.source_vector_origin) {
            result["sourceVectorOrigin"] = *value.source_vector_origin;
          }
          return result;
        } else if constexpr (std::is_same_v<Detail, ExperimentFeedbackActivityDto>) {
          return Json{{"kind", "feedback"},
                      {"kafkaOffset", value.kafka_offset},
                      {"kafkaLag", value.kafka_lag}};
        } else if constexpr (std::is_same_v<Detail, ExperimentTrainingActivityDto>) {
          return Json{{"kind", "training"},
                      {"trainerStep", value.trainer_step},
                      {"rollingRankLoss", value.rolling_rank_loss}};
        } else if constexpr (std::is_same_v<Detail,
                                             ExperimentSynchronizationActivityDto>) {
          return Json{{"kind", "synchronization"},
                      {"checkpointPath", value.checkpoint_path},
                      {"checkpointSha256", value.checkpoint_sha256},
                      {"synchronizationVersion", value.synchronization_version},
                      {"modelId", value.model_id.value},
                      {"modelVersion", value.model_version}};
        } else {
          return Json{{"kind", "lifecycle"}};
        }
      },
      details);
}

Json observableMetrics(const std::map<std::string, double>& metrics) {
  static constexpr std::array<std::string_view, 5> hidden{
      "graph", "ppr", "clickstream", "profile", "random"};
  Json result = Json::object();
  for (const auto& [name, value] : metrics) {
    std::string folded(name.size(), '\0');
    std::transform(name.begin(), name.end(), folded.begin(), [](unsigned char character) {
      return static_cast<char>(std::tolower(character));
    });
    const bool forbidden = std::any_of(hidden.begin(), hidden.end(), [&](auto part) {
      return folded.find(part) != std::string::npos;
    });
    if (!forbidden) result[name] = value;
  }
  return result;
}

Json activityJson(const ExperimentActivityDto& value) {
  return Json{{"schemaVersion", value.schema_version},
              {"runId", value.run_id.value},
              {"sequence", value.sequence},
              {"occurredAtNs", value.occurred_at_ns},
              {"level", value.level},
              {"component", value.component},
              {"event", value.event},
              {"message", value.message},
              {"metrics", observableMetrics(value.metrics)},
              {"details", detailsJson(value.details)}};
}

drogon::HttpStatusCode statusCode(const ApplicationError& error) {
  switch (error.code) {
    case ErrorCode::invalid_argument:
      return drogon::k400BadRequest;
    case ErrorCode::not_found:
      return drogon::k404NotFound;
    case ErrorCode::conflict:
      return drogon::k409Conflict;
    case ErrorCode::database_unavailable:
      return drogon::k503ServiceUnavailable;
    default:
      return drogon::k500InternalServerError;
  }
}

std::string errorName(const ApplicationError& error) {
  switch (error.code) {
    case ErrorCode::invalid_argument:
      return "invalid_argument";
    case ErrorCode::not_found:
      return "not_found";
    case ErrorCode::conflict:
      return "conflict";
    case ErrorCode::database_unavailable:
      return "unavailable";
    default:
      return "internal";
  }
}

drogon::HttpResponsePtr failure(const ApplicationError& error,
                                std::string_view public_message) {
  return jsonResponse(statusCode(error),
                      Json{{"error", Json{{"code", errorName(error)},
                                           {"message", public_message}}}});
}

Result<std::uint64_t> unsignedParameter(std::string_view value, std::uint64_t fallback) {
  if (value.empty()) return fallback;
  std::uint64_t result = 0;
  const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
  if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
    return invalidArgument("invalid pagination parameter");
  }
  return result;
}

Result<ExperimentLaunchRequest> launchRequest(const drogon::HttpRequestPtr& request) {
  auto payload = Json::parse(request->body(), nullptr, false);
  if (!payload.is_object()) return invalidArgument("experiment launch must be JSON");
  const std::set<std::string> allowed{"startingModelId", "retrievalBackend", "creatorCount",
                                      "scenario", "eventBudgetPerMonth", "runSeed"};
  for (const auto& [key, value] : payload.items()) {
    static_cast<void>(value);
    if (!allowed.contains(key)) return invalidArgument("unknown experiment launch field");
  }
  if (!payload.contains("startingModelId") ||
      !payload.at("startingModelId").is_string()) {
    return invalidArgument("startingModelId is required");
  }
  auto model_id = RecommenderModelId::parse(payload.at("startingModelId").get<std::string>());
  if (!model_id) return tl::make_unexpected(model_id.error());

  try {
    const auto backend_name = payload.value("retrievalBackend", "pgvector");
    if (backend_name != "pgvector" && backend_name != "hnswlib") {
      return invalidArgument("retrievalBackend must be pgvector or hnswlib");
    }
    const auto scenario_name = payload.value("scenario", "june_to_july");
    if (scenario_name != "june_only" && scenario_name != "june_to_july") {
      return invalidArgument("scenario must be june_only or june_to_july");
    }
    return ExperimentLaunchRequest{
        .starting_model_id = *model_id,
        .retrieval_backend = backend_name == "hnswlib" ? RetrievalBackend::hnswlib
                                                        : RetrievalBackend::pgvector,
        .creator_count = payload.value("creatorCount", std::size_t{50}),
        .scenario = scenario_name == "june_only" ? ExperimentScenario::june_only
                                                  : ExperimentScenario::june_to_july,
        .event_budget_per_month =
            payload.value("eventBudgetPerMonth", std::size_t{100}),
        .run_seed = payload.value("runSeed", std::uint64_t{0}),
    };
  } catch (const std::exception&) {
    return invalidArgument("experiment launch field has invalid type");
  }
}

}  // namespace

ExperimentController::ExperimentController(AdminSecurity& security,
                                           ExperimentService& service)
    : security_(security), service_(service) {}

void ExperimentController::models(const drogon::HttpRequestPtr&, Callback callback) const {
  try {
    auto models = service_.listModels();
    if (!models) {
      callback(failure(models.error(), "Experiment models unavailable"));
      return;
    }
    Json rows = Json::array();
    for (const auto& model : *models) rows.push_back(modelJson(model));
    callback(jsonResponse(drogon::k200OK, Json{{"models", std::move(rows)}}));
  } catch (...) {
    callback(jsonResponse(drogon::k500InternalServerError,
                          Json{{"error", Json{{"code", "internal"},
                                               {"message", "Experiment models unavailable"}}}}));
  }
}

void ExperimentController::latest(const drogon::HttpRequestPtr&, Callback callback) const {
  try {
    auto run = service_.latestRun();
    if (!run && run.error().code == ErrorCode::not_found) {
      callback(jsonResponse(drogon::k200OK, Json{{"run", nullptr}}));
      return;
    }
    if (!run) {
      callback(failure(run.error(), "Experiment status unavailable"));
      return;
    }
    callback(jsonResponse(drogon::k200OK, Json{{"run", runJson(*run)}}));
  } catch (...) {
    callback(jsonResponse(drogon::k500InternalServerError,
                          Json{{"error", Json{{"code", "internal"},
                                               {"message", "Experiment status unavailable"}}}}));
  }
}

void ExperimentController::run(const drogon::HttpRequestPtr&, std::string run_id,
                               Callback callback) const {
  auto parsed = ExperimentRunId::parse(run_id);
  if (!parsed) {
    callback(failure(parsed.error(), "Invalid experiment run ID"));
    return;
  }
  auto run = service_.getRun(*parsed);
  if (!run) {
    callback(failure(run.error(), "Experiment run unavailable"));
    return;
  }
  callback(jsonResponse(drogon::k200OK, Json{{"run", runJson(*run)}}));
}

void ExperimentController::activity(const drogon::HttpRequestPtr& request,
                                    std::string run_id, Callback callback) const {
  auto parsed = ExperimentRunId::parse(run_id);
  auto after = unsignedParameter(request->getParameter("after"), 0);
  auto limit = unsignedParameter(request->getParameter("limit"), 200);
  if (!parsed || !after || !limit || *limit > 200 || *limit == 0) {
    callback(jsonResponse(drogon::k400BadRequest,
                          Json{{"error", Json{{"code", "invalid_argument"},
                                               {"message", "Invalid activity request"}}}}));
    return;
  }
  auto rows = service_.activity(*parsed, *after, static_cast<std::size_t>(*limit));
  if (!rows) {
    callback(failure(rows.error(), "Experiment activity unavailable"));
    return;
  }
  Json activity = Json::array();
  std::uint64_t next_after = *after;
  for (const auto& row : *rows) {
    activity.push_back(activityJson(row));
    next_after = row.sequence;
  }
  callback(jsonResponse(drogon::k200OK,
                        Json{{"activity", std::move(activity)}, {"nextAfter", next_after}}));
}

void ExperimentController::start(const drogon::HttpRequestPtr& request,
                                 Callback callback) const {
  if (!security_.authorizeMutation(request)) {
    callback(jsonResponse(drogon::k403Forbidden,
                          Json{{"error", Json{{"code", "forbidden"},
                                               {"message", "Experiment request rejected"}}}}));
    return;
  }
  auto parsed = launchRequest(request);
  if (!parsed) {
    callback(failure(parsed.error(), "Invalid experiment launch"));
    return;
  }
  auto started = service_.start(*parsed);
  if (!started) {
    callback(failure(started.error(), "Experiment could not be started"));
    return;
  }
  callback(jsonResponse(drogon::k202Accepted, Json{{"run", runJson(*started)}}));
}

void ExperimentController::gracefulStop(const drogon::HttpRequestPtr& request,
                                        std::string run_id, Callback callback) const {
  if (!security_.authorizeMutation(request)) {
    callback(jsonResponse(drogon::k403Forbidden,
                          Json{{"error", Json{{"code", "forbidden"},
                                               {"message", "Experiment request rejected"}}}}));
    return;
  }
  auto parsed = ExperimentRunId::parse(run_id);
  if (!parsed) {
    callback(failure(parsed.error(), "Invalid experiment run ID"));
    return;
  }
  auto stopped = service_.requestGracefulStop(*parsed);
  if (!stopped) {
    callback(failure(stopped.error(), "Graceful stop could not be requested"));
    return;
  }
  callback(jsonResponse(drogon::k202Accepted,
                        Json{{"runId", stopped->run_id.value},
                             {"status", experimentStatusName(stopped->status)}}));
}

}  // namespace babel
