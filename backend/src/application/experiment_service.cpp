#include "babel/application/experiment_service.hpp"

#include <limits>
#include <algorithm>
#include <string>
#include <utility>

namespace babel {
namespace {

std::vector<std::string> environmentSequence(ExperimentScenario scenario) {
  if (scenario == ExperimentScenario::june_only) return {"2026-06"};
  return {"2026-06", "2026-07"};
}

bool validRevision(std::string_view value) {
  return value.size() >= 40 && value.size() <= 64 &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return (character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f');
         });
}

Result<ExperimentLaunchSnapshot> snapshot(const ExperimentLaunchRequest& request,
                                          const ExperimentSourcePin& source) {
  if (request.creator_count == 0 || request.creator_count > 10000) {
    return invalidArgument("creatorCount must be between 1 and 10000");
  }
  if (request.event_budget_per_month == 0 || request.event_budget_per_month > 1000000) {
    return invalidArgument("eventBudgetPerMonth must be between 1 and 1000000");
  }
  if (request.run_seed > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
    return invalidArgument("runSeed must fit a signed 64-bit integer");
  }
  if (source.repository.empty() || source.configuration.empty() ||
      !validRevision(source.commit_sha)) {
    return invalidArgument("online dataset pin is incomplete");
  }
  if (request.recommendation_k == 0 || request.recommendation_k > 100 ||
      request.top_l == 0 || request.checkpoint_every_events == 0 ||
      request.sync_every_steps == 0 || request.kafka_topic.empty() ||
      request.kafka_group.empty() || request.artifact_root.empty() ||
      request.state_root.empty()) {
    return invalidArgument("online runtime settings are invalid");
  }

  auto environments = environmentSequence(request.scenario);
  return ExperimentLaunchSnapshot{
      .request = request,
      .source = source,
      .environment_sequence = std::move(environments),
  };
}

}  // namespace

std::string_view retrievalBackendName(RetrievalBackend backend) noexcept {
  return backend == RetrievalBackend::pgvector ? "pgvector" : "hnswlib";
}

std::string_view experimentScenarioName(ExperimentScenario scenario) noexcept {
  return scenario == ExperimentScenario::june_only ? "june_only" : "june_to_july";
}

std::string_view experimentStatusName(ExperimentStatus status) noexcept {
  switch (status) {
    case ExperimentStatus::starting:
      return "starting";
    case ExperimentStatus::running:
      return "running";
    case ExperimentStatus::stop_requested:
      return "stop_requested";
    case ExperimentStatus::draining_feedback:
      return "draining_feedback";
    case ExperimentStatus::checkpointing:
      return "checkpointing";
    case ExperimentStatus::exporting_interactions:
      return "exporting_interactions";
    case ExperimentStatus::completed:
      return "completed";
    case ExperimentStatus::failed:
      return "failed";
    case ExperimentStatus::interrupted:
      return "interrupted";
  }
  return "failed";
}

bool isTerminal(ExperimentStatus status) noexcept {
  return status == ExperimentStatus::completed || status == ExperimentStatus::failed ||
         status == ExperimentStatus::interrupted;
}

ExperimentService::ExperimentService(ExperimentRepository& repository,
                                     ExperimentWorker& worker,
                                     ExperimentSourcePin source)
    : repository_(repository), worker_(worker), source_(std::move(source)) {}

Result<std::vector<RecommenderModelDto>> ExperimentService::listModels() {
  return repository_.listModels();
}

Result<ExperimentRunStatusDto> ExperimentService::latestRun() {
  return repository_.latestRun();
}

Result<ExperimentRunStatusDto> ExperimentService::getRun(ExperimentRunId run_id) {
  return repository_.getRun(std::move(run_id));
}

Result<std::vector<ExperimentActivityDto>> ExperimentService::activity(
    ExperimentRunId run_id, std::uint64_t after_sequence, std::size_t limit) {
  if (limit == 0 || limit > 200) return invalidArgument("limit must be between 1 and 200");
  return repository_.activity(std::move(run_id), after_sequence, limit);
}

Result<ExperimentRunStatusDto> ExperimentService::start(
    const ExperimentLaunchRequest& request) {
  auto model = repository_.getModel(request.starting_model_id);
  if (!model) return tl::make_unexpected(model.error());
  if (!model->immutable || !model->compatible) {
    return invalidArgument(model->incompatibility_reason.value_or(
        "starting model is not compatible with this experiment"));
  }

  auto frozen = snapshot(request, source_);
  if (!frozen) return tl::make_unexpected(frozen.error());
  auto created = repository_.createRun(*frozen);
  if (!created) return tl::make_unexpected(created.error());

  auto launched = worker_.start(created->run_id);
  if (!launched) {
    const auto recorded = repository_.markLaunchFailed(created->run_id, launched.error().message);
    if (!recorded) return tl::make_unexpected(recorded.error());
    return tl::make_unexpected(launched.error());
  }
  return created;
}

Result<ExperimentRunStatusDto> ExperimentService::requestGracefulStop(
    ExperimentRunId run_id) {
  auto stopped = repository_.requestGracefulStop(run_id);
  if (!stopped) return tl::make_unexpected(stopped.error());
  auto requested = worker_.requestGracefulStop(run_id);
  if (!requested) return tl::make_unexpected(requested.error());
  return stopped;
}

}  // namespace babel
