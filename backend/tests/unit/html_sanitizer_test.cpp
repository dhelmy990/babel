#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

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

TEST_CASE("sanitizer rejects malformed and credentialed HTTPS URLs") {
  LibxmlHtmlSanitizer sanitizer;
  const auto result = sanitizer.sanitize(
      "<p><a href=\"https://user@example.test/path\">credential link</a>"
      "<a href=\"https:///missing-host\">malformed link</a>"
      "<a href=\"https://example.test/path\">safe link</a></p>",
      "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("credential link") != std::string::npos);
  REQUIRE(result->value.find("malformed link") != std::string::npos);
  REQUIRE(result->value.find("https://user@example.test") == std::string::npos);
  REQUIRE(result->value.find("https:///missing-host") == std::string::npos);
  REQUIRE(result->value.find("href=\"https://example.test/path\"") != std::string::npos);
}

TEST_CASE("sanitizer permits arbitrary absolute HTTPS image hosts") {
  LibxmlHtmlSanitizer sanitizer;
  const auto result = sanitizer.sanitize(
      "<p>images"
      "<img src=\"https://cdn.example.org/article.jpg\" alt=\"article\">"
      "<img src=\"//cdn.example.org/protocol-relative.jpg\" alt=\"protocol-relative\">"
      "<img src=\"https://user@cdn.example.org/bad.jpg\" alt=\"credentialed\">"
      "<img src=\"https:///missing-host.jpg\" alt=\"malformed\">"
      "</p>",
      "https://en.wikipedia.org/wiki/Film");

  REQUIRE(result.has_value());
  REQUIRE(result->value.find("src=\"https://cdn.example.org/article.jpg\"") !=
          std::string::npos);
  REQUIRE(result->value.find("protocol-relative") == std::string::npos);
  REQUIRE(result->value.find("credentialed") == std::string::npos);
  REQUIRE(result->value.find("malformed") == std::string::npos);
}

TEST_CASE("sanitizer rejects credentialed or malformed canonical base URLs") {
  LibxmlHtmlSanitizer sanitizer;
  const std::vector<std::string> invalid_urls{
      "https://user@en.wikipedia.org/wiki/Film",
      "https:///wiki/Film",
  };

  for (const auto& url : invalid_urls) {
    const auto result = sanitizer.sanitize("<p>content</p>", url);
    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::sanitizer_rejected);
  }
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

TEST_CASE("sanitizer rejects excessive sibling markup before DOM construction") {
  LibxmlHtmlSanitizer sanitizer;
  std::string siblings;
  siblings.reserve(60'000U * 4U);
  for (std::size_t index = 0; index < 60'000U; ++index) {
    siblings += "<br>";
  }

  const auto result = sanitizer.sanitize(siblings, "https://en.wikipedia.org/wiki/Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::sanitizer_rejected);
}

TEST_CASE("sanitizer rejects serialized output that expands beyond its output budget") {
  LibxmlHtmlSanitizer sanitizer;
  const std::string expanding_html = "<p>" + std::string(1'100'000U, '&') + "</p>";

  const auto result =
      sanitizer.sanitize(expanding_html, "https://en.wikipedia.org/wiki/Film");

  REQUIRE_FALSE(result.has_value());
  REQUIRE(result.error().code == ErrorCode::sanitizer_rejected);
}

TEST_CASE("sanitizer rejects empty and whitespace-only sanitized content") {
  LibxmlHtmlSanitizer sanitizer;
  const std::vector<std::string> empty_inputs{"", " \n\t ", "<p> \n\t </p>"};

  for (const auto& html : empty_inputs) {
    const auto result = sanitizer.sanitize(html, "https://en.wikipedia.org/wiki/Film");
    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::sanitizer_rejected);
  }
}

TEST_CASE("sanitizer rejects content composed only of dropped subtrees") {
  LibxmlHtmlSanitizer sanitizer;
  const std::vector<std::string> dropped_inputs{
      "<script>execute()</script>",
      "<table><tr><td>layout</td></tr></table>",
      "<div class=\"infobox\"><p>metadata</p></div>",
  };

  for (const auto& html : dropped_inputs) {
    const auto result = sanitizer.sanitize(html, "https://en.wikipedia.org/wiki/Film");
    REQUIRE_FALSE(result.has_value());
    REQUIRE(result.error().code == ErrorCode::sanitizer_rejected);
  }
}

}  // namespace
