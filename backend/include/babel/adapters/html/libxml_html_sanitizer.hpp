#pragma once

#include "babel/application/ports.hpp"

namespace babel {

class LibxmlHtmlSanitizer final : public HtmlSanitizer {
 public:
  Result<SanitizedHtml> sanitize(std::string_view html,
                                 std::string_view canonical_url) override;
};

}  // namespace babel
