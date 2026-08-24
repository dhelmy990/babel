#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "babel/runtime/seed_job_runner.hpp"

namespace {

using namespace babel;

class RunnerSeedRepository final : public SeedRunRepository {
 public:
  Result<SeedRunId> createRun(std::string_view manifest_version,
                              std::span<const SeedAssignment> assignments) override {
    std::scoped_lock lock(mutex_);
    ++create_count;
    if (!create_results.empty()) {
      auto result = std::move(create_results.front());
      create_results.pop_front();
      if (!result) return tl::make_unexpected(result.error());
    }
    captured_version = manifest_version;
    captured_assignments.assign(assignments.begin(), assignments.end());
    latest_run = SeedRunId::v5("runner-run:" + std::to_string(create_count)).value();
    item_updates.clear();
    has_run = true;
    state = SeedRunState::queued;
    return latest_run;
  }

  Result<bool> assignmentExists(SeedAssignmentId) override {
    std::scoped_lock lock(mutex_);
    if (!assignment_exists_results.empty()) {
      auto result = std::move(assignment_exists_results.front());
      assignment_exists_results.pop_front();
      return result;
    }
    return false;
  }

  Result<void> recordItemState(SeedRunId, SeedAssignmentId assignment_id,
                               const SeedItemUpdate& update) override {
    std::scoped_lock lock(mutex_);
    item_updates.insert_or_assign(assignment_id, update);
    return {};
  }

  Result<void> setRunState(SeedRunId, SeedRunState next) override {
    std::unique_lock lock(mutex_);
    if (blocked_run_state && next == *blocked_run_state) {
      run_state_write_entered_ = true;
      changed_.notify_all();
      changed_.wait(lock, [&] { return run_state_write_released_; });
    }
    state = next;
    state_history.push_back(next);
    changed_.notify_all();
    return {};
  }

  Result<SeedStatusDto> status(SeedRunId run_id) override {
    std::scoped_lock lock(mutex_);
    SeedStatusDto result{
        .kind = SeedStatusKind::persisted,
        .run_id = run_id,
        .run_state = state,
        .total = captured_assignments.size(),
        .imported = 0,
        .skipped = 0,
        .failed = 0,
        .current_profile = std::nullopt,
        .current_article = std::nullopt,
        .errors = {},
    };
    for (const auto& [assignment_id, update] : item_updates) {
      (void)assignment_id;
      if (update.state == SeedItemState::imported) ++result.imported;
      if (update.state == SeedItemState::skipped) ++result.skipped;
      if (update.state == SeedItemState::failed) ++result.failed;
    }
    return result;
  }

  Result<SeedStatusDto> latestStatus() override {
    std::scoped_lock lock(mutex_);
    ++latest_status_calls;
    if (!has_run) return SeedStatusDto{};
    SeedStatusDto result{
        .kind = SeedStatusKind::persisted,
        .run_id = latest_run,
        .run_state = state,
        .total = captured_assignments.size(),
        .imported = 0,
        .skipped = 0,
        .failed = 0,
        .current_profile = std::nullopt,
        .current_article = std::nullopt,
        .errors = {},
    };
    for (const auto& [assignment_id, update] : item_updates) {
      (void)assignment_id;
      if (update.state == SeedItemState::imported) ++result.imported;
      if (update.state == SeedItemState::skipped) ++result.skipped;
      if (update.state == SeedItemState::failed) ++result.failed;
    }
    return result;
  }

  Result<void> markRunningAsInterrupted() override {
    std::scoped_lock lock(mutex_);
    ++mark_interrupted_calls;
    if (state == SeedRunState::running) state = SeedRunState::interrupted;
    changed_.notify_all();
    return {};
  }

  bool waitForState(SeedRunState expected) {
    std::unique_lock lock(mutex_);
    return changed_.wait_for(lock, std::chrono::seconds(2),
                             [&] { return state == expected; });
  }

  int createCount() const {
    std::scoped_lock lock(mutex_);
    return create_count;
  }

  int markInterruptedCalls() const {
    std::scoped_lock lock(mutex_);
    return mark_interrupted_calls;
  }

  std::string capturedVersion() const {
    std::scoped_lock lock(mutex_);
    return captured_version;
  }

  std::vector<SeedAssignment> capturedAssignments() const {
    std::scoped_lock lock(mutex_);
    return captured_assignments;
  }

  bool waitForRunStateWrite() {
    std::unique_lock lock(mutex_);
    return changed_.wait_for(lock, std::chrono::seconds(2),
                             [&] { return run_state_write_entered_; });
  }

  void releaseRunStateWrite() {
    std::scoped_lock lock(mutex_);
    run_state_write_released_ = true;
    changed_.notify_all();
  }

  int create_count{0};
  int mark_interrupted_calls{0};
  int latest_status_calls{0};
  bool has_run{false};
  std::string captured_version;
  std::vector<SeedAssignment> captured_assignments;
  std::unordered_map<SeedAssignmentId, SeedItemUpdate> item_updates;
  std::vector<SeedRunState> state_history;
  std::deque<Result<SeedRunId>> create_results;
  std::deque<Result<bool>> assignment_exists_results;
  std::optional<SeedRunState> blocked_run_state;
  SeedRunId latest_run{SeedRunId::v5("runner-run:initial").value()};
  SeedRunState state{SeedRunState::queued};

 private:
  mutable std::mutex mutex_;
  std::condition_variable changed_;
  bool run_state_write_entered_{false};
  bool run_state_write_released_{false};
};

class BlockingSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    std::unique_lock lock(mutex_);
    entered_ = true;
    changed_.notify_all();
    changed_.wait(lock, [&] { return released_; });
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(17).value(),
        .canonical_title = "Resolved",
        .canonical_url = "https://en.wikipedia.org/wiki/Resolved",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "unexpected fetch"});
  }

  bool waitUntilEntered() {
    std::unique_lock lock(mutex_);
    return changed_.wait_for(lock, std::chrono::seconds(2), [&] { return entered_; });
  }

  void release() {
    std::scoped_lock lock(mutex_);
    released_ = true;
    changed_.notify_all();
  }

 private:
  std::mutex mutex_;
  std::condition_variable changed_;
  bool entered_{false};
  bool released_{false};
};

class RunnerImporter final : public WikipediaImporter {
 public:
  Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId,
                                                          WikipediaPageId) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "seed context required"});
  }

  Result<ImportWikipediaBabelResult> importWikipediaBabel(
      CreatorId, WikipediaPageId, SeedImportContext context) override {
    return ImportWikipediaBabelResult{
        .status = ImportWikipediaStatus::imported,
        .babel_id = BabelId::v5("runner-import:" + context.assignment_id.value).value(),
        .canonical_title = "Resolved",
    };
  }
};

class SlowSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    {
      std::scoped_lock lock(mutex_);
      entered_ = true;
      changed_.notify_all();
    }
    std::this_thread::sleep_for(std::chrono::milliseconds{40});
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(23).value(),
        .canonical_title = "Resolved",
        .canonical_url = "https://en.wikipedia.org/wiki/Resolved",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "unexpected fetch"});
  }

  bool waitUntilEntered() {
    std::unique_lock lock(mutex_);
    return changed_.wait_for(lock, std::chrono::seconds(2), [&] { return entered_; });
  }

 private:
  std::mutex mutex_;
  std::condition_variable changed_;
  bool entered_{false};
};

class FailingSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::wikipedia_not_found,
        .message = "not found",
    });
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "unexpected fetch"});
  }
};

SeedAssignment runnerAssignment() {
  return SeedAssignment{
      .id = SeedAssignmentId::v5("runner-assignment").value(),
      .creator_id = CreatorId::v5("runner-creator").value(),
      .declared_title = "Distributed computing",
  };
}

TEST_CASE("seed runner rejects a second active start", "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  const auto first = runner.start();
  REQUIRE(first.has_value());
  REQUIRE(source.waitUntilEntered());

  const auto second = runner.start();
  REQUIRE_FALSE(second.has_value());
  CHECK(second.error().code == ErrorCode::conflict);
  CHECK(runs.createCount() == 1);
  CHECK(runs.capturedVersion() == "manifest-test-v1");
  REQUIRE(runs.capturedAssignments().size() == 1);
  CHECK(runs.capturedAssignments().front().id == assignments.front().id);

  source.release();
  REQUIRE(runs.waitForState(SeedRunState::completed));
}

TEST_CASE("seed runner never starts automatically and reports durable latest status",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  CHECK(runs.createCount() == 0);
  const auto status = runner.currentStatus();
  REQUIRE(status.has_value());
  CHECK(status->kind == SeedStatusKind::not_started);
  CHECK(runs.createCount() == 0);
}

TEST_CASE("seed runner releases its guard after a terminal run", "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);
  REQUIRE(runner.start().has_value());
  REQUIRE(source.waitUntilEntered());
  source.release();
  REQUIRE(runs.waitForState(SeedRunState::completed));

  auto second = runner.start();
  for (int retry = 0; !second && second.error().code == ErrorCode::conflict && retry < 100;
       ++retry) {
    std::this_thread::yield();
    second = runner.start();
  }

  REQUIRE(second.has_value());
  REQUIRE(runs.waitForState(SeedRunState::completed));
  CHECK(runs.createCount() == 2);
}

TEST_CASE("destroying an inactive seed runner preserves natural completion",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  auto runner = std::make_unique<SeedJobRunner>("manifest-test-v1", service, runs);
  REQUIRE(runner->start().has_value());
  REQUIRE(source.waitUntilEntered());
  source.release();
  REQUIRE(runs.waitForState(SeedRunState::completed));

  runs.create_results.push_back(tl::make_unexpected(ApplicationError{
      .code = ErrorCode::database_unavailable,
      .message = "guard release probe",
  }));
  auto probe = runner->start();
  for (int retry = 0; !probe && probe.error().code == ErrorCode::conflict && retry < 100;
       ++retry) {
    std::this_thread::yield();
    probe = runner->start();
  }
  REQUIRE_FALSE(probe.has_value());
  CHECK(probe.error().code == ErrorCode::database_unavailable);

  runner.reset();

  const auto status = runs.latestStatus();
  REQUIRE(status.has_value());
  CHECK(status->run_state == SeedRunState::completed);
}

TEST_CASE("seed runner releases its guard when run creation fails", "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  runs.create_results.push_back(tl::make_unexpected(ApplicationError{
      .code = ErrorCode::database_unavailable,
      .message = "cannot create run",
  }));
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  const auto failed = runner.start();
  REQUIRE_FALSE(failed.has_value());
  CHECK(failed.error().code == ErrorCode::database_unavailable);

  const auto recovered = runner.start();
  REQUIRE(recovered.has_value());
  REQUIRE(source.waitUntilEntered());
  source.release();
  REQUIRE(runs.waitForState(SeedRunState::completed));
  CHECK(runs.createCount() == 2);
}

TEST_CASE("seed runner releases its guard after an unrecoverable execution failure",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  runs.assignment_exists_results.push_back(tl::make_unexpected(ApplicationError{
      .code = ErrorCode::database_unavailable,
      .message = "assignment lookup unavailable",
  }));
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  REQUIRE(runner.start().has_value());
  REQUIRE(runs.waitForState(SeedRunState::failed));

  auto recovered = runner.start();
  for (int retry = 0;
       !recovered && recovered.error().code == ErrorCode::conflict && retry < 100; ++retry) {
    std::this_thread::yield();
    recovered = runner.start();
  }
  REQUIRE(recovered.has_value());
  REQUIRE(source.waitUntilEntered());
  source.release();
  REQUIRE(runs.waitForState(SeedRunState::completed));
}

TEST_CASE("seed runner startup method marks running runs interrupted",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  BlockingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  REQUIRE(runner.markInterruptedRuns().has_value());

  CHECK(runs.markInterruptedCalls() == 1);
  CHECK(runs.createCount() == 0);
}

TEST_CASE("seed runner reports completed with errors for item failures",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  FailingSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  SeedJobRunner runner("manifest-test-v1", service, runs);

  REQUIRE(runner.start().has_value());
  REQUIRE(runs.waitForState(SeedRunState::completed_with_errors));

  const auto status = runner.currentStatus();
  REQUIRE(status.has_value());
  CHECK(status->run_state == SeedRunState::completed_with_errors);
  CHECK(status->failed == 1);
}

TEST_CASE("destroying an active seed runner persists interruption before returning",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  SlowSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  {
    SeedJobRunner runner("manifest-test-v1", service, runs);
    REQUIRE(runner.start().has_value());
    REQUIRE(source.waitUntilEntered());
  }

  const auto status = runs.latestStatus();
  REQUIRE(status.has_value());
  CHECK(status->run_state == SeedRunState::interrupted);
  CHECK(status->imported == 0);
}

TEST_CASE("runner destruction overrides a racing completed write with interrupted",
          "[seed_job_runner]") {
  const std::vector assignments{runnerAssignment()};
  RunnerSeedRepository runs;
  runs.blocked_run_state = SeedRunState::completed;
  SlowSource source;
  RunnerImporter importer;
  SeedService service(assignments, runs, source, importer, SeedRetryPolicy::withoutDelay());
  auto runner = std::make_unique<SeedJobRunner>("manifest-test-v1", service, runs);
  REQUIRE(runner->start().has_value());
  REQUIRE(runs.waitForRunStateWrite());

  std::thread releasing([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds{20});
    runs.releaseRunStateWrite();
  });
  runner.reset();
  releasing.join();

  const auto status = runs.latestStatus();
  REQUIRE(status.has_value());
  CHECK(status->run_state == SeedRunState::interrupted);
}

}  // namespace
