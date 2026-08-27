#pragma once

#include <filesystem>
#include <memory>
#include <string>

#include "babel/adapters/wikipedia/mediawiki_article_source.hpp"

namespace babel {

class HuggingFaceArticleSourceFactory final : public ArticleSourceFactory {
 public:
  HuggingFaceArticleSourceFactory(HttpTransport&, std::filesystem::path cache_root,
                                  std::string token);

  Result<std::shared_ptr<PinnedArticleSource>> pin(const SourceSelection&) override;

 private:
  HttpTransport& transport_;
  std::filesystem::path cache_root_;
  std::string token_;
};

}  // namespace babel
