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
  virtual Result<PerformanceExperimentDto> createPerformanceExperiment(
      const PerformanceLaunchRequest&) = 0;
  virtual Result<std::vector<PerformanceExperimentDto>> listPerformanceExperiments(
      std::size_t limit, std::optional<std::string> before) = 0;
  virtual Result<PerformanceExperimentDto> getPerformanceExperiment(
      std::string_view experiment_id) = 0;
  virtual Result<PerformanceExperimentDto> requestPerformanceGracefulStop(
      std::string_view experiment_id) = 0;
  virtual Result<PerformanceExperimentDto> approvePerformanceNextScale(
      std::string_view experiment_id) = 0;
  virtual Result<void> markPerformanceLaunchFailed(
      std::string_view experiment_id, std::string_view message) = 0;
  virtual Result<PerformanceExperimentDto> markPerformancePopulationReady(
      std::string_view experiment_id, const PerformancePopulationEvidence&) = 0;
  virtual Result<PerformanceExperimentDto> attachPerformanceArtifact(
      std::string_view experiment_id, const PerformanceArtifactReceipt&) = 0;
  virtual Result<void> appendPerformanceProgress(
      std::string_view experiment_id, const PerformanceProgressDto&) = 0;
  virtual Result<void> savePerformanceResult(
      std::string_view experiment_id, const PerformanceResultDto&) = 0;
};

class ExperimentWorker {
 public:
  virtual ~ExperimentWorker() = default;
  virtual Result<void> start(ExperimentRunId) = 0;
  virtual Result<void> requestGracefulStop(ExperimentRunId) = 0;
};

class PerformanceExperimentWorker {
 public:
  virtual ~PerformanceExperimentWorker() = default;
  virtual Result<void> start(std::string_view experiment_id) = 0;
  virtual Result<void> requestGracefulStop(std::string_view experiment_id) = 0;
  virtual Result<void> approveNextScale(std::string_view experiment_id) = 0;
};

}  // namespace babel
