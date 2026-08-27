#include "babel/adapters/postgres/experiment_repository.hpp"

#include <array>
#include <iomanip>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include <boost/uuid/random_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <nlohmann/json.hpp>
#include <openssl/evp.h>
#include <pqxx/pqxx>

#include "babel/adapters/postgres/postgres_database.hpp"

namespace babel {
namespace {

using Json = nlohmann::json;

constexpr auto kRunColumns = R"(
  id, status, retrieval_backend, creator_count, environment_sequence,
  starting_model_id, active_model_id, active_model_version,
  created_babel_count, feedback_count, event_rate, kafka_offset, kafka_lag,
  trainer_steps, rolling_rank_loss, checkpoint_path, checkpoint_sha256,
  serving_synced,
  CASE WHEN started_at IS NULL THEN NULL ELSE
    to_char(timezone('UTC', started_at), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END AS started_at_text,
  CASE WHEN completed_at IS NULL THEN NULL ELSE
    to_char(timezone('UTC', completed_at), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') END AS completed_at_text,
  failure
)";

std::string sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (EVP_Digest(value.data(), value.size(), digest.data(), &size, EVP_sha256(), nullptr) !=
      1) {
    return {};
  }
  std::ostringstream encoded;
  encoded << std::hex << std::setfill('0');
  for (unsigned int index = 0; index < size; ++index) {
    encoded << std::setw(2) << static_cast<unsigned int>(digest[index]);
  }
  return encoded.str();
}

RetrievalBackend retrievalBackendFromName(std::string_view value) {
  return value == "hnswlib" ? RetrievalBackend::hnswlib : RetrievalBackend::pgvector;
}

ExperimentStatus experimentStatusFromName(std::string_view value) {
  if (value == "running") return ExperimentStatus::running;
  if (value == "stop_requested") return ExperimentStatus::stop_requested;
  if (value == "draining_feedback") return ExperimentStatus::draining_feedback;
  if (value == "checkpointing") return ExperimentStatus::checkpointing;
  if (value == "exporting_interactions") return ExperimentStatus::exporting_interactions;
  if (value == "completed") return ExperimentStatus::completed;
  if (value == "failed") return ExperimentStatus::failed;
  if (value == "interrupted") return ExperimentStatus::interrupted;
  return ExperimentStatus::starting;
}

PerformanceTopology performanceTopologyFromName(std::string_view value) {
  if (value == "same_process") return PerformanceTopology::same_process;
  if (value == "same_host_isolated") return PerformanceTopology::same_host_isolated;
  return PerformanceTopology::same_host_split;
}

PerformanceExperimentStatus performanceStatusFromName(std::string_view value) {
  if (value == "population_ready") return PerformanceExperimentStatus::population_ready;
  if (value == "approved") return PerformanceExperimentStatus::approved;
  if (value == "running") return PerformanceExperimentStatus::running;
  if (value == "stop_requested") return PerformanceExperimentStatus::stop_requested;
  if (value == "draining") return PerformanceExperimentStatus::draining;
  if (value == "completed") return PerformanceExperimentStatus::completed;
  if (value == "failed") return PerformanceExperimentStatus::failed;
  if (value == "interrupted") return PerformanceExperimentStatus::interrupted;
  return PerformanceExperimentStatus::population_pending;
}

template <typename T>
std::optional<T> optionalField(const pqxx::row& row, const char* name) {
  const auto field = row[name];
  if (field.is_null()) return std::nullopt;
  return field.as<T>();
}

ExperimentRunStatusDto statusFromRow(const pqxx::row& row) {
  const auto environment = Json::parse(row["environment_sequence"].as<std::string>());
  return ExperimentRunStatusDto{
      .run_id = ExperimentRunId::parse(row["id"].as<std::string>()).value(),
      .status = experimentStatusFromName(row["status"].as<std::string>()),
      .retrieval_backend = retrievalBackendFromName(
          row["retrieval_backend"].as<std::string>()),
      .creator_count = row["creator_count"].as<std::size_t>(),
      .environment_sequence = environment.get<std::vector<std::string>>(),
      .starting_model_id =
          RecommenderModelId::parse(row["starting_model_id"].as<std::string>()).value(),
      .active_model_id =
          RecommenderModelId::parse(row["active_model_id"].as<std::string>()).value(),
      .active_model_version = row["active_model_version"].as<std::uint64_t>(),
      .created_babel_count = row["created_babel_count"].as<std::uint64_t>(),
      .feedback_count = row["feedback_count"].as<std::uint64_t>(),
      .event_rate = row["event_rate"].as<double>(),
      .kafka_offset = row["kafka_offset"].as<std::uint64_t>(),
      .kafka_lag = row["kafka_lag"].as<std::uint64_t>(),
      .trainer_steps = row["trainer_steps"].as<std::uint64_t>(),
      .rolling_rank_loss = optionalField<double>(row, "rolling_rank_loss"),
      .checkpoint_path = optionalField<std::string>(row, "checkpoint_path"),
      .checkpoint_sha256 = optionalField<std::string>(row, "checkpoint_sha256"),
      .serving_synced = row["serving_synced"].as<bool>(),
      .started_at = optionalField<std::string>(row, "started_at_text"),
      .completed_at = optionalField<std::string>(row, "completed_at_text"),
      .failure = optionalField<std::string>(row, "failure"),
  };
}

constexpr auto kPerformanceColumns = R"(
  id, status, topology, starting_model_id, model_repository, model_revision,
  dataset_repository, dataset_revision, retrieval_backend, creator_count,
  seeded_articles, target_created_babels, concurrent_users,
  recommendation_start_probability, continuation_probability,
  maximum_traversal_depth, maximum_requests_per_traversal,
  training_micro_batch_size, sync_every_steps,
  interleave_creation_and_recommendations, auto_advance, warmup_seconds,
  duration_seconds, target_rps, latency_safety_threshold_ms,
  placement_manifest, placement_sha256, hardware_identity, resource_identity,
  request_identity, feedback_identity, population_ready, population_vector_count,
  population_vector_sha256, population_model_repository, population_model_revision,
  population_model_sha256, population_dataset_repository, population_dataset_revision,
  population_dataset_sha256,
  operator_approved, artifact_sha256, remote_hf_commit_sha, remote_hf_bundle_path,
  run_id, population_manifest_sha256, population_bundle_path, failure,
  to_char(timezone('UTC', created_at), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS created_at_text
)";

PerformanceExperimentDto performanceFromRow(const pqxx::row& row) {
  const auto model_id =
      RecommenderModelId::parse(row["starting_model_id"].as<std::string>()).value();
  PerformanceExperimentDto result{
      .experiment_id = row["id"].as<std::string>(),
      .status = performanceStatusFromName(row["status"].as<std::string>()),
      .launch = PerformanceLaunchRequest{
          .starting_model_id = model_id,
          .topology = performanceTopologyFromName(row["topology"].as<std::string>()),
          .model_repository = row["model_repository"].as<std::string>(),
          .model_revision = row["model_revision"].as<std::string>(),
          .dataset_repository = row["dataset_repository"].as<std::string>(),
          .dataset_revision = row["dataset_revision"].as<std::string>(),
          .retrieval_backend = retrievalBackendFromName(
              row["retrieval_backend"].as<std::string>()),
          .creator_count = row["creator_count"].as<std::size_t>(),
          .seeded_articles = row["seeded_articles"].as<std::size_t>(),
          .target_created_babels = row["target_created_babels"].as<std::size_t>(),
          .concurrent_users = row["concurrent_users"].as<std::size_t>(),
          .recommendation_start_probability =
              row["recommendation_start_probability"].as<double>(),
          .continuation_probability = row["continuation_probability"].as<double>(),
          .maximum_traversal_depth =
              row["maximum_traversal_depth"].as<std::size_t>(),
          .maximum_requests_per_traversal =
              row["maximum_requests_per_traversal"].as<std::size_t>(),
          .training_micro_batch_size =
              row["training_micro_batch_size"].as<std::size_t>(),
          .sync_every_steps = row["sync_every_steps"].as<std::size_t>(),
          .interleave_creation_and_recommendations =
              row["interleave_creation_and_recommendations"].as<bool>(),
          .auto_advance = row["auto_advance"].as<bool>(),
          .warmup_seconds = row["warmup_seconds"].as<std::size_t>(),
          .duration_seconds = row["duration_seconds"].as<std::size_t>(),
          .target_rps = row["target_rps"].as<double>(),
          .latency_safety_threshold_ms =
              row["latency_safety_threshold_ms"].as<double>(),
      },
      .population_ready = row["population_ready"].as<bool>(),
      .population_vector_count =
          optionalField<std::uint64_t>(row, "population_vector_count").value_or(0),
      .population_vector_sha256 =
          optionalField<std::string>(row, "population_vector_sha256"),
      .population_model_repository =
          optionalField<std::string>(row, "population_model_repository"),
      .population_model_revision =
          optionalField<std::string>(row, "population_model_revision"),
      .population_model_sha256 =
          optionalField<std::string>(row, "population_model_sha256"),
      .population_dataset_repository =
          optionalField<std::string>(row, "population_dataset_repository"),
      .population_dataset_revision =
          optionalField<std::string>(row, "population_dataset_revision"),
      .population_dataset_sha256 =
          optionalField<std::string>(row, "population_dataset_sha256"),
      .operator_approved = row["operator_approved"].as<bool>(),
      .run_id = row["run_id"].is_null()
                    ? std::nullopt
                    : std::optional{ExperimentRunId::parse(
                                          row["run_id"].as<std::string>()).value()},
      .population_manifest_sha256 =
          optionalField<std::string>(row, "population_manifest_sha256"),
      .population_bundle_path = optionalField<std::string>(row, "population_bundle_path"),
      .failure = optionalField<std::string>(row, "failure"),
      .placement_manifest_json = optionalField<std::string>(row, "placement_manifest"),
      .placement_sha256 = optionalField<std::string>(row, "placement_sha256"),
      .hardware_identity_json = row["hardware_identity"].as<std::string>(),
      .resource_identity_json = row["resource_identity"].as<std::string>(),
      .request_identity_json = row["request_identity"].as<std::string>(),
      .feedback_identity_json = row["feedback_identity"].as<std::string>(),
      .artifact_sha256 = optionalField<std::string>(row, "artifact_sha256"),
      .remote_hf_commit_sha = optionalField<std::string>(row, "remote_hf_commit_sha"),
      .remote_hf_bundle_path = optionalField<std::string>(row, "remote_hf_bundle_path"),
      .progress = std::nullopt,
      .results = {},
      .created_at = row["created_at_text"].as<std::string>(),
  };
  return result;
}

void attachLatestProgress(pqxx::transaction_base& transaction,
                          PerformanceExperimentDto& result) {
  const auto rows = transaction.exec(R"(
    SELECT phase, condition_index, condition_count, seeded_articles, created_babels,
           indexed_babels, requested, completed, elapsed_seconds, recent_rate,
           draining, telemetry
    FROM performance_progress_snapshots
    WHERE experiment_id = $1
    ORDER BY sequence DESC LIMIT 1
  )", pqxx::params{result.experiment_id});
  if (rows.empty()) return;
  const auto& row = rows.one_row();
  result.progress = PerformanceProgressDto{
      .phase = row["phase"].as<std::string>(),
      .condition_index = optionalField<std::size_t>(row, "condition_index"),
      .condition_count = row["condition_count"].as<std::size_t>(),
      .seeded_articles = row["seeded_articles"].as<std::uint64_t>(),
      .created_babels = row["created_babels"].as<std::uint64_t>(),
      .indexed_babels = row["indexed_babels"].as<std::uint64_t>(),
      .requested = row["requested"].as<std::uint64_t>(),
      .completed = row["completed"].as<std::uint64_t>(),
      .elapsed_seconds = row["elapsed_seconds"].as<double>(),
      .recent_rate = row["recent_rate"].as<double>(),
      .draining = row["draining"].as<bool>(),
      .telemetry_json = row["telemetry"].as<std::string>(),
  };
}

void attachPerformanceResults(pqxx::transaction_base& transaction,
                              PerformanceExperimentDto& result) {
  const auto rows = transaction.exec(R"(
    SELECT r.condition_id, c.condition_index, c.topology, c.training_enabled,
           c.synchronization_enabled, r.raw_evidence, r.evidence_sha256,
           r.serving_p95_ms, r.training_p95_ms, r.full_p95_ms,
           r.itraining, r.ifull, r.iactivation_increment
    FROM performance_results r
    JOIN performance_conditions c ON c.id = r.condition_id
    WHERE r.experiment_id = $1
    ORDER BY c.condition_index
  )", pqxx::params{result.experiment_id});
  result.results.reserve(rows.size());
  for (const auto& row : rows) {
    result.results.push_back(PerformanceResultDto{
        .condition_id = row["condition_id"].as<std::string>(),
        .condition_index = row["condition_index"].as<std::size_t>(),
        .topology = performanceTopologyFromName(row["topology"].as<std::string>()),
        .training_enabled = row["training_enabled"].as<bool>(),
        .synchronization_enabled = row["synchronization_enabled"].as<bool>(),
        .raw_evidence_json = row["raw_evidence"].as<std::string>(),
        .evidence_sha256 = row["evidence_sha256"].as<std::string>(),
        .serving_p95_ms = row["serving_p95_ms"].as<double>(),
        .training_p95_ms = optionalField<double>(row, "training_p95_ms"),
        .full_p95_ms = optionalField<double>(row, "full_p95_ms"),
        .itraining = optionalField<double>(row, "itraining"),
        .ifull = optionalField<double>(row, "ifull"),
        .iactivation_increment =
            optionalField<double>(row, "iactivation_increment"),
    });
  }
}

RecommenderModelDto modelFromRow(const pqxx::row& row) {
  const auto embedding = Json::parse(row["embedding_space"].as<std::string>());
  const auto environments = Json::parse(row["environment_sequence"].as<std::string>());
  const auto dimension = embedding.at("dimension").get<int>();
  const auto distance = embedding.at("distance").get<std::string>();
  const bool compatible = dimension == 100 && distance == "cosine";
  return RecommenderModelDto{
      .model_id = RecommenderModelId::parse(row["id"].as<std::string>()).value(),
      .label = row["label"].as<std::string>(),
      .parent_model_id = row["parent_model_id"].is_null()
                             ? std::nullopt
                             : std::optional{RecommenderModelId::parse(
                                                   row["parent_model_id"].as<std::string>())
                                                   .value()},
      .producing_run_id = row["producing_run_id"].is_null()
                              ? std::nullopt
                              : std::optional{ExperimentRunId::parse(
                                                    row["producing_run_id"].as<std::string>())
                                                    .value()},
      .encoder_repo = row["encoder_repo"].as<std::string>(),
      .encoder_revision = row["encoder_revision"].as<std::string>(),
      .dataset_repo = row["dataset_repo"].as<std::string>(),
      .dataset_revision = row["dataset_revision"].as<std::string>(),
      .environment_sequence = environments.get<std::vector<std::string>>(),
      .training_examples = row["training_examples"].as<std::uint64_t>(),
      .checkpoint_path = row["checkpoint_path"].as<std::string>(),
      .checkpoint_sha256 = row["checkpoint_sha256"].as<std::string>(),
      .embedding_space = EmbeddingSpaceDto{
          .schema_version = embedding.at("schemaVersion").get<int>(),
          .embedding_space_id = embedding.at("embeddingSpaceId").get<std::string>(),
          .dimension = dimension,
          .distance = distance,
          .distilled_encoder_artifact =
              embedding.at("distilledEncoderArtifact").get<std::string>(),
          .dataset_revision = embedding.at("datasetRevision").get<std::string>(),
          .compatibility_version =
              embedding.at("compatibilityVersion").get<std::string>(),
      },
      .created_at = row["created_at_text"].as<std::string>(),
      .immutable = row["immutable"].as<bool>(),
      .compatible = compatible,
      .incompatibility_reason = compatible
                                      ? std::nullopt
                                      : std::optional<std::string>{
                                            "requires 100-dimensional cosine embedding space"},
  };
}

constexpr auto kModelQuery = R"(
  SELECT id, label, parent_model_id, producing_run_id, encoder_repo,
         encoder_revision, dataset_repo, dataset_revision, environment_sequence,
         training_examples, checkpoint_path, checkpoint_sha256, embedding_space,
         immutable,
         to_char(timezone('UTC', created_at), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
           AS created_at_text
  FROM recommender_models
)";

Json runConfig(ExperimentRunId run_id, const ExperimentLaunchSnapshot& snapshot) {
  Json budgets = Json::object();
  for (const auto& month : snapshot.environment_sequence) {
    budgets[month] = snapshot.request.event_budget_per_month;
  }
  // std::map-backed JSON gives the same sorted-key, compact UTF-8 bytes used by
  // the Python worker's canonical launch verifier after PostgreSQL jsonb decode.
  return Json{
      {"schemaVersion", 1},
      {"runId", run_id.value},
      {"datasetRepo", snapshot.source.repository},
      {"datasetConfig", snapshot.source.configuration},
      {"datasetRevision", snapshot.source.commit_sha},
      {"startingModelId", snapshot.request.starting_model_id.value},
      {"retrievalBackend", retrievalBackendName(snapshot.request.retrieval_backend)},
      {"creatorCount", snapshot.request.creator_count},
      {"runSeed", snapshot.request.run_seed},
      {"embeddingDimension", 100},
      {"environmentSequence", snapshot.environment_sequence},
      {"perMonthEventBudget", budgets},
      {"recommendationK", snapshot.request.recommendation_k},
      {"topL", snapshot.request.top_l},
      {"kafkaTopic", snapshot.request.kafka_topic},
      {"kafkaGroup", snapshot.request.kafka_group},
      {"checkpointEveryEvents", snapshot.request.checkpoint_every_events},
      {"syncEverySteps", snapshot.request.sync_every_steps},
      {"artifactRoot", snapshot.request.artifact_root},
      {"stateRoot", snapshot.request.state_root},
  };
}

std::vector<BabelId> babelIds(const Json& values) {
  std::vector<BabelId> result;
  result.reserve(values.size());
  for (const auto& value : values) {
    result.push_back(BabelId::parse(value.get<std::string>()).value());
  }
  return result;
}

std::optional<std::string> optionalString(const Json& value, std::string_view key) {
  const auto found = value.find(key);
  if (found == value.end() || !found->is_string()) return std::nullopt;
  return found->get<std::string>();
}

ExperimentActivityDetails activityDetails(const Json& details) {
  const auto kind = details.at("kind").get<std::string>();
  if (kind == "recommendation") {
    return ExperimentRecommendationActivityDto{
        .creator_id = CreatorId::parse(details.at("creatorId").get<std::string>()).value(),
        .new_babel_id = BabelId::parse(details.at("newBabelId").get<std::string>()).value(),
        .new_babel_title = details.at("newBabelTitle").get<std::string>(),
        .candidate_babel_ids = babelIds(details.at("candidateBabelIds")),
        .include_babel_ids = babelIds(details.at("includeBabelIds")),
        .exclude_babel_ids = babelIds(details.at("excludeBabelIds")),
        .ignore_babel_ids = babelIds(details.at("ignoreBabelIds")),
        .accepted_edge_count = details.at("acceptedEdgeCount").get<std::size_t>(),
        .model_id = RecommenderModelId::parse(details.at("modelId").get<std::string>()).value(),
        .model_version = details.at("modelVersion").get<std::uint64_t>(),
        .request_id = optionalString(details, "requestId"),
        .traversal_session_id = optionalString(details, "traversalSessionId"),
        .source_vector_origin = optionalString(details, "sourceVectorOrigin"),
    };
  }
  if (kind == "feedback") {
    return ExperimentFeedbackActivityDto{
        .kafka_offset = details.at("kafkaOffset").get<std::uint64_t>(),
        .kafka_lag = details.at("kafkaLag").get<std::uint64_t>(),
    };
  }
  if (kind == "training") {
    return ExperimentTrainingActivityDto{
        .trainer_step = details.at("trainerStep").get<std::uint64_t>(),
        .rolling_rank_loss = details.at("rollingRankLoss").get<double>(),
    };
  }
  if (kind == "synchronization") {
    return ExperimentSynchronizationActivityDto{
        .checkpoint_path = details.at("checkpointPath").get<std::string>(),
        .checkpoint_sha256 = details.at("checkpointSha256").get<std::string>(),
        .synchronization_version =
            details.at("synchronizationVersion").get<std::uint64_t>(),
        .model_id = RecommenderModelId::parse(details.at("modelId").get<std::string>()).value(),
        .model_version = details.at("modelVersion").get<std::uint64_t>(),
    };
  }
  return ExperimentLifecycleActivityDto{};
}

}  // namespace

PostgresExperimentRepository::PostgresExperimentRepository(PostgresDatabase& database)
    : database_(database) {}

Result<std::vector<RecommenderModelDto>> PostgresExperimentRepository::listModels() {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(std::string{kModelQuery} + " ORDER BY created_at, id");
    std::vector<RecommenderModelDto> models;
    models.reserve(rows.size());
    for (const auto& row : rows) models.push_back(modelFromRow(row));
    return models;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<RecommenderModelDto> PostgresExperimentRepository::getModel(
    RecommenderModelId model_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(std::string{kModelQuery} + " WHERE id = $1",
                                       pqxx::params{model_id.value});
    if (rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "recommender model not found"});
    }
    return modelFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<ExperimentRunStatusDto> PostgresExperimentRepository::createRun(
    const ExperimentLaunchSnapshot& snapshot) {
  try {
    const auto generated = ExperimentRunId::parse(
        boost::uuids::to_string(boost::uuids::random_generator()())).value();
    const auto config = runConfig(generated, snapshot).dump();
    const auto digest = sha256(config);
    if (digest.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::internal, "could not hash experiment launch"});
    }
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"INSERT INTO experiment_runs("}
            + "id, status, retrieval_backend, creator_count, scenario, "
              "environment_sequence, event_budget_per_month, run_seed, "
              "dataset_repository, dataset_config, dataset_revision, recommendation_k, top_l, "
              "kafka_topic, kafka_group, checkpoint_every_events, sync_every_steps, "
              "artifact_root, state_root, starting_model_id, active_model_id, "
              "launch_config, launch_sha256) VALUES ("
              "$1, 'starting', $2, $3, $4, CAST($5 AS jsonb), $6, $7, $8, $9, $10, "
              "$11, $12, $13, $14, $15, $16, $17, $18, $19, $19, "
              "CAST($20 AS jsonb), $21) RETURNING " +
            kRunColumns,
        pqxx::params{
            generated.value,
            std::string{retrievalBackendName(snapshot.request.retrieval_backend)},
            static_cast<std::int64_t>(snapshot.request.creator_count),
            std::string{experimentScenarioName(snapshot.request.scenario)},
            Json(snapshot.environment_sequence).dump(),
            static_cast<std::int64_t>(snapshot.request.event_budget_per_month),
            static_cast<std::int64_t>(snapshot.request.run_seed), snapshot.source.repository,
            snapshot.source.configuration, snapshot.source.commit_sha,
            static_cast<std::int64_t>(snapshot.request.recommendation_k),
            static_cast<std::int64_t>(snapshot.request.top_l), snapshot.request.kafka_topic,
            snapshot.request.kafka_group,
            static_cast<std::int64_t>(snapshot.request.checkpoint_every_events),
            static_cast<std::int64_t>(snapshot.request.sync_every_steps),
            snapshot.request.artifact_root, snapshot.request.state_root,
            snapshot.request.starting_model_id.value, config, digest});
    transaction.commit();
    return statusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<ExperimentRunStatusDto> PostgresExperimentRepository::latestRun() {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(std::string{"SELECT "} + kRunColumns +
                                       " FROM experiment_runs ORDER BY created_at DESC LIMIT 1");
    if (rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "no experiment run exists"});
    }
    return statusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<ExperimentRunStatusDto> PostgresExperimentRepository::getRun(
    ExperimentRunId run_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(std::string{"SELECT "} + kRunColumns +
                                           " FROM experiment_runs WHERE id = $1",
                                       pqxx::params{run_id.value});
    if (rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "experiment run not found"});
    }
    return statusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<ExperimentRunStatusDto> PostgresExperimentRepository::requestGracefulStop(
    ExperimentRunId run_id) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    auto rows = transaction.exec(
        std::string{"UPDATE experiment_runs SET status = 'stop_requested', "}
            + "stop_requested_at = COALESCE(stop_requested_at, now()) "
              "WHERE id = $1 AND status IN ('starting', 'running') RETURNING " +
            kRunColumns,
        pqxx::params{run_id.value});
    if (rows.empty()) {
      rows = transaction.exec(std::string{"SELECT "} + kRunColumns +
                                  " FROM experiment_runs WHERE id = $1",
                              pqxx::params{run_id.value});
      if (rows.empty()) {
        return tl::make_unexpected(
            ApplicationError{ErrorCode::not_found, "experiment run not found"});
      }
      const auto existing = statusFromRow(rows.one_row());
      if (existing.status != ExperimentStatus::stop_requested) {
        return tl::make_unexpected(
            ApplicationError{ErrorCode::conflict, "experiment run cannot be stopped"});
      }
    }
    transaction.commit();
    return statusFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresExperimentRepository::markLaunchFailed(
    ExperimentRunId run_id, std::string_view message) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto changed = transaction.exec(
        "UPDATE experiment_runs SET status = 'failed', failure = $2, completed_at = now() "
        "WHERE id = $1 AND status = 'starting'",
        pqxx::params{run_id.value, message});
    if (changed.affected_rows() != 1) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::conflict, "experiment launch state changed"});
    }
    transaction.commit();
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresExperimentRepository::markInterruptedRuns() {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    transaction.exec(R"(
      UPDATE experiment_runs
      SET status = 'interrupted', completed_at = now(),
          failure = 'backend restarted before experiment completed'
      WHERE status IN (
        'starting', 'running', 'stop_requested', 'draining_feedback',
        'checkpointing', 'exporting_interactions'
      )
    )");
    transaction.exec(R"(
      UPDATE performance_experiments
      SET status = 'interrupted'
      WHERE status IN ('approved', 'running', 'stop_requested', 'draining')
    )");
    transaction.commit();
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<std::vector<ExperimentActivityDto>> PostgresExperimentRepository::activity(
    ExperimentRunId run_id, std::uint64_t after_sequence, std::size_t limit) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(R"(
      SELECT sequence, occurred_at_ns, level, component, event, message, metrics, details,
             schema_version
      FROM experiment_activity_logs
      WHERE run_id = $1 AND sequence > $2
      ORDER BY sequence
      LIMIT $3
    )",
                                       pqxx::params{run_id.value, after_sequence, limit});
    std::vector<ExperimentActivityDto> result;
    result.reserve(rows.size());
    for (const auto& row : rows) {
      std::map<std::string, double> metrics;
      const auto parsed_metrics = Json::parse(row["metrics"].as<std::string>());
      for (const auto& [name, value] : parsed_metrics.items()) {
        metrics.emplace(name, value.get<double>());
      }
      result.push_back(ExperimentActivityDto{
          .schema_version = row["schema_version"].as<int>(),
          .run_id = run_id,
          .sequence = row["sequence"].as<std::uint64_t>(),
          .occurred_at_ns = row["occurred_at_ns"].as<std::uint64_t>(),
          .level = row["level"].as<std::string>(),
          .component = row["component"].as<std::string>(),
          .event = row["event"].as<std::string>(),
          .message = row["message"].as<std::string>(),
          .metrics = std::move(metrics),
          .details = activityDetails(Json::parse(row["details"].as<std::string>())),
      });
    }
    return result;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::createPerformanceExperiment(
    const PerformanceLaunchRequest& request) {
  try {
    const auto generated = boost::uuids::to_string(boost::uuids::random_generator()());
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"INSERT INTO performance_experiments("}
            + "id, topology, starting_model_id, model_repository, model_revision, "
              "dataset_repository, dataset_revision, retrieval_backend, creator_count, "
              "seeded_articles, target_created_babels, concurrent_users, "
              "recommendation_start_probability, continuation_probability, "
              "maximum_traversal_depth, maximum_requests_per_traversal, "
              "training_micro_batch_size, sync_every_steps, "
              "interleave_creation_and_recommendations, auto_advance, warmup_seconds, "
              "duration_seconds, target_rps, latency_safety_threshold_ms) VALUES ("
              "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,"
              "$19,$20,$21,$22,$23,$24) RETURNING " +
            kPerformanceColumns,
        pqxx::params{
            generated, std::string{performanceTopologyName(request.topology)},
            request.starting_model_id.value, request.model_repository,
            request.model_revision, request.dataset_repository, request.dataset_revision,
            std::string{retrievalBackendName(request.retrieval_backend)},
            static_cast<std::int64_t>(request.creator_count),
            static_cast<std::int64_t>(request.seeded_articles),
            static_cast<std::int64_t>(request.target_created_babels),
            static_cast<std::int64_t>(request.concurrent_users),
            request.recommendation_start_probability, request.continuation_probability,
            static_cast<std::int64_t>(request.maximum_traversal_depth),
            static_cast<std::int64_t>(request.maximum_requests_per_traversal),
            static_cast<std::int64_t>(request.training_micro_batch_size),
            static_cast<std::int64_t>(request.sync_every_steps),
            request.interleave_creation_and_recommendations, request.auto_advance,
            static_cast<std::int64_t>(request.warmup_seconds),
            static_cast<std::int64_t>(request.duration_seconds), request.target_rps,
            request.latency_safety_threshold_ms});

    const std::vector<std::string_view> topologies =
        request.creator_count == 50
            ? std::vector<std::string_view>{"same_process", "same_host_split",
                                            "same_host_isolated"}
            : std::vector<std::string_view>{"same_process", "same_host_split"};
    for (std::size_t topology_index = 0; topology_index < topologies.size(); ++topology_index) {
      for (std::size_t mode = 0; mode < 3; ++mode) {
        const auto condition_id =
            boost::uuids::to_string(boost::uuids::random_generator()());
        const auto condition_index = topology_index * 3 + mode + 1;
        const bool training_enabled = mode >= 1;
        const bool synchronization_enabled = mode == 2;
        const auto config = Json{{"schemaVersion", 1},
                                 {"experimentId", generated},
                                 {"conditionIndex", condition_index},
                                 {"topology", topologies[topology_index]},
                                 {"trainingEnabled", training_enabled},
                                 {"synchronizationEnabled", synchronization_enabled},
                                 {"trainingMicroBatchSize", request.training_micro_batch_size},
                                 {"syncEverySteps", request.sync_every_steps}}
                                .dump();
        transaction.exec(R"(
          INSERT INTO performance_conditions(
            id, experiment_id, condition_index, topology, training_enabled,
            synchronization_enabled, launch_config, launch_sha256
          ) VALUES ($1,$2,$3,$4,$5,$6,CAST($7 AS jsonb),$8)
        )", pqxx::params{condition_id, generated,
                         static_cast<std::int64_t>(condition_index),
                         topologies[topology_index], training_enabled,
                         synchronization_enabled, config, sha256(config)});
      }
    }
    const auto condition_count = topologies.size() * 3;
    transaction.exec(R"(
      INSERT INTO performance_progress_snapshots(
        experiment_id, sequence, phase, condition_count
      ) VALUES ($1, 0, 'population', $2)
    )", pqxx::params{generated, condition_count});
    transaction.commit();
    auto result = performanceFromRow(rows.one_row());
    result.progress = PerformanceProgressDto{.condition_count = condition_count};
    return result;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<std::vector<PerformanceExperimentDto>>
PostgresExperimentRepository::listPerformanceExperiments(
    std::size_t limit, std::optional<std::string> before) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    pqxx::result rows;
    if (before) {
      rows = transaction.exec(std::string{"SELECT "} + kPerformanceColumns +
                                  " FROM performance_experiments "
                                  "WHERE created_at < CAST($1 AS timestamptz) "
                                  "ORDER BY created_at DESC, id DESC LIMIT $2",
                              pqxx::params{*before, limit});
    } else {
      rows = transaction.exec(std::string{"SELECT "} + kPerformanceColumns +
                                  " FROM performance_experiments "
                                  "ORDER BY created_at DESC, id DESC LIMIT $1",
                              pqxx::params{limit});
    }
    std::vector<PerformanceExperimentDto> result;
    result.reserve(rows.size());
    for (const auto& row : rows) result.push_back(performanceFromRow(row));
    return result;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::getPerformanceExperiment(
    std::string_view experiment_id) {
  try {
    auto connection = database_.connect();
    pqxx::read_transaction transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"SELECT "} + kPerformanceColumns +
            " FROM performance_experiments WHERE id = $1",
        pqxx::params{experiment_id});
    if (rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "performance experiment not found"});
    }
    auto result = performanceFromRow(rows.one_row());
    attachLatestProgress(transaction, result);
    attachPerformanceResults(transaction, result);
    return result;
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::requestPerformanceGracefulStop(
    std::string_view experiment_id) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto existing_rows = transaction.exec(
        std::string{"SELECT "} + kPerformanceColumns +
            " FROM performance_experiments WHERE id = $1 FOR UPDATE",
        pqxx::params{experiment_id});
    if (existing_rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "performance experiment not found"});
    }
    const auto existing = performanceFromRow(existing_rows.one_row());
    if (existing.status == PerformanceExperimentStatus::stop_requested) {
      transaction.commit();
      return existing;
    }
    if (existing.status != PerformanceExperimentStatus::population_pending &&
        existing.status != PerformanceExperimentStatus::population_ready &&
        existing.status != PerformanceExperimentStatus::approved &&
        existing.status != PerformanceExperimentStatus::running) {
      return tl::make_unexpected(ApplicationError{
          ErrorCode::conflict, "performance experiment cannot be gracefully stopped"});
    }
    const auto rows = transaction.exec(
        std::string{"UPDATE performance_experiments SET status = 'stop_requested' "}
            + "WHERE id = $1 RETURNING " +
            kPerformanceColumns,
        pqxx::params{experiment_id});
    transaction.commit();
    return performanceFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::approvePerformanceNextScale(
    std::string_view experiment_id) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    auto existing_rows = transaction.exec(
        std::string{"SELECT "} + kPerformanceColumns +
            " FROM performance_experiments WHERE id = $1 FOR UPDATE",
        pqxx::params{experiment_id});
    if (existing_rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "performance experiment not found"});
    }
    auto existing = performanceFromRow(existing_rows.one_row());
    if (!existing.population_ready ||
        existing.population_vector_count != existing.launch.target_created_babels ||
        !existing.population_vector_sha256 || !existing.population_model_sha256 ||
        !existing.population_dataset_sha256) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::conflict, "population evidence is not ready"});
    }
    if (existing.operator_approved &&
        existing.status == PerformanceExperimentStatus::approved) {
      transaction.commit();
      return existing;
    }
    transaction.exec(R"(
      INSERT INTO performance_approvals(
        experiment_id, approval_sequence, action, population_vector_count,
        population_vector_sha256
      )
      SELECT $1, COALESCE(MAX(approval_sequence), 0) + 1,
             'approve_next_scale', $2, $3
      FROM performance_approvals WHERE experiment_id = $1
    )", pqxx::params{experiment_id, existing.population_vector_count,
                     *existing.population_vector_sha256});
    const auto updated_rows = transaction.exec(
        std::string{"UPDATE performance_experiments "}
            + "SET operator_approved = true, status = 'approved' WHERE id = $1 RETURNING " +
            kPerformanceColumns,
        pqxx::params{experiment_id});
    transaction.commit();
    return performanceFromRow(updated_rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::markPerformancePopulationReady(
    std::string_view experiment_id, const PerformancePopulationEvidence& evidence) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"UPDATE performance_experiments SET "}
            + "status = 'population_ready', population_ready = true, "
              "population_vector_count = $2, population_vector_sha256 = $3, "
              "population_model_repository = $4, population_model_revision = $5, "
              "population_model_sha256 = $6, population_dataset_repository = $7, "
              "population_dataset_revision = $8, population_dataset_sha256 = $9 "
              "WHERE id = $1 AND status = 'population_pending' RETURNING " +
            kPerformanceColumns,
        pqxx::params{experiment_id, evidence.vector_count, evidence.vector_sha256,
                     evidence.model_repository, evidence.model_revision,
                     evidence.model_sha256, evidence.dataset_repository,
                     evidence.dataset_revision, evidence.dataset_sha256});
    if (rows.empty()) {
      return tl::make_unexpected(ApplicationError{
          ErrorCode::conflict, "performance population state has already changed"});
    }
    transaction.commit();
    return performanceFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresExperimentRepository::markPerformanceLaunchFailed(
    std::string_view experiment_id, std::string_view message) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto rows = transaction.exec(R"(
      UPDATE performance_experiments
      SET status = 'failed', failure = $2
      WHERE id = $1
      RETURNING id
    )", pqxx::params{experiment_id, message});
    if (rows.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "performance experiment not found"});
    }
    transaction.commit();
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<PerformanceExperimentDto>
PostgresExperimentRepository::attachPerformanceArtifact(
    std::string_view experiment_id, const PerformanceArtifactReceipt& receipt) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto rows = transaction.exec(
        std::string{"UPDATE performance_experiments SET "}
            + "artifact_sha256 = $2, remote_hf_commit_sha = $3, "
              "remote_hf_bundle_path = $4 WHERE id = $1 AND ("
              "remote_hf_commit_sha IS NULL OR (artifact_sha256 = $2 AND "
              "remote_hf_commit_sha = $3 AND remote_hf_bundle_path = $4)) RETURNING " +
            kPerformanceColumns,
        pqxx::params{experiment_id, receipt.artifact_sha256,
                     receipt.remote_hf_commit_sha, receipt.remote_hf_bundle_path});
    if (rows.empty()) {
      return tl::make_unexpected(ApplicationError{
          ErrorCode::conflict, "saved trial already has a different remote artifact"});
    }
    transaction.commit();
    return performanceFromRow(rows.one_row());
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresExperimentRepository::appendPerformanceProgress(
    std::string_view experiment_id, const PerformanceProgressDto& progress) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    const auto existing = transaction.exec(
        "SELECT id FROM performance_experiments WHERE id = $1 FOR UPDATE",
        pqxx::params{experiment_id});
    if (existing.empty()) {
      return tl::make_unexpected(
          ApplicationError{ErrorCode::not_found, "performance experiment not found"});
    }
    transaction.exec(R"(
      INSERT INTO performance_progress_snapshots(
        experiment_id, sequence, phase, condition_index, condition_count,
        seeded_articles, created_babels, indexed_babels, requested, completed,
        elapsed_seconds, recent_rate, draining, telemetry
      )
      SELECT $1, COALESCE(MAX(sequence), -1) + 1, $2, $3, $4,
             $5, $6, $7, $8, $9, $10, $11, $12, CAST($13 AS jsonb)
      FROM performance_progress_snapshots WHERE experiment_id = $1
    )", pqxx::params{experiment_id, progress.phase, progress.condition_index,
                     progress.condition_count, progress.seeded_articles,
                     progress.created_babels, progress.indexed_babels,
                     progress.requested, progress.completed,
                     progress.elapsed_seconds, progress.recent_rate,
                     progress.draining, progress.telemetry_json});
    transaction.commit();
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

Result<void> PostgresExperimentRepository::savePerformanceResult(
    std::string_view experiment_id, const PerformanceResultDto& result) {
  try {
    auto connection = database_.connect();
    pqxx::work transaction(*connection);
    transaction.exec(R"(
      INSERT INTO performance_results(
        experiment_id, condition_id, raw_evidence, evidence_sha256,
        serving_p95_ms, training_p95_ms, full_p95_ms,
        itraining, ifull, iactivation_increment
      ) VALUES ($1,$2,CAST($3 AS jsonb),$4,$5,$6,$7,$8,$9,$10)
    )", pqxx::params{experiment_id, result.condition_id, result.raw_evidence_json,
                     result.evidence_sha256, result.serving_p95_ms,
                     result.training_p95_ms, result.full_p95_ms, result.itraining,
                     result.ifull, result.iactivation_increment});
    transaction.commit();
    return {};
  } catch (const std::exception& exception) {
    return tl::make_unexpected(mapPostgresError(exception));
  }
}

}  // namespace babel
