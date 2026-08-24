#include "babel/application/profile_manifest.hpp"

#include <array>
#include <string_view>

namespace babel {
namespace {

struct CreatorDefinition {
  std::string_view slug;
  std::string_view display_name;
  std::string_view color;
  std::array<std::string_view, 4> titles;
};

constexpr std::array<CreatorDefinition, 20> kGeneratedCreators{{
    {"distributed-systems", "Distributed Systems Creator", "#3DDC97",
     {"Distributed computing", "Consensus (computer science)", "Operating system", "Database"}},
    {"machine-learning-systems", "Machine Learning Systems Creator", "#4CC9F0",
     {"Machine learning", "Recommender system", "Graphics processing unit",
      "Artificial neural network"}},
    {"programming-languages", "Programming Languages Creator", "#F72585",
     {"Programming language", "Compiler", "Type system", "Functional programming"}},
    {"cybersecurity-networks", "Cybersecurity and Networks Creator", "#FF9F1C",
     {"Computer security", "Cryptography", "Computer network", "Malware"}},
    {"cpu-performance", "Low-Latency CPU and Performance Creator", "#A9DEF9",
     {"Central processing unit", "CPU cache", "Branch predictor", "Instruction pipelining"}},
    {"digital-art", "Digital Art Creator", "#E4C1F9",
     {"Digital art", "Computer graphics", "Generative art", "Animation"}},
    {"classical-visual-arts", "Classical Visual Arts Creator", "#FF6B6B",
     {"Painting", "Renaissance art", "Sculpture", "Art history"}},
    {"film-cinema", "Film and Cinema Creator", "#FFD166",
     {"Film", "Cinematography", "Film editing", "Screenwriting"}},
    {"literature-poetry", "Literature and Poetry Creator", "#06D6A0",
     {"Literature", "Novel", "Poetry", "Literary criticism"}},
    {"theatre-performance", "Theatre and Performance Creator", "#EF476F",
     {"Theatre", "Acting", "Stagecraft", "Play (theatre)"}},
    {"music-composition", "Music and Composition Creator", "#90BE6D",
     {"Music", "Music theory", "Musical composition", "Electronic music"}},
    {"photography-design", "Photography and Graphic Design Creator", "#F9844A",
     {"Photography", "Graphic design", "Typography", "Visual arts"}},
    {"computational-neuroscience", "Computational Neuroscience Creator", "#43AA8B",
     {"Computational neuroscience", "Neural coding", "Artificial neural network",
      "Visual perception"}},
    {"cognitive-neuroscience", "Cognitive Neuroscience Creator", "#7897C5",
     {"Cognitive neuroscience", "Memory", "Attention", "Functional magnetic resonance imaging"}},
    {"quantitative-finance", "Quantitative Finance Creator", "#F9C74F",
     {"Algorithmic trading", "Financial market", "Derivative (finance)", "Portfolio (finance)"}},
    {"macroeconomics-markets", "Macroeconomics and Markets Creator", "#00BBF9",
     {"Monetary policy", "Inflation", "Interest rate", "Central bank"}},
    {"corporate-finance", "Corporate Finance and Valuation Creator", "#F15BB5",
     {"Corporate finance", "Valuation (finance)", "Financial statement", "Stock"}},
    {"public-policy", "Public Policy and Institutions Creator", "#9BDEAC",
     {"Public policy", "Constitution", "Governance", "Regulation"}},
    {"international-relations", "International Relations Creator", "#F8961E",
     {"International relations", "Diplomacy", "Geopolitics", "International trade"}},
    {"political-economy", "Political Economy Creator", "#B8F2E6",
     {"Political economy", "Economic inequality", "Tax", "Regulation"}},
}};

CreatorId creatorId(std::string_view slug) {
  return CreatorId::v5("creator:" + std::string(slug)).value();
}

SeedAssignmentId seedAssignmentId(std::string_view slug, std::string_view title) {
  return SeedAssignmentId::v5("seed:" + std::string(slug) + ":" + std::string(title)).value();
}

}  // namespace

std::vector<Creator> ProfileManifest::creators() {
  std::vector<Creator> result;
  result.reserve(kGeneratedCreators.size() + 1);
  result.push_back(Creator{
      .id = creatorId("personal"),
      .slug = "personal",
      .display_name = "Personal",
      .color = "#F4E7D3",
      .kind = CreatorKind::personal,
      .order = 0,
  });

  for (std::size_t index = 0; index < kGeneratedCreators.size(); ++index) {
    const auto& definition = kGeneratedCreators.at(index);
    result.push_back(Creator{
        .id = creatorId(definition.slug),
        .slug = std::string(definition.slug),
        .display_name = std::string(definition.display_name),
        .color = std::string(definition.color),
        .kind = CreatorKind::generated,
        .order = static_cast<int>(index + 1),
    });
  }
  return result;
}

std::vector<SeedAssignment> ProfileManifest::seedAssignments() {
  std::vector<SeedAssignment> result;
  result.reserve(kGeneratedCreators.size() * 4);

  for (const auto& definition : kGeneratedCreators) {
    const auto profile_id = creatorId(definition.slug);
    for (const auto title : definition.titles) {
      result.push_back(SeedAssignment{
          .id = seedAssignmentId(definition.slug, title),
          .creator_id = profile_id,
          .declared_title = std::string(title),
      });
    }
  }
  return result;
}

}  // namespace babel
