#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <future>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <catch2/catch_test_macros.hpp>

#include "babel/adapters/html/libxml_html_sanitizer.hpp"
#include "babel/application/legacy_migration_service.hpp"
#include "babel/application/profile_manifest.hpp"

namespace {

using namespace babel;

std::filesystem::path fixturePath(std::string_view name) {
  return std::filesystem::path(__FILE__).parent_path().parent_path() / "fixtures" / name;
}

class FakeLegacyMigrationRepository final : public LegacyMigrationRepository {
 public:
  Result<bool> digestExists(std::string_view sha256) override {
    ++digest_count;
    last_digest = sha256;
    if (digest_error) return tl::make_unexpected(*digest_error);
    return digest_exists;
  }

  Result<bool> importPersonalGraph(std::string_view sha256, std::span<const Babel> babels,
                                   std::span<const Edge> edges) override {
    ++transaction_count;
    last_digest = sha256;
    imported_babels.assign(babels.begin(), babels.end());
    imported_edges.assign(edges.begin(), edges.end());
    if (import_error) return tl::make_unexpected(*import_error);
    return import_result;
  }

  bool digest_exists{false};
  bool import_result{true};
  std::optional<ApplicationError> digest_error;
  std::optional<ApplicationError> import_error;
  std::string last_digest;
  std::vector<Babel> imported_babels;
  std::vector<Edge> imported_edges;
  int digest_count{0};
  int transaction_count{0};
};

class RecordingSanitizer final : public HtmlSanitizer {
 public:
  Result<SanitizedHtml> sanitize(std::string_view html,
                                 std::string_view canonical_url) override {
    inputs.emplace_back(html);
    canonical_urls.emplace_back(canonical_url);
    if (sanitize_error) return tl::make_unexpected(*sanitize_error);
    const auto script = html.find("<script>");
    return SanitizedHtml{.value = std::string(html.substr(0, script)) +
                                 (script == std::string_view::npos ? "" : "</p>")};
  }

  std::vector<std::string> inputs;
  std::vector<std::string> canonical_urls;
  std::optional<ApplicationError> sanitize_error;
};

class CountingLibxmlSanitizer final : public HtmlSanitizer {
 public:
  Result<SanitizedHtml> sanitize(std::string_view html,
                                 std::string_view canonical_url) override {
    ++sanitize_count;
    return sanitizer_.sanitize(html, canonical_url);
  }

  int sanitize_count{0};

 private:
  LibxmlHtmlSanitizer sanitizer_;
};

CreatorId personalCreatorId() { return ProfileManifest::creators().front().id; }

std::string readBytes(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  REQUIRE(input.is_open());
  return std::string(std::istreambuf_iterator<char>(input), {});
}

class TemporaryLegacyFile {
 public:
  explicit TemporaryLegacyFile(std::string_view bytes)
      : path_(std::filesystem::temp_directory_path() /
              ("babel-legacy-migration-" +
               std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
               ".json")) {
    std::ofstream output(path_, std::ios::binary);
    REQUIRE(output.is_open());
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    REQUIRE(output.good());
  }

  ~TemporaryLegacyFile() {
    std::error_code ignored;
    std::filesystem::remove(path_, ignored);
  }

  const std::filesystem::path& path() const { return path_; }

 private:
  std::filesystem::path path_;
};

class TemporaryPath {
 public:
  explicit TemporaryPath(std::string_view suffix)
      : path_(std::filesystem::temp_directory_path() /
              ("babel-legacy-entry-" +
               std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
               std::string(suffix))) {}

  ~TemporaryPath() {
    std::error_code ignored;
    std::filesystem::remove(path_, ignored);
  }

  const std::filesystem::path& path() const { return path_; }

 private:
  std::filesystem::path path_;
};

ApplicationError testError(ErrorCode code, std::string message) {
  return ApplicationError{.code = code, .message = std::move(message)};
}

const Babel& babelWithTitle(const std::vector<Babel>& babels, std::string_view title) {
  const auto found = std::ranges::find(babels, title, &Babel::title);
  REQUIRE(found != babels.end());
  return *found;
}

TEST_CASE("legacy migration targets Personal and preserves edge connectivity") {
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(fixturePath("legacy_graph.json"));

  REQUIRE(result.has_value());
  CHECK(result->status == LegacyMigrationStatus::imported);
  CHECK(result->babel_count == 2);
  CHECK(result->edge_count == 1);
  REQUIRE(repository.imported_babels.size() == 2);
  REQUIRE(repository.imported_edges.size() == 1);
  CHECK(repository.imported_babels[0].owner_id == personalCreatorId());
  CHECK(repository.imported_babels[1].owner_id == personalCreatorId());
  CHECK(repository.imported_edges[0].owner_id == personalCreatorId());
  CHECK(repository.imported_edges[0].source_id == repository.imported_babels[0].id);
  CHECK(repository.imported_edges[0].target_id == repository.imported_babels[1].id);
  CHECK(repository.transaction_count == 1);
  CHECK(repository.last_digest ==
        "c16d958099a777f5413243738aec99add49fbc385f17b2e5a89c5b160c665171");
  CHECK(repository.imported_babels[0].content_html == "<p>Safe <strong>origin</strong></p>");
  CHECK(repository.imported_babels[0].content_html.find("Delta") == std::string::npos);
  CHECK(repository.imported_babels[0].content_revision == 1);
  CHECK(repository.imported_babels[0].content_hash ==
        "ed120c2894b5cfd4895f663bdcecb2ce5503a7da1cba6b0a31d0140431af8325");
  REQUIRE(sanitizer.canonical_urls.size() == 2);
  CHECK(sanitizer.canonical_urls[0] == "https://legacy.babel.invalid/graph");
}

TEST_CASE("repeated legacy parsing derives stable entity UUIDs from Personal and legacy identity") {
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);
  const auto path = fixturePath("legacy_graph.json");

  REQUIRE(service.migrateFile(path).has_value());
  const auto first_babels = repository.imported_babels;
  const auto first_edges = repository.imported_edges;
  REQUIRE(service.migrateFile(path).has_value());

  const auto owner = personalCreatorId().value;
  const auto expected_origin =
      BabelId::v5("legacy:" + owner + ":babel:13:1726502400000").value();
  const auto expected_destination =
      BabelId::v5("legacy:" + owner + ":babel:13:1726502401000").value();
  const auto expected_edge = EdgeId::v5("legacy:" + owner +
                                        ":edge:13:1726502400000:13:1726502401000")
                                 .value();
  CHECK(babelWithTitle(first_babels, "Origin").id == expected_origin);
  CHECK(babelWithTitle(first_babels, "Destination").id == expected_destination);
  REQUIRE(first_edges.size() == 1);
  CHECK(first_edges.front().id == expected_edge);
  CHECK(babelWithTitle(repository.imported_babels, "Origin").id == expected_origin);
  CHECK(babelWithTitle(repository.imported_babels, "Destination").id == expected_destination);
  REQUIRE(repository.imported_edges.size() == 1);
  CHECK(repository.imported_edges.front().id == expected_edge);
}

TEST_CASE("reordered legacy Babel arrays preserve stable UUIDs and edge connectivity") {
  TemporaryLegacyFile reordered(
      R"({"babels":[{"id":"1726502401000","title":"Destination","description":"<p>Connected destination</p>","color":"#FEDCBA"},{"id":"1726502400000","title":"Origin","description":"<p>Safe origin</p>","color":"#12ABEF"}],"edges":[{"id":"1726502400000-1726502401000","source":"1726502400000","target":"1726502401000"}]})");
  FakeLegacyMigrationRepository original_repository;
  FakeLegacyMigrationRepository reordered_repository;
  RecordingSanitizer original_sanitizer;
  RecordingSanitizer reordered_sanitizer;
  LegacyMigrationService original_service(personalCreatorId(), original_repository,
                                          original_sanitizer);
  LegacyMigrationService reordered_service(personalCreatorId(), reordered_repository,
                                           reordered_sanitizer);

  REQUIRE(original_service.migrateFile(fixturePath("legacy_graph.json")).has_value());
  REQUIRE(reordered_service.migrateFile(reordered.path()).has_value());

  const auto original_origin = babelWithTitle(original_repository.imported_babels, "Origin").id;
  const auto original_destination =
      babelWithTitle(original_repository.imported_babels, "Destination").id;
  CHECK(babelWithTitle(reordered_repository.imported_babels, "Origin").id == original_origin);
  CHECK(babelWithTitle(reordered_repository.imported_babels, "Destination").id ==
        original_destination);
  REQUIRE(reordered_repository.imported_edges.size() == 1);
  CHECK(reordered_repository.imported_edges.front().source_id == original_origin);
  CHECK(reordered_repository.imported_edges.front().target_id == original_destination);
  REQUIRE(original_repository.imported_edges.size() == 1);
  CHECK(reordered_repository.imported_edges.front().id ==
        original_repository.imported_edges.front().id);
}

TEST_CASE("legacy migration hashes exact bytes without changing source bytes or mtime") {
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);
  const auto path = fixturePath("legacy_graph.json");
  const auto bytes_before = readBytes(path);
  const auto mtime_before = std::filesystem::last_write_time(path);

  REQUIRE(service.migrateFile(path).has_value());

  CHECK(readBytes(path) == bytes_before);
  CHECK(std::filesystem::last_write_time(path) == mtime_before);
  CHECK(repository.last_digest ==
        "c16d958099a777f5413243738aec99add49fbc385f17b2e5a89c5b160c665171");
}

TEST_CASE("malformed and unreadable legacy files return typed errors without writes") {
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto malformed = service.migrateFile(fixturePath("legacy_graph_invalid.json"));
  const auto unreadable = service.migrateFile(fixturePath("does-not-exist.json"));

  REQUIRE_FALSE(malformed.has_value());
  CHECK(malformed.error().code == ErrorCode::invalid_legacy_file);
  REQUIRE_FALSE(unreadable.has_value());
  CHECK(unreadable.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.transaction_count == 0);
}

TEST_CASE("oversized regular legacy files are rejected before hashing or allocation") {
  constexpr std::uintmax_t expected_source_limit = 64U * 1024U * 1024U;
  TemporaryPath file(".json");
  {
    std::ofstream output(file.path(), std::ios::binary);
    REQUIRE(output.is_open());
    output.seekp(static_cast<std::streamoff>(expected_source_limit));
    output.put('x');
    REQUIRE(output.good());
  }
  REQUIRE(std::filesystem::file_size(file.path()) == expected_source_limit + 1U);
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(file.path());

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.digest_count == 0);
  CHECK(repository.transaction_count == 0);
  CHECK(sanitizer.inputs.empty());
  CHECK(std::filesystem::file_size(file.path()) == expected_source_limit + 1U);
}

TEST_CASE("legacy migration rejects a FIFO promptly without waiting for a writer") {
  TemporaryPath fifo(".fifo");
  REQUIRE(::mkfifo(fifo.path().c_str(), 0600) == 0);
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  auto migration = std::async(std::launch::async, [&] { return service.migrateFile(fifo.path()); });
  const auto completion = migration.wait_for(std::chrono::seconds(1));
  if (completion != std::future_status::ready) {
    const int unblock = ::open(fifo.path().c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC);
    REQUIRE(unblock >= 0);
    REQUIRE(::close(unblock) == 0);
  }
  const auto result = migration.get();

  CHECK(completion == std::future_status::ready);
  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.digest_count == 0);
  CHECK(repository.transaction_count == 0);
}

TEST_CASE("legacy migration rejects symlinks instead of following their target") {
  TemporaryPath link(".json");
  std::error_code link_error;
  std::filesystem::create_symlink(fixturePath("legacy_graph.json"), link.path(), link_error);
  REQUIRE_FALSE(link_error);
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(link.path());

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.digest_count == 0);
  CHECK(repository.transaction_count == 0);
}

TEST_CASE("legacy migration rejects invalid graph schemas and invariants before writes") {
  const std::vector<std::pair<std::string, std::string>> invalid_graphs{
      {"root must be an object", R"([])"},
      {"babels must be an array", R"({"babels":{},"edges":[]})"},
      {"edges must be an array", R"({"babels":[],"edges":{}})"},
      {"Babel fields are required",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>"}],"edges":[]})"},
      {"Babel fields have strict types",
       R"({"babels":[{"id":1,"title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[]})"},
      {"legacy IDs are not blank",
       R"({"babels":[{"id":" ","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[]})"},
      {"titles are not blank",
       R"({"babels":[{"id":"a","title":" \n\t","description":"<p>A</p>","color":"#123456"}],"edges":[]})"},
      {"colors use RRGGBB",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#FFF"}],"edges":[]})"},
      {"legacy Babel IDs are unique",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"a","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[]})"},
      {"edge fields are required",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[{"id":"e","source":"a"}]})"},
      {"edge fields have strict types",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[{"id":1,"source":"a","target":"a"}]})"},
      {"edges cannot reference missing Babels",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[{"id":"e","source":"a","target":"missing"}]})"},
      {"edges cannot be self references",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[{"id":"e","source":"a","target":"a"}]})"},
      {"legacy edge IDs are unique",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"b","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[{"id":"e","source":"a","target":"b"},{"id":"e","source":"b","target":"a"}]})"},
      {"directed edges are unique",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"b","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[{"id":"e1","source":"a","target":"b"},{"id":"e2","source":"a","target":"b"}]})"},
      {"duplicate JSON keys are ambiguous",
       R"({"babels":[{"id":"a","id":"b","title":"B","description":"<p>B</p>","color":"#123456"}],"edges":[]})"},
  };

  for (const auto& [name, json] : invalid_graphs) {
    DYNAMIC_SECTION(name) {
      TemporaryLegacyFile file(json);
      FakeLegacyMigrationRepository repository;
      RecordingSanitizer sanitizer;
      LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

      const auto result = service.migrateFile(file.path());

      REQUIRE_FALSE(result.has_value());
      CHECK(result.error().code == ErrorCode::invalid_legacy_file);
      CHECK(repository.transaction_count == 0);
      CHECK(sanitizer.inputs.empty());
    }
  }
}

TEST_CASE("embedded NUL in any legacy identity or content prevents sanitization and writes") {
  const std::vector<std::pair<std::string, std::string>> nul_graphs{
      {"Babel ID",
       R"({"babels":[{"id":"a\u0000","title":"A","description":"<p>A</p>","color":"#123456"}],"edges":[]})"},
      {"title",
       R"({"babels":[{"id":"a","title":"A\u0000","description":"<p>A</p>","color":"#123456"}],"edges":[]})"},
      {"description",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A\u0000</p>","color":"#123456"}],"edges":[]})"},
      {"color",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#12\u0000456"}],"edges":[]})"},
      {"edge ID",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"b","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[{"id":"e\u0000","source":"a","target":"b"}]})"},
      {"edge source",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"b","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[{"id":"e","source":"a\u0000","target":"b"}]})"},
      {"edge target",
       R"({"babels":[{"id":"a","title":"A","description":"<p>A</p>","color":"#123456"},{"id":"b","title":"B","description":"<p>B</p>","color":"#654321"}],"edges":[{"id":"e","source":"a","target":"b\u0000"}]})"},
  };

  for (const auto& [name, json] : nul_graphs) {
    DYNAMIC_SECTION(name) {
      TemporaryLegacyFile file(json);
      FakeLegacyMigrationRepository repository;
      RecordingSanitizer sanitizer;
      LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

      const auto result = service.migrateFile(file.path());

      REQUIRE_FALSE(result.has_value());
      CHECK(result.error().code == ErrorCode::invalid_legacy_file);
      CHECK(sanitizer.inputs.empty());
      CHECK(repository.transaction_count == 0);
    }
  }
}

TEST_CASE("unsafe-only legacy HTML is rejected by the real sanitizer before writes") {
  TemporaryLegacyFile file(
      R"({"babels":[{"id":"a","title":"A","description":"<script>alert('legacy')</script>","color":"#123456"}],"edges":[]})");
  FakeLegacyMigrationRepository repository;
  LibxmlHtmlSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(file.path());

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.transaction_count == 0);
}

TEST_CASE("blank legacy descriptions become canonical empty Quill HTML without sanitizing") {
  TemporaryLegacyFile file(
      R"({"babels":[{"id":"empty","title":"Empty","description":"","color":"#123456"},{"id":"whitespace","title":"Whitespace","description":" \n\t","color":"#654321"}],"edges":[]})");
  FakeLegacyMigrationRepository repository;
  CountingLibxmlSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(file.path());

  REQUIRE(result.has_value());
  CHECK(result->status == LegacyMigrationStatus::imported);
  REQUIRE(repository.imported_babels.size() == 2);
  CHECK(babelWithTitle(repository.imported_babels, "Empty").content_html == "<p><br></p>");
  CHECK(babelWithTitle(repository.imported_babels, "Whitespace").content_html ==
        "<p><br></p>");
  CHECK(sanitizer.sanitize_count == 0);
  CHECK(repository.transaction_count == 1);
}

TEST_CASE("sanitizer rejection becomes invalid legacy file and prevents writes") {
  TemporaryLegacyFile file(
      R"({"babels":[{"id":"a","title":"A","description":"<p>broken","color":"#123456"}],"edges":[]})");
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  sanitizer.sanitize_error = testError(ErrorCode::sanitizer_rejected, "malformed HTML");
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(file.path());

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_legacy_file);
  CHECK(repository.transaction_count == 0);
}

TEST_CASE("known source digest is an early no-op") {
  FakeLegacyMigrationRepository repository;
  repository.digest_exists = true;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(fixturePath("legacy_graph_invalid.json"));

  REQUIRE(result.has_value());
  CHECK(result->status == LegacyMigrationStatus::already_migrated);
  CHECK(result->babel_count == 0);
  CHECK(result->edge_count == 0);
  CHECK(repository.digest_count == 1);
  CHECK(repository.transaction_count == 0);
  CHECK(sanitizer.inputs.empty());
}

TEST_CASE("repository race no-op is reported as already migrated") {
  FakeLegacyMigrationRepository repository;
  repository.import_result = false;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(fixturePath("legacy_graph.json"));

  REQUIRE(result.has_value());
  CHECK(result->status == LegacyMigrationStatus::already_migrated);
  CHECK(result->babel_count == 0);
  CHECK(result->edge_count == 0);
  CHECK(repository.transaction_count == 1);
}

TEST_CASE("empty legacy graph is a valid atomic import") {
  TemporaryLegacyFile file(R"({"babels":[],"edges":[]})");
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(personalCreatorId(), repository, sanitizer);

  const auto result = service.migrateFile(file.path());

  REQUIRE(result.has_value());
  CHECK(result->status == LegacyMigrationStatus::imported);
  CHECK(result->babel_count == 0);
  CHECK(result->edge_count == 0);
  CHECK(repository.transaction_count == 1);
  CHECK(sanitizer.inputs.empty());
}

TEST_CASE("legacy migration rejects any owner other than manifest Personal") {
  FakeLegacyMigrationRepository repository;
  RecordingSanitizer sanitizer;
  LegacyMigrationService service(ProfileManifest::creators().at(1).id, repository, sanitizer);

  const auto result = service.migrateFile(fixturePath("does-not-exist.json"));

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_argument);
  CHECK(repository.digest_count == 0);
}

}  // namespace
