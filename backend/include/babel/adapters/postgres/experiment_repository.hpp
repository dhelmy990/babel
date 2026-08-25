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

 private:
  PostgresDatabase& database_;
};

}  // namespace babel
