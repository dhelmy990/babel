#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "babel/application/ports.hpp"

namespace babel {

struct HttpRequest {
  std::string url;
  std::int64_t connect_timeout_ms;
  std::int64_t total_timeout_ms;
  bool follow_redirects;
  std::vector<std::string> headers;
};

struct HttpResponse {
  long status_code;
  std::string body;
};

class HttpTransport {
 public:
  virtual ~HttpTransport() = default;

  virtual Result<HttpResponse> get(const HttpRequest& request) = 0;
};

class CurlHttpTransport final : public HttpTransport {
 public:
  Result<HttpResponse> get(const HttpRequest& request) override;
};

class MediaWikiArticleSource final : public ArticleSource {
 public:
  explicit MediaWikiArticleSource(HttpTransport& transport);

  Result<ResolvedWikipediaPage> resolveTitle(std::string_view title) override;
  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId page_id) override;

 private:
  HttpTransport& transport_;
};

}  // namespace babel
