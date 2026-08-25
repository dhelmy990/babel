#include "babel/runtime/experiment_job_runner.hpp"

#include <utility>

namespace babel {
namespace {

Result<void> unavailable() {
  return tl::make_unexpected(ApplicationError{
      .code = ErrorCode::database_unavailable,
      .message = "online experiment supervisor is not connected",
  });
}

}  // namespace

ExperimentJobRunner::ExperimentJobRunner(Start start, GracefulStop graceful_stop)
    : start_(std::move(start)), graceful_stop_(std::move(graceful_stop)) {}

Result<void> ExperimentJobRunner::start(ExperimentRunId run_id) {
  if (!start_) return unavailable();
  return start_(std::move(run_id));
}

Result<void> ExperimentJobRunner::requestGracefulStop(ExperimentRunId run_id) {
  if (!graceful_stop_) return unavailable();
  return graceful_stop_(std::move(run_id));
}

}  // namespace babel
