#include <catch2/catch_test_macros.hpp>

#include <string>
#include <type_traits>

#include "babel/application/dtos.hpp"

static_assert(std::is_same_v<decltype(babel::CreatorId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::BabelId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::EdgeId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::SeedRunId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::SeedAssignmentId::value), std::string>);
static_assert(std::is_same_v<decltype(babel::WikipediaPageId::value), std::int64_t>);

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
