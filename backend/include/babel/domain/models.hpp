#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "babel/domain/ids.hpp"

namespace babel {

enum class CreatorKind { personal, generated };

enum class SeedRunState {
  queued,
  running,
  completed,
  completed_with_errors,
  failed,
  interrupted,
};

enum class SeedItemState {
  pending,
  resolving,
  importing,
  imported,
  skipped,
  failed,
};

struct Creator {
  CreatorId id;
  std::string slug;
  std::string display_name;
  std::string color;
  CreatorKind kind;
  int order;
};

struct Babel {
  BabelId id;
  CreatorId owner_id;
  std::string title;
  std::string content_html;
  std::string color;
  std::uint64_t content_revision;
  std::string content_hash;
};

struct Edge {
  EdgeId id;
  CreatorId owner_id;
  BabelId source_id;
  BabelId target_id;
};

struct BabelSource {
  BabelId babel_id;
  CreatorId owner_id;
  std::string provider;
  WikipediaPageId external_page_id;
  std::string canonical_url;
  std::optional<std::int64_t> source_revision_id;
  std::optional<SeedAssignmentId> seed_assignment_id;
  std::string declared_title;
};

struct ResolvedWikipediaPage {
  WikipediaPageId page_id;
  std::string canonical_title;
  std::string canonical_url;
};

struct RawWikipediaArticle {
  WikipediaPageId page_id;
  std::string canonical_title;
  std::string canonical_url;
  std::optional<std::int64_t> revision_id;
  std::string rendered_html;
};

struct SanitizedHtml {
  std::string value;
};

}  // namespace babel
