#pragma once

#include <functional>

#include "babel/application/experiment_ports.hpp"

namespace babel {

class ExperimentJobRunner final : public ExperimentWorker {
 public:
  using Start = std::function<Result<void>(ExperimentRunId)>;
  using GracefulStop = std::function<Result<void>(ExperimentRunId)>;

  ExperimentJobRunner(Start, GracefulStop);

  Result<void> start(ExperimentRunId) override;
  Result<void> requestGracefulStop(ExperimentRunId) override;

 private:
  Start start_;
  GracefulStop graceful_stop_;
};

}  // namespace babel
