#pragma once

#include <mutex>
#include <stop_token>
#include <string>
#include <thread>

#include "babel/application/seed_service.hpp"

namespace babel {

class SeedJobRunner final {
 public:
  SeedJobRunner(std::string manifest_version, SeedService&, SeedRunRepository&);
  ~SeedJobRunner();

  SeedJobRunner(const SeedJobRunner&) = delete;
  SeedJobRunner& operator=(const SeedJobRunner&) = delete;

  Result<SeedRunId> start();
  Result<SeedStatusDto> currentStatus();
  Result<void> markInterruptedRuns();

 private:
  void execute(SeedRunId, std::stop_token) noexcept;
  void releaseActiveGuard() noexcept;

  std::string manifest_version_;
  SeedService& service_;
  SeedRunRepository& runs_;
  std::mutex mutex_;
  bool active_{false};
  std::jthread worker_;
};

}  // namespace babel
