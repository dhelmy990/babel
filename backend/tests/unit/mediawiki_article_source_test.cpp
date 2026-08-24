#include <algorithm>
#include <chrono>
#include <string>
#include <utility>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "babel/adapters/wikipedia/mediawiki_article_source.hpp"

namespace {

using babel::ApplicationError;
using babel::CurlHttpTransport;
using babel::ErrorCode;
using babel::HttpRequest;
using babel::HttpResponse;
using babel::HttpTransport;
using babel::MediaWikiArticleSource;
using babel::Result;
using babel::WikipediaPageId;

class RecordingTransport final : public HttpTransport {
 public:
  Result<HttpResponse> get(const HttpRequest& request) override {
    requests.push_back(request);
    return response;
  }

  HttpResponse response{.status_code = 200, .body = "{}"};
  std::vector<HttpRequest> requests;
};

bool hasHeader(const HttpRequest& request, std::string_view prefix) {
  return std::ranges::any_of(request.headers, [prefix](const std::string& header) {
    return header.starts_with(prefix);
  });
}

TEST_CASE("title resolution sends the exact deterministic MediaWiki query") {
  RecordingTransport transport;
  transport.response.body = R"json({
    "batchcomplete": true,
    "query": {
      "redirects": [{"from": "C++ & cinema", "to": "Film"}],
      "pages": [{
        "pageid": 18630637,
        "ns": 0,
        "title": "Film",
        "fullurl": "https://en.wikipedia.org/wiki/Film"
      }]
    }
  })json";
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("C++ & cinema");

  REQUIRE(result.has_value());
  REQUIRE(result->page_id.value == 18630637);
  REQUIRE(result->canonical_title == "Film");
  REQUIRE(result->canonical_url == "https://en.wikipedia.org/wiki/Film");
  REQUIRE(transport.requests.size() == 1);
  const auto& request = transport.requests.front();
  REQUIRE(request.url ==
          "https://en.wikipedia.org/w/api.php?action=query&redirects=1&prop=info&inprop="
          "url&titles=C%2B%2B%20%26%20cinema&format=json&formatversion=2");
  REQUIRE(request.connect_timeout_ms > 0);
  REQUIRE(request.total_timeout_ms >= request.connect_timeout_ms);
  REQUIRE(request.follow_redirects);
  REQUIRE(hasHeader(request, "User-Agent: Babel/"));
  REQUIRE(hasHeader(request, "Accept: application/json"));
}

TEST_CASE("page fetching sends the exact numeric MediaWiki parse query") {
  RecordingTransport transport;
  transport.response.body = R"json({
    "parse": {
      "title": "Distributed computing",
      "pageid": 46805,
      "revid": 1301234567,
      "displaytitle": "Distributed computing",
      "text": "<div class=\"mw-parser-output\"><p>Article</p></div>"
    }
  })json";
  MediaWikiArticleSource source(transport);
  const auto page_id = WikipediaPageId::fromInt(46805).value();

  const auto result = source.fetchByPageId(page_id);

  REQUIRE(result.has_value());
  REQUIRE(result->page_id == page_id);
  REQUIRE(result->canonical_title == "Distributed computing");
  REQUIRE(result->canonical_url ==
          "https://en.wikipedia.org/wiki/Distributed%20computing");
  REQUIRE(result->revision_id == 1301234567);
  REQUIRE(result->rendered_html ==
          "<div class=\"mw-parser-output\"><p>Article</p></div>");
  REQUIRE(transport.requests.size() == 1);
  REQUIRE(transport.requests.front().url ==
          "https://en.wikipedia.org/w/api.php?action=parse&pageid=46805&prop=text%7Crevid%7C"
          "displaytitle&format=json&formatversion=2");
}

TEST_CASE("missing MediaWiki pages map to wikipedia_not_found") {
  RecordingTransport transport;
  transport.response.body = R"json({
    "batchcomplete": true,
    "query": {"pages": [{"ns": 0, "title": "No such Babel page", "missing": true}]}
  })json";
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("No such Babel page");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::wikipedia_not_found);
}

TEST_CASE("MediaWiki transport and server failures map to wikipedia_unavailable") {
  SECTION("transport failure") {
    class FailingTransport final : public HttpTransport {
     public:
      Result<HttpResponse> get(const HttpRequest&) override {
        return tl::make_unexpected(ApplicationError{
            .code = ErrorCode::internal,
            .message = "TLS connection failed",
        });
      }
    } transport;
    MediaWikiArticleSource source(transport);

    const auto result = source.resolveTitle("Film");

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
  }

  SECTION("HTTP 503") {
    RecordingTransport transport;
    transport.response = HttpResponse{.status_code = 503, .body = "maintenance"};
    MediaWikiArticleSource source(transport);

    const auto result = source.fetchByPageId(WikipediaPageId::fromInt(42).value());

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
  }
}

TEST_CASE("MediaWiki HTTP status classification distinguishes endpoint and request failures") {
  const std::vector<std::pair<long, ErrorCode>> cases{
      {404, ErrorCode::wikipedia_unavailable},
      {408, ErrorCode::wikipedia_unavailable},
      {429, ErrorCode::wikipedia_unavailable},
      {500, ErrorCode::wikipedia_unavailable},
      {400, ErrorCode::internal},
      {403, ErrorCode::internal},
  };

  for (const auto& [status, expected_code] : cases) {
    RecordingTransport transport;
    transport.response = HttpResponse{.status_code = status, .body = "request failed"};
    MediaWikiArticleSource source(transport);

    const auto result = source.resolveTitle("Film");

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == expected_code);
  }
}

TEST_CASE("MediaWiki HTTP 429 exposes the server retry delay") {
  RecordingTransport transport;
  transport.response = HttpResponse{
      .status_code = 429,
      .body = "rate limited",
      .retry_after = std::chrono::seconds{45},
  };
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
  REQUIRE(result.error().retry_after == std::chrono::seconds{45});
}

TEST_CASE("MediaWiki HTTP 429 defaults to a one minute retry delay") {
  RecordingTransport transport;
  transport.response = HttpResponse{.status_code = 429, .body = "rate limited"};
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().retry_after == std::chrono::seconds{60});
}

TEST_CASE("MediaWiki top-level missing errors map to wikipedia_not_found") {
  for (const std::string code : {"missingtitle", "missingrev"}) {
    RecordingTransport transport;
    transport.response.body = "{\"error\":{\"code\":\"" + code +
                              "\",\"info\":\"not found\"}}";
    MediaWikiArticleSource source(transport);

    const auto result = source.fetchByPageId(WikipediaPageId::fromInt(42).value());

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::wikipedia_not_found);
  }
}

TEST_CASE("MediaWiki transient API errors map to wikipedia_unavailable") {
  for (const std::string code : {"readonly", "maxlag", "ratelimited", "readonlytext"}) {
    RecordingTransport transport;
    transport.response.body = "{\"error\":{\"code\":\"" + code +
                              "\",\"info\":\"retry later\"}}";
    MediaWikiArticleSource source(transport);

    const auto result = source.resolveTitle("Film");

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
  }
}

TEST_CASE("MediaWiki deterministic API errors map to internal") {
  RecordingTransport transport;
  transport.response.body =
      R"json({"error":{"code":"badvalue","info":"invalid parameter"}})json";
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::internal);
}

TEST_CASE("resolved MediaWiki full URLs require the exact canonical HTTPS authority") {
  const std::vector<std::string> invalid_urls{
      "https://en.wikipedia.org.evil.test/wiki/Film",
      "https://user@en.wikipedia.org/wiki/Film",
      "https://en.wikipedia.org:444/wiki/Film",
      "https://en.wikipedia.org:0/wiki/Film",
      "https:///wiki/Film",
  };

  for (const auto& full_url : invalid_urls) {
    RecordingTransport transport;
    transport.response.body =
        "{\"query\":{\"pages\":[{\"pageid\":42,\"title\":\"Film\",\"fullurl\":\"" +
        full_url + "\"}]}}";
    MediaWikiArticleSource source(transport);

    const auto result = source.resolveTitle("Film");

    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::internal);
  }
}

TEST_CASE("curl transport rejects invalid timeout configuration before I/O") {
  CurlHttpTransport transport;
  const std::vector<HttpRequest> invalid_requests{
      HttpRequest{.url = "http://127.0.0.1/",
                  .connect_timeout_ms = 0,
                  .total_timeout_ms = 10'000,
                  .follow_redirects = true,
                  .headers = {}},
      HttpRequest{.url = "http://127.0.0.1/",
                  .connect_timeout_ms = 3'000,
                  .total_timeout_ms = -1,
                  .follow_redirects = true,
                  .headers = {}},
      HttpRequest{.url = "http://127.0.0.1/",
                  .connect_timeout_ms = 3'000,
                  .total_timeout_ms = 300'001,
                  .follow_redirects = true,
                  .headers = {}},
  };

  for (const auto& request : invalid_requests) {
    const auto result = transport.get(request);
    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
    REQUIRE(result.error().message.find("timeout") != std::string::npos);
  }
}

TEST_CASE("curl transport rejects non-HTTPS request URLs before I/O") {
  CurlHttpTransport transport;
  const HttpRequest request{
      .url = "http://127.0.0.1/",
      .connect_timeout_ms = 3'000,
      .total_timeout_ms = 10'000,
      .follow_redirects = true,
      .headers = {},
  };

  const auto result = transport.get(request);

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::wikipedia_unavailable);
  REQUIRE(result.error().message.find("HTTPS") != std::string::npos);
}

TEST_CASE("malformed and schema-invalid MediaWiki responses map to internal") {
  const std::vector<std::string> invalid_responses{
      "{not-json",
      R"json({"query":{"pages":[]}})json",
      R"json({"query":{"pages":[{"pageid":"42","title":"Film","fullurl":"https://en.wikipedia.org/wiki/Film"}]}})json",
      R"json({"parse":{"title":"Film","pageid":42,"displaytitle":"Film"}})json",
  };

  for (const auto& body : invalid_responses) {
    RecordingTransport transport;
    transport.response.body = body;
    MediaWikiArticleSource source(transport);

    const auto resolved = body.find("query") != std::string::npos
                              ? source.resolveTitle("Film").transform([](const auto&) {})
                              : source.fetchByPageId(WikipediaPageId::fromInt(42).value())
                                    .transform([](const auto&) {});

    REQUIRE_FALSE(resolved.has_value());
    REQUIRE(resolved.error().code == ErrorCode::internal);
  }
}

TEST_CASE("the MediaWiki adapter performs no retries") {
  class CountingFailureTransport final : public HttpTransport {
   public:
    Result<HttpResponse> get(const HttpRequest&) override {
      ++calls;
      return tl::make_unexpected(ApplicationError{
          .code = ErrorCode::wikipedia_unavailable,
          .message = "offline",
      });
    }

    int calls = 0;
  } transport;
  MediaWikiArticleSource source(transport);

  const auto result = source.resolveTitle("Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(transport.calls == 1);
}

}  // namespace
