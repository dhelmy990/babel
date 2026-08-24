#include <catch2/catch_test_macros.hpp>

#include <functional>
#include <optional>
#include <span>
#include <string>
#include <type_traits>

#include "babel/application/dtos.hpp"
#include "babel/application/ports.hpp"

static_assert(std::is_same_v<decltype(babel::CreatorId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::BabelId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::EdgeId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::SeedRunId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::SeedAssignmentId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::WikipediaPageId::value), std::int64_t>);
static_assert(std::is_same_v<decltype(babel::SeedAssignment::id), babel::SeedAssignmentId>);
static_assert(std::is_same_v<decltype(babel::SeedAssignment::creator_id), babel::CreatorId>);
static_assert(std::is_same_v<decltype(babel::SeedAssignment::declared_title), std::string>);
static_assert(std::is_same_v<decltype(babel::SeedItemUpdate::state), babel::SeedItemState>);
static_assert(std::is_same_v<decltype(babel::SeedItemUpdate::resolved_page_id),
                             std::optional<babel::WikipediaPageId>>);
static_assert(std::is_same_v<decltype(babel::SeedItemUpdate::babel_id),
                             std::optional<babel::BabelId>>);
static_assert(std::is_same_v<decltype(babel::SeedItemUpdate::error),
                             std::optional<babel::ApplicationError>>);

using CreateSeedRun = babel::Result<babel::SeedRunId> (babel::SeedRunRepository::*)(
    std::string_view, std::span<const babel::SeedAssignment>);
using RecordSeedItemState = babel::Result<void> (babel::SeedRunRepository::*)(
    babel::SeedRunId, babel::SeedAssignmentId, const babel::SeedItemUpdate&);

static_assert(std::is_same_v<decltype(&babel::SeedRunRepository::createRun), CreateSeedRun>);
static_assert(std::is_same_v<decltype(&babel::SeedRunRepository::recordItemState),
                             RecordSeedItemState>);

TEST_CASE("an empty personal profile graph is a successful result") {
  const auto personal_id = babel::CreatorId::parse(
      "00000000-0000-5000-8000-000000000000");
  REQUIRE(personal_id.has_value());

  babel::ProfileGraphDto graph{
      .profile = babel::ProfileSummaryDto{
          .id = personal_id.value(),
          .display_name = "Personal",
          .color = "#F4E7D3",
          .order = 0,
      },
      .babels = {},
      .edges = {},
  };

  babel::Result<babel::ProfileGraphDto> result = graph;

  REQUIRE(result.has_value());
  CHECK(result->profile.id.value == "00000000-0000-5000-8000-000000000000");
  CHECK(result->profile.display_name == "Personal");
  CHECK(result->babels.empty());
  CHECK(result->edges.empty());
}

TEST_CASE("Wikipedia page identifiers require a positive value") {
  const auto accepted = babel::WikipediaPageId::fromInt(1);
  const auto rejected = babel::WikipediaPageId::fromInt(0);

  REQUIRE(accepted.has_value());
  CHECK(accepted->value == 1);
  REQUIRE_FALSE(rejected.has_value());
}

TEST_CASE("Creator UUID v5 generation uses the fixed Babel namespace") {
  const auto generated = babel::CreatorId::v5("creator:personal");

  REQUIRE(generated.has_value());
  CHECK(generated->value == "4b98a489-2eef-5cf8-90db-e21c5f7e067c");
}

TEST_CASE("Creator UUID parsing rejects malformed input") {
  const auto malformed = babel::CreatorId::parse("not-a-uuid");

  REQUIRE_FALSE(malformed.has_value());
}

TEST_CASE("Creator UUID parsing canonicalizes case and preserves equality and hashing") {
  const auto uppercase = babel::CreatorId::parse("ABCDEFAB-CDEF-5ABC-8DEF-ABCDEFABCDEF");
  const auto lowercase = babel::CreatorId::parse("abcdefab-cdef-5abc-8def-abcdefabcdef");

  REQUIRE(uppercase.has_value());
  REQUIRE(lowercase.has_value());
  CHECK(uppercase->value == "abcdefab-cdef-5abc-8def-abcdefabcdef");
  CHECK(*uppercase == *lowercase);
  CHECK(std::hash<babel::CreatorId>{}(*uppercase) == std::hash<babel::CreatorId>{}(*lowercase));
}
