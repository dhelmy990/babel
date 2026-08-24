#include <catch2/catch_test_macros.hpp>

#include "babel/application/wikipedia_import_service.hpp"
#include "fakes.hpp"

namespace {

using namespace babel;
using namespace babel::test;

Creator distributedSystemsCreator() {
  return Creator{
      .id = CreatorId::v5("creator:distributed-systems").value(),
      .slug = "distributed-systems",
      .display_name = "Distributed Systems Creator",
      .color = "#3DDC97",
      .kind = CreatorKind::generated,
      .order = 1,
  };
}

Creator machineLearningCreator() {
  return Creator{
      .id = CreatorId::v5("creator:machine-learning-systems").value(),
      .slug = "machine-learning-systems",
      .display_name = "Machine Learning Systems Creator",
      .color = "#4CC9F0",
      .kind = CreatorKind::generated,
      .order = 2,
  };
}

BabelSource wikipediaSource(const Babel& babel, WikipediaPageId page_id) {
  return BabelSource{
      .babel_id = babel.id,
      .owner_id = babel.owner_id,
      .provider = "wikipedia",
      .external_page_id = page_id,
      .canonical_url = "https://en.wikipedia.org/?curid=" + std::to_string(page_id.value),
      .source_revision_id = std::nullopt,
      .seed_assignment_id = std::nullopt,
      .declared_title = babel.title,
  };
}

class MismatchedArticleSource final : public ArticleSource {
 public:
  Result<ResolvedWikipediaPage> resolveTitle(std::string_view) override {
    return tl::make_unexpected(error(ErrorCode::internal, "not used"));
  }

  Result<RawWikipediaArticle> fetchByPageId(WikipediaPageId) override {
    ++fetch_count;
    return article;
  }

  RawWikipediaArticle article{
      .page_id = WikipediaPageId::fromInt(56).value(),
      .canonical_title = "Wrong page",
      .canonical_url = "https://en.wikipedia.org/wiki/Wrong_page",
      .revision_id = 1,
      .rendered_html = "<p>Wrong</p>",
  };
  int fetch_count{0};
};

struct ImportFixture {
  ImportFixture() {
    creators.creator = distributedSystemsCreator();
    source.addArticle(RawWikipediaArticle{
        .page_id = page_id,
        .canonical_title = "Canonical title",
        .canonical_url = "https://en.wikipedia.org/wiki/Canonical_title",
        .revision_id = 21,
        .rendered_html = "<p>Raw</p>",
    });
  }

  FakeCreatorRepository creators;
  FakeWikipediaBabelRepository babels;
  FakeArticleSource source;
  FakeHtmlSanitizer sanitizer;
  FakeIdGenerator ids;
  WikipediaPageId page_id{WikipediaPageId::fromInt(55).value()};
};

TEST_CASE("page ID import creates one owned sanitized Babel") {
  FakeCreatorRepository creators;
  creators.creator = distributedSystemsCreator();
  FakeArticleSource source;
  const RawWikipediaArticle article{
      .page_id = WikipediaPageId::fromInt(42).value(),
      .canonical_title = "Distributed computing",
      .canonical_url = "https://en.wikipedia.org/wiki/Distributed_computing",
      .revision_id = 1301234567,
      .rendered_html = "<p onclick='x()'>Safe</p>",
  };
  source.addArticle(article);
  FakeHtmlSanitizer sanitizer;
  sanitizer.sanitized = SanitizedHtml{.value = "<p>Safe</p>"};
  FakeWikipediaBabelRepository babels;
  FakeIdGenerator ids;
  WikipediaImportService service(creators, babels, source, sanitizer, ids);
  const auto page_id = WikipediaPageId::fromInt(42).value();

  const auto first = service.importWikipediaBabel(creators.creator->id, page_id);
  const auto second = service.importWikipediaBabel(creators.creator->id, page_id);

  REQUIRE(first.has_value());
  REQUIRE(second.has_value());
  CHECK(first->status == ImportWikipediaStatus::imported);
  CHECK(second->status == ImportWikipediaStatus::already_exists);
  CHECK(first->babel_id == ids.babel_id);
  CHECK(first->canonical_title == "Distributed computing");
  REQUIRE(babels.last_babel.has_value());
  CHECK(babels.last_babel->owner_id == creators.creator->id);
  CHECK(babels.last_babel->title == article.canonical_title);
  CHECK(babels.last_babel->content_html == "<p>Safe</p>");
  CHECK(babels.last_babel->color == creators.creator->color);
  CHECK(babels.last_babel->content_revision == 1);
  CHECK(babels.last_babel->content_hash ==
        "dbcc3a6541c3126da1e089007fa02ba8b54f2265b00d0664d9b55701f6307bb3");
  REQUIRE(babels.last_source.has_value());
  CHECK(babels.last_source->babel_id == ids.babel_id);
  CHECK(babels.last_source->owner_id == creators.creator->id);
  CHECK(babels.last_source->provider == "wikipedia");
  CHECK(babels.last_source->external_page_id == page_id);
  CHECK(babels.last_source->canonical_url == article.canonical_url);
  CHECK(babels.last_source->source_revision_id == article.revision_id);
  CHECK_FALSE(babels.last_source->seed_assignment_id.has_value());
  CHECK(babels.last_source->declared_title == article.canonical_title);
  CHECK(babels.insert_count == 1);
  CHECK(source.fetch_count == 1);
  CHECK(ids.babel_count == 1);
  CHECK(sanitizer.last_canonical_url == article.canonical_url);
}

TEST_CASE("different pages for one owner never share fake repository identity") {
  ImportFixture fixture;
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto first =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);
  auto second_article = fixture.source.articleFor(fixture.page_id);
  second_article.page_id = WikipediaPageId::fromInt(56).value();
  second_article.canonical_title = "Another page";
  fixture.source.addArticle(second_article);
  fixture.ids.babel_id = BabelId::v5("test:another-page").value();
  const auto second = service.importWikipediaBabel(fixture.creators.creator->id,
                                                   second_article.page_id);

  REQUIRE(first.has_value());
  REQUIRE(second.has_value());
  CHECK(first->status == ImportWikipediaStatus::imported);
  CHECK(second->status == ImportWikipediaStatus::imported);
  CHECK(fixture.source.fetch_count == 2);
  CHECK(fixture.babels.insert_count == 2);
}

TEST_CASE("the same page for different owners never shares fake repository identity") {
  ImportFixture fixture;
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto first =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);
  fixture.creators.creator = machineLearningCreator();
  fixture.ids.babel_id = BabelId::v5("test:other-owner").value();
  const auto second =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE(first.has_value());
  REQUIRE(second.has_value());
  CHECK(first->status == ImportWikipediaStatus::imported);
  CHECK(second->status == ImportWikipediaStatus::imported);
  CHECK(fixture.source.fetch_count == 2);
  CHECK(fixture.babels.insert_count == 2);
}

TEST_CASE("seed import stores its stable assignment and declared title atomically") {
  FakeCreatorRepository creators;
  creators.creator = distributedSystemsCreator();
  FakeArticleSource source;
  const RawWikipediaArticle article{
      .page_id = WikipediaPageId::fromInt(43).value(),
      .canonical_title = "Canonical title",
      .canonical_url = "https://en.wikipedia.org/wiki/Canonical_title",
      .revision_id = 17,
      .rendered_html = "<p>Body</p>",
  };
  source.addArticle(article);
  FakeHtmlSanitizer sanitizer;
  FakeWikipediaBabelRepository babels;
  FakeIdGenerator ids;
  WikipediaImportService service(creators, babels, source, sanitizer, ids);
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:distributed-systems:Declared title").value(),
      .declared_title = "Declared title",
  };

  const auto result =
      service.importWikipediaBabel(creators.creator->id, article.page_id, context);

  REQUIRE(result.has_value());
  REQUIRE(babels.last_source.has_value());
  CHECK(babels.last_source->seed_assignment_id == context.assignment_id);
  CHECK(babels.last_source->declared_title == context.declared_title);
  CHECK(babels.attach_count == 0);
}

TEST_CASE("seed import rejects a whitespace-only declared title before repository work") {
  ImportFixture fixture;
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:blank-title").value(),
      .declared_title = " \t\n",
  };
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result = service.importWikipediaBabel(fixture.creators.creator->id,
                                                   fixture.page_id, context);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::invalid_argument);
  CHECK(fixture.creators.get_count == 0);
  CHECK(fixture.babels.find_count == 0);
  CHECK(fixture.source.fetch_count == 0);
  CHECK(fixture.sanitizer.sanitize_count == 0);
  CHECK(fixture.ids.babel_count == 0);
  CHECK(fixture.babels.insert_count == 0);
}

TEST_CASE("seed import attaches context to an existing owner page without refetching") {
  FakeCreatorRepository creators;
  creators.creator = distributedSystemsCreator();
  FakeArticleSource source;
  FakeHtmlSanitizer sanitizer;
  FakeWikipediaBabelRepository babels;
  FakeIdGenerator ids;
  const auto page_id = WikipediaPageId::fromInt(44).value();
  const Babel existing{
      .id = BabelId::v5("existing:babel").value(),
      .owner_id = creators.creator->id,
      .title = "Existing canonical title",
      .content_html = "<p>Existing</p>",
      .color = creators.creator->color,
      .content_revision = 1,
      .content_hash = std::string(64, 'a'),
  };
  REQUIRE(babels.seedRecord(existing, wikipediaSource(existing, page_id)).has_value());
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:existing").value(),
      .declared_title = "Manifest title",
  };
  WikipediaImportService service(creators, babels, source, sanitizer, ids);

  const auto result = service.importWikipediaBabel(creators.creator->id, page_id, context);

  REQUIRE(result.has_value());
  CHECK(result->status == ImportWikipediaStatus::already_exists);
  CHECK(result->babel_id == existing.id);
  CHECK(result->canonical_title == existing.title);
  CHECK(babels.attach_count == 1);
  CHECK(babels.last_attached_babel == existing.id);
  CHECK(babels.last_assignment_id == context.assignment_id);
  CHECK(babels.last_declared_title == context.declared_title);
  REQUIRE(babels.records.size() == 1);
  CHECK(babels.records.front().source.seed_assignment_id == context.assignment_id);
  CHECK(babels.records.front().source.declared_title == context.declared_title);
  CHECK(babels.insert_count == 0);
  CHECK(source.fetch_count == 0);
  CHECK(sanitizer.sanitize_count == 0);
  CHECK(ids.babel_count == 0);

  const auto repeated = service.importWikipediaBabel(creators.creator->id, page_id, context);
  REQUIRE(repeated.has_value());
  CHECK(repeated->status == ImportWikipediaStatus::already_exists);

  auto changed_title = context;
  changed_title.declared_title = "Changed manifest title";
  const auto title_conflict =
      service.importWikipediaBabel(creators.creator->id, page_id, changed_title);
  REQUIRE_FALSE(title_conflict.has_value());
  CHECK(title_conflict.error().code == ErrorCode::conflict);
  CHECK(babels.records.front().source.declared_title == context.declared_title);

  const SeedImportContext other_assignment{
      .assignment_id = SeedAssignmentId::v5("seed:other-existing").value(),
      .declared_title = context.declared_title,
  };
  const auto assignment_conflict =
      service.importWikipediaBabel(creators.creator->id, page_id, other_assignment);
  REQUIRE_FALSE(assignment_conflict.has_value());
  CHECK(assignment_conflict.error().code == ErrorCode::conflict);
  CHECK(babels.records.front().source.seed_assignment_id == context.assignment_id);
}

TEST_CASE("fake article source distinguishes missing and mismatched page identities") {
  FakeArticleSource source;
  const auto requested = WikipediaPageId::fromInt(60).value();

  const auto missing = source.fetchByPageId(requested);
  REQUIRE_FALSE(missing.has_value());
  CHECK(missing.error().code == ErrorCode::wikipedia_not_found);

  source.setArticleFor(
      requested,
      RawWikipediaArticle{
          .page_id = WikipediaPageId::fromInt(61).value(),
          .canonical_title = "Mismatch",
          .canonical_url = "https://en.wikipedia.org/wiki/Mismatch",
          .revision_id = 1,
          .rendered_html = "<p>Mismatch</p>",
      });
  const auto mismatch = source.fetchByPageId(requested);
  REQUIRE_FALSE(mismatch.has_value());
  CHECK(mismatch.error().code == ErrorCode::internal);
}

TEST_CASE("fake repository validates insert identity and requires a source for attachment") {
  FakeWikipediaBabelRepository babels;
  const auto owner = distributedSystemsCreator().id;
  const Babel article{
      .id = BabelId::v5("fake:identity").value(),
      .owner_id = owner,
      .title = "Identity",
      .content_html = "<p>Identity</p>",
      .color = "#3DDC97",
      .content_revision = 1,
      .content_hash = std::string(64, 'd'),
  };
  auto mismatched = wikipediaSource(article, WikipediaPageId::fromInt(62).value());
  mismatched.owner_id = machineLearningCreator().id;

  const auto insert = babels.insertWikipediaBabel(article, mismatched);
  REQUIRE_FALSE(insert.has_value());
  CHECK(insert.error().code == ErrorCode::invalid_argument);
  CHECK(babels.records.empty());

  const auto attach = babels.attachSeedAssignment(
      article.id, SeedAssignmentId::v5("seed:missing-source").value(), "Missing source");
  REQUIRE_FALSE(attach.has_value());
  CHECK(attach.error().code == ErrorCode::not_found);
}

TEST_CASE("fake repository enforces seed assignment uniqueness across sources") {
  FakeWikipediaBabelRepository babels;
  const auto owner = distributedSystemsCreator().id;
  const auto assignment = SeedAssignmentId::v5("seed:unique-across-sources").value();
  const Babel first{
      .id = BabelId::v5("fake:first-source").value(),
      .owner_id = owner,
      .title = "First",
      .content_html = "<p>First</p>",
      .color = "#3DDC97",
      .content_revision = 1,
      .content_hash = std::string(64, 'e'),
  };
  auto first_source = wikipediaSource(first, WikipediaPageId::fromInt(63).value());
  first_source.seed_assignment_id = assignment;
  first_source.declared_title = "First assignment";
  const Babel second{
      .id = BabelId::v5("fake:second-source").value(),
      .owner_id = owner,
      .title = "Second",
      .content_html = "<p>Second</p>",
      .color = "#3DDC97",
      .content_revision = 1,
      .content_hash = std::string(64, 'f'),
  };
  REQUIRE(babels.seedRecord(first, first_source).has_value());
  REQUIRE(babels
              .seedRecord(second,
                          wikipediaSource(second, WikipediaPageId::fromInt(64).value()))
              .has_value());

  const auto attach = babels.attachSeedAssignment(second.id, assignment, "Second assignment");

  REQUIRE_FALSE(attach.has_value());
  CHECK(attach.error().code == ErrorCode::conflict);
  CHECK_FALSE(babels.records.at(1).source.seed_assignment_id.has_value());
}

TEST_CASE("unknown creator fails before lookup or source fetch") {
  ImportFixture fixture;
  fixture.creators.creator.reset();
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result = service.importWikipediaBabel(distributedSystemsCreator().id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::not_found);
  CHECK(fixture.creators.get_count == 1);
  CHECK(fixture.babels.find_count == 0);
  CHECK(fixture.source.fetch_count == 0);
  CHECK(fixture.babels.insert_count == 0);
}

TEST_CASE("creator repository failure propagates before source fetch") {
  ImportFixture fixture;
  fixture.creators.get_error = error(ErrorCode::database_unavailable, "creator database down");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.creators.get_error);
  CHECK(fixture.babels.find_count == 0);
  CHECK(fixture.source.fetch_count == 0);
}

TEST_CASE("Wikipedia failure propagates without sanitizing or inserting") {
  ImportFixture fixture;
  fixture.source.fetch_error = error(ErrorCode::wikipedia_unavailable, "Wikipedia down");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.source.fetch_error);
  CHECK(fixture.sanitizer.sanitize_count == 0);
  CHECK(fixture.babels.insert_count == 0);
  CHECK(fixture.babels.records.empty());
}

TEST_CASE("mismatched fetched page identity fails before sanitizing or inserting") {
  ImportFixture fixture;
  MismatchedArticleSource source;
  WikipediaImportService service(fixture.creators, fixture.babels, source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error().code == ErrorCode::internal);
  CHECK(source.fetch_count == 1);
  CHECK(fixture.sanitizer.sanitize_count == 0);
  CHECK(fixture.ids.babel_count == 0);
  CHECK(fixture.babels.insert_count == 0);
  CHECK(fixture.babels.records.empty());
}

TEST_CASE("sanitizer rejection propagates without generating or inserting") {
  ImportFixture fixture;
  fixture.sanitizer.sanitize_error = error(ErrorCode::sanitizer_rejected, "unsafe HTML");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.sanitizer.sanitize_error);
  CHECK(fixture.ids.babel_count == 0);
  CHECK(fixture.babels.insert_count == 0);
  CHECK(fixture.babels.records.empty());
}

TEST_CASE("repository insertion failure leaves no partial fake records") {
  ImportFixture fixture;
  fixture.babels.insert_error = error(ErrorCode::database_unavailable, "insert failed");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.babels.insert_error);
  CHECK(fixture.babels.insert_count == 1);
  CHECK(fixture.babels.records.empty());
}

TEST_CASE("repository lookup failure propagates without source fetch") {
  ImportFixture fixture;
  fixture.babels.find_error = error(ErrorCode::database_unavailable, "lookup failed");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.babels.find_error);
  CHECK(fixture.source.fetch_count == 0);
  CHECK(fixture.babels.insert_count == 0);
}

TEST_CASE("seed attachment conflict propagates without source fetch") {
  ImportFixture fixture;
  const Babel existing{
      .id = BabelId::v5("attached:babel").value(),
      .owner_id = fixture.creators.creator->id,
      .title = "Canonical title",
      .content_html = "<p>Existing</p>",
      .color = fixture.creators.creator->color,
      .content_revision = 1,
      .content_hash = std::string(64, 'b'),
  };
  REQUIRE(fixture.babels
              .seedRecord(existing, wikipediaSource(existing, fixture.page_id))
              .has_value());
  fixture.babels.attach_error = error(ErrorCode::conflict, "assignment conflict");
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:conflict").value(),
      .declared_title = "Declared title",
  };
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result = service.importWikipediaBabel(fixture.creators.creator->id,
                                                   fixture.page_id, context);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.babels.attach_error);
  CHECK(fixture.source.fetch_count == 0);
  CHECK(fixture.babels.insert_count == 0);
}

TEST_CASE("uniqueness race recovers by returning the concurrent owner page") {
  ImportFixture fixture;
  const Babel concurrent{
      .id = BabelId::v5("concurrent:winner").value(),
      .owner_id = fixture.creators.creator->id,
      .title = "Concurrent canonical title",
      .content_html = "<p>Winner</p>",
      .color = fixture.creators.creator->color,
      .content_revision = 1,
      .content_hash = std::string(64, 'c'),
  };
  fixture.babels.insert_error = error(ErrorCode::conflict, "unique owner/page");
  fixture.babels.record_after_failed_insert = FakeWikipediaBabelRepository::Record{
      .babel = concurrent,
      .source = wikipediaSource(concurrent, fixture.page_id),
  };
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:concurrent").value(),
      .declared_title = "Concurrent manifest title",
  };
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result = service.importWikipediaBabel(fixture.creators.creator->id,
                                                   fixture.page_id, context);

  REQUIRE(result.has_value());
  CHECK(result->status == ImportWikipediaStatus::already_exists);
  CHECK(result->babel_id == concurrent.id);
  CHECK(result->canonical_title == concurrent.title);
  CHECK(fixture.babels.find_count == 2);
  CHECK(fixture.babels.insert_count == 1);
  CHECK(fixture.babels.attach_count == 1);
  CHECK(fixture.babels.last_attached_babel == concurrent.id);
  CHECK(fixture.babels.last_assignment_id == context.assignment_id);
  CHECK(fixture.babels.last_declared_title == context.declared_title);
}

TEST_CASE("uniqueness conflict remains an error when no owner page appears") {
  ImportFixture fixture;
  fixture.babels.insert_error = error(ErrorCode::conflict, "unrelated uniqueness conflict");
  WikipediaImportService service(fixture.creators, fixture.babels, fixture.source,
                                 fixture.sanitizer, fixture.ids);

  const auto result =
      service.importWikipediaBabel(fixture.creators.creator->id, fixture.page_id);

  REQUIRE_FALSE(result.has_value());
  CHECK(result.error() == *fixture.babels.insert_error);
  CHECK(fixture.babels.find_count == 2);
  CHECK(fixture.babels.attach_count == 0);
}

}  // namespace
