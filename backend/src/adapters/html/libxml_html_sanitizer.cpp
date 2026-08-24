#include "babel/adapters/html/libxml_html_sanitizer.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <memory>
#include <new>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>

#include <libxml/HTMLparser.h>
#include <libxml/HTMLtree.h>
#include <libxml/tree.h>
#include <libxml/uri.h>

namespace babel {
namespace {

constexpr std::size_t kMaxInputBytes = 5U * 1024U * 1024U;
constexpr std::size_t kMaxMarkupTokens = 50'000U;
constexpr std::size_t kMaxDomNodes = 100'000U;
constexpr std::size_t kMaxOutputBytes = 5U * 1024U * 1024U;

using HtmlDoc = std::unique_ptr<xmlDoc, decltype(&xmlFreeDoc)>;
using XmlBuffer = std::unique_ptr<xmlBuffer, decltype(&xmlBufferFree)>;

struct XmlUriDeleter {
  void operator()(xmlURI* value) const { xmlFreeURI(value); }
};

using XmlUri = std::unique_ptr<xmlURI, XmlUriDeleter>;

struct XmlStringDeleter {
  void operator()(xmlChar* value) const { xmlFree(value); }
};

using XmlString = std::unique_ptr<xmlChar, XmlStringDeleter>;

ApplicationError rejected(std::string message) {
  return ApplicationError{.code = ErrorCode::sanitizer_rejected, .message = std::move(message)};
}

ApplicationError internalFailure(std::string message) {
  return ApplicationError{.code = ErrorCode::internal, .message = std::move(message)};
}

std::string lower(std::string_view value) {
  std::string result;
  result.reserve(value.size());
  std::ranges::transform(value, std::back_inserter(result), [](const unsigned char character) {
    return static_cast<char>(std::tolower(character));
  });
  return result;
}

std::string nodeName(const xmlNode& node) {
  if (node.name == nullptr) {
    return {};
  }
  return lower(reinterpret_cast<const char*>(node.name));
}

std::optional<std::string> attribute(const xmlNode& node, const char* name) {
  XmlString value(xmlGetProp(&node, BAD_CAST name));
  if (value == nullptr) {
    return std::nullopt;
  }
  return std::string(reinterpret_cast<const char*>(value.get()));
}

bool tokenMatches(std::string_view token) {
  constexpr std::array<std::string_view, 11> exact{
      "infobox",       "navbox",        "vertical-navbox", "sidebar",
      "mw-editsection", "reflist",       "references",      "reference",
      "mw-references-wrap", "toc",       "metadata",
  };
  if (std::ranges::find(exact, token) != exact.end()) {
    return true;
  }
  return token.starts_with("infobox-") || token.starts_with("navbox-") ||
         token.starts_with("mw-editsection-");
}

bool hasWikipediaUiClass(const xmlNode& node) {
  const auto class_name = attribute(node, "class");
  if (!class_name) {
    return false;
  }
  const auto normalized = lower(*class_name);
  std::size_t start = 0;
  while (start < normalized.size()) {
    while (start < normalized.size() &&
           std::isspace(static_cast<unsigned char>(normalized[start])) != 0) {
      ++start;
    }
    auto end = start;
    while (end < normalized.size() &&
           std::isspace(static_cast<unsigned char>(normalized[end])) == 0) {
      ++end;
    }
    if (start != end && tokenMatches(std::string_view(normalized).substr(start, end - start))) {
      return true;
    }
    start = end;
  }
  return false;
}

bool hasWikipediaUiId(const xmlNode& node) {
  const auto id = attribute(node, "id");
  if (!id) {
    return false;
  }
  const auto normalized = lower(*id);
  return normalized == "references" || normalized == "toc" ||
         normalized.starts_with("cite_note-") || normalized.starts_with("cite_ref-") ||
         normalized.starts_with("mw-navigation");
}

bool isEditControl(const xmlNode& node, std::string_view name) {
  if (name != "a") {
    return false;
  }
  const auto href = attribute(node, "href");
  if (!href) {
    return false;
  }
  const auto normalized = lower(*href);
  return normalized.find("action=edit") != std::string::npos ||
         normalized.find("veaction=edit") != std::string::npos;
}

bool shouldDropSubtree(const xmlNode& node, std::string_view name) {
  static const std::unordered_set<std::string> dropped_elements{
      "script",   "style",  "nav",      "table",  "template", "iframe",
      "object",   "embed",  "svg",      "form",   "button",   "input",
      "textarea", "select", "option",   "link",   "meta",     "base",
  };
  if (dropped_elements.contains(std::string(name)) || hasWikipediaUiClass(node) ||
      hasWikipediaUiId(node) || isEditControl(node, name)) {
    return true;
  }
  const auto role = attribute(node, "role");
  return role && lower(*role) == "navigation";
}

bool allowedElement(std::string_view name) {
  static const std::unordered_set<std::string> allowed{
      "p",   "br",         "h1",   "h2",     "h3", "ul", "ol", "li",
      "blockquote", "pre", "code", "strong", "b",  "em", "i",  "u",
      "s",   "a",          "img",
  };
  return allowed.contains(std::string(name));
}

std::string_view trim(std::string_view value) {
  while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front())) != 0) {
    value.remove_prefix(1);
  }
  while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back())) != 0) {
    value.remove_suffix(1);
  }
  return value;
}

bool hasUnsafeUrlCharacter(std::string_view value) {
  return std::ranges::any_of(value, [](const unsigned char character) {
    return character <= 0x20U || character == 0x7fU || character == '\\';
  });
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
  return lower(authority);
}

bool validHttpsUrl(std::string_view value, bool wikimedia_image = false) {
  if (value.empty() || hasUnsafeUrlCharacter(value)) {
    return false;
  }

  const std::string null_terminated(value);
  xmlURI* parsed_raw = nullptr;
  if (xmlParseURISafe(null_terminated.c_str(), &parsed_raw) != 0 || parsed_raw == nullptr) {
    if (parsed_raw != nullptr) {
      xmlFreeURI(parsed_raw);
    }
    return false;
  }
  XmlUri parsed(parsed_raw);
  if (parsed->scheme == nullptr || lower(parsed->scheme) != "https" || parsed->server == nullptr ||
      std::string_view(parsed->server).empty() || parsed->user != nullptr || parsed->opaque != nullptr) {
    return false;
  }

  if (!wikimedia_image) {
    return true;
  }
  const auto host = lower(parsed->server);
  const bool expected_host =
      host == "upload.wikimedia.org" || host == "commons.wikimedia.org";
  const auto authority = urlAuthority(value);
  if (!expected_host || !authority) {
    return false;
  }
  return *authority == host || *authority == host + ":443";
}

std::optional<std::string> sanitizedUrl(std::string_view raw, bool image) {
  const auto value = trim(raw);
  if (value.empty() || hasUnsafeUrlCharacter(value)) {
    return std::nullopt;
  }

  std::string resolved;
  if (value.starts_with("https://")) {
    resolved = value;
  } else if (value.starts_with("//") && value.size() > 2 && value[2] != '/') {
    resolved = "https:" + std::string(value);
  } else if (!image && value.starts_with("/wiki/")) {
    resolved = "https://en.wikipedia.org" + std::string(value);
  } else if (image && value.starts_with("/wikipedia/commons/")) {
    resolved = "https://upload.wikimedia.org" + std::string(value);
  } else {
    return std::nullopt;
  }

  if (!validHttpsUrl(resolved, image)) {
    return std::nullopt;
  }
  return resolved;
}

xmlNodePtr findBody(xmlNodePtr node) {
  for (auto* current = node; current != nullptr; current = current->next) {
    if (current->type == XML_ELEMENT_NODE && nodeName(*current) == "body") {
      return current;
    }
    if (auto* nested = findBody(current->children); nested != nullptr) {
      return nested;
    }
  }
  return nullptr;
}

Result<void> validateNodeBudget(xmlNodePtr node, std::size_t& node_count) {
  for (auto* current = node; current != nullptr; current = current->next) {
    ++node_count;
    if (node_count > kMaxDomNodes) {
      return tl::make_unexpected(rejected("HTML DOM exceeds the sanitizer node limit"));
    }
    auto children = validateNodeBudget(current->children, node_count);
    if (!children) {
      return tl::make_unexpected(children.error());
    }
  }
  return {};
}

Result<void> attachNode(xmlNodePtr parent, xmlNodePtr child) {
  if (child == nullptr) {
    return tl::make_unexpected(internalFailure("libxml2 could not allocate sanitized HTML"));
  }
  if (xmlAddChild(parent, child) == nullptr) {
    xmlFreeNode(child);
    return tl::make_unexpected(internalFailure("libxml2 could not attach sanitized HTML"));
  }
  return {};
}

Result<void> addProperty(xmlNodePtr node, const char* name, const std::string& value) {
  if (xmlNewProp(node, BAD_CAST name, BAD_CAST value.c_str()) == nullptr) {
    return tl::make_unexpected(
        internalFailure("libxml2 could not allocate a sanitized HTML attribute"));
  }
  return {};
}

Result<void> appendSanitized(xmlNodePtr input, xmlNodePtr output_parent) {
  for (auto* current = input; current != nullptr; current = current->next) {
    if (current->type == XML_TEXT_NODE || current->type == XML_CDATA_SECTION_NODE) {
      if (current->content != nullptr) {
        auto attached = attachNode(output_parent, xmlNewText(current->content));
        if (!attached) {
          return tl::make_unexpected(attached.error());
        }
      }
      continue;
    }
    if (current->type != XML_ELEMENT_NODE) {
      continue;
    }

    const auto name = nodeName(*current);
    if (shouldDropSubtree(*current, name)) {
      continue;
    }
    if (!allowedElement(name)) {
      auto children = appendSanitized(current->children, output_parent);
      if (!children) {
        return tl::make_unexpected(children.error());
      }
      continue;
    }

    std::optional<std::string> href;
    std::optional<std::string> src;
    if (name == "a") {
      if (const auto value = attribute(*current, "href")) {
        href = sanitizedUrl(*value, false);
      }
    } else if (name == "img") {
      const auto value = attribute(*current, "src");
      if (!value) {
        continue;
      }
      src = sanitizedUrl(*value, true);
      if (!src) {
        continue;
      }
    }

    auto* output = xmlNewNode(nullptr, BAD_CAST name.c_str());
    auto attached = attachNode(output_parent, output);
    if (!attached) {
      return tl::make_unexpected(attached.error());
    }

    if (href) {
      auto property = addProperty(output, "href", *href);
      if (!property) {
        return tl::make_unexpected(property.error());
      }
    }
    if (src) {
      auto property = addProperty(output, "src", *src);
      if (!property) {
        return tl::make_unexpected(property.error());
      }
    }
    if (name == "img") {
      if (const auto alt = attribute(*current, "alt")) {
        auto property = addProperty(output, "alt", *alt);
        if (!property) {
          return tl::make_unexpected(property.error());
        }
      }
    }
    if (const auto title = attribute(*current, "title")) {
      auto property = addProperty(output, "title", *title);
      if (!property) {
        return tl::make_unexpected(property.error());
      }
    }

    if (name != "img" && name != "br") {
      auto children = appendSanitized(current->children, output);
      if (!children) {
        return tl::make_unexpected(children.error());
      }
    }
  }
  return {};
}

bool hasMeaningfulContent(xmlNodePtr node) {
  for (auto* current = node; current != nullptr; current = current->next) {
    if (current->type == XML_TEXT_NODE || current->type == XML_CDATA_SECTION_NODE) {
      if (current->content != nullptr) {
        const std::string_view content(reinterpret_cast<const char*>(current->content));
        const bool non_whitespace = std::ranges::any_of(content, [](const unsigned char character) {
          return std::isspace(character) == 0;
        });
        if (non_whitespace) {
          return true;
        }
      }
      continue;
    }
    if (current->type == XML_ELEMENT_NODE && nodeName(*current) == "img") {
      return true;
    }
    if (hasMeaningfulContent(current->children)) {
      return true;
    }
  }
  return false;
}

}  // namespace

Result<SanitizedHtml> LibxmlHtmlSanitizer::sanitize(std::string_view html,
                                                    std::string_view canonical_url) {
  try {
    if (!validHttpsUrl(canonical_url)) {
      return tl::make_unexpected(rejected("Sanitizer canonical URL must use HTTPS"));
    }
    if (html.size() > kMaxInputBytes) {
      return tl::make_unexpected(rejected("HTML input exceeds the sanitizer size limit"));
    }
    if (html.find('\0') != std::string_view::npos) {
      return tl::make_unexpected(rejected("HTML input contains a NUL byte"));
    }
    if (static_cast<std::size_t>(std::ranges::count(html, '<')) > kMaxMarkupTokens) {
      return tl::make_unexpected(rejected("HTML input exceeds the sanitizer markup limit"));
    }

    constexpr int parse_options = HTML_PARSE_NONET | HTML_PARSE_NOERROR |
                                  HTML_PARSE_NOWARNING | HTML_PARSE_COMPACT;
    const std::string parser_base_url(canonical_url);
    HtmlDoc input(htmlReadMemory(html.data(), static_cast<int>(html.size()),
                                 parser_base_url.c_str(), "UTF-8", parse_options),
                  xmlFreeDoc);
    if (input == nullptr) {
      return tl::make_unexpected(rejected("libxml2 could not parse the HTML fragment"));
    }

    std::size_t node_count = 0;
    auto node_budget = validateNodeBudget(xmlDocGetRootElement(input.get()), node_count);
    if (!node_budget) {
      return tl::make_unexpected(node_budget.error());
    }

    auto* body = findBody(xmlDocGetRootElement(input.get()));
    if (body == nullptr) {
      return tl::make_unexpected(rejected("libxml2 did not produce an HTML body"));
    }

    HtmlDoc output(htmlNewDocNoDtD(nullptr, nullptr), xmlFreeDoc);
    if (output == nullptr) {
      return tl::make_unexpected(internalFailure("libxml2 could not allocate sanitized HTML"));
    }
    auto* output_body = xmlNewNode(nullptr, BAD_CAST "body");
    if (output_body == nullptr) {
      return tl::make_unexpected(internalFailure("libxml2 could not allocate sanitized HTML"));
    }
    xmlDocSetRootElement(output.get(), output_body);
    auto reconstructed = appendSanitized(body->children, output_body);
    if (!reconstructed) {
      return tl::make_unexpected(reconstructed.error());
    }
    if (!hasMeaningfulContent(output_body->children)) {
      return tl::make_unexpected(rejected("Sanitized HTML contains no meaningful content"));
    }

    XmlBuffer buffer(xmlBufferCreate(), xmlBufferFree);
    if (buffer == nullptr) {
      return tl::make_unexpected(
          internalFailure("libxml2 could not allocate a serialization buffer"));
    }
    for (auto* child = output_body->children; child != nullptr; child = child->next) {
      if (htmlNodeDump(buffer.get(), output.get(), child) < 0) {
        return tl::make_unexpected(internalFailure("libxml2 could not serialize sanitized HTML"));
      }
      const auto output_size = xmlBufferLength(buffer.get());
      if (output_size < 0 || static_cast<std::size_t>(output_size) > kMaxOutputBytes) {
        return tl::make_unexpected(rejected("Sanitized HTML exceeds the output size limit"));
      }
    }

    const auto* content = xmlBufferContent(buffer.get());
    if (content == nullptr) {
      return tl::make_unexpected(internalFailure("libxml2 returned an empty serialization buffer"));
    }
    return SanitizedHtml{
        .value = std::string(reinterpret_cast<const char*>(content), xmlBufferLength(buffer.get())),
    };
  } catch (const std::bad_alloc&) {
    return tl::make_unexpected(
        internalFailure("Memory allocation failed while sanitizing HTML"));
  }
}

}  // namespace babel
