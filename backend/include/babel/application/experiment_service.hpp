#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "babel/application/experiment_ports.hpp"

namespace babel {

class ExperimentService final {
 public:
  ExperimentService(ExperimentRepository&, ExperimentWorker&, ExperimentSourcePin,
                    PerformanceExperimentWorker* performance_worker = nullptr);

  Result<std::vector<RecommenderModelDto>> listModels();
  Result<ExperimentRunStatusDto> latestRun();
  Result<ExperimentRunStatusDto> getRun(ExperimentRunId);
  Result<std::vector<ExperimentActivityDto>> activity(ExperimentRunId,
                                                       std::uint64_t after_sequence,
                                                       std::size_t limit);
  Result<ExperimentRunStatusDto> start(const ExperimentLaunchRequest&);
  Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId);
  Result<PerformanceExperimentDto> createPerformanceExperiment(
      const PerformanceLaunchRequest&);
  Result<std::vector<PerformanceExperimentDto>> listPerformanceExperiments(
      std::size_t limit, std::optional<std::string> before);
  Result<PerformanceExperimentDto> getPerformanceExperiment(std::string_view experiment_id);
  Result<PerformanceExperimentDto> requestPerformanceGracefulStop(
      std::string_view experiment_id);
  Result<PerformanceExperimentDto> approvePerformanceNextScale(
      std::string_view experiment_id);
  Result<PerformanceExperimentDto> markPerformancePopulationReady(
      std::string_view experiment_id, const PerformancePopulationEvidence&);
  Result<PerformanceExperimentDto> attachPerformanceArtifact(
      std::string_view experiment_id, const PerformanceArtifactReceipt&);
  Result<void> appendPerformanceProgress(std::string_view experiment_id,
                                         const PerformanceProgressDto&);
  Result<void> savePerformanceResult(std::string_view experiment_id,
                                     const PerformanceResultDto&);

 private:
  ExperimentRepository& repository_;
  ExperimentWorker& worker_;
  PerformanceExperimentWorker* performance_worker_;
  ExperimentSourcePin source_;
};

}  // namespace babel
