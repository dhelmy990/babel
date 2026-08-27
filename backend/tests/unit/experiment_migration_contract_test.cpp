#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

TEST_CASE("online experiment migration freezes launch identity and source reuse") {
  const auto migration_path =
      std::filesystem::path(__FILE__).parent_path().parent_path().parent_path() /
      "migrations/005_online_experiment.sql";
  std::ifstream migration_file(migration_path);
  REQUIRE(migration_file.good());

  const std::string migration{std::istreambuf_iterator<char>{migration_file}, {}};
  CHECK(migration.find("CREATE TABLE recommender_models") != std::string::npos);
  CHECK(migration.find("CREATE TABLE experiment_runs") != std::string::npos);
  CHECK(migration.find("CREATE TABLE experiment_babels") != std::string::npos);
  CHECK(migration.find("CREATE TABLE experiment_activity_logs") != std::string::npos);
  CHECK(migration.find("UNIQUE (run_id, creator_id, source_article_key)") !=
        std::string::npos);
  CHECK(migration.find("retrieval_backend IN ('pgvector', 'hnswlib')") !=
        std::string::npos);
  CHECK(migration.find("prevent_experiment_launch_mutation") != std::string::npos);
  CHECK(migration.find("prevent_recommender_model_mutation") != std::string::npos);
  CHECK(migration.find("INSERT INTO recommender_models") == std::string::npos);
}

TEST_CASE("online runtime migration gives pgvector and the worker durable state") {
  const auto migration_path =
      std::filesystem::path(__FILE__).parent_path().parent_path().parent_path() /
      "migrations/006_online_runtime.sql";
  std::ifstream migration_file(migration_path);
  REQUIRE(migration_file.good());

  const std::string migration{std::istreambuf_iterator<char>{migration_file}, {}};
  CHECK(migration.find("CREATE TABLE babel_embeddings") != std::string::npos);
  CHECK(migration.find("embedding public.vector(100)") != std::string::npos);
  CHECK(migration.find("USING hnsw") != std::string::npos);
  CHECK(migration.find("CREATE TABLE run_embedding_states") != std::string::npos);
  CHECK(migration.find("finalized_at") != std::string::npos);
  CHECK(migration.find("INSERT INTO recommender_models") == std::string::npos);
}

TEST_CASE("scaled experiment migration persists schedules and directed canonical edges") {
  const auto migration_path =
      std::filesystem::path(__FILE__).parent_path().parent_path().parent_path() /
      "migrations/007_scaled_experiment.sql";
  std::ifstream migration_file(migration_path);
  REQUIRE(migration_file.good());

  const std::string migration{std::istreambuf_iterator<char>{migration_file}, {}};
  CHECK(migration.find("CREATE TABLE experiment_work_schedule") != std::string::npos);
  CHECK(migration.find("CREATE TABLE experiment_edges") != std::string::npos);
  CHECK(migration.find("CREATE TABLE experiment_traversal_rolls") !=
        std::string::npos);
  CHECK(migration.find("ADD COLUMN schema_version integer NOT NULL DEFAULT 1") !=
        std::string::npos);
  CHECK(migration.find("draw_value double precision NOT NULL") !=
        std::string::npos);
  CHECK(migration.find("roll_succeeded boolean NOT NULL") !=
        std::string::npos);
  CHECK(migration.find("outcome text NOT NULL") != std::string::npos);
  CHECK(migration.find("feedback_occurred_at_ns bigint NOT NULL") != std::string::npos);
  CHECK(migration.find("PRIMARY KEY (run_id, source_babel_id, target_babel_id)") !=
        std::string::npos);
  CHECK(migration.find("FOREIGN KEY (run_id, source_babel_id)") != std::string::npos);
  CHECK(migration.find("FOREIGN KEY (run_id, target_babel_id)") != std::string::npos);
  CHECK(migration.find("REFERENCES users") == std::string::npos);
  CHECK(migration.find("NEW.concurrent_users IS DISTINCT FROM OLD.concurrent_users") !=
        std::string::npos);
  CHECK(migration.find("NEW.continuation_probability IS DISTINCT FROM OLD.continuation_probability") !=
        std::string::npos);
  CHECK(migration.find("experiment_work_schedule_immutable") != std::string::npos);
  CHECK(migration.find("experiment_traversal_rolls_immutable") !=
        std::string::npos);
}

TEST_CASE("runtime readiness requires every migration through scaled experiment version seven") {
  const auto application_path =
      std::filesystem::path(__FILE__).parent_path().parent_path().parent_path() /
      "src/runtime/application.cpp";
  std::ifstream application_file(application_path);
  REQUIRE(application_file.good());

  const std::string source{std::istreambuf_iterator<char>{application_file}, {}};
  CHECK(source.find("SELECT count(*) = 7 FROM schema_migrations") !=
        std::string::npos);
  CHECK(source.find("version IN ('1', '2', '3', '4', '5', '6', '7')") !=
        std::string::npos);
}
