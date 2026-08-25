#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "babel/runtime/seed_job_runner.hpp"

namespace {

using namespace babel;
using namespace std::chrono_literals;

class RunRepository final : public SeedRunRepository {
 public:
  Result<SeedRunId> createRun(std::string_view,
                              std::span<const SeedAssignment>) override {
    return run_id;
  }
  Result<bool> assignmentExists(SeedAssignmentId) override { return false; }
  Result<void> recordItemState(SeedRunId, SeedAssignmentId,
                               const SeedItemUpdate&) override {
    ++item_updates;
    return {};
  }
  Result<void> setRunState(SeedRunId, SeedRunState next) override {
    state = next;
    return {};
  }
  Result<SeedStatusDto> status(SeedRunId) override { return SeedStatusDto{}; }
  Result<SeedStatusDto> latestStatus() override { return SeedStatusDto{}; }
  Result<void> markNonterminalAsInterrupted() override { return {}; }
  Result<void> recordSourcePin(SeedRunId,
                               const PinnedSourceProvenance& provenance) override {
    pin = provenance;
    return {};
  }

  SeedRunId run_id{SeedRunId::v5("hf-seed-run").value()};
  std::optional<PinnedSourceProvenance> pin;
  std::atomic_int item_updates{0};
  std::atomic<SeedRunState> state{SeedRunState::queued};
};

class UnavailableFactory final : public ArticleSourceFactory {
 public:
  Result<std::shared_ptr<PinnedArticleSource>> pin(const SourceSelection&) override {
    ++pin_count;
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::wikipedia_unavailable,
        .message = "Hugging Face unavailable",
    });
  }
  int pin_count{0};
};

class PinnedFixtureSource final : public PinnedArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "not used"});
  }
  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    return tl::make_unexpected(ApplicationError{.code = ErrorCode::internal,
                                                 .message = "not used"});
  }
  const PinnedSourceProvenance& provenance() const noexcept override { return pin; }

  PinnedSourceProvenance pin{
      .repository = "repo",
      .configuration = "catalog_2026_06",
      .commit_sha = std::string(40, 'a'),
      .snapshot_date = "2026-06-01",
  };
};

class FixtureFactory final : public ArticleSourceFactory {
 public:
  Result<std::shared_ptr<PinnedArticleSource>> pin(const SourceSelection&) override {
    ++pin_count;
    return std::static_pointer_cast<PinnedArticleSource>(source);
  }
  int pin_count{0};
  std::shared_ptr<PinnedFixtureSource> source{std::make_shared<PinnedFixtureSource>()};
};

TEST_CASE("unavailable Hugging Face snapshot never starts item execution or fallback") {
  RunRepository runs;
  UnavailableFactory factory;
  std::atomic_int execution_count{0};
  const std::vector assignments{SeedAssignment{
      .id = SeedAssignmentId::v5("hf-assignment").value(),
      .creator_id = CreatorId::v5("hf-creator").value(),
      .declared_title = "Virtual memory",
  }};
  SeedJobRunner runner(
      "manifest-test-v1", assignments, factory,
      SourceSelection{.repository = "repo",
                      .configuration = "catalog_2026_06",
                      .requested_revision = "ref",
                      .artifact_path = "backend-seed/2026-06/catalog.jsonl"},
      [&](SeedRunId, std::shared_ptr<PinnedArticleSource>, std::stop_token) -> Result<void> {
        ++execution_count;
        return {};
      },
      runs);

  REQUIRE(runner.start().has_value());
  for (int wait = 0; wait < 100 && runs.state.load() != SeedRunState::failed; ++wait) {
    std::this_thread::sleep_for(1ms);
  }

  CHECK(factory.pin_count == 1);
  CHECK(execution_count == 0);
  CHECK(runs.item_updates.load() == 0);
  CHECK_FALSE(runs.pin.has_value());
  CHECK(runs.state.load() == SeedRunState::failed);
}

TEST_CASE("seed execution receives one immutable source only after its pin is durable") {
  RunRepository runs;
  FixtureFactory factory;
  std::atomic_int execution_count{0};
  const std::vector assignments{SeedAssignment{
      .id = SeedAssignmentId::v5("hf-success-assignment").value(),
      .creator_id = CreatorId::v5("hf-success-creator").value(),
      .declared_title = "Virtual memory",
  }};
  SeedJobRunner runner(
      "manifest-test-v1", assignments, factory,
      SourceSelection{.repository = "repo",
                      .configuration = "catalog_2026_06",
                      .requested_revision = "ref",
                      .artifact_path = "backend-seed/2026-06/catalog.jsonl"},
      [&](SeedRunId, std::shared_ptr<PinnedArticleSource> source,
          std::stop_token) -> Result<void> {
        REQUIRE(runs.pin.has_value());
        CHECK(runs.pin->commit_sha == std::string(40, 'a'));
        CHECK(source == factory.source);
        ++execution_count;
        return {};
      },
      runs);

  REQUIRE(runner.start().has_value());
  for (int wait = 0; wait < 100 && execution_count.load() == 0; ++wait) {
    std::this_thread::sleep_for(1ms);
  }

  CHECK(factory.pin_count == 1);
  CHECK(execution_count.load() == 1);
  REQUIRE(runs.pin.has_value());
  CHECK(runs.pin->repository == "repo");
}

}  // namespace
