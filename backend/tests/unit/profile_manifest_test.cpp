#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <ranges>
#include <string>
#include <unordered_set>

#include "babel/application/profile_manifest.hpp"

TEST_CASE("profile manifest contains Personal and 20 generated archetypes") {
  const auto creators = babel::ProfileManifest::creators();
  const auto seeds = babel::ProfileManifest::seedAssignments();

  REQUIRE(creators.size() == 21);
  REQUIRE(creators.front().slug == "personal");
  REQUIRE(creators.front().kind == babel::CreatorKind::personal);
  REQUIRE(creators.front().order == 0);
  REQUIRE(creators.front().id == babel::CreatorId::v5("creator:personal").value());
  REQUIRE(seeds.size() == 80);
  REQUIRE(std::ranges::none_of(seeds, [&](const auto& seed) {
    return seed.creator_id == creators.front().id;
  }));
  REQUIRE(seeds.front().declared_title == "Distributed computing");
  REQUIRE(seeds.back().declared_title == "Regulation");
}

TEST_CASE("profile manifest groups four deterministic assignments per generated creator") {
  const auto creators = babel::ProfileManifest::creators();
  const auto seeds = babel::ProfileManifest::seedAssignments();

  std::unordered_set<std::string> assignment_ids;
  for (std::size_t creator_index = 1; creator_index < creators.size(); ++creator_index) {
    const auto& creator = creators.at(creator_index);
    REQUIRE(creator.kind == babel::CreatorKind::generated);
    REQUIRE(creator.order == static_cast<int>(creator_index));
    REQUIRE(creator.id == babel::CreatorId::v5("creator:" + creator.slug).value());

    const auto first_seed = seeds.begin() + static_cast<std::ptrdiff_t>((creator_index - 1) * 4);
    const auto last_seed = first_seed + 4;
    REQUIRE(std::all_of(first_seed, last_seed, [&](const auto& seed) {
      return seed.creator_id == creator.id;
    }));

    for (auto seed = first_seed; seed != last_seed; ++seed) {
      const auto expected = babel::SeedAssignmentId::v5(
          "seed:" + creator.slug + ":" + seed->declared_title);
      REQUIRE(expected.has_value());
      CHECK(seed->id == expected.value());
      CHECK(assignment_ids.insert(seed->id.value).second);
    }
  }

  CHECK(assignment_ids.size() == 80);
}
