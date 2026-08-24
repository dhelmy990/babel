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

struct ImportFixture {
  ImportFixture() {
    creators.creator = distributedSystemsCreator();
    source.article = RawWikipediaArticle{
        .page_id = page_id,
        .canonical_title = "Canonical title",
        .canonical_url = "https://en.wikipedia.org/wiki/Canonical_title",
        .revision_id = 21,
        .rendered_html = "<p>Raw</p>",
    };
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
  source.article = RawWikipediaArticle{
      .page_id = WikipediaPageId::fromInt(42).value(),
      .canonical_title = "Distributed computing",
      .canonical_url = "https://en.wikipedia.org/wiki/Distributed_computing",
      .revision_id = 1301234567,
      .rendered_html = "<p onclick='x()'>Safe</p>",
  };
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
  CHECK(babels.last_babel->title == source.article.canonical_title);
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
  CHECK(babels.last_source->canonical_url == source.article.canonical_url);
  CHECK(babels.last_source->source_revision_id == source.article.revision_id);
  CHECK_FALSE(babels.last_source->seed_assignment_id.has_value());
  CHECK(babels.last_source->declared_title == source.article.canonical_title);
  CHECK(babels.insert_count == 1);
  CHECK(source.fetch_count == 1);
  CHECK(ids.babel_count == 1);
  CHECK(sanitizer.last_canonical_url == source.article.canonical_url);
}

TEST_CASE("seed import stores its stable assignment and declared title atomically") {
  FakeCreatorRepository creators;
  creators.creator = distributedSystemsCreator();
  FakeArticleSource source;
  source.article = RawWikipediaArticle{
      .page_id = WikipediaPageId::fromInt(43).value(),
      .canonical_title = "Canonical title",
      .canonical_url = "https://en.wikipedia.org/wiki/Canonical_title",
      .revision_id = 17,
      .rendered_html = "<p>Body</p>",
  };
  FakeHtmlSanitizer sanitizer;
  FakeWikipediaBabelRepository babels;
  FakeIdGenerator ids;
  WikipediaImportService service(creators, babels, source, sanitizer, ids);
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:distributed-systems:Declared title").value(),
      .declared_title = "Declared title",
  };

  const auto result =
      service.importWikipediaBabel(creators.creator->id, source.article.page_id, context);

  REQUIRE(result.has_value());
  REQUIRE(babels.last_source.has_value());
  CHECK(babels.last_source->seed_assignment_id == context.assignment_id);
  CHECK(babels.last_source->declared_title == context.declared_title);
  CHECK(babels.attach_count == 0);
}

TEST_CASE("seed import attaches context to an existing owner page without refetching") {
  FakeCreatorRepository creators;
  creators.creator = distributedSystemsCreator();
  FakeArticleSource source;
  FakeHtmlSanitizer sanitizer;
  FakeWikipediaBabelRepository babels;
  FakeIdGenerator ids;
  const auto page_id = WikipediaPageId::fromInt(44).value();
  babels.stored_babel = Babel{
      .id = BabelId::v5("existing:babel").value(),
      .owner_id = creators.creator->id,
      .title = "Existing canonical title",
      .content_html = "<p>Existing</p>",
      .color = creators.creator->color,
      .content_revision = 1,
      .content_hash = std::string(64, 'a'),
  };
  const SeedImportContext context{
      .assignment_id = SeedAssignmentId::v5("seed:existing").value(),
      .declared_title = "Manifest title",
  };
  WikipediaImportService service(creators, babels, source, sanitizer, ids);

  const auto result = service.importWikipediaBabel(creators.creator->id, page_id, context);

  REQUIRE(result.has_value());
  CHECK(result->status == ImportWikipediaStatus::already_exists);
  CHECK(result->babel_id == babels.stored_babel->id);
  CHECK(result->canonical_title == babels.stored_babel->title);
  CHECK(babels.attach_count == 1);
  CHECK(babels.last_attached_babel == babels.stored_babel->id);
  CHECK(babels.last_assignment_id == context.assignment_id);
  CHECK(babels.last_declared_title == context.declared_title);
  CHECK(babels.insert_count == 0);
  CHECK(source.fetch_count == 0);
  CHECK(sanitizer.sanitize_count == 0);
  CHECK(ids.babel_count == 0);
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
  CHECK_FALSE(fixture.babels.stored_babel.has_value());
  CHECK_FALSE(fixture.babels.stored_source.has_value());
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
  CHECK_FALSE(fixture.babels.stored_babel.has_value());
  CHECK_FALSE(fixture.babels.stored_source.has_value());
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
  CHECK_FALSE(fixture.babels.stored_babel.has_value());
  CHECK_FALSE(fixture.babels.stored_source.has_value());
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
  fixture.babels.stored_babel = Babel{
      .id = BabelId::v5("attached:babel").value(),
      .owner_id = fixture.creators.creator->id,
      .title = "Canonical title",
      .content_html = "<p>Existing</p>",
      .color = fixture.creators.creator->color,
      .content_revision = 1,
      .content_hash = std::string(64, 'b'),
  };
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
  fixture.babels.stored_babel_after_failed_insert = concurrent;
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
