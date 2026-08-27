#pragma once

#include <functional>
#include <string>
#include <string_view>

#include "babel/application/experiment_ports.hpp"

namespace babel {

class PerformanceWorkerHttpClient final : public PerformanceExperimentWorker {
 public:
  using Post = std::function<Result<long>(std::string_view, std::string_view)>;

  PerformanceWorkerHttpClient(std::string endpoint, std::string token, Post post = {});

  Result<void> start(std::string_view experiment_id) override;
  Result<void> requestGracefulStop(std::string_view experiment_id) override;
  Result<void> approveNextScale(std::string_view experiment_id) override;

 private:
  Result<void> command(std::string_view experiment_id, std::string_view action);

  std::string endpoint_;
  std::string token_;
  Post post_;
};

}  // namespace babel
