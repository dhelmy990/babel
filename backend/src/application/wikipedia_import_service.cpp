#include "babel/application/wikipedia_import_service.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <utility>

#include <openssl/evp.h>

namespace babel {
namespace {

Result<std::string> sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_Digest(value.data(), value.size(), digest.data(), &digest_size, EVP_sha256(), nullptr) !=
          1 ||
      digest_size != 32) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "OpenSSL could not hash sanitized Wikipedia HTML",
    });
  }

  constexpr char hex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(digest_size * 2U);
  for (unsigned int index = 0; index < digest_size; ++index) {
    encoded.push_back(hex[(digest[index] >> 4U) & 0x0fU]);
    encoded.push_back(hex[digest[index] & 0x0fU]);
  }
  return encoded;
}

bool hasNonWhitespace(std::string_view value) {
  return std::ranges::any_of(value, [](unsigned char character) {
    return !std::isspace(character);
  });
}

}  // namespace

WikipediaImportService::WikipediaImportService(CreatorRepository& creators,
                                               WikipediaBabelRepository& babels,
                                               ArticleSource& source,
                                               HtmlSanitizer& sanitizer,
                                               IdGenerator& ids)
    : creators_(creators), babels_(babels), source_(source), sanitizer_(sanitizer), ids_(ids) {}

Result<ImportWikipediaBabelResult> WikipediaImportService::importWikipediaBabel(
    CreatorId creator_id, WikipediaPageId page_id) {
  return importCanonical(std::move(creator_id), page_id, std::nullopt);
}

Result<ImportWikipediaBabelResult> WikipediaImportService::importWikipediaBabel(
    CreatorId creator_id, WikipediaPageId page_id, SeedImportContext context) {
  if (!hasNonWhitespace(context.declared_title)) {
    return invalidArgument("seed import declared title must not be blank");
  }
  return importCanonical(std::move(creator_id), page_id, std::move(context));
}

Result<ImportWikipediaBabelResult> WikipediaImportService::importCanonical(
    CreatorId creator_id, WikipediaPageId page_id,
    const std::optional<SeedImportContext>& context) {
  auto creator = creators_.get(creator_id);
  if (!creator) return tl::make_unexpected(creator.error());

  auto existing = babels_.findByPage(creator_id, page_id);
  if (!existing) return tl::make_unexpected(existing.error());
  if (*existing) return existingResult(**existing, context);

  auto article = source_.fetchByPageId(page_id);
  if (!article) return tl::make_unexpected(article.error());
  if (article->page_id != page_id) {
    return tl::make_unexpected(ApplicationError{
        .code = ErrorCode::internal,
        .message = "Wikipedia source returned a different page ID than requested",
    });
  }

  auto sanitized = sanitizer_.sanitize(article->rendered_html, article->canonical_url);
  if (!sanitized) return tl::make_unexpected(sanitized.error());
  auto content_hash = sha256(sanitized->value);
  if (!content_hash) return tl::make_unexpected(content_hash.error());

  const auto babel_id = ids_.newBabelId();
  const Babel babel{
      .id = babel_id,
      .owner_id = creator_id,
      .title = article->canonical_title,
      .content_html = sanitized->value,
      .color = creator->color,
      .content_revision = 1,
      .content_hash = std::move(*content_hash),
  };
  const BabelSource babel_source{
      .babel_id = babel_id,
      .owner_id = creator_id,
      .provider = "wikipedia",
      .external_page_id = page_id,
      .canonical_url = article->canonical_url,
      .source_revision_id = article->revision_id,
      .seed_assignment_id = context ? std::optional<SeedAssignmentId>{context->assignment_id}
                                    : std::nullopt,
      .declared_title = context ? context->declared_title : article->canonical_title,
  };

  auto inserted = babels_.insertWikipediaBabel(babel, babel_source);
  if (!inserted) {
    if (inserted.error().code != ErrorCode::conflict) {
      return tl::make_unexpected(inserted.error());
    }

    auto concurrent = babels_.findByPage(creator_id, page_id);
    if (!concurrent) return tl::make_unexpected(concurrent.error());
    if (!*concurrent) return tl::make_unexpected(inserted.error());
    return existingResult(**concurrent, context);
  }

  return ImportWikipediaBabelResult{
      .status = ImportWikipediaStatus::imported,
      .babel_id = babel_id,
      .canonical_title = article->canonical_title,
  };
}

Result<ImportWikipediaBabelResult> WikipediaImportService::existingResult(
    const Babel& existing, const std::optional<SeedImportContext>& context) {
  if (context) {
    auto attached = babels_.attachSeedAssignment(existing.id, context->assignment_id,
                                                 context->declared_title);
    if (!attached) return tl::make_unexpected(attached.error());
  }
  return ImportWikipediaBabelResult{
      .status = ImportWikipediaStatus::already_exists,
      .babel_id = existing.id,
      .canonical_title = existing.title,
  };
}

}  // namespace babel
