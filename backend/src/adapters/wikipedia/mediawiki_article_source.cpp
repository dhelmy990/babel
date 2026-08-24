#include "babel/adapters/wikipedia/mediawiki_article_source.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <curl/curl.h>
#include <nlohmann/json.hpp>

namespace babel {
namespace {

constexpr std::string_view kApiEndpoint = "https://en.wikipedia.org/w/api.php";
constexpr std::int64_t kConnectTimeoutMs = 3'000;
constexpr std::int64_t kTotalTimeoutMs = 10'000;
constexpr std::size_t kMaxResponseBytes = 20U * 1024U * 1024U;

ApplicationError error(ErrorCode code, std::string message) {
  return ApplicationError{.code = code, .message = std::move(message)};
}

std::string percentEncode(std::string_view value) {
  constexpr std::array<char, 16> hex{'0', '1', '2', '3', '4', '5', '6', '7',
                                     '8', '9', 'A', 'B', 'C', 'D', 'E', 'F'};
  std::string encoded;
  encoded.reserve(value.size());
  for (const unsigned char character : value) {
    const bool ascii_alphanumeric =
        (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
        (character >= '0' && character <= '9');
    if (ascii_alphanumeric || character == '-' || character == '.' || character == '_' ||
        character == '~') {
      encoded.push_back(static_cast<char>(character));
      continue;
    }
    encoded.push_back('%');
    encoded.push_back(hex[character >> 4U]);
    encoded.push_back(hex[character & 0x0fU]);
  }
  return encoded;
}

HttpRequest requestFor(std::string query) {
  return HttpRequest{
      .url = std::string(kApiEndpoint) + '?' + std::move(query),
      .connect_timeout_ms = kConnectTimeoutMs,
      .total_timeout_ms = kTotalTimeoutMs,
      .follow_redirects = true,
      .headers = {
          "User-Agent: Babel/0.1 local-use Wikipedia ingestion (localhost operator tool)",
          "Accept: application/json",
      },
  };
}

Result<nlohmann::json> parsedJson(const HttpResponse& response) {
  if (response.status_code == 404) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_not_found, "MediaWiki page was not found"));
  }
  if (response.status_code < 200 || response.status_code >= 300) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki returned HTTP status " + std::to_string(response.status_code)));
  }

  auto json = nlohmann::json::parse(response.body, nullptr, false);
  if (json.is_discarded() || !json.is_object()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki returned malformed JSON"));
  }
  return json;
}

bool validRedirects(const nlohmann::json& query) {
  const auto redirects = query.find("redirects");
  if (redirects == query.end()) {
    return true;
  }
  if (!redirects->is_array()) {
    return false;
  }
  for (const auto& redirect : *redirects) {
    if (!redirect.is_object() || !redirect.contains("from") || !redirect["from"].is_string() ||
        !redirect.contains("to") || !redirect["to"].is_string()) {
      return false;
    }
  }
  return true;
}

bool isMissingError(const nlohmann::json& json) {
  const auto api_error = json.find("error");
  if (api_error == json.end() || !api_error->is_object()) {
    return false;
  }
  const auto code = api_error->find("code");
  if (code == api_error->end() || !code->is_string()) {
    return false;
  }
  const auto value = code->get<std::string>();
  return value == "missingtitle" || value == "nosuchpageid" || value == "invalidpageid";
}

Result<nlohmann::json> execute(HttpTransport& transport, HttpRequest request) {
  auto response = transport.get(request);
  if (!response) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "MediaWiki transport failed: " +
                                         response.error().message));
  }
  return parsedJson(response.value());
}

Result<WikipediaPageId> parsePageId(const nlohmann::json& value) {
  if (!value.is_number_integer()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki pageid was not an integer"));
  }
  const auto page_id = value.get<std::int64_t>();
  auto parsed = WikipediaPageId::fromInt(page_id);
  if (!parsed) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki pageid was not positive"));
  }
  return parsed.value();
}

std::string canonicalUrl(std::string_view title) {
  return "https://en.wikipedia.org/wiki/" + percentEncode(title);
}

std::size_t writeResponse(char* data, std::size_t size, std::size_t count, void* user_data) {
  if (size != 0 && count > std::numeric_limits<std::size_t>::max() / size) {
    return 0;
  }
  const auto bytes = size * count;
  auto& output = *static_cast<std::string*>(user_data);
  if (bytes > kMaxResponseBytes || output.size() > kMaxResponseBytes - bytes) {
    return 0;
  }
  try {
    output.append(data, bytes);
  } catch (...) {
    return 0;
  }
  return bytes;
}

class CurlHandle final {
 public:
  CurlHandle() : value(curl_easy_init()) {}
  ~CurlHandle() {
    if (value != nullptr) {
      curl_easy_cleanup(value);
    }
  }

  CurlHandle(const CurlHandle&) = delete;
  CurlHandle& operator=(const CurlHandle&) = delete;

  CURL* value;
};

class CurlHeaders final {
 public:
  ~CurlHeaders() {
    if (value != nullptr) {
      curl_slist_free_all(value);
    }
  }

  bool append(const std::string& header) {
    auto* appended = curl_slist_append(value, header.c_str());
    if (appended == nullptr) {
      return false;
    }
    value = appended;
    return true;
  }

  CurlHeaders(const CurlHeaders&) = delete;
  CurlHeaders& operator=(const CurlHeaders&) = delete;
  CurlHeaders() = default;

  curl_slist* value = nullptr;
};

}  // namespace

Result<HttpResponse> CurlHttpTransport::get(const HttpRequest& request) {
  static const CURLcode global_init = curl_global_init(CURL_GLOBAL_DEFAULT);
  if (global_init != CURLE_OK) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_unavailable, "libcurl global initialization failed"));
  }

  CurlHandle handle;
  if (handle.value == nullptr) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_unavailable, "libcurl could not create a request"));
  }

  CurlHeaders headers;
  for (const auto& header : request.headers) {
    if (!headers.append(header)) {
      return tl::make_unexpected(
          error(ErrorCode::wikipedia_unavailable, "libcurl could not allocate headers"));
    }
  }

  std::string body;
  curl_easy_setopt(handle.value, CURLOPT_URL, request.url.c_str());
  curl_easy_setopt(handle.value, CURLOPT_CONNECTTIMEOUT_MS,
                   static_cast<long>(request.connect_timeout_ms));
  curl_easy_setopt(handle.value, CURLOPT_TIMEOUT_MS, static_cast<long>(request.total_timeout_ms));
  curl_easy_setopt(handle.value, CURLOPT_FOLLOWLOCATION, request.follow_redirects ? 1L : 0L);
  curl_easy_setopt(handle.value, CURLOPT_MAXREDIRS, 5L);
  curl_easy_setopt(handle.value, CURLOPT_PROTOCOLS_STR, "https");
  curl_easy_setopt(handle.value, CURLOPT_REDIR_PROTOCOLS_STR, "https");
  curl_easy_setopt(handle.value, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(handle.value, CURLOPT_HTTPHEADER, headers.value);
  curl_easy_setopt(handle.value, CURLOPT_WRITEFUNCTION, writeResponse);
  curl_easy_setopt(handle.value, CURLOPT_WRITEDATA, &body);

  const auto result = curl_easy_perform(handle.value);
  if (result != CURLE_OK) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "libcurl request failed: " +
                                         std::string(curl_easy_strerror(result))));
  }

  long status_code = 0;
  if (curl_easy_getinfo(handle.value, CURLINFO_RESPONSE_CODE, &status_code) != CURLE_OK) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "libcurl could not read the HTTP status"));
  }
  return HttpResponse{.status_code = status_code, .body = std::move(body)};
}

MediaWikiArticleSource::MediaWikiArticleSource(HttpTransport& transport) : transport_(transport) {}

Result<ResolvedWikipediaPage> MediaWikiArticleSource::resolveTitle(std::string_view title) {
  auto json = execute(transport_, requestFor("action=query&redirects=1&prop=info&inprop=url&titles=" +
                                             percentEncode(title) +
                                             "&format=json&formatversion=2"));
  if (!json) {
    return tl::make_unexpected(json.error());
  }
  if (isMissingError(json.value())) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_not_found, "MediaWiki title was not found"));
  }

  const auto query = json->find("query");
  if (query == json->end() || !query->is_object() || !validRedirects(*query)) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki query response had an invalid schema"));
  }
  const auto pages = query->find("pages");
  if (pages == query->end() || !pages->is_array() || pages->size() != 1 ||
      !pages->front().is_object()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki query response did not contain one page"));
  }

  const auto& page = pages->front();
  if ((page.contains("missing") && page["missing"].is_boolean() &&
       page["missing"].get<bool>()) ||
      (page.contains("invalid") && page["invalid"].is_boolean() &&
       page["invalid"].get<bool>())) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_not_found, "MediaWiki title was not found"));
  }
  if (!page.contains("pageid") || !page.contains("title") || !page["title"].is_string() ||
      !page.contains("fullurl") || !page["fullurl"].is_string()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki page response had an invalid schema"));
  }

  auto page_id = parsePageId(page["pageid"]);
  const auto canonical_title = page["title"].get<std::string>();
  const auto canonical_url = page["fullurl"].get<std::string>();
  if (!page_id || canonical_title.empty() || !canonical_url.starts_with("https://")) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki page response had invalid canonical fields"));
  }
  return ResolvedWikipediaPage{
      .page_id = page_id.value(),
      .canonical_title = canonical_title,
      .canonical_url = canonical_url,
  };
}

Result<RawWikipediaArticle> MediaWikiArticleSource::fetchByPageId(WikipediaPageId page_id) {
  auto json = execute(
      transport_,
      requestFor("action=parse&pageid=" + std::to_string(page_id.value) +
                 "&prop=text%7Crevid%7Cdisplaytitle&format=json&formatversion=2"));
  if (!json) {
    return tl::make_unexpected(json.error());
  }
  if (isMissingError(json.value())) {
    return tl::make_unexpected(
        error(ErrorCode::wikipedia_not_found, "MediaWiki page ID was not found"));
  }

  const auto parsed = json->find("parse");
  if (parsed == json->end() || !parsed->is_object() || !parsed->contains("title") ||
      !(*parsed)["title"].is_string() || !parsed->contains("pageid") ||
      !parsed->contains("displaytitle") || !(*parsed)["displaytitle"].is_string() ||
      !parsed->contains("text") || !(*parsed)["text"].is_string()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki parse response had an invalid schema"));
  }

  auto parsed_page_id = parsePageId((*parsed)["pageid"]);
  if (!parsed_page_id || parsed_page_id->value != page_id.value) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki parse response returned the wrong page ID"));
  }

  std::optional<std::int64_t> revision_id;
  if (parsed->contains("revid") && !(*parsed)["revid"].is_null()) {
    if (!(*parsed)["revid"].is_number_integer()) {
      return tl::make_unexpected(
          error(ErrorCode::internal, "MediaWiki revision ID was not an integer"));
    }
    revision_id = (*parsed)["revid"].get<std::int64_t>();
    if (*revision_id <= 0) {
      return tl::make_unexpected(
          error(ErrorCode::internal, "MediaWiki revision ID was not positive"));
    }
  }

  const auto canonical_title = (*parsed)["title"].get<std::string>();
  const auto rendered_html = (*parsed)["text"].get<std::string>();
  if (canonical_title.empty() || rendered_html.empty()) {
    return tl::make_unexpected(
        error(ErrorCode::internal, "MediaWiki parse response contained empty article fields"));
  }

  return RawWikipediaArticle{
      .page_id = parsed_page_id.value(),
      .canonical_title = canonical_title,
      .canonical_url = canonicalUrl(canonical_title),
      .revision_id = revision_id,
      .rendered_html = rendered_html,
  };
}

}  // namespace babel
