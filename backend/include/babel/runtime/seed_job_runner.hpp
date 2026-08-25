#pragma once

#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <stop_token>
#include <string>
#include <thread>
#include <vector>

#include "babel/application/seed_service.hpp"

namespace babel {

class SeedJobRunner final {
 public:
  using PinnedExecution = std::function<Result<void>(
      SeedRunId, std::shared_ptr<PinnedArticleSource>, std::stop_token)>;

  SeedJobRunner(std::string manifest_version, SeedService&, SeedRunRepository&);
  SeedJobRunner(std::string manifest_version, std::vector<SeedAssignment>,
                ArticleSourceFactory&, SourceSelection, PinnedExecution,
                SeedRunRepository&);
  ~SeedJobRunner();

  SeedJobRunner(const SeedJobRunner&) = delete;
  SeedJobRunner& operator=(const SeedJobRunner&) = delete;

  Result<SeedRunId> start();
  Result<SeedStatusDto> currentStatus();
  Result<void> markInterruptedRuns();

 private:
  void execute(SeedRunId, std::stop_token) noexcept;
  void releaseActiveGuard(SeedRunId) noexcept;

  std::string manifest_version_;
  SeedService* service_{nullptr};
  std::vector<SeedAssignment> assignments_;
  ArticleSourceFactory* source_factory_{nullptr};
  std::optional<SourceSelection> source_selection_;
  PinnedExecution pinned_execution_;
  SeedRunRepository& runs_;
  std::mutex mutex_;
  bool active_{false};
  std::optional<SeedRunId> active_run_id_;
  std::jthread worker_;
};

}  // namespace babel
