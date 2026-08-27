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
enum class PerformanceTopology { same_process, same_host_split, same_host_isolated };
enum class PerformanceExperimentStatus {
  population_pending,
  population_ready,
  approved,
  running,
  stop_requested,
  draining,
  completed,
  failed,
  interrupted,
};
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

struct PerformanceLaunchRequest {
  RecommenderModelId starting_model_id;
  PerformanceTopology topology{PerformanceTopology::same_host_split};
  std::string model_repository{"dhelmy990/babel-qwen-navigation-2016-interview"};
  std::string model_revision{"57d949cd634b920cc1a46f27c9b21df094b5240e"};
  std::string dataset_repository{"dhelmy990/babel-wikipedia-experiment"};
  std::string dataset_revision{"0d1ab2c7f0e2295682288fcf10077d2d776bf559"};
  RetrievalBackend retrieval_backend{RetrievalBackend::pgvector};
  std::size_t creator_count{50};
  std::size_t seeded_articles{10000};
  std::size_t target_created_babels{10000};
  std::size_t concurrent_users{50};
  double recommendation_start_probability{0.40};
  double continuation_probability{0.40};
  std::size_t maximum_traversal_depth{2};
  std::size_t maximum_requests_per_traversal{10};
  std::size_t training_micro_batch_size{8};
  std::size_t sync_every_steps{10};
  bool interleave_creation_and_recommendations{true};
  bool auto_advance{false};
  std::size_t warmup_seconds{30};
  std::size_t duration_seconds{120};
  double target_rps{5.0};
  double latency_safety_threshold_ms{5000.0};
};

struct PerformanceProgressDto {
  std::string phase{"population"};
  std::optional<std::size_t> condition_index{};
  std::size_t condition_count{9};
  std::uint64_t seeded_articles{0};
  std::uint64_t created_babels{0};
  std::uint64_t indexed_babels{0};
  std::uint64_t requested{0};
  std::uint64_t completed{0};
  double elapsed_seconds{0};
  double recent_rate{0};
  bool draining{false};
  std::string telemetry_json{"{}"};
};

struct PerformanceResultDto {
  std::string condition_id;
  std::size_t condition_index{0};
  PerformanceTopology topology{PerformanceTopology::same_host_split};
  bool training_enabled{false};
  bool synchronization_enabled{false};
  std::string raw_evidence_json{"{}"};
  std::string evidence_sha256;
  double serving_p95_ms{0};
  std::optional<double> training_p95_ms{};
  std::optional<double> full_p95_ms{};
  std::optional<double> itraining{};
  std::optional<double> ifull{};
  std::optional<double> iactivation_increment{};
};

struct PerformancePopulationEvidence {
  std::uint64_t vector_count{0};
  std::string vector_sha256;
  std::string model_repository;
  std::string model_revision;
  std::string model_sha256;
  std::string dataset_repository;
  std::string dataset_revision;
  std::string dataset_sha256;
};

struct PerformanceArtifactReceipt {
  std::string artifact_sha256;
  std::string remote_hf_commit_sha;
  std::string remote_hf_bundle_path;
};

struct PerformanceExperimentDto {
  std::string experiment_id;
  PerformanceExperimentStatus status{PerformanceExperimentStatus::population_pending};
  PerformanceLaunchRequest launch;
  bool population_ready{false};
  std::uint64_t population_vector_count{0};
  std::optional<std::string> population_vector_sha256{};
  std::optional<std::string> population_model_repository{};
  std::optional<std::string> population_model_revision{};
  std::optional<std::string> population_model_sha256{};
  std::optional<std::string> population_dataset_repository{};
  std::optional<std::string> population_dataset_revision{};
  std::optional<std::string> population_dataset_sha256{};
  bool operator_approved{false};
  std::optional<ExperimentRunId> run_id{};
  std::optional<std::string> population_manifest_sha256{};
  std::optional<std::string> population_bundle_path{};
  std::optional<std::string> failure{};
  std::optional<std::string> placement_manifest_json{};
  std::optional<std::string> placement_sha256{};
  std::string hardware_identity_json{"{}"};
  std::string resource_identity_json{"{}"};
  std::string request_identity_json{"{}"};
  std::string feedback_identity_json{"{}"};
  std::optional<std::string> artifact_sha256{};
  std::optional<std::string> remote_hf_commit_sha{};
  std::optional<std::string> remote_hf_bundle_path{};
  std::optional<PerformanceProgressDto> progress{};
  std::vector<PerformanceResultDto> results;
  std::string created_at;
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
  std::optional<std::string> request_id{};
  std::optional<std::string> traversal_session_id{};
  std::optional<std::string> source_vector_origin{};
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
[[nodiscard]] std::string_view performanceTopologyName(PerformanceTopology) noexcept;
[[nodiscard]] std::string_view performanceExperimentStatusName(
    PerformanceExperimentStatus) noexcept;
[[nodiscard]] bool isTerminal(ExperimentStatus) noexcept;

}  // namespace babel
