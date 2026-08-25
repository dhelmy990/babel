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
