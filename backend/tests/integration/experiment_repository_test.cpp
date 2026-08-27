#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <memory>
#include <array>
#include <random>
#include <set>
#include <iomanip>
#include <sstream>
#include <string>

#include <pqxx/pqxx>
#include <nlohmann/json.hpp>
#include <openssl/evp.h>

#include "babel/adapters/postgres/experiment_repository.hpp"
#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"

namespace {

std::string sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  REQUIRE(EVP_Digest(value.data(), value.size(), digest.data(), &size, EVP_sha256(), nullptr) ==
          1);
  std::ostringstream encoded;
  encoded << std::hex << std::setfill('0');
  for (unsigned int index = 0; index < size; ++index) {
    encoded << std::setw(2) << static_cast<unsigned int>(digest[index]);
  }
  return encoded.str();
}

std::string baseUrl() {
  if (const auto* configured = std::getenv("BABEL_TEST_DATABASE_URL")) return configured;
  return "postgresql://babel:babel-local-dev@127.0.0.1:54329/babel";
}

class ExperimentPostgresFixture {
 public:
  ExperimentPostgresFixture()
      : base_(baseUrl()),
        schema_("babel_test_experiment_" + std::to_string(std::random_device{}())),
        database_(schemaUrl()),
        migrations_(database_),
        repository_(database_) {
    pqxx::connection connection(base_);
    pqxx::work transaction(connection);
    transaction.exec("CREATE SCHEMA " + transaction.quote_name(schema_));
    transaction.commit();
    REQUIRE(migrations_.run().has_value());
    insertOriginal();
  }

  ~ExperimentPostgresFixture() {
    try {
      pqxx::connection connection(base_);
      pqxx::work transaction(connection);
      transaction.exec("DROP SCHEMA IF EXISTS " + transaction.quote_name(schema_) + " CASCADE");
      transaction.commit();
    } catch (...) {
    }
  }

  babel::ExperimentLaunchSnapshot launch() const {
    return babel::ExperimentLaunchSnapshot{
        .request = babel::ExperimentLaunchRequest{
            .starting_model_id = model_id_,
            .retrieval_backend = babel::RetrievalBackend::pgvector,
            .creator_count = 50,
            .scenario = babel::ExperimentScenario::june_to_july,
            .event_budget_per_month = 100,
            .run_seed = 7,
        },
        .source = babel::ExperimentSourcePin{
            .repository = "dhelmy990/babel-wikipedia-experiment",
            .configuration = "demo_crosswalk",
            .commit_sha = std::string(40, 'c'),
        },
        .environment_sequence = {"2026-06", "2026-07"},
    };
  }

 protected:
  std::string schemaUrl() const {
    return base_ + (base_.find('?') == std::string::npos ? '?' : '&') +
           "options=-csearch_path%3D" + schema_;
  }

  void insertOriginal() {
    pqxx::connection connection(schemaUrl());
    pqxx::work transaction(connection);
    transaction.exec(R"(
      INSERT INTO recommender_models(
        id, label, encoder_repo, encoder_revision, dataset_repo, dataset_revision,
        environment_sequence, training_examples, checkpoint_path, checkpoint_sha256,
        embedding_space
      ) VALUES ($1, 'Original 2016 baseline',
        'dhelmy990/babel-two-tower-recommender', $2,
        'dhelmy990/babel-wikipedia-experiment', $3,
        '["2026-06"]'::jsonb, 1000, 'models/original/checkpoint.safetensors', $4,
        '{"schemaVersion":1,"embeddingSpaceId":"66666666-6666-5666-8666-666666666666","dimension":100,"distance":"cosine","distilledEncoderArtifact":"dhelmy990/babel-two-tower-recommender@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","datasetRevision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","compatibilityVersion":"babel-embedding-v1"}'::jsonb)
    )",
                     pqxx::params{model_id_.value, std::string(40, 'a'),
                                  std::string(40, 'b'), std::string(64, 'e')});
    transaction.commit();
  }

  std::string base_;
  std::string schema_;
  babel::PostgresDatabase database_;
  babel::MigrationRunner migrations_;
  babel::PostgresExperimentRepository repository_;
  babel::RecommenderModelId model_id_{
      babel::RecommenderModelId::parse("11111111-1111-5111-8111-111111111111").value()};
};

}  // namespace

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "experiment repository persists immutable launch and rejects another active run") {
  const auto first = repository_.createRun(launch());
  REQUIRE(first.has_value());
  CHECK(first->status == babel::ExperimentStatus::starting);
  CHECK(first->creator_count == 50);
  CHECK(first->environment_sequence ==
        std::vector<std::string>{"2026-06", "2026-07"});

  pqxx::connection connection(schemaUrl());
  pqxx::read_transaction transaction(connection);
  const auto row = transaction.exec(
      "SELECT dataset_repository, dataset_config, dataset_revision, run_seed, "
      "launch_config, launch_sha256 FROM experiment_runs WHERE id = $1",
      pqxx::params{first->run_id.value}).one_row();
  const auto launch_config = nlohmann::json::parse(row["launch_config"].as<std::string>());
  CHECK(row["dataset_repository"].as<std::string>() ==
        "dhelmy990/babel-wikipedia-experiment");
  CHECK(row["dataset_config"].as<std::string>() == "demo_crosswalk");
  CHECK(row["dataset_revision"].as<std::string>() == std::string(40, 'c'));
  CHECK(row["run_seed"].as<std::uint64_t>() == 7);
  CHECK(row["launch_sha256"].as<std::string>().size() == 64);
  CHECK(row["launch_sha256"].as<std::string>() == sha256(launch_config.dump()));
  CHECK(launch_config.at("schemaVersion") == 1);
  CHECK(launch_config.at("datasetConfig") == "demo_crosswalk");
  CHECK(launch_config.at("runSeed") == 7);
  CHECK(launch_config.at("embeddingDimension") == 100);
  CHECK(launch_config.at("perMonthEventBudget").at("2026-07") == 100);

  const auto second = repository_.createRun(launch());
  REQUIRE_FALSE(second.has_value());
  CHECK(second.error().code == babel::ErrorCode::conflict);

  const auto stopped = repository_.requestGracefulStop(first->run_id);
  REQUIRE(stopped.has_value());
  CHECK(stopped->status == babel::ExperimentStatus::stop_requested);
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "model registry rows cannot be overwritten or deleted") {
  const auto models = repository_.listModels();
  REQUIRE(models.has_value());
  REQUIRE(models->size() == 1);
  CHECK(models->front().label == "Original 2016 baseline");
  CHECK(models->front().immutable);
  CHECK(models->front().compatible);

  const auto update = [&] {
    pqxx::connection connection(schemaUrl());
    pqxx::work transaction(connection);
    transaction.exec("UPDATE recommender_models SET label = 'changed'");
    transaction.commit();
  };
  const auto remove = [&] {
    pqxx::connection connection(schemaUrl());
    pqxx::work transaction(connection);
    transaction.exec("DELETE FROM recommender_models");
    transaction.commit();
  };
  CHECK_THROWS(update());
  CHECK_THROWS(remove());
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "one experiment creator cannot reuse one source article") {
  const auto run = repository_.createRun(launch()).value();
  const auto creator = "33333333-3333-5333-8333-333333333333";
  const auto insert = [&](std::string_view babel_id) {
    pqxx::connection connection(schemaUrl());
    pqxx::work transaction(connection);
    transaction.exec(R"(
      INSERT INTO experiment_babels(run_id, babel_id, creator_id, source_article_key, title)
      VALUES ($1, $2, $3, 'enwiki:42', 'Experiment Babel')
    )",
                     pqxx::params{run.run_id.value, babel_id, creator});
    transaction.commit();
  };
  insert("44444444-4444-5444-8444-444444444444");
  CHECK_THROWS(insert("55555555-5555-5555-8555-555555555555"));
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "persisted online activity can be read for the dashboard") {
  const auto run = repository_.createRun(launch()).value();
  pqxx::connection connection(schemaUrl());
  pqxx::work transaction(connection);
  transaction.exec(R"(
    INSERT INTO experiment_activity_logs(
      run_id, sequence, occurred_at_ns, level, component, event, message, metrics, details,
      schema_version
    ) VALUES
      ($1, 1, 1787686757789485714, 'info', 'serving',
       'recommendation_completed', 'Creator created a Babel.',
       '{"annNs":9859276,"clientTotalNs":42690337,"serverTotalNs":10604546}'::jsonb,
       '{"kind":"recommendation","creatorId":"33333333-3333-5333-8333-333333333333",'
       '"newBabelId":"44444444-4444-5444-8444-444444444444",'
       '"newBabelTitle":"Virtual memory",'
       '"candidateBabelIds":["55555555-5555-5555-8555-555555555555"],'
       '"includeBabelIds":["55555555-5555-5555-8555-555555555555"],'
       '"excludeBabelIds":[],"ignoreBabelIds":[],"acceptedEdgeCount":1,'
       '"modelId":"11111111-1111-5111-8111-111111111111","modelVersion":10,'
       '"requestId":"66666666-6666-5666-8666-666666666666",'
       '"traversalSessionId":"77777777-7777-5777-8777-777777777777",'
       '"sourceVectorOrigin":"pgvector_load"}'::jsonb, 2),
      ($1, 2, 1787686757808909079, 'info', 'training',
       'online_training_progress', 'Online trainer reached step 10.',
       '{"stepTimeMs":1.174327}'::jsonb,
       '{"kind":"training","trainerStep":10,"rollingRankLoss":0.6905}'::jsonb, 1)
  )",
                   pqxx::params{run.run_id.value});
  transaction.commit();

  const auto activity = repository_.activity(run.run_id, 0, 10);

  REQUIRE(activity.has_value());
  REQUIRE(activity->size() == 2);
  CHECK(activity->at(0).metrics.at("clientTotalNs") == 42690337.0);
  CHECK(activity->at(0).schema_version == 2);
  const auto& recommendation =
      std::get<babel::ExperimentRecommendationActivityDto>(activity->at(0).details);
  CHECK(recommendation.model_version == 10);
  CHECK(recommendation.accepted_edge_count == 1);
  CHECK(recommendation.request_id == "66666666-6666-5666-8666-666666666666");
  CHECK(recommendation.traversal_session_id ==
        "77777777-7777-5777-8777-777777777777");
  CHECK(recommendation.source_vector_origin == "pgvector_load");
  CHECK(activity->at(1).metrics.at("stepTimeMs") == 1.174327);
  const auto& training =
      std::get<babel::ExperimentTrainingActivityDto>(activity->at(1).details);
  CHECK(training.trainer_step == 10);
  CHECK(training.rolling_rank_loss == 0.6905);
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "startup recovery interrupts an experiment left active") {
  const auto run = repository_.createRun(launch()).value();
  REQUIRE(repository_.markInterruptedRuns().has_value());
  const auto recovered = repository_.getRun(run.run_id);
  REQUIRE(recovered.has_value());
  CHECK(recovered->status == babel::ExperimentStatus::interrupted);
  CHECK(recovered->failure == "backend restarted before experiment completed");
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "performance trials save nine conditions and require frozen population evidence") {
  const babel::PerformanceLaunchRequest launch{
      .starting_model_id = model_id_,
  };
  const auto created = repository_.createPerformanceExperiment(launch);
  REQUIRE(created.has_value());
  CHECK(created->status == babel::PerformanceExperimentStatus::population_pending);
  CHECK(created->launch.topology == babel::PerformanceTopology::same_host_split);
  CHECK(created->launch.target_created_babels == 10000);
  REQUIRE(created->progress.has_value());
  CHECK(created->progress->condition_count == 9);
  REQUIRE(repository_.appendPerformanceProgress(
      created->experiment_id,
      babel::PerformanceProgressDto{
          .phase = "measuring", .condition_index = 1, .condition_count = 9,
          .seeded_articles = 10000, .created_babels = 100,
          .indexed_babels = 90, .requested = 20, .completed = 18,
          .elapsed_seconds = 10, .recent_rate = 10, .draining = false,
          .telemetry_json = R"({"kafkaLag":2})",
      }).has_value());

  pqxx::connection connection(schemaUrl());
  pqxx::work transaction(connection);
  CHECK(transaction.exec(
      "SELECT count(*) FROM performance_conditions WHERE experiment_id = $1",
      pqxx::params{created->experiment_id}).one_field().as<int>() == 9);
  CHECK_THROWS(transaction.exec(
      "UPDATE performance_experiments SET topology = 'same_process' WHERE id = $1",
      pqxx::params{created->experiment_id}));
  transaction.abort();

  const auto blocked = repository_.approvePerformanceNextScale(created->experiment_id);
  REQUIRE_FALSE(blocked.has_value());
  CHECK(blocked.error().code == babel::ErrorCode::conflict);

  const auto ready = repository_.markPerformancePopulationReady(
      created->experiment_id,
      babel::PerformancePopulationEvidence{
          .vector_count = 10000,
          .vector_sha256 = std::string(64, 'a'),
          .model_repository = "dhelmy990/babel-qwen-navigation-2016-interview",
          .model_revision = "57d949cd634b920cc1a46f27c9b21df094b5240e",
          .model_sha256 = std::string(64, 'b'),
          .dataset_repository = "dhelmy990/babel-wikipedia-experiment",
          .dataset_revision = "0d1ab2c7f0e2295682288fcf10077d2d776bf559",
          .dataset_sha256 = std::string(64, 'c'),
      });
  REQUIRE(ready.has_value());

  pqxx::connection condition_connection(schemaUrl());
  pqxx::read_transaction condition_read(condition_connection);
  const auto condition_id = condition_read.exec(
      "SELECT id FROM performance_conditions WHERE experiment_id = $1 "
      "ORDER BY condition_index LIMIT 1",
      pqxx::params{created->experiment_id}).one_field().as<std::string>();
  REQUIRE(repository_.savePerformanceResult(
      created->experiment_id,
      babel::PerformanceResultDto{
          .condition_id = condition_id, .condition_index = 1,
          .topology = babel::PerformanceTopology::same_process,
          .training_enabled = false, .synchronization_enabled = false,
          .raw_evidence_json = R"({"requestCount":6})",
          .evidence_sha256 = std::string(64, 'f'), .serving_p95_ms = 10,
          .training_p95_ms = 15, .full_p95_ms = 18,
          .itraining = 1.5, .ifull = 1.8, .iactivation_increment = 1.2,
      }).has_value());

  const auto approved = repository_.approvePerformanceNextScale(created->experiment_id);
  REQUIRE(approved.has_value());
  CHECK(approved->operator_approved);
  CHECK(approved->status == babel::PerformanceExperimentStatus::approved);
  const auto approval_retry =
      repository_.approvePerformanceNextScale(created->experiment_id);
  REQUIRE(approval_retry.has_value());
  CHECK(approval_retry->status == babel::PerformanceExperimentStatus::approved);
  {
    pqxx::connection approval_connection(schemaUrl());
    pqxx::read_transaction approval_read(approval_connection);
    CHECK(approval_read.exec(
        "SELECT count(*) FROM performance_approvals WHERE experiment_id=$1",
        pqxx::params{created->experiment_id}).one_field().as<int>() == 1);
  }

  const auto stopped =
      repository_.requestPerformanceGracefulStop(created->experiment_id);
  REQUIRE(stopped.has_value());
  const auto stop_retry =
      repository_.requestPerformanceGracefulStop(created->experiment_id);
  REQUIRE(stop_retry.has_value());
  CHECK(stop_retry->status == babel::PerformanceExperimentStatus::stop_requested);

  const auto attached = repository_.attachPerformanceArtifact(
      created->experiment_id,
      babel::PerformanceArtifactReceipt{
          .artifact_sha256 = std::string(64, 'd'),
          .remote_hf_commit_sha = std::string(40, 'e'),
          .remote_hf_bundle_path = "runs/trial/evidence",
      });
  REQUIRE(attached.has_value());
  CHECK(attached->remote_hf_bundle_path == "runs/trial/evidence");
  const auto replacement = repository_.attachPerformanceArtifact(
      created->experiment_id,
      babel::PerformanceArtifactReceipt{
          .artifact_sha256 = std::string(64, 'f'),
          .remote_hf_commit_sha = std::string(40, 'a'),
          .remote_hf_bundle_path = "runs/trial/replacement",
      });
  REQUIRE_FALSE(replacement.has_value());
  CHECK(replacement.error().code == babel::ErrorCode::conflict);

  const auto reloaded = repository_.getPerformanceExperiment(created->experiment_id);
  REQUIRE(reloaded.has_value());
  REQUIRE(reloaded->progress.has_value());
  CHECK(reloaded->progress->phase == "measuring");
  CHECK(reloaded->progress->telemetry_json == R"({"kafkaLag": 2})");
  REQUIRE(reloaded->results.size() == 1);
  CHECK(reloaded->results.front().itraining == 1.5);
  CHECK(reloaded->results.front().ifull == 1.8);
  CHECK(reloaded->results.front().iactivation_increment == 1.2);
  const auto listed = repository_.listPerformanceExperiments(10, std::nullopt);
  REQUIRE(listed.has_value());
  REQUIRE(listed->size() == 1);
  CHECK(listed->front().experiment_id == created->experiment_id);
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "higher performance cohorts save the selected six-condition matrix") {
  const babel::PerformanceLaunchRequest launch{
      .starting_model_id = model_id_,
      .topology = babel::PerformanceTopology::same_host_split,
      .creator_count = 100,
      .seeded_articles = 10000,
      .target_created_babels = 10000,
      .concurrent_users = 100,
  };
  const auto created = repository_.createPerformanceExperiment(launch);
  REQUIRE(created.has_value());
  REQUIRE(created->progress.has_value());
  CHECK(created->progress->condition_count == 6);

  pqxx::connection connection(schemaUrl());
  pqxx::read_transaction transaction(connection);
  const auto rows = transaction.exec(R"(
    SELECT topology, training_enabled, synchronization_enabled
    FROM performance_conditions WHERE experiment_id=$1
    ORDER BY condition_index
  )", pqxx::params{created->experiment_id});
  REQUIRE(rows.size() == 6);
  std::set<std::string> topologies;
  for (const auto& row : rows) topologies.insert(row["topology"].as<std::string>());
  CHECK(topologies == std::set<std::string>{"same_process", "same_host_split"});
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "performance stop is durable and retryable throughout population and execution") {
  const std::vector<std::string> stoppable_statuses{
      "population_pending", "population_ready", "approved", "running"};

  for (const auto& status : stoppable_statuses) {
    const auto created = repository_.createPerformanceExperiment(
        babel::PerformanceLaunchRequest{.starting_model_id = model_id_});
    REQUIRE(created.has_value());
    {
      pqxx::connection connection(schemaUrl());
      pqxx::work transaction(connection);
      transaction.exec(
          "UPDATE performance_experiments SET status=$2 WHERE id=$1",
          pqxx::params{created->experiment_id, status});
      transaction.commit();
    }

    const auto stopped =
        repository_.requestPerformanceGracefulStop(created->experiment_id);
    REQUIRE(stopped.has_value());
    CHECK(stopped->status == babel::PerformanceExperimentStatus::stop_requested);
    const auto retried =
        repository_.requestPerformanceGracefulStop(created->experiment_id);
    REQUIRE(retried.has_value());
    CHECK(retried->status == babel::PerformanceExperimentStatus::stop_requested);
    const auto reloaded =
        repository_.getPerformanceExperiment(created->experiment_id);
    REQUIRE(reloaded.has_value());
    CHECK(reloaded->status == babel::PerformanceExperimentStatus::stop_requested);
  }
}

TEST_CASE_METHOD(ExperimentPostgresFixture,
                 "performance execution identities and launch failure are durable") {
  const auto trial = repository_.createPerformanceExperiment(
      babel::PerformanceLaunchRequest{.starting_model_id = model_id_}).value();
  const auto population_run = repository_.createRun(launch()).value();

  pqxx::connection connection(schemaUrl());
  pqxx::work transaction(connection);
  const auto condition_id = transaction.exec(
      "SELECT id FROM performance_conditions WHERE experiment_id=$1 "
      "ORDER BY condition_index LIMIT 1",
      pqxx::params{trial.experiment_id}).one_field().as<std::string>();
  transaction.exec(
      "UPDATE performance_experiments SET run_id=$2, "
      "population_manifest_sha256=$3, population_bundle_path=$4 WHERE id=$1",
      pqxx::params{trial.experiment_id, population_run.run_id.value,
                   std::string(64, 'a'), "runs/population"});
  transaction.exec(
      "UPDATE performance_conditions SET run_id=$3 WHERE experiment_id=$1 AND id=$2",
      pqxx::params{trial.experiment_id, condition_id, population_run.run_id.value});
  transaction.commit();

  {
    pqxx::connection retry_connection(schemaUrl());
    pqxx::work retry(retry_connection);
    retry.exec(
        "UPDATE performance_experiments SET run_id=$2, "
        "population_manifest_sha256=$3, population_bundle_path=$4 WHERE id=$1",
        pqxx::params{trial.experiment_id, population_run.run_id.value,
                     std::string(64, 'a'), "runs/population"});
    retry.exec(
        "UPDATE performance_conditions SET run_id=$3 "
        "WHERE experiment_id=$1 AND id=$2",
        pqxx::params{trial.experiment_id, condition_id, population_run.run_id.value});
    retry.commit();
  }

  const auto conflict = [&] {
    pqxx::connection changed_connection(schemaUrl());
    pqxx::work changed(changed_connection);
    changed.exec(
        "UPDATE performance_experiments SET population_bundle_path='runs/other' "
        "WHERE id=$1", pqxx::params{trial.experiment_id});
    changed.commit();
  };
  CHECK_THROWS(conflict());

  const auto duplicate_condition_run = [&] {
    pqxx::connection duplicate_connection(schemaUrl());
    pqxx::work duplicate(duplicate_connection);
    duplicate.exec(
        "UPDATE performance_conditions SET run_id=$2 WHERE experiment_id=$1 "
        "AND id<>$3",
        pqxx::params{trial.experiment_id, population_run.run_id.value, condition_id});
    duplicate.commit();
  };
  CHECK_THROWS(duplicate_condition_run());

  {
    pqxx::connection completed_connection(schemaUrl());
    pqxx::work completed(completed_connection);
    completed.exec("UPDATE experiment_runs SET status='completed' WHERE id=$1",
                   pqxx::params{population_run.run_id.value});
    completed.commit();
  }
  const auto other_run = repository_.createRun(launch()).value();
  const auto conflicting_condition_run = [&] {
    pqxx::connection changed_connection(schemaUrl());
    pqxx::work changed(changed_connection);
    changed.exec(
        "UPDATE performance_conditions SET run_id=$2 WHERE id=$1",
        pqxx::params{condition_id, other_run.run_id.value});
    changed.commit();
  };
  CHECK_THROWS(conflicting_condition_run());

  REQUIRE(repository_.markPerformanceLaunchFailed(
      trial.experiment_id, "performance worker unavailable").has_value());
  const auto failed = repository_.getPerformanceExperiment(trial.experiment_id);
  REQUIRE(failed.has_value());
  CHECK(failed->status == babel::PerformanceExperimentStatus::failed);
  CHECK(failed->failure == "performance worker unavailable");
  CHECK(failed->run_id == population_run.run_id);
  CHECK(failed->population_manifest_sha256 == std::string(64, 'a'));
  CHECK(failed->population_bundle_path == "runs/population");
}
