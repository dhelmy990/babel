#include <catch2/catch_test_macros.hpp>

#include <future>
#include <optional>
#include <string>
#include <vector>

#include <drogon/HttpRequest.h>
#include <nlohmann/json.hpp>

#include "babel/http/experiment_controller.hpp"

namespace {

using namespace babel;
using NJson = nlohmann::json;

ExperimentRunId runId() {
  return ExperimentRunId::parse("22222222-2222-5222-8222-222222222222").value();
}

RecommenderModelId modelId() {
  return RecommenderModelId::parse("11111111-1111-5111-8111-111111111111").value();
}

RecommenderModelDto model() {
  return RecommenderModelDto{
      .model_id = modelId(),
      .label = "Original 2016 baseline",
      .parent_model_id = std::nullopt,
      .producing_run_id = std::nullopt,
      .encoder_repo = "dhelmy990/babel-qwen-navigation-2016-interview",
      .encoder_revision = "57d949cd634b920cc1a46f27c9b21df094b5240e",
      .dataset_repo = "dhelmy990/babel-wikipedia-experiment",
      .dataset_revision = std::string(40, 'b'),
      .environment_sequence = {"2026-06"},
      .training_examples = 100,
      .checkpoint_path = "models/original/checkpoint.safetensors",
      .checkpoint_sha256 = std::string(64, 'c'),
      .embedding_space = EmbeddingSpaceDto{
          .schema_version = 1,
          .embedding_space_id = "66666666-6666-5666-8666-666666666666",
          .dimension = 100,
          .distance = "cosine",
          .distilled_encoder_artifact = "encoder@revision",
          .dataset_revision = std::string(40, 'b'),
          .compatibility_version = "babel-embedding-v1",
      },
      .created_at = "2026-08-26T00:00:00Z",
      .immutable = true,
      .compatible = true,
      .incompatibility_reason = std::nullopt,
  };
}

ExperimentRunStatusDto status() {
  ExperimentRunStatusDto value{
      .run_id = runId(),
      .status = ExperimentStatus::running,
      .retrieval_backend = RetrievalBackend::pgvector,
      .creator_count = 50,
      .environment_sequence = {"2026-06", "2026-07"},
      .starting_model_id = modelId(),
      .active_model_id = modelId(),
      .active_model_version = 4,
      .created_babel_count = 21,
      .feedback_count = 18,
      .event_rate = 3.5,
      .kafka_offset = 42,
      .kafka_lag = 2,
      .trainer_steps = 9,
      .rolling_rank_loss = 0.25,
      .checkpoint_path = "checkpoints/run/9",
      .checkpoint_sha256 = std::string(64, 'd'),
      .serving_synced = true,
      .started_at = "2026-08-26T00:00:00Z",
  };
  return value;
}

class Repository final : public ExperimentRepository {
 public:
  Result<std::vector<RecommenderModelDto>> listModels() override {
    return std::vector<RecommenderModelDto>{model()};
  }
  Result<RecommenderModelDto> getModel(RecommenderModelId) override { return model(); }
  Result<ExperimentRunStatusDto> createRun(const ExperimentLaunchSnapshot& snapshot) override {
    last_snapshot = snapshot;
    auto result = status();
    result.status = ExperimentStatus::starting;
    result.creator_count = snapshot.request.creator_count;
    result.retrieval_backend = snapshot.request.retrieval_backend;
    return result;
  }
  Result<ExperimentRunStatusDto> latestRun() override { return status(); }
  Result<ExperimentRunStatusDto> getRun(ExperimentRunId) override { return status(); }
  Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId) override {
    auto result = status();
    result.status = ExperimentStatus::stop_requested;
    return result;
  }
  Result<void> markLaunchFailed(ExperimentRunId, std::string_view) override { return {}; }
  Result<std::vector<ExperimentActivityDto>> activity(ExperimentRunId id,
                                                       std::uint64_t,
                                                       std::size_t) override {
    return std::vector<ExperimentActivityDto>{ExperimentActivityDto{
        .schema_version = 2,
        .run_id = id,
        .sequence = 7,
        .occurred_at_ns = 99,
        .level = "info",
        .component = "serving",
        .event = "recommendation_completed",
        .message = "Creator created a Babel and evaluated candidates",
        .metrics = {{"eventRate", 3.5}, {"pprScore", 0.9}, {"randomDraw", 0.2}},
        .details = ExperimentRecommendationActivityDto{
            .creator_id = CreatorId::parse(
                "33333333-3333-5333-8333-333333333333").value(),
            .new_babel_id = BabelId::parse(
                "44444444-4444-5444-8444-444444444444").value(),
            .new_babel_title = "Virtual memory",
            .candidate_babel_ids = {BabelId::parse(
                "55555555-5555-5555-8555-555555555555").value()},
            .include_babel_ids = {BabelId::parse(
                "55555555-5555-5555-8555-555555555555").value()},
            .exclude_babel_ids = {},
            .ignore_babel_ids = {},
            .accepted_edge_count = 1,
            .model_id = modelId(),
            .model_version = 4,
            .request_id = "66666666-6666-5666-8666-666666666666",
            .traversal_session_id = "77777777-7777-5777-8777-777777777777",
            .source_vector_origin = "cache_hit",
        },
    }};
  }
  Result<PerformanceExperimentDto> createPerformanceExperiment(
      const PerformanceLaunchRequest& request) override {
    performance.launch = request;
    return performance;
  }
  Result<std::vector<PerformanceExperimentDto>> listPerformanceExperiments(
      std::size_t limit, std::optional<std::string> before) override {
    performance_limit = limit;
    performance_before = std::move(before);
    return std::vector<PerformanceExperimentDto>{performance};
  }
  Result<PerformanceExperimentDto> getPerformanceExperiment(std::string_view) override {
    return performance;
  }
  Result<PerformanceExperimentDto> requestPerformanceGracefulStop(
      std::string_view) override {
    performance.status = PerformanceExperimentStatus::stop_requested;
    return performance;
  }
  Result<PerformanceExperimentDto> approvePerformanceNextScale(
      std::string_view) override {
    performance.status = PerformanceExperimentStatus::approved;
    performance.operator_approved = true;
    return performance;
  }
  Result<void> markPerformanceLaunchFailed(
      std::string_view, std::string_view message) override {
    performance.status = PerformanceExperimentStatus::failed;
    performance.failure = std::string(message);
    return {};
  }
  Result<PerformanceExperimentDto> markPerformancePopulationReady(
      std::string_view, const PerformancePopulationEvidence&) override {
    return performance;
  }
  Result<PerformanceExperimentDto> attachPerformanceArtifact(
      std::string_view, const PerformanceArtifactReceipt& receipt) override {
    attached_receipt = receipt;
    performance.artifact_sha256 = receipt.artifact_sha256;
    performance.remote_hf_commit_sha = receipt.remote_hf_commit_sha;
    performance.remote_hf_bundle_path = receipt.remote_hf_bundle_path;
    return performance;
  }
  Result<void> appendPerformanceProgress(
      std::string_view, const PerformanceProgressDto&) override {
    return {};
  }
  Result<void> savePerformanceResult(
      std::string_view, const PerformanceResultDto&) override {
    return {};
  }
  std::optional<ExperimentLaunchSnapshot> last_snapshot;
  PerformanceExperimentDto performance{
      .experiment_id = "33333333-3333-5333-8333-333333333333",
      .status = PerformanceExperimentStatus::population_pending,
      .launch = PerformanceLaunchRequest{.starting_model_id = modelId()},
      .progress = PerformanceProgressDto{
          .phase = "population", .condition_index = std::nullopt,
          .condition_count = 9, .seeded_articles = 5000, .created_babels = 100,
          .indexed_babels = 90, .requested = 20, .completed = 18,
          .elapsed_seconds = 10, .recent_rate = 10, .draining = false,
          .telemetry_json = R"({"kafkaLag":2,"trainerSteps":9})",
      },
      .results = {PerformanceResultDto{
          .condition_id = "44444444-4444-5444-8444-444444444444",
          .condition_index = 3,
          .topology = PerformanceTopology::same_host_split,
          .training_enabled = true,
          .synchronization_enabled = true,
          .raw_evidence_json = R"({"requestCount":6})",
          .evidence_sha256 = std::string(64, 'a'),
          .serving_p95_ms = 10,
          .training_p95_ms = 15,
          .full_p95_ms = 18,
          .itraining = 1.5,
          .ifull = 1.8,
          .iactivation_increment = 1.2,
      }},
      .created_at = "2026-08-27T00:00:00.000Z",
  };
  std::size_t performance_limit{0};
  std::optional<std::string> performance_before;
  std::optional<PerformanceArtifactReceipt> attached_receipt;
};

class Worker final : public ExperimentWorker {
 public:
  Result<void> start(ExperimentRunId) override { return {}; }
  Result<void> requestGracefulStop(ExperimentRunId) override { return {}; }
};

class PerformanceWorker final : public PerformanceExperimentWorker {
 public:
  Result<void> start(std::string_view) override { return {}; }
  Result<void> requestGracefulStop(std::string_view) override { return {}; }
  Result<void> approveNextScale(std::string_view) override { return {}; }
  Result<void> prepareRerun(
      std::string_view source, const PerformanceRerunRequest& request) override {
    prepared_source = source;
    prepared = request;
    return {};
  }
  std::string prepared_source;
  std::optional<PerformanceRerunRequest> prepared;
};

drogon::HttpRequestPtr request(drogon::HttpMethod method, std::string body = {}) {
  auto value = drogon::HttpRequest::newHttpRequest();
  value->setMethod(method);
  value->addHeader("host", "127.0.0.1:8787");
  value->addHeader("origin", "http://127.0.0.1:8787");
  if (!body.empty()) {
    value->setBody(std::move(body));
    value->setContentTypeCode(drogon::CT_APPLICATION_JSON);
  }
  return value;
}

template <typename Invoke>
drogon::HttpResponsePtr invoke(Invoke call) {
  std::promise<drogon::HttpResponsePtr> promise;
  auto result = promise.get_future();
  call([&](const drogon::HttpResponsePtr& response) { promise.set_value(response); });
  return result.get();
}

NJson body(const drogon::HttpResponsePtr& response) {
  return NJson::parse(std::string{response->body()});
}

}  // namespace

TEST_CASE("experiment start and stop require the existing admin mutation boundary") {
  Repository repository;
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);
  const auto launch = NJson{{"startingModelId", modelId().value},
                           {"retrievalBackend", "pgvector"},
                           {"creatorCount", 50},
                           {"scenario", "june_to_july"},
                           {"eventBudgetPerMonth", 100},
                           {"runSeed", 7}}
                          .dump();

  const auto forbidden = invoke([&](auto callback) {
    controller.start(request(drogon::Post, launch), std::move(callback));
  });
  CHECK(forbidden->getStatusCode() == drogon::k403Forbidden);

  auto authorized = request(drogon::Post, launch);
  authorized->addHeader("x-babel-admin-nonce", "nonce");
  const auto accepted = invoke(
      [&](auto callback) { controller.start(authorized, std::move(callback)); });
  REQUIRE(accepted->getStatusCode() == drogon::k202Accepted);
  CHECK(body(accepted).at("run").at("creatorCount") == 50);
  CHECK(repository.last_snapshot->request.run_seed == 7);

  auto stop = request(drogon::Post);
  stop->addHeader("x-babel-admin-nonce", "nonce");
  const auto stopping = invoke([&](auto callback) {
    controller.gracefulStop(stop, runId().value, std::move(callback));
  });
  REQUIRE(stopping->getStatusCode() == drogon::k202Accepted);
  CHECK(body(stopping).at("status") == "stop_requested");
}

TEST_CASE("experiment read DTOs expose health and typed observable activity only") {
  Repository repository;
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);

  const auto latest = invoke(
      [&](auto callback) { controller.latest(request(drogon::Get), std::move(callback)); });
  REQUIRE(latest->getStatusCode() == drogon::k200OK);
  const auto latest_json = body(latest).at("run");
  CHECK(latest_json.at("kafkaLag") == 2);
  CHECK(latest_json.at("trainerSteps") == 9);
  CHECK(latest_json.at("rollingRankLoss") == 0.25);
  const auto rendered = latest_json.dump();
  CHECK(rendered.find("vectorLoss") == std::string::npos);
  CHECK(rendered.find("relationalLoss") == std::string::npos);
  CHECK(rendered.find("ppr") == std::string::npos);
  CHECK(rendered.find("clickstream") == std::string::npos);

  const auto logs = invoke([&](auto callback) {
    controller.activity(request(drogon::Get), runId().value, std::move(callback));
  });
  REQUIRE(logs->getStatusCode() == drogon::k200OK);
  const auto row = body(logs).at("activity").at(0);
  CHECK(row.at("schemaVersion") == 2);
  CHECK(row.at("details").at("kind") == "recommendation");
  CHECK(row.at("details").at("newBabelTitle") == "Virtual memory");
  CHECK(row.at("details").at("includeBabelIds").size() == 1);
  CHECK(row.at("details").at("acceptedEdgeCount") == 1);
  CHECK(row.at("details").at("requestId") ==
        "66666666-6666-5666-8666-666666666666");
  CHECK(row.at("details").at("traversalSessionId") ==
        "77777777-7777-5777-8777-777777777777");
  CHECK(row.at("details").at("sourceVectorOrigin") == "cache_hit");
  CHECK(row.at("metrics").at("eventRate") == 3.5);
  CHECK(row.at("metrics").find("pprScore") == row.at("metrics").end());
  CHECK(row.at("metrics").find("randomDraw") == row.at("metrics").end());
}

TEST_CASE("experiment launch rejects malformed and unknown operator fields") {
  Repository repository;
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);

  for (const auto& launch : {
           NJson{{"startingModelId", modelId().value}, {"retrievalBackend", 7}},
           NJson{{"startingModelId", modelId().value}, {"hiddenProfile", "no"}},
           NJson{{"startingModelId", modelId().value}, {"runSeed", -1}},
       }) {
    auto malformed = request(drogon::Post, launch.dump());
    malformed->addHeader("x-babel-admin-nonce", "nonce");
    const auto rejected = invoke(
        [&](auto callback) { controller.start(malformed, std::move(callback)); });
    CHECK(rejected->getStatusCode() == drogon::k400BadRequest);
  }
  CHECK_FALSE(repository.last_snapshot.has_value());
}

TEST_CASE("saved performance list and progress detail are read only") {
  Repository repository;
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);

  auto list_request = request(drogon::Get);
  list_request->setParameter("limit", "10");
  list_request->setParameter("before", "2026-08-28T00:00:00.000Z");
  const auto listed = invoke([&](auto callback) {
    controller.performanceList(list_request, std::move(callback));
  });
  REQUIRE(listed->getStatusCode() == drogon::k200OK);
  CHECK(repository.performance_limit == 10);
  CHECK(repository.performance_before == "2026-08-28T00:00:00.000Z");
  CHECK(body(listed).at("trials").size() == 1);

  const auto detail = invoke([&](auto callback) {
    controller.performance(request(drogon::Get), repository.performance.experiment_id,
                           std::move(callback));
  });
  REQUIRE(detail->getStatusCode() == drogon::k200OK);
  const auto trial = body(detail).at("trial");
  CHECK(trial.at("topology") == "same_host_split");
  CHECK(trial.at("progress").at("conditionCount") == 9);
  CHECK(trial.at("progress").at("telemetry").at("kafkaLag") == 2);
  REQUIRE(trial.at("results").size() == 1);
  CHECK(trial.at("results").at(0).at("Itraining") == 1.5);
  CHECK(trial.at("results").at(0).at("Ifull") == 1.8);
  CHECK(trial.at("results").at(0).at("IActivationIncrement") == 1.2);
  CHECK(trial.at("results").at(0).at("evidenceSha256") == std::string(64, 'a'));
  CHECK(trial.contains("failure"));
  CHECK(trial.at("failure").is_null());
}

TEST_CASE("performance mutations require nonce and approval waits for population evidence") {
  Repository repository;
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);
  const auto launch = NJson{{"startingModelId", modelId().value}}.dump();

  const auto forbidden = invoke([&](auto callback) {
    controller.createPerformance(request(drogon::Post, launch), std::move(callback));
  });
  CHECK(forbidden->getStatusCode() == drogon::k403Forbidden);

  auto authorized = request(drogon::Post, launch);
  authorized->addHeader("x-babel-admin-nonce", "nonce");
  const auto created = invoke([&](auto callback) {
    controller.createPerformance(authorized, std::move(callback));
  });
  REQUIRE(created->getStatusCode() == drogon::k201Created);
  CHECK(body(created).at("trial").at("datasetRevision") ==
        "0d1ab2c7f0e2295682288fcf10077d2d776bf559");
  CHECK_FALSE(body(created).at("trial").at("autoAdvance").get<bool>());

  auto approve = request(drogon::Post);
  approve->addHeader("x-babel-admin-nonce", "nonce");
  const auto blocked = invoke([&](auto callback) {
    controller.approveNextScale(approve, repository.performance.experiment_id,
                                std::move(callback));
  });
  CHECK(blocked->getStatusCode() == drogon::k409Conflict);
}

TEST_CASE("completed performance trial attaches one verified remote artifact receipt") {
  Repository repository;
  repository.performance.results = std::vector<PerformanceResultDto>(9);
  Worker worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')});
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);
  const auto receipt = NJson{
      {"artifactSha256", std::string(64, 'a')},
      {"remoteHfCommitSha", std::string(40, 'b')},
      {"remoteHfBundlePath", "runs/33333333-3333-5333-8333-333333333333"},
  };

  const auto forbidden = invoke([&](auto callback) {
    controller.attachPerformanceArtifact(
        request(drogon::Post, receipt.dump()), repository.performance.experiment_id,
        std::move(callback));
  });
  CHECK(forbidden->getStatusCode() == drogon::k403Forbidden);
  CHECK_FALSE(repository.attached_receipt.has_value());

  auto authorized = request(drogon::Post, receipt.dump());
  authorized->addHeader("x-babel-admin-nonce", "nonce");
  const auto premature = invoke([&](auto callback) {
    controller.attachPerformanceArtifact(
        authorized, repository.performance.experiment_id, std::move(callback));
  });
  CHECK(premature->getStatusCode() == drogon::k409Conflict);
  CHECK_FALSE(repository.attached_receipt.has_value());

  repository.performance.status = PerformanceExperimentStatus::completed;
  auto wrong_trial_receipt = receipt;
  wrong_trial_receipt["remoteHfBundlePath"] =
      "runs/99999999-9999-5999-8999-999999999999";
  auto wrong_trial_request = request(drogon::Post, wrong_trial_receipt.dump());
  wrong_trial_request->addHeader("x-babel-admin-nonce", "nonce");
  const auto wrong_trial = invoke([&](auto callback) {
    controller.attachPerformanceArtifact(
        wrong_trial_request, repository.performance.experiment_id,
        std::move(callback));
  });
  CHECK(wrong_trial->getStatusCode() == drogon::k400BadRequest);
  CHECK_FALSE(repository.attached_receipt.has_value());

  const auto attached = invoke([&](auto callback) {
    controller.attachPerformanceArtifact(
        authorized, repository.performance.experiment_id, std::move(callback));
  });
  REQUIRE(attached->getStatusCode() == drogon::k200OK);
  REQUIRE(repository.attached_receipt.has_value());
  CHECK(repository.attached_receipt->remote_hf_bundle_path ==
        "runs/33333333-3333-5333-8333-333333333333");
  CHECK(body(attached).at("trial").at("remoteHfCommitSha") == std::string(40, 'b'));
}

TEST_CASE("dashboard prepares one unapproved representative rerun through worker") {
  Repository repository;
  repository.performance.population_ready = true;
  repository.performance.run_id = ExperimentRunId::parse(
      "55555555-5555-5555-8555-555555555555").value();
  repository.performance.population_manifest_sha256 = std::string(64, 'a');
  repository.performance.population_bundle_path = "/verified/population";
  Worker worker;
  PerformanceWorker performance_worker;
  ExperimentService service(repository, worker,
                            ExperimentSourcePin{"repo", "config", std::string(40, 'e')},
                            &performance_worker);
  AdminSecurity security("nonce");
  ExperimentController controller(security, service);
  auto prepare = request(
      drogon::Post,
      NJson{{"rerunId", "44444444-4444-5444-8444-444444444444"},
            {"matrix", "2x3"},
            {"warmupSeconds", 5},
            {"durationSeconds", 25},
            {"targetRps", 5.0}}
          .dump());
  prepare->addHeader("x-babel-admin-nonce", "nonce");

  const auto response = invoke([&](auto callback) {
    controller.preparePerformanceRerun(
        prepare, repository.performance.experiment_id, std::move(callback));
  });

  REQUIRE(response->getStatusCode() == drogon::k201Created);
  REQUIRE(performance_worker.prepared.has_value());
  CHECK(performance_worker.prepared_source == repository.performance.experiment_id);
  CHECK(performance_worker.prepared->matrix == "2x3");
  CHECK(body(response).at("trial").at("operatorApproved") == false);
}
