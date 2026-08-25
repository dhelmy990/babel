#include "babel/adapters/postgres/experiment_repository.hpp"

#include <array>
#include <iomanip>
#include <sstream>
#include <string>
#include <utility>

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
      SELECT sequence, occurred_at_ns, level, component, event, message, metrics, details
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
          .schema_version = 1,
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

}  // namespace babel
