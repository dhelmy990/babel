#include <catch2/catch_test_macros.hpp>

#include <string>

#include "babel/runtime/experiment_job_runner.hpp"

TEST_CASE("experiment job runner forwards only run identity to its worker callbacks") {
  const auto run_id = babel::ExperimentRunId::parse(
      "77777777-7777-5777-8777-777777777777").value();
  std::string started;
  std::string stopped;
  babel::ExperimentJobRunner runner(
      [&](babel::ExperimentRunId id) -> babel::Result<void> {
        started = id.value;
        return {};
      },
      [&](babel::ExperimentRunId id) -> babel::Result<void> {
        stopped = id.value;
        return {};
      });

  REQUIRE(runner.start(run_id).has_value());
  REQUIRE(runner.requestGracefulStop(run_id).has_value());
  CHECK(started == run_id.value);
  CHECK(stopped == run_id.value);
}
TEST_CASE("experiment job runner reports an unavailable integration without throwing") {
  const auto run_id = babel::ExperimentRunId::parse(
      "77777777-7777-5777-8777-777777777777").value();
  babel::ExperimentJobRunner runner({}, {});

  const auto started = runner.start(run_id);
  const auto stopped = runner.requestGracefulStop(run_id);

  REQUIRE_FALSE(started.has_value());
  REQUIRE_FALSE(stopped.has_value());
  CHECK(started.error().code == babel::ErrorCode::database_unavailable);
  CHECK(stopped.error().code == babel::ErrorCode::database_unavailable);
}
