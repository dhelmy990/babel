#include "babel/runtime/seed_job_runner.hpp"

#include <exception>
#include <utility>

namespace babel {

SeedJobRunner::SeedJobRunner(std::string manifest_version, SeedService& service,
                             SeedRunRepository& runs)
    : manifest_version_(std::move(manifest_version)), service_(service), runs_(runs) {}

SeedJobRunner::~SeedJobRunner() {
  std::optional<SeedRunId> interrupted_run_id;
  std::jthread worker;
  {
    std::scoped_lock lock(mutex_);
    if (active_ && active_run_id_) {
      interrupted_run_id = active_run_id_;
      if (worker_.joinable()) worker_.request_stop();
    }
    worker = std::move(worker_);
  }
  if (worker.joinable()) worker.join();
  if (interrupted_run_id) {
    try {
      (void)runs_.setRunState(*interrupted_run_id, SeedRunState::interrupted);
    } catch (...) {
    }
  }
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
  active_run_id_ = run_id.value();
  try {
    worker_ = std::jthread(
        [this, id = run_id.value()](std::stop_token token) { execute(id, token); });
  } catch (const std::exception& exception) {
    active_ = false;
    active_run_id_.reset();
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
  const auto persistTerminal = [&](SeedRunState state) noexcept {
    try {
      (void)runs_.setRunState(run_id, state);
    } catch (...) {
    }
  };
  try {
    const auto result = service_.run(run_id, stop_token);
    if (stop_token.stop_requested()) {
      persistTerminal(SeedRunState::interrupted);
    } else if (!result) {
      persistTerminal(SeedRunState::failed);
    }
  } catch (...) {
    const auto terminal = stop_token.stop_requested() ? SeedRunState::interrupted
                                                      : SeedRunState::failed;
    persistTerminal(terminal);
  }
  releaseActiveGuard(run_id);
}

void SeedJobRunner::releaseActiveGuard(SeedRunId run_id) noexcept {
  std::scoped_lock lock(mutex_);
  if (active_run_id_ == run_id) {
    active_ = false;
    active_run_id_.reset();
  }
}

}  // namespace babel
