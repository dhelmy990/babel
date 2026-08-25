#include <catch2/catch_test_macros.hpp>

#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <utility>
#include <vector>

#include <openssl/evp.h>

#include "babel/adapters/huggingface/huggingface_article_source.hpp"

namespace {

using namespace babel;

std::string sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  REQUIRE(EVP_Digest(value.data(), value.size(), digest.data(), &size, EVP_sha256(), nullptr) ==
          1);
  REQUIRE(size == 32);
  constexpr char hex[] = "0123456789abcdef";
  std::string encoded;
  for (unsigned int index = 0; index < size; ++index) {
    encoded.push_back(hex[(digest[index] >> 4U) & 0x0fU]);
    encoded.push_back(hex[digest[index] & 0x0fU]);
  }
  return encoded;
}

std::string fixtureCatalog() {
  const auto path = std::filesystem::path(__FILE__).parent_path().parent_path() /
                    "fixtures/huggingface_catalog.jsonl";
  std::ifstream input(path);
  REQUIRE(input.good());
  return {std::istreambuf_iterator<char>{input}, {}};
}

class TemporaryDirectory final {
 public:
  TemporaryDirectory()
      : path_(std::filesystem::temp_directory_path() /
              ("babel-hf-source-" + std::to_string(++sequence_))) {
    std::filesystem::remove_all(path_);
    std::filesystem::create_directories(path_);
  }
  ~TemporaryDirectory() { std::filesystem::remove_all(path_); }
  const std::filesystem::path& path() const { return path_; }

 private:
  inline static int sequence_{0};
  std::filesystem::path path_;
};

class FakeHubTransport final : public HttpTransport {
 public:
  Result<HttpResponse> get(const HttpRequest& request) override {
    requests.push_back(request);
    if (request.url.find("/api/datasets/") != std::string::npos) {
      return HttpResponse{.status_code = metadata_status,
                          .body = "{\"sha\":\"" + commit_sha + "\"}"};
    }
    if (request.url.ends_with(".sha256")) {
      return HttpResponse{.status_code = artifact_status,
                          .body = sha256(catalog) + "  catalog.jsonl\n"};
    }
    return HttpResponse{.status_code = artifact_status, .body = catalog};
  }

  std::string commit_sha{std::string(40, 'a')};
  std::string catalog{fixtureCatalog()};
  long metadata_status{200};
  long artifact_status{200};
  std::vector<HttpRequest> requests;
};

SourceSelection selection() {
  return SourceSelection{
      .repository = "dhelmy990/babel-wikipedia-experiment",
      .configuration = "catalog_2026_06",
      .requested_revision = "wikipedia-experiment-data-v1",
      .artifact_path = "backend-seed/2026-06/catalog.jsonl",
  };
}

TEST_CASE("snapshot text becomes deterministic escaped paragraph HTML") {
  TemporaryDirectory cache;
  FakeHubTransport hub;
  HuggingFaceArticleSourceFactory factory(hub, cache.path(), "secret-token");

  const auto pinned = factory.pin(selection());
  REQUIRE(pinned.has_value());
  const auto article = (*pinned)->fetchByPageId(WikipediaPageId::fromInt(42).value());

  REQUIRE(article.has_value());
  CHECK(article->rendered_html ==
        "<p>First &amp; &lt;unsafe&gt;</p>\n<p>Second line continued</p>");
  REQUIRE(article->provenance.has_value());
  CHECK(article->provenance->commit_sha == std::string(40, 'a'));
  CHECK(article->provenance->article_key == "enwiki:42");
  CHECK(article->provenance->snapshot_date == "2026-06-01");
  CHECK(article->provenance->content_sha256 ==
        "12cea06c9c915caad144b9c4d9f30928b6129a91d83177a6c361ad8d17a56639");
}

TEST_CASE("Hugging Face titles use exact canonical and redirect lookup without fuzzy matching") {
  TemporaryDirectory cache;
  FakeHubTransport hub;
  HuggingFaceArticleSourceFactory factory(hub, cache.path(), "secret-token");
  const auto pinned = factory.pin(selection());
  REQUIRE(pinned.has_value());

  const auto canonical = (*pinned)->resolveTitle("Virtual_memory");
  const auto redirect = (*pinned)->resolveTitle("VM");
  const auto missing = (*pinned)->resolveTitle("Virtual mem");

  REQUIRE(canonical.has_value());
  CHECK(canonical->page_id.value == 42);
  REQUIRE(redirect.has_value());
  CHECK(redirect->canonical_title == "Virtual memory");
  CHECK_FALSE(missing.has_value());
  CHECK(missing.error().code == ErrorCode::wikipedia_not_found);
}

TEST_CASE("pin resolves one commit authenticates server side and reuses verified cache files") {
  TemporaryDirectory cache;
  FakeHubTransport first_hub;
  HuggingFaceArticleSourceFactory first_factory(first_hub, cache.path(), "private-hf-token");
  REQUIRE(first_factory.pin(selection()).has_value());
  REQUIRE(first_hub.requests.size() == 3);

  for (const auto& request : first_hub.requests) {
    CHECK(request.url.find("private-hf-token") == std::string::npos);
    REQUIRE(request.headers.size() == 1);
    CHECK(request.headers.front() == "Authorization: Bearer private-hf-token");
  }

  FakeHubTransport cached_hub;
  cached_hub.artifact_status = 503;
  HuggingFaceArticleSourceFactory cached_factory(cached_hub, cache.path(), "private-hf-token");
  const auto pinned = cached_factory.pin(selection());

  REQUIRE(pinned.has_value());
  REQUIRE(cached_hub.requests.size() == 1);
  CHECK(cached_hub.requests.front().url.find("/api/datasets/") != std::string::npos);
  const auto provenance = (*pinned)->provenance();
  CHECK(provenance.repository == selection().repository);
  CHECK(provenance.configuration == selection().configuration);
  CHECK(provenance.commit_sha == std::string(40, 'a'));
  CHECK(provenance.snapshot_date == "2026-06-01");
}

TEST_CASE("checksum mismatch refuses to construct a pinned source") {
  TemporaryDirectory cache;
  FakeHubTransport hub;
  hub.catalog += "corrupt";
  HuggingFaceArticleSourceFactory factory(hub, cache.path(), "secret-token");

  // The fake checksum follows the returned body, so force a stale cached checksum.
  const auto commit_directory = cache.path() / std::string(40, 'a');
  std::filesystem::create_directories(commit_directory);
  std::ofstream checksum(commit_directory / "catalog.jsonl.sha256");
  checksum << std::string(64, '0') << "  catalog.jsonl\n";
  checksum.close();

  const auto pinned = factory.pin(selection());
  CHECK_FALSE(pinned.has_value());
  CHECK(pinned.error().code == ErrorCode::internal);
}

TEST_CASE("real Hugging Face adapter accepts an explicitly pinned seed catalog",
          "[.remote]") {
  const auto* token = std::getenv("HF_TOKEN");
  const auto* revision = std::getenv("BABEL_HF_REMOTE_REVISION");
  const auto* artifact = std::getenv("BABEL_HF_REMOTE_ARTIFACT_PATH");
  if (token == nullptr || revision == nullptr || artifact == nullptr) {
    SKIP("explicit private-Hub acceptance environment is not configured");
  }

  TemporaryDirectory cache;
  CurlHttpTransport transport;
  HuggingFaceArticleSourceFactory factory(transport, cache.path(), token);
  const SourceSelection remote_selection{
      .repository = "dhelmy990/babel-wikipedia-experiment",
      .configuration = "demo_catalog_2026_06",
      .requested_revision = revision,
      .artifact_path = artifact,
  };

  const auto pinned = factory.pin(remote_selection);

  INFO((pinned ? "pin succeeded" : pinned.error().message));
  REQUIRE(pinned.has_value());
  CHECK((*pinned)->provenance().commit_sha == revision);
  CHECK((*pinned)->provenance().repository == remote_selection.repository);
  CHECK((*pinned)->provenance().configuration == remote_selection.configuration);
}

}  // namespace
