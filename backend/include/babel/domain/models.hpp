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
  std::optional<std::string> source_repository{};
  std::optional<std::string> source_config{};
  std::optional<std::string> source_commit_sha{};
  std::optional<std::string> source_article_key{};
  std::optional<std::string> source_snapshot_date{};
  std::optional<std::string> source_content_sha256{};
};

struct SourceSelection {
  std::string repository;
  std::string configuration;
  std::string requested_revision;
  std::string artifact_path;
};

struct PinnedSourceProvenance {
  std::string repository;
  std::string configuration;
  std::string commit_sha;
  std::string snapshot_date;
};

struct ArticleProvenance {
  std::string repository;
  std::string configuration;
  std::string commit_sha;
  std::string article_key;
  std::string snapshot_date;
  std::string content_sha256;
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
  std::optional<ArticleProvenance> provenance{};
};

struct SanitizedHtml {
  std::string value;
};

}  // namespace babel
