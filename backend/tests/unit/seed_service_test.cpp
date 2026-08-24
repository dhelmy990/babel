#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <deque>
#include <iterator>
#include <mutex>
#include <optional>
#include <ranges>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <utility>
#include <vector>

#include "babel/application/seed_service.hpp"

namespace {

using namespace babel;

ApplicationError testError(ErrorCode code, std::string message) {
  return ApplicationError{.code = code, .message = std::move(message)};
}

SeedAssignment assignment(std::string_view name, std::string title) {
  return SeedAssignment{
      .id = SeedAssignmentId::v5(std::string{"seed-test:"} + std::string{name}).value(),
      .creator_id = CreatorId::v5(std::string{"seed-creator-test:"} + std::string{name}).value(),
      .declared_title = std::move(title),
  };
}

class RecordingSeedRepository final : public SeedRunRepository {
 public:
  struct Transition {
    SeedAssignmentId assignment_id;
    SeedItemUpdate update;
  };

  Result<SeedRunId> createRun(std::string_view,
                              std::span<const SeedAssignment> assignments) override {
    std::scoped_lock lock(mutex_);
    snapshot.assign(assignments.begin(), assignments.end());
    return run_id;
  }

  Result<bool> assignmentExists(SeedAssignmentId assignment_id) override {
    std::scoped_lock lock(mutex_);
    if (assignment_exists_error) return tl::make_unexpected(*assignment_exists_error);
    ++assignment_checks;
    return existing.contains(assignment_id);
  }

  Result<void> recordItemState(SeedRunId, SeedAssignmentId assignment_id,
                               const SeedItemUpdate& update) override {
    std::scoped_lock lock(mutex_);
    ++record_calls;
    if (record_error_at && record_calls == *record_error_at) {
      return tl::make_unexpected(
          testError(ErrorCode::database_unavailable, "seed item persistence unavailable"));
    }
    transitions.push_back(Transition{.assignment_id = assignment_id, .update = update});
    return {};
  }

  Result<void> setRunState(SeedRunId, SeedRunState state) override {
    std::scoped_lock lock(mutex_);
    if (throw_setting_running && state == SeedRunState::running) {
      throw std::runtime_error("seed repository threw during run preflight");
    }
    state_history.push_back(state);
    run_state = state;
    return {};
  }

  Result<SeedStatusDto> status(SeedRunId) override {
    std::scoped_lock lock(mutex_);
    SeedStatusDto result{
        .kind = SeedStatusKind::persisted,
        .run_id = run_id,
        .run_state = run_state,
        .total = snapshot.size(),
    };
    for (const auto& assignment : snapshot) {
      auto found = transitions.rend();
      for (auto candidate = transitions.rbegin(); candidate != transitions.rend(); ++candidate) {
        if (candidate->assignment_id == assignment.id) {
          found = candidate;
          break;
        }
      }
      if (found == transitions.rend()) continue;
      if (found->update.state == SeedItemState::imported) ++result.imported;
      if (found->update.state == SeedItemState::skipped) ++result.skipped;
      if (found->update.state == SeedItemState::failed) ++result.failed;
    }
    return result;
  }

  Result<SeedStatusDto> latestStatus() override { return status(run_id); }
  Result<void> markRunningAsInterrupted() override {
    std::scoped_lock lock(mutex_);
    run_state = SeedRunState::interrupted;
    return {};
  }

  SeedRunId run_id{SeedRunId::v5("seed-run-test").value()};
  std::unordered_set<SeedAssignmentId> existing;
  std::vector<SeedAssignment> snapshot;
  std::vector<Transition> transitions;
  std::vector<SeedRunState> state_history;
  std::optional<ApplicationError> assignment_exists_error;
  std::optional<int> record_error_at;
  bool throw_setting_running{false};
  int assignment_checks{0};
  int record_calls{0};
  SeedRunState run_state{SeedRunState::queued};

 private:
  std::mutex mutex_;
};

class ResolvingSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    std::scoped_lock lock(mutex_);
    ++resolve_count;
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(resolve_count).value(),
        .canonical_title = "Resolved",
        .canonical_url = "https://en.wikipedia.org/wiki/Resolved",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "unexpected fetch"));
  }

  int resolve_count{0};

 private:
  std::mutex mutex_;
};

class RecordingImporter final : public WikipediaImporter {
 public:
  Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId,
                                                          WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "seed context required"));
  }

  Result<ImportWikipediaBabelResult> importWikipediaBabel(
      CreatorId, WikipediaPageId, SeedImportContext context) override {
    std::scoped_lock lock(mutex_);
    calls.push_back(std::move(context));
    return ImportWikipediaBabelResult{
        .status = ImportWikipediaStatus::imported,
        .babel_id = BabelId::v5("seed-import-result:" + calls.back().assignment_id.value).value(),
        .canonical_title = "Resolved",
    };
  }

  std::vector<SeedImportContext> calls;

 private:
  std::mutex mutex_;
};

class ScriptedSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view title) override {
    std::scoped_lock lock(mutex_);
    titles.emplace_back(title);
    if (!results.empty()) {
      auto result = std::move(results.front());
      results.pop_front();
      return result;
    }
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(42).value(),
        .canonical_title = "Canonical",
        .canonical_url = "https://en.wikipedia.org/wiki/Canonical",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "unexpected fetch"));
  }

  std::deque<Result<ResolvedWikipediaPage>> results;
  std::vector<std::string> titles;

 private:
  std::mutex mutex_;
};

class ScriptedImporter final : public WikipediaImporter {
 public:
  Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId,
                                                          WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "seed context required"));
  }

  Result<ImportWikipediaBabelResult> importWikipediaBabel(
      CreatorId creator_id, WikipediaPageId page_id, SeedImportContext context) override {
    std::scoped_lock lock(mutex_);
    creator_ids.push_back(creator_id);
    page_ids.push_back(page_id);
    contexts.push_back(context);
    if (!results.empty()) {
      auto result = std::move(results.front());
      results.pop_front();
      return result;
    }
    return ImportWikipediaBabelResult{
        .status = ImportWikipediaStatus::imported,
        .babel_id = BabelId::v5("scripted-import:" + context.assignment_id.value).value(),
        .canonical_title = "Canonical",
    };
  }

  std::deque<Result<ImportWikipediaBabelResult>> results;
  std::vector<CreatorId> creator_ids;
  std::vector<WikipediaPageId> page_ids;
  std::vector<SeedImportContext> contexts;

 private:
  std::mutex mutex_;
};

class SelectiveSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view title) override {
    if (title == "Broken") {
      return tl::make_unexpected(testError(
          ErrorCode::wikipedia_not_found,
          "missing <script>alert('unsafe')</script> & no canonical page"));
    }
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(title == "First" ? 1 : 2).value(),
        .canonical_title = std::string{title},
        .canonical_url = "https://en.wikipedia.org/wiki/Resolved",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "unexpected fetch"));
  }
};

class ConcurrencySource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    const auto current = active.fetch_add(1) + 1;
    auto observed = maximum.load();
    while (current > observed && !maximum.compare_exchange_weak(observed, current)) {
    }
    const auto call = calls.fetch_add(1) + 1;
    std::this_thread::sleep_for(std::chrono::milliseconds{15});
    active.fetch_sub(1);
    return ResolvedWikipediaPage{
        .page_id = WikipediaPageId::fromInt(call).value(),
        .canonical_title = "Resolved",
        .canonical_url = "https://en.wikipedia.org/wiki/Resolved",
    };
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(testError(ErrorCode::internal, "unexpected fetch"));
  }

  std::atomic_int active{0};
  std::atomic_int maximum{0};
  std::atomic_int calls{0};
};

std::vector<RecordingSeedRepository::Transition> transitionsFor(
    const RecordingSeedRepository& runs, SeedAssignmentId assignment_id) {
  std::vector<RecordingSeedRepository::Transition> result;
  std::ranges::copy_if(runs.transitions, std::back_inserter(result),
                       [&](const auto& transition) {
                         return transition.assignment_id == assignment_id;
                       });
  return result;
}

TEST_CASE("seed processes only missing durable assignments", "[seed_service]") {
  const std::vector manifest{
      assignment("existing", "Existing"),
      assignment("first", "First"),
      assignment("second", "Second"),
  };
  RecordingSeedRepository runs;
  runs.existing.insert(manifest.front().id);
  ResolvingSource source;
  RecordingImporter importer;
  const auto policy = SeedRetryPolicy::withoutDelay();
  SeedService service(manifest, runs, source, importer, policy);
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  const auto status = runs.status(runs.run_id).value();
  CHECK(importer.calls.size() == 2);
  CHECK(status.imported == 2);
  CHECK(status.skipped == 1);
  CHECK(runs.run_state == SeedRunState::completed);
}

TEST_CASE("seed persists the exact successful transition sequence", "[seed_service]") {
  const std::vector manifest{assignment("success", "Declared title")};
  RecordingSeedRepository runs;
  ScriptedSource source;
  ScriptedImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  const auto transitions = transitionsFor(runs, manifest.front().id);
  REQUIRE(transitions.size() == 3);
  CHECK(transitions.at(0).update.state == SeedItemState::resolving);
  CHECK(transitions.at(0).update.attempt_count == 1);
  CHECK_FALSE(transitions.at(0).update.resolved_page_id.has_value());
  CHECK(transitions.at(1).update.state == SeedItemState::importing);
  CHECK(transitions.at(1).update.attempt_count == 1);
  REQUIRE(transitions.at(1).update.resolved_page_id.has_value());
  CHECK(transitions.at(1).update.resolved_page_id->value == 42);
  CHECK(transitions.at(2).update.state == SeedItemState::imported);
  CHECK(transitions.at(2).update.attempt_count == 1);
  CHECK(transitions.at(2).update.resolved_page_id->value == 42);
  CHECK(transitions.at(2).update.babel_id.has_value());
  CHECK_FALSE(transitions.at(2).update.error.has_value());
  REQUIRE(importer.contexts.size() == 1);
  CHECK(importer.contexts.front().assignment_id == manifest.front().id);
  CHECK(importer.contexts.front().declared_title == "Declared title");
}

TEST_CASE("seed retries unavailable imports twice with fixed backoff", "[seed_service]") {
  const std::vector manifest{assignment("retry", "Retry article")};
  RecordingSeedRepository runs;
  ScriptedSource source;
  ScriptedImporter importer;
  importer.results.push_back(
      tl::make_unexpected(testError(ErrorCode::wikipedia_unavailable, "timeout one")));
  importer.results.push_back(
      tl::make_unexpected(testError(ErrorCode::wikipedia_unavailable, "timeout two")));
  std::vector<std::chrono::milliseconds> delays;
  SeedRetryPolicy policy{[&](std::chrono::milliseconds delay) { delays.push_back(delay); }};
  SeedService service(manifest, runs, source, importer, std::move(policy));
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  CHECK(source.titles.size() == 3);
  CHECK(importer.contexts.size() == 3);
  REQUIRE(delays.size() == 2);
  CHECK(delays.at(0) == std::chrono::milliseconds{500});
  CHECK(delays.at(1) == std::chrono::milliseconds{1500});
  const auto transitions = transitionsFor(runs, manifest.front().id);
  const std::vector expected_states{
      SeedItemState::resolving, SeedItemState::importing,
      SeedItemState::resolving, SeedItemState::importing,
      SeedItemState::resolving, SeedItemState::importing,
      SeedItemState::imported,
  };
  const std::vector<std::uint32_t> expected_attempts{1, 1, 2, 2, 3, 3, 3};
  REQUIRE(transitions.size() == expected_states.size());
  for (std::size_t index = 0; index < expected_states.size(); ++index) {
    CHECK(transitions.at(index).update.state == expected_states.at(index));
    CHECK(transitions.at(index).update.attempt_count == expected_attempts.at(index));
  }
  CHECK(transitions.back().update.attempt_count == 3);
}

TEST_CASE("seed retries unavailable title resolution and records the final failure",
          "[seed_service]") {
  const std::vector manifest{assignment("resolve-retry", "Unavailable")};
  RecordingSeedRepository runs;
  ScriptedSource source;
  for (int attempt = 0; attempt < 3; ++attempt) {
    source.results.push_back(tl::make_unexpected(
        testError(ErrorCode::wikipedia_unavailable, "upstream still unavailable")));
  }
  ScriptedImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  CHECK(source.titles.size() == 3);
  CHECK(importer.contexts.empty());
  const auto transitions = transitionsFor(runs, manifest.front().id);
  REQUIRE(transitions.size() == 4);
  CHECK(transitions.at(0).update.state == SeedItemState::resolving);
  CHECK(transitions.at(0).update.attempt_count == 1);
  CHECK(transitions.at(1).update.attempt_count == 2);
  CHECK(transitions.at(2).update.attempt_count == 3);
  CHECK(transitions.at(3).update.state == SeedItemState::failed);
  CHECK(transitions.at(3).update.attempt_count == 3);
  REQUIRE(transitions.at(3).update.error.has_value());
  CHECK(transitions.at(3).update.error->code == ErrorCode::wikipedia_unavailable);
  CHECK(runs.run_state == SeedRunState::completed_with_errors);
}

TEST_CASE("seed does not retry permanent import failures", "[seed_service]") {
  const std::vector permanent_errors{
      ErrorCode::not_found,
      ErrorCode::wikipedia_not_found,
      ErrorCode::sanitizer_rejected,
      ErrorCode::invalid_argument,
      ErrorCode::database_unavailable,
  };

  for (const auto error_code : permanent_errors) {
    const std::vector manifest{assignment("permanent-" + std::to_string(static_cast<int>(error_code)),
                                          "Permanent failure")};
    RecordingSeedRepository runs;
    ScriptedSource source;
    ScriptedImporter importer;
    importer.results.push_back(
        tl::make_unexpected(testError(error_code, "permanent failure")));
    SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
    REQUIRE(runs.createRun("test-v1", manifest).has_value());

    REQUIRE(service.run(runs.run_id).has_value());

    CHECK(source.titles.size() == 1);
    CHECK(importer.contexts.size() == 1);
    const auto transitions = transitionsFor(runs, manifest.front().id);
    REQUIRE(transitions.size() == 3);
    CHECK(transitions.back().update.state == SeedItemState::failed);
    CHECK(transitions.back().update.attempt_count == 1);
    REQUIRE(transitions.back().update.error.has_value());
    CHECK(transitions.back().update.error->code == error_code);
    CHECK(runs.run_state == SeedRunState::completed_with_errors);
  }
}

TEST_CASE("seed completes with errors while preserving successful assignments",
          "[seed_service]") {
  const std::vector manifest{
      assignment("partial-first", "First"),
      assignment("partial-broken", "Broken"),
      assignment("partial-second", "Second"),
  };
  RecordingSeedRepository runs;
  SelectiveSource source;
  RecordingImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  const auto status = runs.status(runs.run_id).value();
  CHECK(status.imported == 2);
  CHECK(status.failed == 1);
  CHECK(runs.run_state == SeedRunState::completed_with_errors);
  const auto failed = transitionsFor(runs, manifest.at(1).id).back().update;
  REQUIRE(failed.error.has_value());
  CHECK(failed.error->code == ErrorCode::wikipedia_not_found);
  CHECK(failed.error->message ==
        "missing &lt;script&gt;alert(&#39;unsafe&#39;)&lt;/script&gt; &amp; no canonical page");
  CHECK_FALSE(failed.resolved_page_id.has_value());
}

TEST_CASE("seed escalates seed repository failures to a failed run", "[seed_service]") {
  const std::vector manifest{assignment("repository-failure", "Repository failure")};
  RecordingSeedRepository runs;
  runs.assignment_exists_error =
      testError(ErrorCode::database_unavailable, "assignment lookup unavailable");
  ScriptedSource source;
  ScriptedImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  const auto result = service.run(runs.run_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::database_unavailable);
  CHECK(runs.run_state == SeedRunState::failed);
  CHECK(source.titles.empty());
  CHECK(importer.contexts.empty());
}

TEST_CASE("seed converts thrown execution failures into a failed durable run",
          "[seed_service]") {
  const std::vector manifest{assignment("throwing-repository", "Throwing repository")};
  RecordingSeedRepository runs;
  runs.throw_setting_running = true;
  ScriptedSource source;
  ScriptedImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  std::optional<Result<void>> result;
  CHECK_NOTHROW(result = service.run(runs.run_id));

  REQUIRE(result.has_value());
  REQUIRE_FALSE(result->has_value());
  CHECK(result->error().code == ErrorCode::internal);
  CHECK(result->error().message == "seed repository threw during run preflight");
  CHECK(runs.run_state == SeedRunState::failed);
}

TEST_CASE("seed bounds concurrent assignment work at four", "[seed_service]") {
  std::vector<SeedAssignment> manifest;
  for (int index = 0; index < 12; ++index) {
    manifest.push_back(assignment("concurrency-" + std::to_string(index),
                                  "Article " + std::to_string(index)));
  }
  RecordingSeedRepository runs;
  ConcurrencySource source;
  RecordingImporter importer;
  SeedService service(manifest, runs, source, importer, SeedRetryPolicy::withoutDelay());
  REQUIRE(runs.createRun("test-v1", manifest).has_value());

  REQUIRE(service.run(runs.run_id).has_value());

  CHECK(source.calls.load() == 12);
  CHECK(source.maximum.load() <= 4);
  CHECK(source.maximum.load() > 1);
  CHECK(importer.calls.size() == 12);
  CHECK(runs.assignment_checks == 12);
  CHECK(runs.status(runs.run_id)->imported == 12);
}

}  // namespace
