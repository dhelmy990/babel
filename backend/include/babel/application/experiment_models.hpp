#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include "babel/domain/ids.hpp"

namespace babel {

enum class RetrievalBackend { pgvector, hnswlib };
enum class ExperimentScenario { june_only, june_to_july };
enum class ExperimentStatus {
  starting,
  running,
  stop_requested,
  draining_feedback,
  checkpointing,
  exporting_interactions,
  completed,
  failed,
  interrupted,
};

struct EmbeddingSpaceDto {
  int schema_version{1};
  std::string embedding_space_id;
  int dimension{100};
  std::string distance{"cosine"};
  std::string distilled_encoder_artifact;
  std::string dataset_revision;
  std::string compatibility_version;
};

struct RecommenderModelDto {
  RecommenderModelId model_id;
  std::string label;
  std::optional<RecommenderModelId> parent_model_id{};
  std::optional<ExperimentRunId> producing_run_id{};
  std::string encoder_repo;
  std::string encoder_revision;
  std::string dataset_repo;
  std::string dataset_revision;
  std::vector<std::string> environment_sequence;
  std::uint64_t training_examples{0};
  std::string checkpoint_path;
  std::string checkpoint_sha256;
  EmbeddingSpaceDto embedding_space;
  std::string created_at;
  bool immutable{true};
  bool compatible{true};
  std::optional<std::string> incompatibility_reason{};
};

struct ExperimentSourcePin {
  std::string repository;
  std::string configuration;
  std::string commit_sha;
};

struct ExperimentLaunchRequest {
  RecommenderModelId starting_model_id;
  RetrievalBackend retrieval_backend{RetrievalBackend::pgvector};
  std::size_t creator_count{50};
  ExperimentScenario scenario{ExperimentScenario::june_to_july};
  std::size_t event_budget_per_month{100};
  std::uint64_t run_seed{0};
  std::size_t recommendation_k{10};
  std::size_t top_l{100};
  std::string kafka_topic{"babel.feedback.v1"};
  std::string kafka_group{"babel-online-trainer-v1"};
  std::size_t checkpoint_every_events{100};
  std::size_t sync_every_steps{10};
  std::string artifact_root{"artifacts/online"};
  std::string state_root{"state/online"};
};

struct ExperimentLaunchSnapshot {
  ExperimentLaunchRequest request;
  ExperimentSourcePin source;
  std::vector<std::string> environment_sequence;
};

struct ExperimentRunStatusDto {
  ExperimentRunId run_id;
  ExperimentStatus status{ExperimentStatus::starting};
  RetrievalBackend retrieval_backend{RetrievalBackend::pgvector};
  std::size_t creator_count{50};
  std::vector<std::string> environment_sequence;
  RecommenderModelId starting_model_id;
  RecommenderModelId active_model_id;
  std::uint64_t active_model_version{0};
  std::uint64_t created_babel_count{0};
  std::uint64_t feedback_count{0};
  double event_rate{0};
  std::uint64_t kafka_offset{0};
  std::uint64_t kafka_lag{0};
  std::uint64_t trainer_steps{0};
  std::optional<double> rolling_rank_loss{};
  std::optional<std::string> checkpoint_path{};
  std::optional<std::string> checkpoint_sha256{};
  bool serving_synced{true};
  std::optional<std::string> started_at{};
  std::optional<std::string> completed_at{};
  std::optional<std::string> failure{};
};

struct ExperimentLifecycleActivityDto {};

struct ExperimentRecommendationActivityDto {
  CreatorId creator_id;
  BabelId new_babel_id;
  std::string new_babel_title;
  std::vector<BabelId> candidate_babel_ids;
  std::vector<BabelId> include_babel_ids;
  std::vector<BabelId> exclude_babel_ids;
  std::vector<BabelId> ignore_babel_ids;
  std::size_t accepted_edge_count{0};
  RecommenderModelId model_id;
  std::uint64_t model_version{0};
};

struct ExperimentFeedbackActivityDto {
  std::uint64_t kafka_offset{0};
  std::uint64_t kafka_lag{0};
};

struct ExperimentTrainingActivityDto {
  std::uint64_t trainer_step{0};
  double rolling_rank_loss{0};
};

struct ExperimentSynchronizationActivityDto {
  std::string checkpoint_path;
  std::string checkpoint_sha256;
  std::uint64_t synchronization_version{0};
  RecommenderModelId model_id;
  std::uint64_t model_version{0};
};

using ExperimentActivityDetails =
    std::variant<ExperimentLifecycleActivityDto, ExperimentRecommendationActivityDto,
                 ExperimentFeedbackActivityDto, ExperimentTrainingActivityDto,
                 ExperimentSynchronizationActivityDto>;

struct ExperimentActivityDto {
  int schema_version{1};
  ExperimentRunId run_id;
  std::uint64_t sequence{0};
  std::uint64_t occurred_at_ns{0};
  std::string level;
  std::string component;
  std::string event;
  std::string message;
  std::map<std::string, double> metrics;
  ExperimentActivityDetails details{ExperimentLifecycleActivityDto{}};
};

[[nodiscard]] std::string_view retrievalBackendName(RetrievalBackend) noexcept;
[[nodiscard]] std::string_view experimentScenarioName(ExperimentScenario) noexcept;
[[nodiscard]] std::string_view experimentStatusName(ExperimentStatus) noexcept;
[[nodiscard]] bool isTerminal(ExperimentStatus) noexcept;

}  // namespace babel
