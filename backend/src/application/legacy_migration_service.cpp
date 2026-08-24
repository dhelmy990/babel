#include "babel/application/legacy_migration_service.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <exception>
#include <initializer_list>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <nlohmann/json.hpp>
#include <openssl/evp.h>
#include <sys/stat.h>
#include <unistd.h>

#include "babel/application/profile_manifest.hpp"

namespace babel {
namespace {

// `.invalid` is reserved and cannot resolve on the public Internet. It gives the sanitizer a
// deterministic HTTPS parser base without attributing local legacy content to a real publisher.
constexpr std::string_view kLegacyCanonicalBase = "https://legacy.babel.invalid/graph";
// Local graph exports are expected to be small. This generous cap bounds memory and hashing work
// while allowing many maximum-sized editor documents in one source file.
constexpr std::size_t kMaxLegacySourceBytes = 64U * 1024U * 1024U;
constexpr std::size_t kSourceReadBufferBytes = 64U * 1024U;

ApplicationError invalidLegacy(std::string message) {
  return ApplicationError{.code = ErrorCode::invalid_legacy_file, .message = std::move(message)};
}

struct LegacyBabel {
  std::string id;
  std::string title;
  std::string description;
  std::string color;
};

struct LegacyEdge {
  std::string id;
  std::string source;
  std::string target;
};

struct LegacyGraph {
  std::vector<LegacyBabel> babels;
  std::vector<LegacyEdge> edges;
};

class DuplicateJsonKey final : public std::exception {
 public:
  const char* what() const noexcept override { return "duplicate JSON object key"; }
};

class FileDescriptor final {
 public:
  explicit FileDescriptor(int value) : value_(value) {}
  ~FileDescriptor() {
    if (value_ >= 0) ::close(value_);
  }

  FileDescriptor(const FileDescriptor&) = delete;
  FileDescriptor& operator=(const FileDescriptor&) = delete;

  int get() const { return value_; }

 private:
  int value_;
};

bool hasNonWhitespace(std::string_view value) {
  return std::ranges::any_of(value, [](const unsigned char character) {
    return std::isspace(character) == 0;
  });
}

bool containsNul(std::string_view value) { return value.find('\0') != std::string_view::npos; }

bool validColor(std::string_view color) {
  return color.size() == 7 && color.front() == '#' &&
         std::ranges::all_of(color.substr(1), [](const unsigned char character) {
           return std::isxdigit(character) != 0;
         });
}

bool requiredString(const nlohmann::json& object, std::string_view field) {
  const auto found = object.find(field);
  return found != object.end() && found->is_string();
}

Result<nlohmann::json> parseJson(std::string_view bytes) {
  try {
    std::vector<std::unordered_set<std::string>> object_keys;
    auto reject_duplicate_keys = [&object_keys](int, nlohmann::json::parse_event_t event,
                                                nlohmann::json& parsed) {
      if (event == nlohmann::json::parse_event_t::object_start) {
        object_keys.emplace_back();
      } else if (event == nlohmann::json::parse_event_t::key) {
        if (object_keys.empty() || !object_keys.back().insert(parsed.get<std::string>()).second) {
          throw DuplicateJsonKey{};
        }
      } else if (event == nlohmann::json::parse_event_t::object_end) {
        object_keys.pop_back();
      }
      return true;
    };
    return nlohmann::json::parse(bytes, reject_duplicate_keys);
  } catch (const DuplicateJsonKey& exception) {
    return tl::make_unexpected(invalidLegacy("Legacy graph contains " +
                                             std::string(exception.what())));
  } catch (const nlohmann::json::exception& exception) {
    return tl::make_unexpected(
        invalidLegacy("Legacy graph JSON is malformed: " + std::string(exception.what())));
  }
}

Result<LegacyGraph> validateGraph(const nlohmann::json& graph) {
  if (!graph.is_object()) {
    return tl::make_unexpected(invalidLegacy("Legacy graph root must be an object"));
  }
  const auto babels_field = graph.find("babels");
  const auto edges_field = graph.find("edges");
  if (babels_field == graph.end() || !babels_field->is_array() || edges_field == graph.end() ||
      !edges_field->is_array()) {
    return tl::make_unexpected(
        invalidLegacy("Legacy graph must contain babels and edges arrays"));
  }

  LegacyGraph result;
  result.babels.reserve(babels_field->size());
  std::unordered_set<std::string> babel_ids;
  for (const auto& babel : *babels_field) {
    if (!babel.is_object() || !requiredString(babel, "id") || !requiredString(babel, "title") ||
        !requiredString(babel, "description") || !requiredString(babel, "color")) {
      return tl::make_unexpected(invalidLegacy(
          "Every legacy Babel must have string id, title, description, and color fields"));
    }
    LegacyBabel parsed{
        .id = babel.at("id").get<std::string>(),
        .title = babel.at("title").get<std::string>(),
        .description = babel.at("description").get<std::string>(),
        .color = babel.at("color").get<std::string>(),
    };
    if (containsNul(parsed.id) || containsNul(parsed.title) || containsNul(parsed.description) ||
        containsNul(parsed.color)) {
      return tl::make_unexpected(invalidLegacy("Legacy Babel fields must not contain NUL"));
    }
    if (!hasNonWhitespace(parsed.id)) {
      return tl::make_unexpected(invalidLegacy("Legacy Babel ID must not be blank"));
    }
    if (!hasNonWhitespace(parsed.title)) {
      return tl::make_unexpected(invalidLegacy("Legacy Babel title must not be blank"));
    }
    if (!validColor(parsed.color)) {
      return tl::make_unexpected(invalidLegacy("Legacy Babel color must use #RRGGBB form"));
    }
    if (!babel_ids.insert(parsed.id).second) {
      return tl::make_unexpected(invalidLegacy("Legacy Babel IDs must be unique"));
    }
    result.babels.push_back(std::move(parsed));
  }

  result.edges.reserve(edges_field->size());
  std::unordered_set<std::string> edge_ids;
  std::set<std::pair<std::string, std::string>> endpoint_pairs;
  for (const auto& edge : *edges_field) {
    if (!edge.is_object() || !requiredString(edge, "id") || !requiredString(edge, "source") ||
        !requiredString(edge, "target")) {
      return tl::make_unexpected(
          invalidLegacy("Every legacy edge must have string id, source, and target fields"));
    }
    LegacyEdge parsed{
        .id = edge.at("id").get<std::string>(),
        .source = edge.at("source").get<std::string>(),
        .target = edge.at("target").get<std::string>(),
    };
    if (containsNul(parsed.id) || containsNul(parsed.source) || containsNul(parsed.target)) {
      return tl::make_unexpected(invalidLegacy("Legacy edge fields must not contain NUL"));
    }
    if (!hasNonWhitespace(parsed.id)) {
      return tl::make_unexpected(invalidLegacy("Legacy edge ID must not be blank"));
    }
    if (!edge_ids.insert(parsed.id).second) {
      return tl::make_unexpected(invalidLegacy("Legacy edge IDs must be unique"));
    }
    if (!babel_ids.contains(parsed.source) || !babel_ids.contains(parsed.target)) {
      return tl::make_unexpected(invalidLegacy("Legacy edge references a missing Babel"));
    }
    if (parsed.source == parsed.target) {
      return tl::make_unexpected(invalidLegacy("Legacy edge must not reference itself"));
    }
    if (!endpoint_pairs.emplace(parsed.source, parsed.target).second) {
      return tl::make_unexpected(invalidLegacy("Legacy directed edges must be unique"));
    }
    result.edges.push_back(std::move(parsed));
  }
  return result;
}

Result<std::string> sha256(std::string_view bytes, std::string_view subject) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_Digest(bytes.data(), bytes.size(), digest.data(), &digest_size, EVP_sha256(), nullptr) !=
          1 ||
      digest_size != 32) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "OpenSSL could not hash " + std::string(subject),
    });
  }

  constexpr char hex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(digest_size * 2U);
  for (unsigned int index = 0; index < digest_size; ++index) {
    encoded.push_back(hex[(digest[index] >> 4U) & 0x0fU]);
    encoded.push_back(hex[digest[index] & 0x0fU]);
  }
  return encoded;
}

Result<std::string> readSource(const std::filesystem::path& source_path) {
  const FileDescriptor input(
      ::open(source_path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
  if (input.get() < 0) {
    return tl::make_unexpected(
        invalidLegacy("Legacy graph source must be an accessible non-symlink regular file"));
  }

  struct stat metadata {};
  if (::fstat(input.get(), &metadata) != 0 || !S_ISREG(metadata.st_mode)) {
    return tl::make_unexpected(
        invalidLegacy("Legacy graph source must be a regular file"));
  }
  if (metadata.st_size < 0 ||
      static_cast<std::uintmax_t>(metadata.st_size) > kMaxLegacySourceBytes) {
    return tl::make_unexpected(invalidLegacy("Legacy graph source exceeds the 64 MiB size limit"));
  }

  std::string bytes;
  bytes.reserve(static_cast<std::size_t>(metadata.st_size));
  std::array<char, kSourceReadBufferBytes> buffer{};
  while (true) {
    const auto count = ::read(input.get(), buffer.data(), buffer.size());
    if (count > 0) {
      const auto byte_count = static_cast<std::size_t>(count);
      if (byte_count > kMaxLegacySourceBytes - bytes.size()) {
        return tl::make_unexpected(
            invalidLegacy("Legacy graph source exceeds the 64 MiB size limit"));
      }
      bytes.append(buffer.data(), byte_count);
      continue;
    }
    if (count == 0) break;
    if (errno == EINTR) continue;
    return tl::make_unexpected(invalidLegacy("Legacy graph file could not be read completely"));
  }
  return bytes;
}

std::string stableLegacyName(const CreatorId& owner, std::string_view entity_kind,
                             std::initializer_list<std::string_view> identity_parts) {
  std::string name = "legacy:" + owner.value + ":" + std::string(entity_kind);
  // Length prefixes keep arbitrary legacy IDs unambiguous even when they contain separators.
  for (const auto part : identity_parts) {
    name += ":" + std::to_string(part.size()) + ":" + std::string(part);
  }
  return name;
}

}  // namespace

LegacyMigrationService::LegacyMigrationService(CreatorId personal_creator_id,
                                               LegacyMigrationRepository& repository,
                                               HtmlSanitizer& sanitizer)
    : personal_creator_id_(std::move(personal_creator_id)),
      repository_(repository),
      sanitizer_(sanitizer) {}

Result<LegacyMigrationResult> LegacyMigrationService::migrateFile(
    std::filesystem::path source_path) {
  const auto manifest = ProfileManifest::creators();
  if (manifest.empty() || manifest.front().slug != "personal" ||
      manifest.front().kind != CreatorKind::personal || manifest.front().id != personal_creator_id_) {
    return invalidArgument("legacy migration owner must be the manifest Personal creator");
  }

  auto bytes = readSource(source_path);
  if (!bytes) return tl::make_unexpected(bytes.error());
  auto source_digest = sha256(*bytes, "legacy graph source bytes");
  if (!source_digest) return tl::make_unexpected(source_digest.error());

  auto existing = repository_.digestExists(*source_digest);
  if (!existing) return tl::make_unexpected(existing.error());
  if (*existing) {
    return LegacyMigrationResult{
        .status = LegacyMigrationStatus::already_migrated,
        .babel_count = 0,
        .edge_count = 0,
    };
  }

  auto parsed_json = parseJson(*bytes);
  if (!parsed_json) return tl::make_unexpected(parsed_json.error());
  auto graph = validateGraph(*parsed_json);
  if (!graph) return tl::make_unexpected(graph.error());

  try {
    std::unordered_map<std::string, BabelId> id_map;
    std::unordered_set<std::string> generated_babel_ids;
    std::vector<Babel> babels;
    babels.reserve(graph->babels.size());
    for (const auto& legacy : graph->babels) {
      std::string content_html;
      if (hasNonWhitespace(legacy.description)) {
        auto sanitized = sanitizer_.sanitize(legacy.description, kLegacyCanonicalBase);
        if (!sanitized) {
          return tl::make_unexpected(invalidLegacy("Legacy Babel description was rejected: " +
                                                   sanitized.error().message));
        }
        content_html = std::move(sanitized->value);
      } else {
        content_html = "<p><br></p>";
      }
      auto content_hash = sha256(content_html, "sanitized legacy HTML");
      if (!content_hash) return tl::make_unexpected(content_hash.error());
      auto id = BabelId::v5(stableLegacyName(personal_creator_id_, "babel", {legacy.id}));
      if (!id) return tl::make_unexpected(id.error());
      if (!generated_babel_ids.insert(id->value).second) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "Stable legacy Babel UUID derivation produced a collision",
        });
      }
      id_map.emplace(legacy.id, *id);
      babels.push_back(Babel{
          .id = *id,
          .owner_id = personal_creator_id_,
          .title = legacy.title,
          .content_html = std::move(content_html),
          .color = legacy.color,
          .content_revision = 1,
          .content_hash = std::move(*content_hash),
      });
    }

    std::vector<Edge> edges;
    std::unordered_set<std::string> generated_edge_ids;
    edges.reserve(graph->edges.size());
    for (const auto& legacy : graph->edges) {
      auto edge_id = EdgeId::v5(stableLegacyName(personal_creator_id_, "edge",
                                                {legacy.source, legacy.target}));
      if (!edge_id) return tl::make_unexpected(edge_id.error());
      if (!generated_edge_ids.insert(edge_id->value).second) {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "Stable legacy edge UUID derivation produced a collision",
        });
      }
      edges.push_back(Edge{
          .id = *edge_id,
          .owner_id = personal_creator_id_,
          .source_id = id_map.at(legacy.source),
          .target_id = id_map.at(legacy.target),
      });
    }

    auto imported = repository_.importPersonalGraph(*source_digest, babels, edges);
    if (!imported) return tl::make_unexpected(imported.error());
    return LegacyMigrationResult{
        .status = *imported ? LegacyMigrationStatus::imported
                            : LegacyMigrationStatus::already_migrated,
        .babel_count = *imported ? babels.size() : 0,
        .edge_count = *imported ? edges.size() : 0,
    };
  } catch (const std::out_of_range&) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "Validated legacy graph lost an ID mapping",
    });
  }
}

}  // namespace babel
