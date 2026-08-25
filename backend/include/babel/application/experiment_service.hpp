#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "babel/application/experiment_ports.hpp"

namespace babel {

class ExperimentService final {
 public:
  ExperimentService(ExperimentRepository&, ExperimentWorker&, ExperimentSourcePin);

  Result<std::vector<RecommenderModelDto>> listModels();
  Result<ExperimentRunStatusDto> latestRun();
  Result<ExperimentRunStatusDto> getRun(ExperimentRunId);
  Result<std::vector<ExperimentActivityDto>> activity(ExperimentRunId,
                                                       std::uint64_t after_sequence,
                                                       std::size_t limit);
  Result<ExperimentRunStatusDto> start(const ExperimentLaunchRequest&);
  Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId);

 private:
  ExperimentRepository& repository_;
  ExperimentWorker& worker_;
  ExperimentSourcePin source_;
};

}  // namespace babel
