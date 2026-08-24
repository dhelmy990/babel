#include <catch2/catch_test_macros.hpp>

#include <array>
#include <string>
#include <string_view>
#include <unordered_set>

#include "babel/application/profile_manifest.hpp"

namespace {

struct ExpectedCreator {
  std::string_view slug;
  std::string_view display_name;
  std::string_view color;
  babel::CreatorKind kind;
  int order;
};

struct ExpectedAssignment {
  std::string_view creator_slug;
  std::string_view declared_title;
};

constexpr std::array<ExpectedCreator, 21> kExpectedCreators{{
    {"personal", "Personal", "#F4E7D3", babel::CreatorKind::personal, 0},
    {"distributed-systems", "Distributed Systems Creator", "#3DDC97", babel::CreatorKind::generated, 1},
    {"machine-learning-systems", "Machine Learning Systems Creator", "#4CC9F0", babel::CreatorKind::generated, 2},
    {"programming-languages", "Programming Languages Creator", "#F72585", babel::CreatorKind::generated, 3},
    {"cybersecurity-networks", "Cybersecurity and Networks Creator", "#FF9F1C", babel::CreatorKind::generated, 4},
    {"cpu-performance", "Low-Latency CPU and Performance Creator", "#A9DEF9", babel::CreatorKind::generated, 5},
    {"digital-art", "Digital Art Creator", "#E4C1F9", babel::CreatorKind::generated, 6},
    {"classical-visual-arts", "Classical Visual Arts Creator", "#FF6B6B", babel::CreatorKind::generated, 7},
    {"film-cinema", "Film and Cinema Creator", "#FFD166", babel::CreatorKind::generated, 8},
    {"literature-poetry", "Literature and Poetry Creator", "#06D6A0", babel::CreatorKind::generated, 9},
    {"theatre-performance", "Theatre and Performance Creator", "#EF476F", babel::CreatorKind::generated, 10},
    {"music-composition", "Music and Composition Creator", "#90BE6D", babel::CreatorKind::generated, 11},
    {"photography-design", "Photography and Graphic Design Creator", "#F9844A", babel::CreatorKind::generated, 12},
    {"computational-neuroscience", "Computational Neuroscience Creator", "#43AA8B", babel::CreatorKind::generated, 13},
    {"cognitive-neuroscience", "Cognitive Neuroscience Creator", "#7897C5", babel::CreatorKind::generated, 14},
    {"quantitative-finance", "Quantitative Finance Creator", "#F9C74F", babel::CreatorKind::generated, 15},
    {"macroeconomics-markets", "Macroeconomics and Markets Creator", "#00BBF9", babel::CreatorKind::generated, 16},
    {"corporate-finance", "Corporate Finance and Valuation Creator", "#F15BB5", babel::CreatorKind::generated, 17},
    {"public-policy", "Public Policy and Institutions Creator", "#9BDEAC", babel::CreatorKind::generated, 18},
    {"international-relations", "International Relations Creator", "#F8961E", babel::CreatorKind::generated, 19},
    {"political-economy", "Political Economy Creator", "#B8F2E6", babel::CreatorKind::generated, 20},
}};

constexpr std::array<ExpectedAssignment, 80> kExpectedAssignments{{
    {"distributed-systems", "Distributed computing"},
    {"distributed-systems", "Consensus (computer science)"},
    {"distributed-systems", "Operating system"},
    {"distributed-systems", "Database"},
    {"machine-learning-systems", "Machine learning"},
    {"machine-learning-systems", "Recommender system"},
    {"machine-learning-systems", "Graphics processing unit"},
    {"machine-learning-systems", "Artificial neural network"},
    {"programming-languages", "Programming language"},
    {"programming-languages", "Compiler"},
    {"programming-languages", "Type system"},
    {"programming-languages", "Functional programming"},
    {"cybersecurity-networks", "Computer security"},
    {"cybersecurity-networks", "Cryptography"},
    {"cybersecurity-networks", "Computer network"},
    {"cybersecurity-networks", "Malware"},
    {"cpu-performance", "Central processing unit"},
    {"cpu-performance", "CPU cache"},
    {"cpu-performance", "Branch predictor"},
    {"cpu-performance", "Instruction pipelining"},
    {"digital-art", "Digital art"},
    {"digital-art", "Computer graphics"},
    {"digital-art", "Generative art"},
    {"digital-art", "Animation"},
    {"classical-visual-arts", "Painting"},
    {"classical-visual-arts", "Renaissance art"},
    {"classical-visual-arts", "Sculpture"},
    {"classical-visual-arts", "Art history"},
    {"film-cinema", "Film"},
    {"film-cinema", "Cinematography"},
    {"film-cinema", "Film editing"},
    {"film-cinema", "Screenwriting"},
    {"literature-poetry", "Literature"},
    {"literature-poetry", "Novel"},
    {"literature-poetry", "Poetry"},
    {"literature-poetry", "Literary criticism"},
    {"theatre-performance", "Theatre"},
    {"theatre-performance", "Acting"},
    {"theatre-performance", "Stagecraft"},
    {"theatre-performance", "Play (theatre)"},
    {"music-composition", "Music"},
    {"music-composition", "Music theory"},
    {"music-composition", "Musical composition"},
    {"music-composition", "Electronic music"},
    {"photography-design", "Photography"},
    {"photography-design", "Graphic design"},
    {"photography-design", "Typography"},
    {"photography-design", "Visual arts"},
    {"computational-neuroscience", "Computational neuroscience"},
    {"computational-neuroscience", "Neural coding"},
    {"computational-neuroscience", "Artificial neural network"},
    {"computational-neuroscience", "Visual perception"},
    {"cognitive-neuroscience", "Cognitive neuroscience"},
    {"cognitive-neuroscience", "Memory"},
    {"cognitive-neuroscience", "Attention"},
    {"cognitive-neuroscience", "Functional magnetic resonance imaging"},
    {"quantitative-finance", "Algorithmic trading"},
    {"quantitative-finance", "Financial market"},
    {"quantitative-finance", "Derivative (finance)"},
    {"quantitative-finance", "Portfolio (finance)"},
    {"macroeconomics-markets", "Monetary policy"},
    {"macroeconomics-markets", "Inflation"},
    {"macroeconomics-markets", "Interest rate"},
    {"macroeconomics-markets", "Central bank"},
    {"corporate-finance", "Corporate finance"},
    {"corporate-finance", "Valuation (finance)"},
    {"corporate-finance", "Financial statement"},
    {"corporate-finance", "Stock"},
    {"public-policy", "Public policy"},
    {"public-policy", "Constitution"},
    {"public-policy", "Governance"},
    {"public-policy", "Regulation"},
    {"international-relations", "International relations"},
    {"international-relations", "Diplomacy"},
    {"international-relations", "Geopolitics"},
    {"international-relations", "International trade"},
    {"political-economy", "Political economy"},
    {"political-economy", "Economic inequality"},
    {"political-economy", "Tax"},
    {"political-economy", "Regulation"},
}};

}  // namespace

TEST_CASE("profile manifest exactly matches the creator and assignment catalog") {
  const auto creators = babel::ProfileManifest::creators();
  const auto assignments = babel::ProfileManifest::seedAssignments();

  REQUIRE(creators.size() == kExpectedCreators.size());
  REQUIRE(assignments.size() == kExpectedAssignments.size());

  std::unordered_set<std::string> creator_ids;
  std::unordered_set<std::string> creator_slugs;
  for (std::size_t index = 0; index < kExpectedCreators.size(); ++index) {
    const auto& actual = creators.at(index);
    const auto& expected = kExpectedCreators.at(index);
    const auto expected_id = babel::CreatorId::v5("creator:" + std::string(expected.slug));

    REQUIRE(expected_id.has_value());
    CHECK(actual.slug == expected.slug);
    CHECK(actual.display_name == expected.display_name);
    CHECK(actual.color == expected.color);
    CHECK(actual.kind == expected.kind);
    CHECK(actual.order == expected.order);
    CHECK(actual.id == expected_id.value());
    CHECK(creator_ids.insert(actual.id.value).second);
    CHECK(creator_slugs.insert(actual.slug).second);
  }

  std::unordered_set<std::string> assignment_ids;
  for (std::size_t index = 0; index < kExpectedAssignments.size(); ++index) {
    const auto& actual = assignments.at(index);
    const auto& expected = kExpectedAssignments.at(index);
    const auto expected_creator_id = babel::CreatorId::v5(
        "creator:" + std::string(expected.creator_slug));
    const auto expected_assignment_id = babel::SeedAssignmentId::v5(
        "seed:" + std::string(expected.creator_slug) + ":" + std::string(expected.declared_title));

    REQUIRE(expected_creator_id.has_value());
    REQUIRE(expected_assignment_id.has_value());
    CHECK(actual.creator_id == expected_creator_id.value());
    CHECK(actual.declared_title == expected.declared_title);
    CHECK(actual.id == expected_assignment_id.value());
    CHECK(assignment_ids.insert(actual.id.value).second);
  }

  CHECK(creator_ids.size() == kExpectedCreators.size());
  CHECK(creator_slugs.size() == kExpectedCreators.size());
  CHECK(assignment_ids.size() == kExpectedAssignments.size());
}
