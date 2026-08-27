#pragma once

#include "babel/application/experiment_ports.hpp"

namespace babel {

class PostgresDatabase;

class PostgresExperimentRepository final : public ExperimentRepository {
 public:
  explicit PostgresExperimentRepository(PostgresDatabase&);

  Result<std::vector<RecommenderModelDto>> listModels() override;
  Result<RecommenderModelDto> getModel(RecommenderModelId) override;
  Result<ExperimentRunStatusDto> createRun(const ExperimentLaunchSnapshot&) override;
  Result<ExperimentRunStatusDto> latestRun() override;
  Result<ExperimentRunStatusDto> getRun(ExperimentRunId) override;
  Result<ExperimentRunStatusDto> requestGracefulStop(ExperimentRunId) override;
  Result<void> markLaunchFailed(ExperimentRunId, std::string_view message) override;
  Result<void> markInterruptedRuns();
  Result<std::vector<ExperimentActivityDto>> activity(
      ExperimentRunId, std::uint64_t after_sequence, std::size_t limit) override;
  Result<PerformanceExperimentDto> createPerformanceExperiment(
      const PerformanceLaunchRequest&) override;
  Result<std::vector<PerformanceExperimentDto>> listPerformanceExperiments(
      std::size_t limit, std::optional<std::string> before) override;
  Result<PerformanceExperimentDto> getPerformanceExperiment(
      std::string_view experiment_id) override;
  Result<PerformanceExperimentDto> requestPerformanceGracefulStop(
      std::string_view experiment_id) override;
  Result<PerformanceExperimentDto> approvePerformanceNextScale(
      std::string_view experiment_id) override;
  Result<PerformanceExperimentDto> markPerformancePopulationReady(
      std::string_view experiment_id, const PerformancePopulationEvidence&) override;
  Result<PerformanceExperimentDto> attachPerformanceArtifact(
      std::string_view experiment_id, const PerformanceArtifactReceipt&) override;
  Result<void> appendPerformanceProgress(
      std::string_view experiment_id, const PerformanceProgressDto&) override;
  Result<void> savePerformanceResult(
      std::string_view experiment_id, const PerformanceResultDto&) override;

 private:
  PostgresDatabase& database_;
};

}  // namespace babel
