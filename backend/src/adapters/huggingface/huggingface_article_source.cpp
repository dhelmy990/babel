#include "babel/adapters/huggingface/huggingface_article_source.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <fstream>
#include <iterator>
#include <optional>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <openssl/evp.h>

namespace babel {
namespace {

struct CatalogArticle {
  WikipediaPageId page_id;
  std::string canonical_title;
  std::string canonical_url;
  std::optional<std::int64_t> source_revision_id;
  std::string rendered_html;
  std::string article_key;
  std::string snapshot_date;
  std::string content_sha256;
};

ApplicationError unavailable(std::string message) {
  return ApplicationError{.code = ErrorCode::wikipedia_unavailable,
                          .message = std::move(message)};
}

ApplicationError invalidCatalog(std::string message) {
  return ApplicationError{.code = ErrorCode::internal, .message = std::move(message)};
}

bool lowercaseHex(std::string_view value, std::size_t size) {
  return value.size() == size &&
         std::ranges::all_of(value, [](unsigned char character) {
           return (character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f');
         });
}

Result<std::string> sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_Digest(value.data(), value.size(), digest.data(), &digest_size, EVP_sha256(), nullptr) !=
          1 ||
      digest_size != 32) {
    return tl::make_unexpected(invalidCatalog("could not hash Hugging Face artifact"));
  }
  constexpr char hex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(64);
  for (unsigned int index = 0; index < digest_size; ++index) {
    encoded.push_back(hex[(digest[index] >> 4U) & 0x0fU]);
    encoded.push_back(hex[digest[index] & 0x0fU]);
  }
  return encoded;
}

std::string normalizeTitle(std::string_view title) {
  std::string normalized;
  normalized.reserve(title.size());
  bool pending_space = false;
  for (const unsigned char character : title) {
    if (character == '_' || std::isspace(character)) {
      pending_space = !normalized.empty();
      continue;
    }
    if (pending_space) normalized.push_back(' ');
    pending_space = false;
    normalized.push_back(static_cast<char>(character));
  }
  if (!normalized.empty() && normalized.front() >= 'a' && normalized.front() <= 'z') {
    normalized.front() = static_cast<char>(normalized.front() - 'a' + 'A');
  }
  return normalized;
}

std::string htmlEscape(std::string_view value) {
  std::string escaped;
  for (const unsigned char character : value) {
    switch (character) {
      case '&':
        escaped += "&amp;";
        break;
      case '<':
        escaped += "&lt;";
        break;
      case '>':
        escaped += "&gt;";
        break;
      case '"':
        escaped += "&quot;";
        break;
      case '\'':
        escaped += "&#39;";
        break;
      default:
        escaped.push_back(static_cast<char>(character));
    }
  }
  return escaped;
}

std::string collapseWhitespace(std::string_view value) {
  std::string collapsed;
  bool pending_space = false;
  for (const unsigned char character : value) {
    if (std::isspace(character)) {
      pending_space = !collapsed.empty();
    } else {
      if (pending_space) collapsed.push_back(' ');
      pending_space = false;
      collapsed.push_back(static_cast<char>(character));
    }
  }
  return collapsed;
}

std::string paragraphize(std::string text) {
  text.erase(std::remove(text.begin(), text.end(), '\r'), text.end());
  std::istringstream lines(text);
  std::vector<std::string> paragraphs;
  std::string current;
  std::string line;
  while (std::getline(lines, line)) {
    const auto normalized_line = collapseWhitespace(line);
    if (normalized_line.empty()) {
      if (!current.empty()) {
        paragraphs.push_back(std::move(current));
        current.clear();
      }
      continue;
    }
    if (!current.empty()) current.push_back(' ');
    current += normalized_line;
  }
  if (!current.empty()) paragraphs.push_back(std::move(current));

  std::string html;
  for (const auto& paragraph : paragraphs) {
    if (!html.empty()) html.push_back('\n');
    html += "<p>" + htmlEscape(paragraph) + "</p>";
  }
  return html;
}

std::string wikipediaUrl(std::string_view title) {
  constexpr char hex[] = "0123456789ABCDEF";
  std::string encoded;
  for (const unsigned char character : title) {
    if ((character >= 'A' && character <= 'Z') ||
        (character >= 'a' && character <= 'z') ||
        (character >= '0' && character <= '9') || character == '-' || character == '.' ||
        character == '~') {
      encoded.push_back(static_cast<char>(character));
    } else if (character == ' ') {
      encoded.push_back('_');
    } else {
      encoded.push_back('%');
      encoded.push_back(hex[(character >> 4U) & 0x0fU]);
      encoded.push_back(hex[character & 0x0fU]);
    }
  }
  return "https://en.wikipedia.org/wiki/" + encoded;
}

Result<std::string> readFile(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return tl::make_unexpected(invalidCatalog("could not read cached Hugging Face artifact"));
  }
  return std::string{std::istreambuf_iterator<char>{input}, {}};
}

Result<void> writeFile(const std::filesystem::path& path, std::string_view contents) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output || !(output.write(contents.data(), static_cast<std::streamsize>(contents.size())))) {
    return tl::make_unexpected(invalidCatalog("could not write Hugging Face cache artifact"));
  }
  return {};
}

std::string artifactUrl(const SourceSelection& selection, std::string_view commit,
                        std::string_view path) {
  return "https://huggingface.co/datasets/" + selection.repository + "/resolve/" +
         std::string(commit) + "/" + std::string(path);
}

Result<std::string> authenticatedGet(HttpTransport& transport, std::string_view token,
                                     std::string url) {
  auto response = transport.get(HttpRequest{
      .url = std::move(url),
      .connect_timeout_ms = 5000,
      .total_timeout_ms = 30000,
      .follow_redirects = true,
      .headers = {"Authorization: Bearer " + std::string(token)},
  });
  if (!response) return tl::make_unexpected(response.error());
  if (response->status_code != 200) {
    return tl::make_unexpected(unavailable("Hugging Face request failed"));
  }
  return std::move(response->body);
}

class CatalogSource final : public PinnedArticleSource {
 public:
  CatalogSource(PinnedSourceProvenance provenance,
                std::unordered_map<std::int64_t, CatalogArticle> articles,
                std::unordered_map<std::string, std::int64_t> titles,
                std::unordered_set<std::string> ambiguous_titles)
      : provenance_(std::move(provenance)),
        articles_(std::move(articles)),
        titles_(std::move(titles)),
        ambiguous_titles_(std::move(ambiguous_titles)) {}

  Result<ResolvedWikipediaPage> resolveTitle(std::string_view title) override {
    const auto normalized = normalizeTitle(title);
    if (ambiguous_titles_.contains(normalized)) {
      return tl::make_unexpected(invalidCatalog("snapshot title is ambiguous"));
    }
    const auto indexed = titles_.find(normalized);
    if (indexed == titles_.end()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::wikipedia_not_found,
          .message = "title not found in pinned Hugging Face snapshot",
      });
    }
    const auto& article = articles_.at(indexed->second);
    return ResolvedWikipediaPage{.page_id = article.page_id,
                                 .canonical_title = article.canonical_title,
                                 .canonical_url = article.canonical_url};
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId page_id) override {
    const auto found = articles_.find(page_id.value);
    if (found == articles_.end()) {
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::wikipedia_not_found,
          .message = "page not found in pinned Hugging Face snapshot",
      });
    }
    const auto& article = found->second;
    return RawWikipediaArticle{
        .page_id = article.page_id,
        .canonical_title = article.canonical_title,
        .canonical_url = article.canonical_url,
        .revision_id = article.source_revision_id,
        .rendered_html = article.rendered_html,
        .provenance = ArticleProvenance{
            .repository = provenance_.repository,
            .configuration = provenance_.configuration,
            .commit_sha = provenance_.commit_sha,
            .article_key = article.article_key,
            .snapshot_date = article.snapshot_date,
            .content_sha256 = article.content_sha256,
        },
    };
  }

  const PinnedSourceProvenance& provenance() const noexcept override { return provenance_; }

 private:
  PinnedSourceProvenance provenance_;
  std::unordered_map<std::int64_t, CatalogArticle> articles_;
  std::unordered_map<std::string, std::int64_t> titles_;
  std::unordered_set<std::string> ambiguous_titles_;
};

Result<std::shared_ptr<PinnedArticleSource>> parseCatalog(
    std::string_view catalog, const SourceSelection& selection, std::string commit_sha) {
  std::unordered_map<std::int64_t, CatalogArticle> articles;
  std::unordered_map<std::string, std::int64_t> titles;
  std::unordered_set<std::string> ambiguous_titles;
  std::optional<std::string> snapshot_date;
  std::istringstream lines{std::string(catalog)};
  std::string line;
  try {
    while (std::getline(lines, line)) {
      if (line.empty()) continue;
      const auto row = nlohmann::json::parse(line);
      const auto page = WikipediaPageId::fromInt(row.at("page_id").get<std::int64_t>());
      if (!page) return tl::make_unexpected(page.error());
      const auto title = row.at("canonical_title").get<std::string>();
      const auto text = row.at("article_text").get<std::string>();
      const auto article_key = row.at("article_key").get<std::string>();
      const auto row_snapshot = row.at("snapshot").get<std::string>();
      const auto content_hash = row.at("content_hash").get<std::string>();
      if (title.empty() || text.empty() || article_key.empty() || row_snapshot.empty() ||
          !lowercaseHex(content_hash, 64)) {
        return tl::make_unexpected(invalidCatalog("invalid Hugging Face catalog row"));
      }
      const auto text_hash = sha256(text);
      if (!text_hash || *text_hash != content_hash) {
        return tl::make_unexpected(invalidCatalog("Hugging Face row content checksum mismatch"));
      }
      if (snapshot_date && *snapshot_date != row_snapshot) {
        return tl::make_unexpected(invalidCatalog("Hugging Face catalog mixes snapshots"));
      }
      snapshot_date = row_snapshot;
      CatalogArticle article{
          .page_id = *page,
          .canonical_title = title,
          .canonical_url = wikipediaUrl(title),
          .source_revision_id = row.contains("source_revision_id") &&
                                        !row.at("source_revision_id").is_null()
                                    ? std::optional<std::int64_t>{
                                          row.at("source_revision_id").get<std::int64_t>()}
                                    : std::nullopt,
          .rendered_html = paragraphize(text),
          .article_key = article_key,
          .snapshot_date = row_snapshot,
          .content_sha256 = content_hash,
      };
      if (!articles.emplace(page->value, std::move(article)).second) {
        return tl::make_unexpected(invalidCatalog("duplicate page ID in Hugging Face catalog"));
      }

      std::vector<std::string> row_titles{title};
      for (const auto& redirect : row.at("redirect_titles")) {
        row_titles.push_back(redirect.get<std::string>());
      }
      for (const auto& candidate : row_titles) {
        const auto normalized = normalizeTitle(candidate);
        if (normalized.empty()) {
          return tl::make_unexpected(invalidCatalog("blank title in Hugging Face catalog"));
        }
        const auto [found, inserted] = titles.emplace(normalized, page->value);
        if (!inserted && found->second != page->value) ambiguous_titles.insert(normalized);
      }
    }
  } catch (const std::exception&) {
    return tl::make_unexpected(invalidCatalog("malformed Hugging Face catalog"));
  }
  if (articles.empty() || !snapshot_date) {
    return tl::make_unexpected(invalidCatalog("Hugging Face catalog is empty"));
  }
  return std::static_pointer_cast<PinnedArticleSource>(std::make_shared<CatalogSource>(
      PinnedSourceProvenance{.repository = selection.repository,
                             .configuration = selection.configuration,
                             .commit_sha = std::move(commit_sha),
                             .snapshot_date = *snapshot_date},
      std::move(articles), std::move(titles), std::move(ambiguous_titles)));
}

}  // namespace

HuggingFaceArticleSourceFactory::HuggingFaceArticleSourceFactory(
    HttpTransport& transport, std::filesystem::path cache_root, std::string token)
    : transport_(transport), cache_root_(std::move(cache_root)), token_(std::move(token)) {}

Result<std::shared_ptr<PinnedArticleSource>> HuggingFaceArticleSourceFactory::pin(
    const SourceSelection& selection) {
  if (selection.repository.empty() || selection.configuration.empty() ||
      selection.requested_revision.empty() || selection.artifact_path.empty() || token_.empty()) {
    return invalidArgument("Hugging Face source configuration is incomplete");
  }

  const auto metadata = authenticatedGet(
      transport_, token_, "https://huggingface.co/api/datasets/" + selection.repository +
                              "/revision/" + selection.requested_revision);
  if (!metadata) return tl::make_unexpected(metadata.error());

  std::string commit_sha;
  try {
    commit_sha = nlohmann::json::parse(*metadata).at("sha").get<std::string>();
  } catch (const std::exception&) {
    return tl::make_unexpected(invalidCatalog("invalid Hugging Face revision metadata"));
  }
  if (!lowercaseHex(commit_sha, 40)) {
    return tl::make_unexpected(invalidCatalog("Hugging Face revision is not a commit SHA"));
  }

  const auto commit_directory = cache_root_ / commit_sha;
  std::error_code filesystem_error;
  std::filesystem::create_directories(commit_directory, filesystem_error);
  if (filesystem_error) {
    return tl::make_unexpected(invalidCatalog("could not create Hugging Face cache directory"));
  }
  const auto artifact_name = std::filesystem::path(selection.artifact_path).filename();
  const auto artifact_file = commit_directory / artifact_name;
  const auto checksum_file = commit_directory / (artifact_name.string() + ".sha256");

  if (!std::filesystem::exists(checksum_file)) {
    const auto checksum = authenticatedGet(
        transport_, token_, artifactUrl(selection, commit_sha, selection.artifact_path + ".sha256"));
    if (!checksum) return tl::make_unexpected(checksum.error());
    const auto written = writeFile(checksum_file, *checksum);
    if (!written) return tl::make_unexpected(written.error());
  }
  if (!std::filesystem::exists(artifact_file)) {
    const auto artifact = authenticatedGet(
        transport_, token_, artifactUrl(selection, commit_sha, selection.artifact_path));
    if (!artifact) return tl::make_unexpected(artifact.error());
    const auto written = writeFile(artifact_file, *artifact);
    if (!written) return tl::make_unexpected(written.error());
  }

  const auto checksum_contents = readFile(checksum_file);
  const auto artifact_contents = readFile(artifact_file);
  if (!checksum_contents) return tl::make_unexpected(checksum_contents.error());
  if (!artifact_contents) return tl::make_unexpected(artifact_contents.error());
  std::istringstream checksum_stream(*checksum_contents);
  std::string expected_checksum;
  checksum_stream >> expected_checksum;
  const auto actual_checksum = sha256(*artifact_contents);
  if (!lowercaseHex(expected_checksum, 64) || !actual_checksum ||
      expected_checksum != *actual_checksum) {
    return tl::make_unexpected(invalidCatalog("Hugging Face catalog checksum mismatch"));
  }
  return parseCatalog(*artifact_contents, selection, std::move(commit_sha));
}

}  // namespace babel
