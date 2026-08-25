#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

#include "babel/application/errors.hpp"
#include "babel/application/experiment_models.hpp"

namespace babel {

class ExperimentRepository {
 public:
  virtual ~ExperimentRepository() = default;

  virtual Result<std::vector<RecommenderModelDto>> listModels() = 0;
  virtual Result<RecommenderModelDto> getModel(RecommenderModelId) = 0;
  virtual Result<ExperimentRunStatusDto> createRun(const ExperimentLaunchSnapshot&) = 0;
  virtual Result<ExperimentRunStatusDto> latestRun() = 0;
  virtual Result<ExperimentRunStatusDto> getRun(ExperimentRunId) = 0;
  virtual Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId) = 0;
  virtual Result<void> markLaunchFailed(ExperimentRunId, std::string_view message) = 0;
  virtual Result<std::vector<ExperimentActivityDto>> activity(
      ExperimentRunId, std::uint64_t after_sequence, std::size_t limit) = 0;
};

class ExperimentWorker {
 public:
  virtual ~ExperimentWorker() = default;
  virtual Result<void> start(ExperimentRunId) = 0;
  virtual Result<void> requestGracefulStop(ExperimentRunId) = 0;
};

}  // namespace babel
