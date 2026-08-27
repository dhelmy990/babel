#include <catch2/catch_test_macros.hpp>

#include <stdexcept>
#include <string>
#include <vector>

#include "babel/runtime/experiment_job_runner.hpp"
#include "babel/runtime/experiment_worker_http_client.hpp"
#include "babel/runtime/performance_worker_http_client.hpp"

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

TEST_CASE("loopback experiment worker authenticates start and graceful stop commands") {
  const auto run_id = babel::ExperimentRunId::parse(
      "77777777-7777-5777-8777-777777777777").value();
  struct Call {
    std::string url;
    std::string token;
  };
  std::vector<Call> calls;
  babel::ExperimentWorkerHttpClient worker(
      "http://127.0.0.1:8790", std::string(64, 'a'),
      [&](std::string_view url, std::string_view token) -> babel::Result<long> {
        calls.push_back(Call{std::string(url), std::string(token)});
        return 202L;
      });

  REQUIRE(worker.start(run_id).has_value());
  REQUIRE(worker.requestGracefulStop(run_id).has_value());
  REQUIRE(calls.size() == 2);
  CHECK(calls[0].url ==
        "http://127.0.0.1:8790/v1/runs/77777777-7777-5777-8777-777777777777/start");
  CHECK(calls[1].url ==
        "http://127.0.0.1:8790/v1/runs/77777777-7777-5777-8777-777777777777/graceful-stop");
  CHECK(calls[0].token == std::string(64, 'a'));
}

TEST_CASE("loopback experiment worker rejects non-loopback endpoints and failed commands") {
  CHECK_THROWS_AS(
      babel::ExperimentWorkerHttpClient("http://example.com:8790", std::string(64, 'a')),
      std::invalid_argument);
  babel::ExperimentWorkerHttpClient worker(
      "http://127.0.0.1:8790", std::string(64, 'a'),
      [](std::string_view, std::string_view) -> babel::Result<long> { return 503L; });
  const auto result = worker.start(
      babel::ExperimentRunId::parse("77777777-7777-5777-8777-777777777777").value());
  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == babel::ErrorCode::database_unavailable);
}

TEST_CASE("loopback performance worker authenticates all explicit commands") {
  struct Call { std::string url; std::string token; };
  std::vector<Call> calls;
  babel::PerformanceWorkerHttpClient worker(
      "http://127.0.0.1:8792", std::string(64, 'b'),
      [&](std::string_view url, std::string_view token) -> babel::Result<long> {
        calls.push_back({std::string(url), std::string(token)});
        return 202L;
      });

  REQUIRE(worker.start("33333333-3333-5333-8333-333333333333").has_value());
  REQUIRE(worker.requestGracefulStop(
              "33333333-3333-5333-8333-333333333333").has_value());
  REQUIRE(worker.approveNextScale(
              "33333333-3333-5333-8333-333333333333").has_value());
  REQUIRE(worker.prepareRerun(
              "33333333-3333-5333-8333-333333333333",
              babel::PerformanceRerunRequest{
                  .rerun_id = "44444444-4444-5444-8444-444444444444"})
              .has_value());
  REQUIRE(calls.size() == 4);
  CHECK(calls[0].url == "http://127.0.0.1:8792/v1/performance/33333333-3333-5333-8333-333333333333/start");
  CHECK(calls[1].url == "http://127.0.0.1:8792/v1/performance/33333333-3333-5333-8333-333333333333/graceful-stop");
  CHECK(calls[2].url == "http://127.0.0.1:8792/v1/performance/33333333-3333-5333-8333-333333333333/approve-next-scale");
  CHECK(calls[3].url ==
        "http://127.0.0.1:8792/v1/performance/33333333-3333-5333-8333-333333333333/prepare-rerun/44444444-4444-5444-8444-444444444444?matrix=2x3&warmup_seconds=5&duration_seconds=25&target_rps=5");
  CHECK(calls[0].token == std::string(64, 'b'));
}

TEST_CASE("loopback performance worker validates endpoint token and status") {
  CHECK_THROWS_AS(
      babel::PerformanceWorkerHttpClient("http://example.com:8792", std::string(64, 'a')),
      std::invalid_argument);
  CHECK_THROWS_AS(
      babel::PerformanceWorkerHttpClient("http://127.0.0.1:8792", "secret"),
      std::invalid_argument);
  babel::PerformanceWorkerHttpClient worker(
      "http://127.0.0.1:8792", std::string(64, 'a'),
      [](std::string_view, std::string_view) -> babel::Result<long> { return 401L; });
  const auto rejected = worker.start("33333333-3333-5333-8333-333333333333");
  REQUIRE_FALSE(rejected.has_value());
  CHECK(rejected.error().code == babel::ErrorCode::database_unavailable);
  CHECK(rejected.error().message.find(std::string(64, 'a')) == std::string::npos);
}
