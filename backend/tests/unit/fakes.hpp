#pragma once

#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "babel/application/ports.hpp"

namespace babel::test {

inline ApplicationError error(ErrorCode code, std::string message) {
  return ApplicationError{.code = code, .message = std::move(message)};
}

class FakeCreatorRepository final : public CreatorRepository {
 public:
  Result<bool> exists(CreatorId id) override {
    ++exists_count;
    if (exists_error) return tl::make_unexpected(*exists_error);
    return creator && creator->id == id;
  }

  Result<Creator> get(CreatorId id) override {
    ++get_count;
    if (get_error) return tl::make_unexpected(*get_error);
    if (!creator || creator->id != id) {
      return tl::make_unexpected(error(ErrorCode::not_found, "creator not found"));
    }
    return *creator;
  }

  Result<std::vector<Creator>> listOrdered() override {
    if (list_error) return tl::make_unexpected(*list_error);
    if (!creator) return std::vector<Creator>{};
    return std::vector<Creator>{*creator};
  }

  std::optional<Creator> creator;
  std::optional<ApplicationError> exists_error;
  std::optional<ApplicationError> get_error;
  std::optional<ApplicationError> list_error;
  int exists_count{0};
  int get_count{0};
};

class FakeArticleSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view title) override {
    ++resolve_count;
    last_resolved_title = title;
    if (resolve_error) return tl::make_unexpected(*resolve_error);
    return resolved_page;
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId page_id) override {
    ++fetch_count;
    last_fetched_page = page_id;
    if (fetch_error) return tl::make_unexpected(*fetch_error);
    const auto found = articles.find(page_id.value);
    if (found == articles.end()) {
      return tl::make_unexpected(error(ErrorCode::wikipedia_not_found,
                                       "fake Wikipedia article not found"));
    }
    if (found->second.page_id != page_id) {
      return tl::make_unexpected(
          error(ErrorCode::internal, "fake Wikipedia article page ID mismatch"));
    }
    return found->second;
  }

  [[nodiscard]] std::string_view provider() const noexcept override {
    return provider_name;
  }

  void addArticle(RawWikipediaArticle article) {
    articles.insert_or_assign(article.page_id.value, std::move(article));
  }

  void setArticleFor(WikipediaPageId requested_page_id, RawWikipediaArticle article) {
    articles.insert_or_assign(requested_page_id.value, std::move(article));
  }

  RawWikipediaArticle& articleFor(WikipediaPageId page_id) {
    return articles.at(page_id.value);
  }

  ResolvedWikipediaPage resolved_page{
      .page_id = WikipediaPageId::fromInt(1).value(),
      .canonical_title = "Article",
      .canonical_url = "https://en.wikipedia.org/wiki/Article",
  };
  std::unordered_map<std::int64_t, RawWikipediaArticle> articles;
  std::optional<ApplicationError> resolve_error;
  std::optional<ApplicationError> fetch_error;
  std::string last_resolved_title;
  std::optional<WikipediaPageId> last_fetched_page;
  int resolve_count{0};
  int fetch_count{0};
  std::string provider_name{"wikipedia"};
};

class FakeHtmlSanitizer final : public HtmlSanitizer {
 public:
  Result<SanitizedHtml> sanitize(std::string_view html,
                                 std::string_view canonical_url) override {
    ++sanitize_count;
    last_html = html;
    last_canonical_url = canonical_url;
    if (sanitize_error) return tl::make_unexpected(*sanitize_error);
    return sanitized;
  }

  SanitizedHtml sanitized{.value = "<p>Article</p>"};
  std::optional<ApplicationError> sanitize_error;
  std::string last_html;
  std::string last_canonical_url;
  int sanitize_count{0};
};

class FakeWikipediaBabelRepository final : public WikipediaBabelRepository {
 public:
  struct Record {
    Babel babel;
    BabelSource source;
  };

  Result<std::optional<Babel>> findByPage(
      CreatorId owner_id, WikipediaPageId page_id,
      std::string_view provider = "wikipedia") override {
    ++find_count;
    last_find_owner = owner_id;
    last_find_page = page_id;
    last_find_provider = provider;
    if (find_error) return tl::make_unexpected(*find_error);
    for (const auto& record : records) {
      if (record.source.owner_id == owner_id && record.source.provider == provider &&
          record.source.external_page_id == page_id) {
        return std::optional<Babel>{record.babel};
      }
    }
    return std::optional<Babel>{};
  }

  Result<void> insertWikipediaBabel(const Babel& babel,
                                    const BabelSource& source) override {
    ++insert_count;
    last_babel = babel;
    last_source = source;
    if (insert_error) {
      if (record_after_failed_insert) records.push_back(*record_after_failed_insert);
      return tl::make_unexpected(*insert_error);
    }
    return storeRecord(babel, source);
  }

  Result<void> attachSeedAssignment(BabelId babel_id, SeedAssignmentId assignment_id,
                                    std::string_view declared_title) override {
    ++attach_count;
    last_attached_babel = babel_id;
    last_assignment_id = assignment_id;
    last_declared_title = declared_title;
    if (attach_error) return tl::make_unexpected(*attach_error);

    auto found = records.end();
    for (auto candidate = records.begin(); candidate != records.end(); ++candidate) {
      if (candidate->babel.id == babel_id) {
        found = candidate;
        break;
      }
    }
    if (found == records.end()) {
      return tl::make_unexpected(
          error(ErrorCode::not_found, "fake Wikipedia source not found"));
    }

    if (found->source.seed_assignment_id) {
      if (found->source.seed_assignment_id == assignment_id &&
          found->source.declared_title == declared_title) {
        return {};
      }
      return tl::make_unexpected(
          error(ErrorCode::conflict, "fake Wikipedia source has different seed provenance"));
    }
    for (const auto& record : records) {
      if (record.source.seed_assignment_id == assignment_id) {
        return tl::make_unexpected(
            error(ErrorCode::conflict, "fake seed assignment is already attached"));
      }
    }
    found->source.seed_assignment_id = assignment_id;
    found->source.declared_title = std::string(declared_title);
    return {};
  }

  Result<void> seedRecord(const Babel& babel, const BabelSource& source) {
    return storeRecord(babel, source);
  }

  std::vector<Record> records;
  std::optional<Record> record_after_failed_insert;
  std::optional<ApplicationError> find_error;
  std::optional<ApplicationError> insert_error;
  std::optional<ApplicationError> attach_error;
  std::optional<CreatorId> last_find_owner;
  std::optional<WikipediaPageId> last_find_page;
  std::string last_find_provider;
  std::optional<Babel> last_babel;
  std::optional<BabelSource> last_source;
  std::optional<BabelId> last_attached_babel;
  std::optional<SeedAssignmentId> last_assignment_id;
  std::string last_declared_title;
  int find_count{0};
  int insert_count{0};
  int attach_count{0};

 private:
  Result<void> storeRecord(const Babel& babel, const BabelSource& source) {
    if (source.babel_id != babel.id || source.owner_id != babel.owner_id ||
        (source.provider != "wikipedia" && source.provider != "huggingface_wikipedia")) {
      return tl::make_unexpected(
          error(ErrorCode::invalid_argument, "fake Wikipedia source identity mismatch"));
    }
    for (const auto& record : records) {
      if (record.babel.id == babel.id ||
          (record.source.owner_id == source.owner_id && record.source.provider == source.provider &&
           record.source.external_page_id == source.external_page_id)) {
        return tl::make_unexpected(
            error(ErrorCode::conflict, "fake Wikipedia Babel identity conflict"));
      }
      if (source.seed_assignment_id &&
          record.source.seed_assignment_id == source.seed_assignment_id) {
        return tl::make_unexpected(
            error(ErrorCode::conflict, "fake seed assignment is already attached"));
      }
    }
    records.push_back(Record{.babel = babel, .source = source});
    return {};
  }
};

class FakeIdGenerator final : public IdGenerator {
 public:
  BabelId newBabelId() override {
    ++babel_count;
    return babel_id;
  }

  EdgeId newEdgeId() override {
    ++edge_count;
    return edge_id;
  }

  BabelId babel_id{BabelId::v5("test:babel").value()};
  EdgeId edge_id{EdgeId::v5("test:edge").value()};
  int babel_count{0};
  int edge_count{0};
};

}  // namespace babel::test
