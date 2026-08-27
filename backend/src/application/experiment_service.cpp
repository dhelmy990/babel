#include "babel/application/experiment_service.hpp"

#include <limits>
#include <algorithm>
#include <cmath>
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

bool validDigest(const std::optional<std::string>& value) {
  return value && value->size() == 64 &&
         std::all_of(value->begin(), value->end(), [](unsigned char character) {
           return (character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f');
         });
}

Result<void> validatePerformanceLaunch(const PerformanceLaunchRequest& request) {
  if (request.creator_count == 0 || request.creator_count > 10000) {
    return invalidArgument("creatorCount must be between 1 and 10000");
  }
  if (request.seeded_articles == 0 || request.seeded_articles > 1000000 ||
      request.target_created_babels == 0 || request.target_created_babels > 1000000) {
    return invalidArgument("population counts must be between 1 and 1000000");
  }
  if (request.concurrent_users == 0 || request.concurrent_users > request.creator_count) {
    return invalidArgument("concurrentUsers must be between 1 and creatorCount");
  }
  if (!std::isfinite(request.recommendation_start_probability) ||
      request.recommendation_start_probability < 0 ||
      request.recommendation_start_probability > 1 ||
      !std::isfinite(request.continuation_probability) ||
      request.continuation_probability < 0 || request.continuation_probability > 1) {
    return invalidArgument("walk probabilities must be between 0 and 1");
  }
  if (request.maximum_traversal_depth != 2 ||
      request.maximum_requests_per_traversal == 0 ||
      request.maximum_requests_per_traversal > 10) {
    return invalidArgument("walk must use depth 2 and at most 10 requests");
  }
  if (request.training_micro_batch_size == 0 || request.training_micro_batch_size > 1024 ||
      request.sync_every_steps == 0 || request.sync_every_steps > 1000000) {
    return invalidArgument("training and synchronization settings are invalid");
  }
  if (request.auto_advance) {
    return invalidArgument("autoAdvance must remain false");
  }
  if (!validRevision(request.model_revision) || !validRevision(request.dataset_revision) ||
      request.model_repository.empty() || request.dataset_repository.empty()) {
    return invalidArgument("model and dataset pins must be complete commit revisions");
  }
  if (request.duration_seconds == 0 || request.duration_seconds > 86400 ||
      request.warmup_seconds > 3600 || !std::isfinite(request.target_rps) ||
      request.target_rps <= 0 || !std::isfinite(request.latency_safety_threshold_ms) ||
      request.latency_safety_threshold_ms <= 0) {
    return invalidArgument("performance timing and safety settings are invalid");
  }
  return {};
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

std::string_view performanceTopologyName(PerformanceTopology topology) noexcept {
  switch (topology) {
    case PerformanceTopology::same_process:
      return "same_process";
    case PerformanceTopology::same_host_split:
      return "same_host_split";
    case PerformanceTopology::same_host_isolated:
      return "same_host_isolated";
  }
  return "same_host_split";
}

std::string_view performanceExperimentStatusName(
    PerformanceExperimentStatus status) noexcept {
  switch (status) {
    case PerformanceExperimentStatus::population_pending:
      return "population_pending";
    case PerformanceExperimentStatus::population_ready:
      return "population_ready";
    case PerformanceExperimentStatus::approved:
      return "approved";
    case PerformanceExperimentStatus::running:
      return "running";
    case PerformanceExperimentStatus::stop_requested:
      return "stop_requested";
    case PerformanceExperimentStatus::draining:
      return "draining";
    case PerformanceExperimentStatus::completed:
      return "completed";
    case PerformanceExperimentStatus::failed:
      return "failed";
    case PerformanceExperimentStatus::interrupted:
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
                                     ExperimentSourcePin source,
                                     PerformanceExperimentWorker* performance_worker)
    : repository_(repository),
      worker_(worker),
      performance_worker_(performance_worker),
      source_(std::move(source)) {}

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

Result<PerformanceExperimentDto> ExperimentService::createPerformanceExperiment(
    const PerformanceLaunchRequest& request) {
  auto valid = validatePerformanceLaunch(request);
  if (!valid) return tl::make_unexpected(valid.error());
  auto model = repository_.getModel(request.starting_model_id);
  if (!model) return tl::make_unexpected(model.error());
  if (!model->immutable || !model->compatible) {
    return invalidArgument(model->incompatibility_reason.value_or(
        "starting model is not immutable and compatible"));
  }
  if (model->encoder_repo != request.model_repository ||
      model->encoder_revision != request.model_revision) {
    return invalidArgument(
        "selected model repository and revision must match the immutable registry entry");
  }
  auto created = repository_.createPerformanceExperiment(request);
  if (!created) return tl::make_unexpected(created.error());
  if (performance_worker_ == nullptr) return created;
  auto launched = performance_worker_->start(created->experiment_id);
  if (!launched) {
    const auto recorded = repository_.markPerformanceLaunchFailed(
        created->experiment_id, launched.error().message);
    if (!recorded) return tl::make_unexpected(recorded.error());
    return tl::make_unexpected(launched.error());
  }
  return created;
}

Result<std::vector<PerformanceExperimentDto>>
ExperimentService::listPerformanceExperiments(std::size_t limit,
                                              std::optional<std::string> before) {
  if (limit == 0 || limit > 100) {
    return invalidArgument("limit must be between 1 and 100");
  }
  if (before && (before->empty() || before->size() > 128)) {
    return invalidArgument("before cursor is invalid");
  }
  return repository_.listPerformanceExperiments(limit, std::move(before));
}

Result<PerformanceExperimentDto> ExperimentService::getPerformanceExperiment(
    std::string_view experiment_id) {
  if (experiment_id.empty() || experiment_id.size() > 128) {
    return invalidArgument("performance experiment ID is invalid");
  }
  return repository_.getPerformanceExperiment(experiment_id);
}

Result<PerformanceExperimentDto> ExperimentService::requestPerformanceGracefulStop(
    std::string_view experiment_id) {
  if (experiment_id.empty() || experiment_id.size() > 128) {
    return invalidArgument("performance experiment ID is invalid");
  }
  auto stopped = repository_.requestPerformanceGracefulStop(experiment_id);
  if (!stopped) return tl::make_unexpected(stopped.error());
  if (performance_worker_ == nullptr) return stopped;
  auto requested = performance_worker_->requestGracefulStop(experiment_id);
  if (!requested) return tl::make_unexpected(requested.error());
  return stopped;
}

Result<PerformanceExperimentDto> ExperimentService::approvePerformanceNextScale(
    std::string_view experiment_id) {
  auto trial = getPerformanceExperiment(experiment_id);
  if (!trial) return tl::make_unexpected(trial.error());
  if (!trial->population_ready ||
      trial->population_vector_count != trial->launch.target_created_babels ||
      !validDigest(trial->population_vector_sha256) ||
      trial->population_model_repository != trial->launch.model_repository ||
      trial->population_model_revision != trial->launch.model_revision ||
      !validDigest(trial->population_model_sha256) ||
      trial->population_dataset_repository != trial->launch.dataset_repository ||
      trial->population_dataset_revision != trial->launch.dataset_revision ||
      !validDigest(trial->population_dataset_sha256)) {
    return tl::make_unexpected(ApplicationError{
        ErrorCode::conflict,
        "exact real model, dataset, vector count, and checksum evidence is required",
    });
  }
  auto approved = repository_.approvePerformanceNextScale(experiment_id);
  if (!approved) return tl::make_unexpected(approved.error());
  if (performance_worker_ == nullptr) return approved;
  auto requested = performance_worker_->approveNextScale(experiment_id);
  if (!requested) return tl::make_unexpected(requested.error());
  return approved;
}

Result<PerformanceExperimentDto> ExperimentService::markPerformancePopulationReady(
    std::string_view experiment_id, const PerformancePopulationEvidence& evidence) {
  auto trial = getPerformanceExperiment(experiment_id);
  if (!trial) return tl::make_unexpected(trial.error());
  if (evidence.vector_count != trial->launch.target_created_babels ||
      !validDigest(std::optional<std::string>{evidence.vector_sha256}) ||
      evidence.model_repository != trial->launch.model_repository ||
      evidence.model_revision != trial->launch.model_revision ||
      !validDigest(std::optional<std::string>{evidence.model_sha256}) ||
      evidence.dataset_repository != trial->launch.dataset_repository ||
      evidence.dataset_revision != trial->launch.dataset_revision ||
      !validDigest(std::optional<std::string>{evidence.dataset_sha256})) {
    return invalidArgument(
        "population evidence does not match the frozen model, dataset, or vector target");
  }
  return repository_.markPerformancePopulationReady(experiment_id, evidence);
}

Result<PerformanceExperimentDto> ExperimentService::attachPerformanceArtifact(
    std::string_view experiment_id, const PerformanceArtifactReceipt& receipt) {
  auto trial = getPerformanceExperiment(experiment_id);
  if (!trial) return tl::make_unexpected(trial.error());
  if (!validDigest(std::optional<std::string>{receipt.artifact_sha256}) ||
      !validRevision(receipt.remote_hf_commit_sha) ||
      receipt.remote_hf_bundle_path.empty()) {
    return invalidArgument("verified remote artifact receipt is incomplete");
  }
  return repository_.attachPerformanceArtifact(experiment_id, receipt);
}

Result<void> ExperimentService::appendPerformanceProgress(
    std::string_view experiment_id, const PerformanceProgressDto& progress) {
  auto trial = getPerformanceExperiment(experiment_id);
  if (!trial) return tl::make_unexpected(trial.error());
  if (progress.phase.empty() || progress.phase.size() > 64 ||
      progress.condition_count == 0 || progress.condition_count > 9 ||
      (progress.condition_index && (*progress.condition_index == 0 ||
                                    *progress.condition_index > progress.condition_count)) ||
      progress.seeded_articles > trial->launch.seeded_articles ||
      progress.created_babels > trial->launch.target_created_babels ||
      progress.indexed_babels > trial->launch.target_created_babels ||
      !std::isfinite(progress.elapsed_seconds) || progress.elapsed_seconds < 0 ||
      !std::isfinite(progress.recent_rate) || progress.recent_rate < 0 ||
      progress.telemetry_json.empty()) {
    return invalidArgument("performance progress snapshot is invalid");
  }
  return repository_.appendPerformanceProgress(experiment_id, progress);
}

Result<void> ExperimentService::savePerformanceResult(
    std::string_view experiment_id, const PerformanceResultDto& result) {
  if (result.condition_id.empty() || result.condition_index == 0 ||
      result.condition_index > 9 || result.raw_evidence_json.empty() ||
      !validDigest(std::optional<std::string>{result.evidence_sha256}) ||
      !std::isfinite(result.serving_p95_ms) || result.serving_p95_ms < 0) {
    return invalidArgument("performance result identity or evidence is invalid");
  }
  const auto finiteNonnegative = [](const std::optional<double>& value) {
    return !value || (std::isfinite(*value) && *value >= 0);
  };
  if (!finiteNonnegative(result.training_p95_ms) ||
      !finiteNonnegative(result.full_p95_ms) ||
      !finiteNonnegative(result.itraining) || !finiteNonnegative(result.ifull) ||
      !finiteNonnegative(result.iactivation_increment)) {
    return invalidArgument("performance result measurements are invalid");
  }
  const bool any_ratio = result.itraining || result.ifull ||
                         result.iactivation_increment;
  if (any_ratio) {
    if (!result.training_p95_ms || !result.full_p95_ms || !result.itraining ||
        !result.ifull || !result.iactivation_increment || result.serving_p95_ms <= 0 ||
        *result.training_p95_ms <= 0 ||
        std::abs(*result.itraining -
                 *result.training_p95_ms / result.serving_p95_ms) > 1e-9 ||
        std::abs(*result.ifull - *result.full_p95_ms / result.serving_p95_ms) > 1e-9 ||
        std::abs(*result.iactivation_increment -
                 *result.full_p95_ms / *result.training_p95_ms) > 1e-9) {
      return invalidArgument("interference ratios must equal T/S, F/S, and F/T");
    }
  }
  return repository_.savePerformanceResult(experiment_id, result);
}

}  // namespace babel
