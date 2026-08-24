#pragma once

#include <cstddef>
#include <optional>
#include <span>
#include <string_view>
#include <vector>

#include "babel/application/dtos.hpp"

namespace babel {

class CreatorRepository {
 public:
  virtual ~CreatorRepository() = default;

  virtual Result<bool> exists(CreatorId) = 0;
  virtual Result<Creator> get(CreatorId) = 0;
  virtual Result<std::vector<Creator>> listOrdered() = 0;
};

class GraphRepository {
 public:
  virtual ~GraphRepository() = default;

  // Return an empty graph for an existing creator with no content; use not_found only when absent.
  virtual Result<ProfileGraphDto> loadGraph(CreatorId) = 0;
};

class WikipediaBabelRepository {
 public:
  virtual ~WikipediaBabelRepository() = default;

  virtual Result<std::optional<Babel>> findByPage(CreatorId, WikipediaPageId) = 0;
  // Commit the Babel and its source atomically, or persist neither record.
  virtual Result<void> insertWikipediaBabel(const Babel&, const BabelSource&) = 0;
  virtual Result<void> attachSeedAssignment(BabelId, SeedAssignmentId, std::string_view) = 0;
};

class SeedRunRepository {
 public:
  virtual ~SeedRunRepository() = default;

  // Atomically snapshot every assignment as a pending seed_run_items row and immutable total.
  virtual Result<SeedRunId> createRun(std::string_view manifest_version,
                                      std::span<const SeedAssignment> assignments) = 0;
  virtual Result<bool> assignmentExists(SeedAssignmentId) = 0;
  // Atomically persist the transition outcome and its optional resolved/imported/error fields.
  virtual Result<void> recordItemState(SeedRunId, SeedAssignmentId,
                                       const SeedItemUpdate&) = 0;
  virtual Result<void> setRunState(SeedRunId, SeedRunState) = 0;
  virtual Result<SeedStatusDto> status(SeedRunId) = 0;
  virtual Result<SeedStatusDto> latestStatus() = 0;
  virtual Result<void> markRunningAsInterrupted() = 0;
};

class LegacyMigrationRepository {
 public:
  virtual ~LegacyMigrationRepository() = default;

  virtual Result<bool> digestExists(std::string_view sha256) = 0;
  // True means this call claimed the digest and imported the graph; false is a repeated digest
  // no-op, including a concurrent import that claimed it first.
  virtual Result<bool> importPersonalGraph(std::string_view sha256, std::span<const Babel>,
                                           std::span<const Edge>) = 0;
};

class ArticleSource {
 public:
  virtual ~ArticleSource() = default;

  virtual Result<ResolvedWikipediaPage> resolveTitle(std::string_view) = 0;
  virtual Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) = 0;
};

class HtmlSanitizer {
 public:
  virtual ~HtmlSanitizer() = default;

  virtual Result<SanitizedHtml> sanitize(std::string_view html,
                                         std::string_view canonical_url) = 0;
};

class IdGenerator {
 public:
  virtual ~IdGenerator() = default;

  virtual BabelId newBabelId() = 0;
  virtual EdgeId newEdgeId() = 0;
};

}  // namespace babel
