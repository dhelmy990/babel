#pragma once

#include "babel/application/ports.hpp"

namespace babel {

class PostgresDatabase;

class PostgresCreatorRepository final : public CreatorRepository {
 public:
  explicit PostgresCreatorRepository(PostgresDatabase& database);

  Result<bool> exists(CreatorId id) override;
  Result<Creator> get(CreatorId id) override;
  Result<std::vector<Creator>> listOrdered() override;

 private:
  PostgresDatabase& database_;
};

class PostgresGraphRepository final : public GraphRepository {
 public:
  explicit PostgresGraphRepository(PostgresDatabase& database);

  Result<ProfileGraphDto> loadGraph(CreatorId creator_id) override;

 private:
  PostgresDatabase& database_;
};

class PostgresWikipediaBabelRepository final : public WikipediaBabelRepository {
 public:
  explicit PostgresWikipediaBabelRepository(PostgresDatabase& database);

  Result<std::optional<Babel>> findByPage(CreatorId owner_id,
                                           WikipediaPageId page_id) override;
  Result<void> insertWikipediaBabel(const Babel& babel, const BabelSource& source) override;
  Result<void> attachSeedAssignment(BabelId babel_id, SeedAssignmentId assignment_id,
                                    std::string_view declared_title) override;

 private:
  PostgresDatabase& database_;
};

class PostgresSeedRunRepository final : public SeedRunRepository {
 public:
  explicit PostgresSeedRunRepository(PostgresDatabase& database);

  Result<SeedRunId> createRun(std::string_view manifest_version,
                              std::span<const SeedAssignment> assignments) override;
  Result<bool> assignmentExists(SeedAssignmentId assignment_id) override;
  Result<void> recordItemState(SeedRunId run_id, SeedAssignmentId assignment_id,
                               const SeedItemUpdate& update) override;
  Result<void> setRunState(SeedRunId run_id, SeedRunState state) override;
  Result<SeedStatusDto> status(SeedRunId run_id) override;
  Result<SeedStatusDto> latestStatus() override;
  Result<void> markNonterminalAsInterrupted() override;

 private:
  PostgresDatabase& database_;
};

class PostgresLegacyMigrationRepository final : public LegacyMigrationRepository {
 public:
  explicit PostgresLegacyMigrationRepository(PostgresDatabase& database);

  Result<bool> digestExists(std::string_view sha256) override;
  Result<bool> importPersonalGraph(std::string_view sha256, std::span<const Babel> babels,
                                   std::span<const Edge> edges) override;

 private:
  PostgresDatabase& database_;
};

}  // namespace babel
