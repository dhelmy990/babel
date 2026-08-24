#pragma once

#include <optional>
#include <string>

#include "babel/application/ports.hpp"

namespace babel {

enum class ImportWikipediaStatus { imported, already_exists };

struct ImportWikipediaBabelResult {
  ImportWikipediaStatus status;
  BabelId babel_id;
  std::string canonical_title;
};

struct SeedImportContext {
  SeedAssignmentId assignment_id;
  std::string declared_title;
};

class WikipediaImporter {
 public:
  virtual ~WikipediaImporter() = default;

  virtual Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId,
                                                                  WikipediaPageId) = 0;
  virtual Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId, WikipediaPageId,
                                                                  SeedImportContext) = 0;
};

class WikipediaImportService final : public WikipediaImporter {
 public:
  WikipediaImportService(CreatorRepository&, WikipediaBabelRepository&, ArticleSource&,
                         HtmlSanitizer&, IdGenerator&);

  Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId,
                                                          WikipediaPageId) override;
  Result<ImportWikipediaBabelResult> importWikipediaBabel(CreatorId, WikipediaPageId,
                                                          SeedImportContext) override;

 private:
  Result<ImportWikipediaBabelResult> importCanonical(
      CreatorId, WikipediaPageId, const std::optional<SeedImportContext>&);
  Result<ImportWikipediaBabelResult> existingResult(
      const Babel&, const std::optional<SeedImportContext>&);

  CreatorRepository& creators_;
  WikipediaBabelRepository& babels_;
  ArticleSource& source_;
  HtmlSanitizer& sanitizer_;
  IdGenerator& ids_;
};

}  // namespace babel
