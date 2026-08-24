#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "babel/application/errors.hpp"
#include "babel/domain/models.hpp"

namespace babel {

struct ProfileSummaryDto {
  CreatorId id;
  std::string display_name;
  std::string color;
  int order;
};

struct BabelDto {
  BabelId id;
  std::string title;
  std::string content_html;
  std::string color;
  std::uint64_t content_revision;
};

struct EdgeDto {
  EdgeId id;
  BabelId source_id;
  BabelId target_id;
};

struct ProfileGraphDto {
  ProfileSummaryDto profile;
  std::vector<BabelDto> babels;
  std::vector<EdgeDto> edges;
};

struct SeedAssignment {
  SeedAssignmentId id;
  CreatorId creator_id;
  std::string declared_title;
};

struct SeedItemUpdate {
  SeedItemState state;
  std::uint32_t attempt_count{0};
  std::optional<WikipediaPageId> resolved_page_id;
  std::optional<BabelId> babel_id;
  std::optional<ApplicationError> error;
};

enum class SeedStatusKind { not_started, persisted };

struct SeedStatusDto {
  SeedStatusKind kind{SeedStatusKind::not_started};
  std::optional<SeedRunId> run_id;
  std::optional<SeedRunState> run_state;
  std::size_t total{0};
  std::size_t imported{0};
  std::size_t skipped{0};
  std::size_t failed{0};
};

}  // namespace babel
