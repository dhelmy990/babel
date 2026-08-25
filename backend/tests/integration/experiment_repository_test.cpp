#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <memory>
#include <random>
#include <string>

#include <pqxx/pqxx>
#include <nlohmann/json.hpp>

#include "babel/adapters/postgres/experiment_repository.hpp"
#include "babel/adapters/postgres/migration_runner.hpp"
#include "babel/adapters/postgres/postgres_database.hpp"

namespace {

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
            .configuration = "demo_catalog_2026_06",
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
  CHECK(row["dataset_config"].as<std::string>() == "demo_catalog_2026_06");
  CHECK(row["dataset_revision"].as<std::string>() == std::string(40, 'c'));
  CHECK(row["run_seed"].as<std::uint64_t>() == 7);
  CHECK(row["launch_sha256"].as<std::string>().size() == 64);
  CHECK(launch_config.at("schemaVersion") == 1);
  CHECK(launch_config.at("datasetConfig") == "demo_catalog_2026_06");
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
                 "startup recovery interrupts an experiment left active") {
  const auto run = repository_.createRun(launch()).value();
  REQUIRE(repository_.markInterruptedRuns().has_value());
  const auto recovered = repository_.getRun(run.run_id);
  REQUIRE(recovered.has_value());
  CHECK(recovered->status == babel::ExperimentStatus::interrupted);
  CHECK(recovered->failure == "backend restarted before experiment completed");
}
