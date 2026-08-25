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
      .encoder_repo = "dhelmy990/babel-two-tower-recommender",
      .encoder_revision = std::string(40, 'a'),
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
  std::string failure;
  int create_calls{0};
  int failed_calls{0};
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
