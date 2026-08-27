#pragma once

#include <functional>
#include <string>
#include <string_view>

#include "babel/application/experiment_ports.hpp"

namespace babel {

class ExperimentWorkerHttpClient final : public ExperimentWorker {
 public:
  using Post = std::function<Result<long>(std::string_view, std::string_view)>;

  ExperimentWorkerHttpClient(std::string endpoint, std::string token, Post post = {});

  Result<void> start(ExperimentRunId) override;
  Result<void> requestGracefulStop(ExperimentRunId) override;

 private:
  Result<void> command(ExperimentRunId, std::string_view action);

  std::string endpoint_;
  std::string token_;
  Post post_;
};

}  // namespace babel
