#include "babel/adapters/wikipedia/mediawiki_article_source.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <curl/curl.h>
#include <libxml/uri.h>
#include <nlohmann/json.hpp>

namespace babel {
namespace {

constexpr std::string_view kApiEndpoint = "https://en.wikipedia.org/w/api.php";
constexpr std::int64_t kConnectTimeoutMs = 3'000;
constexpr std::int64_t kTotalTimeoutMs = 10'000;
constexpr std::int64_t kMaxTransportTimeoutMs = 300'000;
constexpr std::size_t kMaxResponseBytes = 20U * 1024U * 1024U;

struct XmlUriDeleter {
  void operator()(xmlURI* value) const { xmlFreeURI(value); }
};

using XmlUri = std::unique_ptr<xmlURI, XmlUriDeleter>;

ApplicationError error(ErrorCode code, std::string message) {
  return ApplicationError{.code = code, .message = std::move(message)};
}

ApplicationError error(ErrorCode code, std::string message,
                       std::chrono::milliseconds retry_after) {
  return ApplicationError{
      .code = code,
      .message = std::move(message),
      .retry_after = retry_after,
  };
}

std::string lowerAscii(std::string_view value) {
  std::string result;
  result.reserve(value.size());
  for (const unsigned char character : value) {
    result.push_back(static_cast<char>(std::tolower(character)));
  }
  return result;
}

XmlUri parsedUri(std::string_view value) {
  const std::string null_terminated(value);
  xmlURI* parsed_raw = nullptr;
  if (xmlParseURISafe(null_terminated.c_str(), &parsed_raw) != 0 || parsed_raw == nullptr) {
    if (parsed_raw != nullptr) {
      xmlFreeURI(parsed_raw);
    }
    return XmlUri{};
  }
  return XmlUri(parsed_raw);
}

bool validHttpsUrl(std::string_view value) {
  auto parsed = parsedUri(value);
  return parsed != nullptr && parsed->scheme != nullptr &&
         lowerAscii(parsed->scheme) == "https" && parsed->server != nullptr &&
         !std::string_view(parsed->server).empty() && parsed->user == nullptr &&
         parsed->opaque == nullptr;
}

std::optional<std::string> urlAuthority(std::string_view value) {
  const auto scheme_end = value.find("://");
  if (scheme_end == std::string_view::npos) {
    return std::nullopt;
  }
  const auto authority_start = scheme_end + 3U;
  const auto authority_end = value.find_first_of("/?#", authority_start);
  const auto authority = value.substr(
      authority_start,
      authority_end == std::string_view::npos ? value.size() - authority_start
                                              : authority_end - authority_start);
  if (authority.empty()) {
    return std::nullopt;
  }
  return lowerAscii(authority);
}

bool validCanonicalWikipediaUrl(std::string_view value) {
  auto parsed = parsedUri(value);
  if (parsed == nullptr || parsed->scheme == nullptr || parsed->server == nullptr ||
      parsed->user != nullptr || parsed->opaque != nullptr) {
    return false;
  }
  const auto authority = urlAuthority(value);
  return lowerAscii(parsed->scheme) == "https" &&
         lowerAscii(parsed->server) == "en.wikipedia.org" &&
         authority && (*authority == "en.wikipedia.org" || *authority == "en.wikipedia.org:443");
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
  if (response.status_code == 429) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki endpoint returned HTTP status 429",
        response.retry_after.value_or(std::chrono::seconds{60})));
  }
  if (response.status_code == 404 || response.status_code == 408 ||
      response.status_code >= 500) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki endpoint returned HTTP status " + std::to_string(response.status_code)));
  }
  if (response.status_code < 200 || response.status_code >= 300) {
    return tl::make_unexpected(error(
        ErrorCode::internal,
        "MediaWiki request returned HTTP status " + std::to_string(response.status_code)));
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

std::optional<ApplicationError> classifiedApiError(const nlohmann::json& json) {
  const auto api_error = json.find("error");
  if (api_error == json.end()) {
    return std::nullopt;
  }
  if (!api_error->is_object()) {
    return error(ErrorCode::internal, "MediaWiki API error had an invalid schema");
  }
  const auto code = api_error->find("code");
  if (code == api_error->end() || !code->is_string()) {
    return error(ErrorCode::internal, "MediaWiki API error had an invalid code");
  }
  const auto value = lowerAscii(code->get<std::string>());
  if (value == "missingtitle" || value == "missingrev" || value == "nosuchpageid" ||
      value == "invalidpageid") {
    return error(ErrorCode::wikipedia_not_found, "MediaWiki page was not found");
  }
  if (value == "readonly" || value == "readonlytext" || value == "maxlag" ||
      value == "ratelimited" || value == "dbqueryerror" ||
      value.starts_with("internal_api_error_db")) {
    return error(ErrorCode::wikipedia_unavailable,
                 "MediaWiki API is temporarily unavailable: " + value);
  }
  return error(ErrorCode::internal, "MediaWiki API rejected the request: " + value);
}

Result<nlohmann::json> execute(HttpTransport& transport, HttpRequest request) {
  auto response = transport.get(request);
  if (!response) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "MediaWiki transport failed: " +
                                         response.error().message));
  }
  auto json = parsedJson(response.value());
  if (!json) {
    return tl::make_unexpected(json.error());
  }
  if (auto api_error = classifiedApiError(json.value())) {
    return tl::make_unexpected(std::move(*api_error));
  }
  return json.value();
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

Result<long> validatedTimeout(std::int64_t value, std::string_view name) {
  if (value <= 0 || value > kMaxTransportTimeoutMs ||
      value > static_cast<std::int64_t>(std::numeric_limits<long>::max())) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki transport " + std::string(name) + " timeout is outside the supported range"));
  }
  return static_cast<long>(value);
}

std::optional<ApplicationError> curlOptionFailure(CURLcode result, std::string_view option) {
  if (result == CURLE_OK) {
    return std::nullopt;
  }
  return error(ErrorCode::wikipedia_unavailable,
               "libcurl could not set " + std::string(option) + ": " +
                   curl_easy_strerror(result));
}

}  // namespace

Result<HttpResponse> CurlHttpTransport::get(const HttpRequest& request) {
  auto connect_timeout = validatedTimeout(request.connect_timeout_ms, "connect");
  if (!connect_timeout) {
    return tl::make_unexpected(connect_timeout.error());
  }
  auto total_timeout = validatedTimeout(request.total_timeout_ms, "total");
  if (!total_timeout) {
    return tl::make_unexpected(total_timeout.error());
  }
  if (total_timeout.value() < connect_timeout.value()) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki transport total timeout must not be shorter than the connect timeout"));
  }
  if (!validHttpsUrl(request.url)) {
    return tl::make_unexpected(error(
        ErrorCode::wikipedia_unavailable,
        "MediaWiki transport requires an HTTPS URL without credentials"));
  }

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
  std::array<char, CURL_ERROR_SIZE> error_buffer{};
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_ERRORBUFFER, error_buffer.data()),
          "CURLOPT_ERRORBUFFER")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_URL, request.url.c_str()), "CURLOPT_URL")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_CONNECTTIMEOUT_MS, connect_timeout.value()),
          "CURLOPT_CONNECTTIMEOUT_MS")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_TIMEOUT_MS, total_timeout.value()),
          "CURLOPT_TIMEOUT_MS")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_FOLLOWLOCATION,
                           request.follow_redirects ? 1L : 0L),
          "CURLOPT_FOLLOWLOCATION")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(curl_easy_setopt(handle.value, CURLOPT_MAXREDIRS, 5L),
                                       "CURLOPT_MAXREDIRS")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_PROTOCOLS_STR, "https"),
          "CURLOPT_PROTOCOLS_STR")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_REDIR_PROTOCOLS_STR, "https"),
          "CURLOPT_REDIR_PROTOCOLS_STR")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(curl_easy_setopt(handle.value, CURLOPT_NOSIGNAL, 1L),
                                       "CURLOPT_NOSIGNAL")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_HTTPHEADER, headers.value),
          "CURLOPT_HTTPHEADER")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_WRITEFUNCTION, writeResponse),
          "CURLOPT_WRITEFUNCTION")) {
    return tl::make_unexpected(std::move(*failure));
  }
  if (auto failure = curlOptionFailure(
          curl_easy_setopt(handle.value, CURLOPT_WRITEDATA, &body), "CURLOPT_WRITEDATA")) {
    return tl::make_unexpected(std::move(*failure));
  }

  const auto result = curl_easy_perform(handle.value);
  if (result != CURLE_OK) {
    const std::string detail = error_buffer.front() == '\0' ? curl_easy_strerror(result)
                                                            : error_buffer.data();
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "libcurl request failed: " + detail));
  }

  long status_code = 0;
  if (curl_easy_getinfo(handle.value, CURLINFO_RESPONSE_CODE, &status_code) != CURLE_OK) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "libcurl could not read the HTTP status"));
  }

  curl_off_t retry_after_seconds = 0;
  if (curl_easy_getinfo(handle.value, CURLINFO_RETRY_AFTER, &retry_after_seconds) !=
      CURLE_OK) {
    return tl::make_unexpected(error(ErrorCode::wikipedia_unavailable,
                                     "libcurl could not read Retry-After"));
  }
  std::optional<std::chrono::milliseconds> retry_after;
  if (retry_after_seconds > 0) {
    constexpr auto kMaxSeconds =
        std::chrono::milliseconds::max().count() / 1000;
    const auto seconds = std::min(
        retry_after_seconds, static_cast<curl_off_t>(kMaxSeconds));
    retry_after = std::chrono::milliseconds{
        static_cast<std::chrono::milliseconds::rep>(seconds * 1000)};
  }
  return HttpResponse{
      .status_code = status_code,
      .body = std::move(body),
      .retry_after = retry_after,
  };
}

MediaWikiArticleSource::MediaWikiArticleSource(HttpTransport& transport) : transport_(transport) {}

Result<ResolvedWikipediaPage> MediaWikiArticleSource::resolveTitle(std::string_view title) {
  auto json = execute(transport_, requestFor("action=query&redirects=1&prop=info&inprop=url&titles=" +
                                             percentEncode(title) +
                                             "&format=json&formatversion=2"));
  if (!json) {
    return tl::make_unexpected(json.error());
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
  if (!page_id || canonical_title.empty() || !validCanonicalWikipediaUrl(canonical_url)) {
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
