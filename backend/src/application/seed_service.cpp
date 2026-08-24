#include "babel/application/seed_service.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <exception>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <utility>

namespace babel {
namespace {

constexpr std::size_t kMaxConcurrentAssignments = 4;

bool retryable(const ApplicationError& error) {
  return error.code == ErrorCode::wikipedia_unavailable;
}

std::string escapedPlainMessage(std::string_view message) {
  std::string escaped;
  escaped.reserve(message.size());
  for (const unsigned char character : message) {
    switch (character) {
      case '&':
        escaped += "&amp;";
        break;
      case '<':
        escaped += "&lt;";
        break;
      case '>':
        escaped += "&gt;";
        break;
      case '"':
        escaped += "&quot;";
        break;
      case '\'':
        escaped += "&#39;";
        break;
      default:
        if (std::iscntrl(character) && character != '\n' && character != '\t') {
          escaped.push_back(' ');
        } else {
          escaped.push_back(static_cast<char>(character));
        }
    }
  }
  if (escaped.empty()) escaped = "seed assignment failed";
  return escaped;
}

ApplicationError durableError(const ApplicationError& error) {
  return ApplicationError{.code = error.code, .message = escapedPlainMessage(error.message)};
}

ApplicationError unexpectedException(const std::exception& exception) {
  return ApplicationError{
      .code = ErrorCode::internal,
      .message = escapedPlainMessage(exception.what()),
  };
}

ApplicationError unknownException() {
  return ApplicationError{
      .code = ErrorCode::internal,
      .message = "unexpected seed worker failure",
  };
}

}  // namespace

SeedRetryPolicy::SeedRetryPolicy(
    Delay delay, std::array<std::chrono::milliseconds, 2> retry_delays)
    : delay_(std::move(delay)), retry_delays_(retry_delays) {
  if (!delay_) delay_ = [](std::chrono::milliseconds) {};
}

SeedRetryPolicy SeedRetryPolicy::withDefaultDelay() {
  return SeedRetryPolicy{
      [](std::chrono::milliseconds duration) { std::this_thread::sleep_for(duration); }};
}

SeedRetryPolicy SeedRetryPolicy::withoutDelay() {
  return SeedRetryPolicy{[](std::chrono::milliseconds) {}};
}

std::size_t SeedRetryPolicy::maxAttempts() const noexcept {
  return retry_delays_.size() + 1;
}

void SeedRetryPolicy::waitBeforeRetry(std::size_t completed_attempt) const {
  delay_(retry_delays_.at(completed_attempt - 1));
}

SeedService::SeedService(std::vector<SeedAssignment> assignments, SeedRunRepository& runs,
                         ArticleSource& source, WikipediaImporter& importer,
                         SeedRetryPolicy retry_policy)
    : assignments_(std::move(assignments)),
      runs_(runs),
      source_(source),
      importer_(importer),
      retry_policy_(std::move(retry_policy)) {}

std::span<const SeedAssignment> SeedService::assignments() const noexcept {
  return assignments_;
}

Result<void> SeedService::run(SeedRunId run_id, std::stop_token stop_token) {
  try {
    return runImpl(run_id, stop_token);
  } catch (const std::exception& exception) {
    const auto error = unexpectedException(exception);
    try {
      return failRun(run_id, error);
    } catch (...) {
      return tl::make_unexpected(error);
    }
  } catch (...) {
    const auto error = unknownException();
    try {
      return failRun(run_id, error);
    } catch (...) {
      return tl::make_unexpected(error);
    }
  }
}

Result<void> SeedService::runImpl(SeedRunId run_id, std::stop_token stop_token) {
  if (assignments_.empty()) {
    const ApplicationError error{
        .code = ErrorCode::invalid_argument,
        .message = "seed manifest must contain at least one assignment",
    };
    return failRun(run_id, error);
  }

  const auto running = runs_.setRunState(run_id, SeedRunState::running);
  if (!running) return failRun(run_id, running.error());

  std::atomic_size_t next_assignment{0};
  std::atomic_bool abort{false};
  std::mutex failure_mutex;
  std::optional<ApplicationError> run_failure;

  const auto worker = [&] {
    try {
      while (!abort.load(std::memory_order_acquire) && !stop_token.stop_requested()) {
        const auto index = next_assignment.fetch_add(1, std::memory_order_relaxed);
        if (index >= assignments_.size()) return;

        const auto result = processAssignment(run_id, assignments_[index], stop_token);
        if (!result) {
          {
            std::scoped_lock lock(failure_mutex);
            if (!run_failure) run_failure = result.error();
          }
          abort.store(true, std::memory_order_release);
          return;
        }
      }
    } catch (const std::exception& exception) {
      {
        std::scoped_lock lock(failure_mutex);
        if (!run_failure) run_failure = unexpectedException(exception);
      }
      abort.store(true, std::memory_order_release);
    } catch (...) {
      {
        std::scoped_lock lock(failure_mutex);
        if (!run_failure) run_failure = unknownException();
      }
      abort.store(true, std::memory_order_release);
    }
  };

  const auto worker_count = std::min(kMaxConcurrentAssignments, assignments_.size());
  std::vector<std::thread> workers;
  workers.reserve(worker_count);
  try {
    for (std::size_t index = 0; index < worker_count; ++index) workers.emplace_back(worker);
  } catch (const std::exception& exception) {
    abort.store(true, std::memory_order_release);
    for (auto& thread : workers) thread.join();
    return failRun(run_id, unexpectedException(exception));
  }
  for (auto& thread : workers) thread.join();

  if (run_failure) return failRun(run_id, *run_failure);
  if (stop_token.stop_requested()) return interruptRun(run_id);
  const auto status = runs_.status(run_id);
  if (stop_token.stop_requested()) return interruptRun(run_id);
  if (!status) return failRun(run_id, status.error());
  const auto terminal = status->failed == 0 ? SeedRunState::completed
                                            : SeedRunState::completed_with_errors;
  if (stop_token.stop_requested()) return interruptRun(run_id);
  const auto completed = runs_.setRunState(run_id, terminal);
  if (!completed) return failRun(run_id, completed.error());
  return {};
}

Result<void> SeedService::processAssignment(SeedRunId run_id,
                                            const SeedAssignment& assignment,
                                            std::stop_token stop_token) {
  const auto exists = runs_.assignmentExists(assignment.id);
  if (stop_token.stop_requested()) return {};
  if (!exists) return tl::make_unexpected(exists.error());
  if (exists.value()) {
    if (stop_token.stop_requested()) return {};
    return runs_.recordItemState(
        run_id, assignment.id,
        SeedItemUpdate{
            .state = SeedItemState::skipped,
            .attempt_count = 0,
            .resolved_page_id = std::nullopt,
            .babel_id = std::nullopt,
            .error = std::nullopt,
        });
  }

  for (std::uint32_t attempt = 1; attempt <= retry_policy_.maxAttempts(); ++attempt) {
    if (stop_token.stop_requested()) return {};

    const auto resolving = runs_.recordItemState(
        run_id, assignment.id,
        SeedItemUpdate{
            .state = SeedItemState::resolving,
            .attempt_count = attempt,
            .resolved_page_id = std::nullopt,
            .babel_id = std::nullopt,
            .error = std::nullopt,
        });
    if (!resolving) return tl::make_unexpected(resolving.error());
    if (stop_token.stop_requested()) return {};

    const auto resolved = [&]() -> Result<ResolvedWikipediaPage> {
      try {
        return source_.resolveTitle(assignment.declared_title);
      } catch (const std::exception& exception) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = exception.what(),
        });
      } catch (...) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "unexpected Wikipedia title resolution failure",
        });
      }
    }();
    if (stop_token.stop_requested()) return {};
    if (!resolved) {
      if (retryable(resolved.error()) && attempt < retry_policy_.maxAttempts()) {
        if (stop_token.stop_requested()) return {};
        retry_policy_.waitBeforeRetry(attempt);
        if (stop_token.stop_requested()) return {};
        continue;
      }
      return recordFailure(run_id, assignment, attempt, std::nullopt, resolved.error());
    }

    const auto importing = runs_.recordItemState(
        run_id, assignment.id,
        SeedItemUpdate{
            .state = SeedItemState::importing,
            .attempt_count = attempt,
            .resolved_page_id = resolved->page_id,
            .babel_id = std::nullopt,
            .error = std::nullopt,
        });
    if (!importing) return tl::make_unexpected(importing.error());
    if (stop_token.stop_requested()) return {};

    const auto imported = [&]() -> Result<ImportWikipediaBabelResult> {
      try {
        return importer_.importWikipediaBabel(
            assignment.creator_id, resolved->page_id,
            SeedImportContext{
                .assignment_id = assignment.id,
                .declared_title = assignment.declared_title,
            });
      } catch (const std::exception& exception) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = exception.what(),
        });
      } catch (...) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "unexpected Wikipedia import failure",
        });
      }
    }();
    if (stop_token.stop_requested()) return {};
    if (!imported) {
      if (retryable(imported.error()) && attempt < retry_policy_.maxAttempts()) {
        if (stop_token.stop_requested()) return {};
        retry_policy_.waitBeforeRetry(attempt);
        if (stop_token.stop_requested()) return {};
        continue;
      }
      return recordFailure(run_id, assignment, attempt, resolved->page_id, imported.error());
    }

    return runs_.recordItemState(
        run_id, assignment.id,
        SeedItemUpdate{
            .state = SeedItemState::imported,
            .attempt_count = attempt,
            .resolved_page_id = resolved->page_id,
            .babel_id = imported->babel_id,
            .error = std::nullopt,
        });
  }

  return tl::make_unexpected(ApplicationError{
      .code = ErrorCode::internal,
      .message = "seed retry policy exhausted unexpectedly",
  });
}

Result<void> SeedService::recordFailure(SeedRunId run_id,
                                        const SeedAssignment& assignment,
                                        std::uint32_t attempt,
                                        std::optional<WikipediaPageId> resolved_page_id,
                                        const ApplicationError& error) {
  return runs_.recordItemState(
      run_id, assignment.id,
      SeedItemUpdate{
          .state = SeedItemState::failed,
          .attempt_count = attempt,
          .resolved_page_id = resolved_page_id,
          .babel_id = std::nullopt,
          .error = durableError(error),
      });
}

Result<void> SeedService::interruptRun(SeedRunId run_id) {
  return runs_.setRunState(run_id, SeedRunState::interrupted);
}

Result<void> SeedService::failRun(SeedRunId run_id, const ApplicationError& error) {
  const auto failed = runs_.setRunState(run_id, SeedRunState::failed);
  if (!failed) return tl::make_unexpected(failed.error());
  return tl::make_unexpected(error);
}

}  // namespace babel
