#include "babel/runtime/seed_job_runner.hpp"

#include <exception>
#include <utility>

namespace babel {

SeedJobRunner::SeedJobRunner(std::string manifest_version, SeedService& service,
                             SeedRunRepository& runs)
    : manifest_version_(std::move(manifest_version)), service_(service), runs_(runs) {}

SeedJobRunner::~SeedJobRunner() {
  std::jthread worker;
  {
    std::scoped_lock lock(mutex_);
    if (worker_.joinable()) worker_.request_stop();
    worker = std::move(worker_);
  }
  if (worker.joinable()) worker.join();
}

Result<SeedRunId> SeedJobRunner::start() {
  std::scoped_lock lock(mutex_);
  if (active_) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::conflict,
        .message = "a seed run is already active",
    });
  }

  const auto run_id = runs_.createRun(manifest_version_, service_.assignments());
  if (!run_id) return tl::make_unexpected(run_id.error());

  active_ = true;
  try {
    worker_ = std::jthread(
        [this, id = run_id.value()](std::stop_token token) { execute(id, token); });
  } catch (const std::exception& exception) {
    active_ = false;
    const ApplicationError error{
        .code = ErrorCode::internal,
        .message = exception.what(),
    };
    const auto failed = runs_.setRunState(run_id.value(), SeedRunState::failed);
    if (!failed) return tl::make_unexpected(failed.error());
    return tl::make_unexpected(error);
  }
  return run_id.value();
}

Result<SeedStatusDto> SeedJobRunner::currentStatus() {
  return runs_.latestStatus();
}

Result<void> SeedJobRunner::markInterruptedRuns() {
  return runs_.markRunningAsInterrupted();
}

void SeedJobRunner::execute(SeedRunId run_id, std::stop_token stop_token) noexcept {
  try {
    const auto result = service_.run(run_id, stop_token);
    if (!result) {
      const auto terminal = stop_token.stop_requested() ? SeedRunState::interrupted
                                                        : SeedRunState::failed;
      (void)runs_.setRunState(run_id, terminal);
    }
  } catch (...) {
    const auto terminal = stop_token.stop_requested() ? SeedRunState::interrupted
                                                      : SeedRunState::failed;
    (void)runs_.setRunState(run_id, terminal);
  }
  releaseActiveGuard();
}

void SeedJobRunner::releaseActiveGuard() noexcept {
  std::scoped_lock lock(mutex_);
  active_ = false;
}

}  // namespace babel
