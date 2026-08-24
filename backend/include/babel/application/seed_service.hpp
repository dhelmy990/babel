#pragma once

#include <array>
#include <chrono>
#include <cstddef>
#include <functional>
#include <optional>
#include <span>
#include <stop_token>
#include <vector>

#include "babel/application/wikipedia_import_service.hpp"

namespace babel {

class SeedRetryPolicy {
 public:
  using Delay = std::function<void(std::chrono::milliseconds)>;

  explicit SeedRetryPolicy(
      Delay delay,
      std::array<std::chrono::milliseconds, 2> retry_delays = {
          std::chrono::milliseconds{500}, std::chrono::milliseconds{1500}});

  [[nodiscard]] static SeedRetryPolicy withDefaultDelay();
  [[nodiscard]] static SeedRetryPolicy withoutDelay();

  [[nodiscard]] std::size_t maxAttempts() const noexcept;
  void waitBeforeRetry(std::size_t completed_attempt) const;

 private:
  Delay delay_;
  std::array<std::chrono::milliseconds, 2> retry_delays_;
};

class SeedService final {
 public:
  SeedService(std::vector<SeedAssignment>, SeedRunRepository&, ArticleSource&,
              WikipediaImporter&, SeedRetryPolicy);

  [[nodiscard]] std::span<const SeedAssignment> assignments() const noexcept;
  Result<void> run(SeedRunId, std::stop_token = {});

 private:
  Result<void> runImpl(SeedRunId, std::stop_token);
  Result<void> processAssignment(SeedRunId, const SeedAssignment&, std::stop_token);
  Result<void> recordFailure(SeedRunId, const SeedAssignment&, std::uint32_t,
                             std::optional<WikipediaPageId>, const ApplicationError&);
  Result<void> interruptRun(SeedRunId);
  Result<void> failRun(SeedRunId, const ApplicationError&);

  std::vector<SeedAssignment> assignments_;
  SeedRunRepository& runs_;
  ArticleSource& source_;
  WikipediaImporter& importer_;
  SeedRetryPolicy retry_policy_;
};

}  // namespace babel
