#include <catch2/catch_test_macros.hpp>

#include <optional>
#include <utility>
#include <vector>

#include "babel/application/experiment_service.hpp"

namespace {

using namespace babel;

RecommenderModelDto originalModel() {
  return RecommenderModelDto{
      .model_id = RecommenderModelId::parse("11111111-1111-5111-8111-111111111111").value(),
      .label = "Original 2016 baseline",
      .parent_model_id = std::nullopt,
      .producing_run_id = std::nullopt,
      .encoder_repo = "dhelmy990/babel-qwen-navigation-2016-interview",
      .encoder_revision = "57d949cd634b920cc1a46f27c9b21df094b5240e",
      .dataset_repo = "dhelmy990/babel-wikipedia-experiment",
      .dataset_revision = std::string(40, 'b'),
      .environment_sequence = {"2026-06"},
      .training_examples = 1000,
      .checkpoint_path = "models/original/checkpoint.safetensors",
      .checkpoint_sha256 = std::string(64, 'e'),
      .embedding_space = EmbeddingSpaceDto{
          .schema_version = 1,
          .embedding_space_id = "66666666-6666-5666-8666-666666666666",
          .dimension = 100,
          .distance = "cosine",
          .distilled_encoder_artifact = "dhelmy990/babel-two-tower-recommender@" +
                                        std::string(40, 'a'),
          .dataset_revision = std::string(40, 'b'),
          .compatibility_version = "babel-embedding-v1",
      },
      .created_at = "2026-08-26T00:00:00Z",
      .immutable = true,
      .compatible = true,
      .incompatibility_reason = std::nullopt,
  };
}

ExperimentLaunchRequest validLaunch() {
  return ExperimentLaunchRequest{
      .starting_model_id = originalModel().model_id,
      .retrieval_backend = RetrievalBackend::pgvector,
      .creator_count = 50,
      .scenario = ExperimentScenario::june_to_july,
      .event_budget_per_month = 100,
      .run_seed = 7,
  };
}

class FakeRepository final : public ExperimentRepository {
 public:
  Result<std::vector<RecommenderModelDto>> listModels() override {
    return std::vector<RecommenderModelDto>{model};
  }

  Result<RecommenderModelDto> getModel(RecommenderModelId id) override {
    if (id != model.model_id) {
      return tl::make_unexpected(ApplicationError{ErrorCode::not_found, "model missing"});
    }
    return model;
  }

  Result<ExperimentRunStatusDto> createRun(const ExperimentLaunchSnapshot& snapshot) override {
    ++create_calls;
    created = snapshot;
    if (create_error) return tl::make_unexpected(*create_error);
    return ExperimentRunStatusDto{
        .run_id = run_id,
        .status = ExperimentStatus::starting,
        .retrieval_backend = snapshot.request.retrieval_backend,
        .creator_count = snapshot.request.creator_count,
        .environment_sequence = snapshot.environment_sequence,
        .starting_model_id = snapshot.request.starting_model_id,
        .active_model_id = snapshot.request.starting_model_id,
    };
  }

  Result<ExperimentRunStatusDto> latestRun() override { return status; }
  Result<ExperimentRunStatusDto> getRun(ExperimentRunId) override { return status; }

  Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId) override {
    status.status = ExperimentStatus::stop_requested;
    return status;
  }

  Result<void> markLaunchFailed(ExperimentRunId, std::string_view message) override {
    ++failed_calls;
    failure = message;
    return {};
  }

  Result<std::vector<ExperimentActivityDto>> activity(ExperimentRunId,
                                                       std::uint64_t,
                                                       std::size_t) override {
    return std::vector<ExperimentActivityDto>{};
  }

  Result<PerformanceExperimentDto> createPerformanceExperiment(
      const PerformanceLaunchRequest& request) override {
    events.push_back("persist-create");
    performance_created = request;
    return performance;
  }
  Result<std::vector<PerformanceExperimentDto>> listPerformanceExperiments(
      std::size_t, std::optional<std::string>) override {
    return std::vector<PerformanceExperimentDto>{performance};
  }
  Result<PerformanceExperimentDto> getPerformanceExperiment(std::string_view) override {
    return performance;
  }
  Result<PerformanceExperimentDto> requestPerformanceGracefulStop(
      std::string_view) override {
    events.push_back("persist-stop");
    performance.status = PerformanceExperimentStatus::stop_requested;
    return performance;
  }
  Result<PerformanceExperimentDto> approvePerformanceNextScale(
      std::string_view) override {
    events.push_back("persist-approval");
    ++approve_calls;
    if (!performance.population_ready) {
      return tl::make_unexpected(ApplicationError{ErrorCode::conflict,
                                                  "population is not ready"});
    }
    performance.operator_approved = true;
    performance.status = PerformanceExperimentStatus::approved;
    return performance;
  }
  Result<void> markPerformanceLaunchFailed(std::string_view,
                                           std::string_view message) override {
    events.push_back("persist-failed");
    if (performance_failure_error) {
      return tl::make_unexpected(*performance_failure_error);
    }
    performance.status = PerformanceExperimentStatus::failed;
    performance.failure = std::string(message);
    return {};
  }
  Result<PerformanceExperimentDto> markPerformancePopulationReady(
      std::string_view, const PerformancePopulationEvidence& evidence) override {
    performance.population_ready = true;
    performance.population_vector_count = evidence.vector_count;
    performance.population_vector_sha256 = evidence.vector_sha256;
    performance.population_model_repository = evidence.model_repository;
    performance.population_model_revision = evidence.model_revision;
    performance.population_model_sha256 = evidence.model_sha256;
    performance.population_dataset_repository = evidence.dataset_repository;
    performance.population_dataset_revision = evidence.dataset_revision;
    performance.population_dataset_sha256 = evidence.dataset_sha256;
    return performance;
  }
  Result<PerformanceExperimentDto> attachPerformanceArtifact(
      std::string_view, const PerformanceArtifactReceipt& receipt) override {
    performance.artifact_sha256 = receipt.artifact_sha256;
    performance.remote_hf_commit_sha = receipt.remote_hf_commit_sha;
    performance.remote_hf_bundle_path = receipt.remote_hf_bundle_path;
    return performance;
  }
  Result<void> appendPerformanceProgress(
      std::string_view, const PerformanceProgressDto& progress) override {
    performance.progress = progress;
    return {};
  }
  Result<void> savePerformanceResult(
      std::string_view, const PerformanceResultDto& result) override {
    performance.results.push_back(result);
    return {};
  }

  RecommenderModelDto model{originalModel()};
  ExperimentRunId run_id{
      ExperimentRunId::parse("22222222-2222-5222-8222-222222222222").value()};
  ExperimentRunStatusDto status{
      .run_id = run_id,
      .status = ExperimentStatus::running,
      .retrieval_backend = RetrievalBackend::pgvector,
      .creator_count = 50,
      .environment_sequence = {"2026-06", "2026-07"},
      .starting_model_id = model.model_id,
      .active_model_id = model.model_id,
  };
  std::optional<ExperimentLaunchSnapshot> created;
  std::optional<ApplicationError> create_error;
  std::optional<ApplicationError> performance_failure_error;
  std::string failure;
  int create_calls{0};
  int failed_calls{0};
  PerformanceExperimentDto performance{
      .experiment_id = "33333333-3333-5333-8333-333333333333",
      .status = PerformanceExperimentStatus::population_pending,
      .launch = PerformanceLaunchRequest{.starting_model_id = model.model_id},
      .results = {},
      .created_at = "",
  };
  std::optional<PerformanceLaunchRequest> performance_created;
  int approve_calls{0};
  std::vector<std::string> events;
};

class FakeWorker final : public ExperimentWorker {
 public:
  Result<void> start(ExperimentRunId run_id) override {
    ++start_calls;
    started_run = run_id;
    if (start_error) return tl::make_unexpected(*start_error);
    return {};
  }

  Result<void> requestGracefulStop(ExperimentRunId run_id) override {
    ++stop_calls;
    stopped_run = run_id;
    if (stop_error) return tl::make_unexpected(*stop_error);
    return {};
  }

  std::optional<ApplicationError> start_error;
  std::optional<ApplicationError> stop_error;
  std::optional<ExperimentRunId> started_run;
  std::optional<ExperimentRunId> stopped_run;
  int start_calls{0};
  int stop_calls{0};
};

class FakePerformanceWorker final : public PerformanceExperimentWorker {
 public:
  explicit FakePerformanceWorker(std::vector<std::string>* events = nullptr)
      : events(events) {}

  Result<void> start(std::string_view experiment_id) override {
    ++start_calls;
    started = experiment_id;
    if (events) events->push_back("command-start");
    if (start_error) return tl::make_unexpected(*start_error);
    return {};
  }
  Result<void> requestGracefulStop(std::string_view experiment_id) override {
    ++stop_calls;
    stopped = experiment_id;
    if (events) events->push_back("command-stop");
    if (stop_failures_remaining > 0) {
      --stop_failures_remaining;
      return tl::make_unexpected(ApplicationError{
          ErrorCode::database_unavailable, "performance stop dispatch failed"});
    }
    return {};
  }
  Result<void> approveNextScale(std::string_view experiment_id) override {
    ++approval_calls;
    approved = experiment_id;
    if (events) events->push_back("command-approval");
    return {};
  }
  Result<void> prepareRerun(
      std::string_view experiment_id,
      const PerformanceRerunRequest& request) override {
    prepared_source = experiment_id;
    prepared_request = request;
    return {};
  }

  std::vector<std::string>* events;
  std::optional<ApplicationError> start_error;
  std::string started;
  std::string stopped;
  std::string approved;
  std::string prepared_source;
  std::optional<PerformanceRerunRequest> prepared_request;
  int start_calls{0};
  int stop_calls{0};
  int stop_failures_remaining{0};
  int approval_calls{0};
};

ExperimentSourcePin sourcePin() {
  return ExperimentSourcePin{
      .repository = "dhelmy990/babel-wikipedia-experiment",
      .configuration = "demo_catalog_2026_06",
      .commit_sha = std::string(40, 'c'),
  };
}

}  // namespace

TEST_CASE("experiment start snapshots defaults before launching exactly one worker") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());

  auto started = service.start(validLaunch());

  REQUIRE(started.has_value());
  REQUIRE(repository.created.has_value());
  CHECK(repository.created->request.creator_count == 50);
  CHECK(repository.created->request.retrieval_backend == RetrievalBackend::pgvector);
  CHECK(repository.created->environment_sequence ==
        std::vector<std::string>{"2026-06", "2026-07"});
  CHECK(repository.create_calls == 1);
  CHECK(worker.start_calls == 1);
  CHECK(worker.started_run == repository.run_id);
}

TEST_CASE("experiment start rejects incompatible models before persistence") {
  FakeRepository repository;
  repository.model.compatible = false;
  repository.model.incompatibility_reason = "embedding space mismatch";
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());

  const auto started = service.start(validLaunch());

  REQUIRE_FALSE(started.has_value());
  CHECK(started.error().code == ErrorCode::invalid_argument);
  CHECK(repository.create_calls == 0);
  CHECK(worker.start_calls == 0);
}

TEST_CASE("experiment start rejects an unregistered original before persistence") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());
  auto launch = validLaunch();
  launch.starting_model_id = RecommenderModelId::parse(
      "99999999-9999-5999-8999-999999999999").value();

  const auto started = service.start(launch);

  REQUIRE_FALSE(started.has_value());
  CHECK(started.error().code == ErrorCode::not_found);
  CHECK(repository.create_calls == 0);
  CHECK(worker.start_calls == 0);
}

TEST_CASE("experiment launch failure becomes durable after persist-before-start") {
  FakeRepository repository;
  FakeWorker worker;
  worker.start_error = ApplicationError{ErrorCode::database_unavailable,
                                        "supervisor unavailable"};
  ExperimentService service(repository, worker, sourcePin());

  const auto started = service.start(validLaunch());

  REQUIRE_FALSE(started.has_value());
  CHECK(repository.create_calls == 1);
  CHECK(worker.start_calls == 1);
  CHECK(repository.failed_calls == 1);
  CHECK(repository.failure == "supervisor unavailable");
}

TEST_CASE("graceful stop persists intent and never exposes a kill operation") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());

  const auto stopped = service.requestGracefulStop(repository.run_id);

  REQUIRE(stopped.has_value());
  CHECK(stopped->status == ExperimentStatus::stop_requested);
  CHECK(worker.stop_calls == 1);
  CHECK(worker.stopped_run == repository.run_id);
}

TEST_CASE("performance trial defaults freeze split real model and scale controls") {
  FakeRepository repository;
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);
  PerformanceLaunchRequest request{.starting_model_id = repository.model.model_id};

  const auto created = service.createPerformanceExperiment(request);

  REQUIRE(created.has_value());
  REQUIRE(repository.performance_created.has_value());
  CHECK(repository.performance_created->topology == PerformanceTopology::same_host_split);
  CHECK(repository.performance_created->dataset_revision ==
        "0d1ab2c7f0e2295682288fcf10077d2d776bf559");
  CHECK(repository.performance_created->model_revision ==
        "57d949cd634b920cc1a46f27c9b21df094b5240e");
  CHECK(repository.performance_created->retrieval_backend == RetrievalBackend::pgvector);
  CHECK(repository.performance_created->creator_count == 50);
  CHECK(repository.performance_created->seeded_articles == 10000);
  CHECK(repository.performance_created->target_created_babels == 10000);
  CHECK(repository.performance_created->concurrent_users == 50);
  CHECK(repository.performance_created->recommendation_start_probability == 0.40);
  CHECK(repository.performance_created->continuation_probability == 0.40);
  CHECK(repository.performance_created->maximum_traversal_depth == 2);
  CHECK(repository.performance_created->maximum_requests_per_traversal == 10);
  CHECK(repository.performance_created->training_micro_batch_size == 8);
  CHECK(repository.performance_created->sync_every_steps == 10);
  CHECK(repository.performance_created->interleave_creation_and_recommendations);
  CHECK_FALSE(repository.performance_created->auto_advance);
  CHECK(performance_worker.start_calls == 1);
  CHECK(performance_worker.started == created->experiment_id);
  CHECK(repository.events == std::vector<std::string>{"persist-create", "command-start"});
}

TEST_CASE("performance higher cohorts keep ten thousand rows and explicit split approval") {
  FakeRepository repository;
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);
  auto request = repository.performance.launch;
  request.creator_count = 100;
  request.concurrent_users = 100;
  request.seeded_articles = 10000;
  request.target_created_babels = 10000;
  request.topology = PerformanceTopology::same_host_split;

  const auto created = service.createPerformanceExperiment(request);

  REQUIRE(created.has_value());
  REQUIRE(repository.performance_created.has_value());
  CHECK(repository.performance_created->creator_count == 100);
  CHECK(repository.performance_created->concurrent_users == 100);
  CHECK_FALSE(repository.performance_created->auto_advance);
  CHECK(performance_worker.start_calls == 1);

  request.creator_count = 101;
  request.concurrent_users = 101;
  const auto unsupported = service.createPerformanceExperiment(request);
  REQUIRE_FALSE(unsupported.has_value());
  CHECK(unsupported.error().message.find("50, 100, or 500") != std::string::npos);

  request.creator_count = 100;
  request.concurrent_users = 100;
  repository.model.parent_model_id = originalModel().model_id;
  const auto child = service.createPerformanceExperiment(request);
  REQUIRE_FALSE(child.has_value());
  CHECK(child.error().message.find("immutable original") != std::string::npos);
}

TEST_CASE("completed higher cohort accepts its six saved results for attachment") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());
  repository.performance.launch.creator_count = 100;
  repository.performance.status = PerformanceExperimentStatus::completed;
  repository.performance.results = std::vector<PerformanceResultDto>(6);

  const auto attached = service.attachPerformanceArtifact(
      repository.performance.experiment_id,
      PerformanceArtifactReceipt{
          .artifact_sha256 = std::string(64, 'a'),
          .remote_hf_commit_sha = std::string(40, 'b'),
          .remote_hf_bundle_path =
              "runs/" + repository.performance.experiment_id,
      });

  REQUIRE(attached.has_value());
  CHECK(attached->artifact_sha256 == std::string(64, 'a'));
}

TEST_CASE("performance launch failure is durable and returns the dispatch error") {
  FakeRepository repository;
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  performance_worker.start_error = ApplicationError{
      ErrorCode::database_unavailable, "performance worker unavailable"};
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);

  const auto created = service.createPerformanceExperiment(
      PerformanceLaunchRequest{.starting_model_id = repository.model.model_id});

  REQUIRE_FALSE(created.has_value());
  CHECK(created.error().message == "performance worker unavailable");
  CHECK(repository.performance.failure == "performance worker unavailable");
  CHECK(repository.events == std::vector<std::string>{
                                 "persist-create", "command-start", "persist-failed"});
}

TEST_CASE("performance launch returns failure persistence error when durability fails") {
  FakeRepository repository;
  repository.performance_failure_error = ApplicationError{
      ErrorCode::internal, "could not persist performance failure"};
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  performance_worker.start_error = ApplicationError{
      ErrorCode::database_unavailable, "performance worker unavailable"};
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);

  const auto created = service.createPerformanceExperiment(
      PerformanceLaunchRequest{.starting_model_id = repository.model.model_id});

  REQUIRE_FALSE(created.has_value());
  CHECK(created.error().code == ErrorCode::internal);
  CHECK(created.error().message == "could not persist performance failure");
}

TEST_CASE("performance population stop redispatches the same durable request after failure") {
  FakeRepository repository;
  repository.performance.status = PerformanceExperimentStatus::population_pending;
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  performance_worker.stop_failures_remaining = 1;
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);

  const auto first = service.requestPerformanceGracefulStop(
      repository.performance.experiment_id);
  const auto retried = service.requestPerformanceGracefulStop(
      repository.performance.experiment_id);

  REQUIRE_FALSE(first.has_value());
  REQUIRE(retried.has_value());
  CHECK(retried->status == PerformanceExperimentStatus::stop_requested);
  CHECK(performance_worker.stop_calls == 2);
}

TEST_CASE("performance stop and approval signal only after durable mutation") {
  FakeRepository repository;
  repository.performance.population_ready = true;
  repository.performance.population_vector_count = 10000;
  repository.performance.population_vector_sha256 = std::string(64, 'a');
  repository.performance.population_model_repository =
      repository.performance.launch.model_repository;
  repository.performance.population_model_revision =
      repository.performance.launch.model_revision;
  repository.performance.population_model_sha256 = std::string(64, 'b');
  repository.performance.population_dataset_repository =
      repository.performance.launch.dataset_repository;
  repository.performance.population_dataset_revision =
      repository.performance.launch.dataset_revision;
  repository.performance.population_dataset_sha256 = std::string(64, 'c');
  FakeWorker worker;
  FakePerformanceWorker performance_worker(&repository.events);
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);

  REQUIRE(service.requestPerformanceGracefulStop(
              repository.performance.experiment_id).has_value());
  repository.performance.status = PerformanceExperimentStatus::population_ready;
  REQUIRE(service.approvePerformanceNextScale(
              repository.performance.experiment_id).has_value());
  REQUIRE(service.approvePerformanceNextScale(
              repository.performance.experiment_id).has_value());

  CHECK(repository.events == std::vector<std::string>{
                                 "persist-stop", "command-stop",
                                 "persist-approval", "command-approval",
                                 "persist-approval", "command-approval"});
  CHECK(performance_worker.stop_calls == 1);
  CHECK(performance_worker.approval_calls == 2);
  CHECK(performance_worker.approved == repository.performance.experiment_id);
}

TEST_CASE("formal performance approval is blocked until exact population evidence exists") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());

  const auto blocked = service.approvePerformanceNextScale(
      repository.performance.experiment_id);
  REQUIRE_FALSE(blocked.has_value());
  CHECK(blocked.error().code == ErrorCode::conflict);

  repository.performance.population_ready = true;
  repository.performance.population_vector_count = 10000;
  repository.performance.population_vector_sha256 = std::string(64, 'a');
  repository.performance.population_model_repository =
      repository.performance.launch.model_repository;
  repository.performance.population_model_revision =
      repository.performance.launch.model_revision;
  repository.performance.population_model_sha256 = std::string(64, 'b');
  repository.performance.population_dataset_repository =
      repository.performance.launch.dataset_repository;
  repository.performance.population_dataset_revision =
      repository.performance.launch.dataset_revision;
  repository.performance.population_dataset_sha256 = std::string(64, 'c');
  const auto approved = service.approvePerformanceNextScale(
      repository.performance.experiment_id);
  REQUIRE(approved.has_value());
  CHECK(approved->operator_approved);
}

TEST_CASE("representative rerun preparation delegates to worker and remains unapproved") {
  FakeRepository repository;
  repository.performance.population_ready = true;
  repository.performance.run_id = ExperimentRunId::parse(
      "55555555-5555-5555-8555-555555555555").value();
  repository.performance.population_manifest_sha256 = std::string(64, 'a');
  repository.performance.population_bundle_path = "/verified/population";
  FakeWorker worker;
  FakePerformanceWorker performance_worker;
  ExperimentService service(repository, worker, sourcePin(), &performance_worker);
  const PerformanceRerunRequest request{
      .rerun_id = "44444444-4444-5444-8444-444444444444"};

  const auto prepared = service.preparePerformanceRerun(
      repository.performance.experiment_id, request);

  REQUIRE(prepared.has_value());
  CHECK(performance_worker.prepared_source == repository.performance.experiment_id);
  REQUIRE(performance_worker.prepared_request.has_value());
  CHECK(performance_worker.prepared_request->matrix == "2x3");
  CHECK(prepared->operator_approved == false);
}

TEST_CASE("population ready receipt must match frozen model dataset and vector target") {
  FakeRepository repository;
  FakeWorker worker;
  ExperimentService service(repository, worker, sourcePin());
  PerformancePopulationEvidence evidence{
      .vector_count = 10000,
      .vector_sha256 = std::string(64, 'a'),
      .model_repository = repository.performance.launch.model_repository,
      .model_revision = repository.performance.launch.model_revision,
      .model_sha256 = std::string(64, 'b'),
      .dataset_repository = repository.performance.launch.dataset_repository,
      .dataset_revision = repository.performance.launch.dataset_revision,
      .dataset_sha256 = std::string(64, 'c'),
  };
  auto wrong = evidence;
  wrong.dataset_revision = std::string(40, 'd');
  const auto rejected = service.markPerformancePopulationReady(
      repository.performance.experiment_id, wrong);
  REQUIRE_FALSE(rejected.has_value());
  CHECK(rejected.error().code == ErrorCode::invalid_argument);

  const auto accepted = service.markPerformancePopulationReady(
      repository.performance.experiment_id, evidence);
  REQUIRE(accepted.has_value());
  CHECK(accepted->population_ready);
}
