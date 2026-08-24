#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "babel/adapters/html/libxml_html_sanitizer.hpp"

namespace {

using babel::ErrorCode;
using babel::LibxmlHtmlSanitizer;

std::string fixture(std::string_view name) {
  const auto path = std::filesystem::path(__FILE__).parent_path().parent_path() / "fixtures" /
                    name;
  std::ifstream input(path);
  REQUIRE(input.good());
  std::ostringstream contents;
  contents << input.rdbuf();
  return contents.str();
}

TEST_CASE("sanitizer reconstructs only Quill-compatible allowlisted content") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("<h1 title=\"Overview\">Film</h1>") != std::string::npos);
  REQUIRE(result->value.find("<p>A <strong>film</strong> is a work of visual art.</p>") !=
          std::string::npos);
  REQUIRE(result->value.find("<h2>History</h2>") != std::string::npos);
  REQUIRE(result->value.find("<blockquote><p><em>Safe quoted text.</em></p></blockquote>") !=
          std::string::npos);
  REQUIRE(result->value.find("<pre><code>frame();</code></pre>") != std::string::npos);
  REQUIRE(result->value.find("class=") == std::string::npos);
  REQUIRE(result->value.find("width=") == std::string::npos);
  REQUIRE(result->value.find("height=") == std::string::npos);
  REQUIRE(result->value.find("<div") == std::string::npos);
  REQUIRE(result->value.find("<span") == std::string::npos);
}

TEST_CASE("sanitizer resolves safe Wikipedia links and Wikimedia images to HTTPS") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("href=\"https://en.wikipedia.org/wiki/Cinema\"") !=
          std::string::npos);
  REQUIRE(result->value.find(
              "src=\"https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg\"") !=
          std::string::npos);
  REQUIRE(result->value.find("alt=\"Example\"") != std::string::npos);
}

TEST_CASE("sanitizer removes executable markup and Wikipedia UI subtrees") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article_malicious.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("<h2>History</h2>") != std::string::npos);
  REQUIRE(result->value.find("Safe <b>history</b> text.") != std::string::npos);
  REQUIRE(result->value.find("https://en.wikipedia.org/wiki/Cinema") != std::string::npos);
  REQUIRE(result->value.find("https://upload.wikimedia.org/") != std::string::npos);
  REQUIRE(result->value.find("bad link text remains") != std::string::npos);
  REQUIRE(result->value.find("insecure link text remains") != std::string::npos);
  REQUIRE(result->value.find("<script") == std::string::npos);
  REQUIRE(result->value.find("window.evil") == std::string::npos);
  REQUIRE(result->value.find("<style") == std::string::npos);
  REQUIRE(result->value.find("display: none") == std::string::npos);
  REQUIRE(result->value.find("onclick") == std::string::npos);
  REQUIRE(result->value.find("onmouseover") == std::string::npos);
  REQUIRE(result->value.find("onerror") == std::string::npos);
  REQUIRE(result->value.find("style=") == std::string::npos);
  REQUIRE(result->value.find("javascript:") == std::string::npos);
  REQUIRE(result->value.find("data:image") == std::string::npos);
  REQUIRE(result->value.find("http://") == std::string::npos);
  REQUIRE(result->value.find("Infobox must disappear") == std::string::npos);
  REQUIRE(result->value.find("Navigation must disappear") == std::string::npos);
  REQUIRE(result->value.find("Reference must disappear") == std::string::npos);
  REQUIRE(result->value.find("Table content must disappear") == std::string::npos);
  REQUIRE(result->value.find("edit") == std::string::npos);
  REQUIRE(result->value.find("<table") == std::string::npos);
}

TEST_CASE("sanitizer preserves safe text while stripping unsupported containers") {
  LibxmlHtmlSanitizer sanitizer;
  const auto result = sanitizer.sanitize(
      "<section><div><span>Kept &amp; escaped</span></div></section>",
      "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("Kept &amp; escaped") != std::string::npos);
  REQUIRE(result->value.find("<section") == std::string::npos);
  REQUIRE(result->value.find("<div") == std::string::npos);
  REQUIRE(result->value.find("<span") == std::string::npos);
}

TEST_CASE("sanitizer drops inline Wikipedia citation markers by cite_ref ID") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article_malicious.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("Released in 1895") != std::string::npos);
  REQUIRE(result->value.find("cite_ref") == std::string::npos);
  REQUIRE(result->value.find("[1]") == std::string::npos);
}

TEST_CASE("sanitizer unwraps noscript fallback prose") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("<p>Fallback prose remains available.</p>") !=
          std::string::npos);
  REQUIRE(result->value.find("<noscript") == std::string::npos);
}

TEST_CASE("sanitizer unwraps safe MathML formula text") {
  LibxmlHtmlSanitizer sanitizer;

  const auto result = sanitizer.sanitize(fixture("wikipedia_article.html"),
                                         "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("E=mc2") != std::string::npos);
  REQUIRE(result->value.find("<math") == std::string::npos);
  REQUIRE(result->value.find("<mi") == std::string::npos);
}

TEST_CASE("sanitizer rejects unsafe base URLs and oversized input") {
  LibxmlHtmlSanitizer sanitizer;

  const auto unsafe_base = sanitizer.sanitize("<p>content</p>", "http://example.test/Film");
  REQUIRE_FALSE(unsafe_base.has_value());
  REQUIRE(unsafe_base.error().code == ErrorCode::sanitizer_rejected);

  const std::string oversized(5U * 1024U * 1024U + 1U, 'x');
  const auto oversized_result = sanitizer.sanitize(
      oversized, "https://en.wikipedia.org/wiki/Film");
  REQUIRE_FALSE(oversized_result.has_value());
  REQUIRE(oversized_result.error().code == ErrorCode::sanitizer_rejected);
}

}  // namespace
