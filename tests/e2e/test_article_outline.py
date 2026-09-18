"""Reader navigation follows actual body headings without changing notes layout."""
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from study.services.content import publish_article


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def outlined_article(publisher):
    paragraphs = "\n\n".join(["A paragraph with enough explanation to make this a long reading section."] * 14)
    return publish_article(
        publisher, title="Navigable notes", color="#1a5276", submission_id=uuid4(), images={},
        markdown=f"# Navigable notes\n\n# Overview\n\n{paragraphs}\n\n## Details\n\n{paragraphs}"
        f"\n\n### Example\n\n{paragraphs}\n\n## Details\n\n{paragraphs}"
        f"\n\n## Details-2\n\n{paragraphs}\n\n### 日本語\n\n{paragraphs}"
        "\n\n## Sources\n\n[Reference](https://example.com)",
    )


def test_reader_outline_jumps_tracks_scroll_and_keeps_stable_unique_ids(outlined_article, anonymous_page, live_server):
    page = anonymous_page
    page.goto(live_server.url + "/navigable-notes")
    outline = page.get_by_role("navigation", name="On this page")
    links = outline.get_by_role("link")
    expect(links).to_have_text(["Overview", "Details", "Example", "Details", "Details-2", "日本語"])
    headings = page.locator(".article-body h1, .article-body h2, .article-body h3")
    ids = headings.evaluate_all("headings => headings.map(heading => heading.id)")
    assert all(ids) and len(set(ids)) == len(ids)
    assert links.evaluate_all("links => links.map(link => decodeURIComponent(link.hash.slice(1)))") == ids
    assert outline.bounding_box()["x"] < page.locator(".article-text").bounding_box()["x"]
    example = outline.get_by_role("link", name="Example", exact=True)
    example.focus()
    example.press("Enter")
    expect(page.locator(".article-body h3").first).to_be_in_viewport()
    expect(example).to_have_attribute("aria-current", "location")
    expect(outline).to_be_in_viewport()
    page.screenshot(path="/tmp/babel-article-outline-desktop.png")
    page.locator(".article-body h2").nth(2).evaluate("heading => heading.scrollIntoView()")
    expect(outline.get_by_role("link", name="Details-2", exact=True)).to_have_attribute("aria-current", "location")
    page.reload()
    assert headings.evaluate_all("headings => headings.map(heading => heading.id)") == ids
    outline.get_by_role("link", name="Example", exact=True).click()
    expect(page.locator(".article-body h3").first).to_be_in_viewport()
    page.goto(live_server.url + "/")
    page.goto(live_server.url + "/navigable-notes#" + ids[2])
    expect(page.locator(".article-body h3").first).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_mobile_outline_collapses_without_squeezing_article(outlined_article, anonymous_page, live_server):
    page = anonymous_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server.url + "/navigable-notes")
    toggle = page.get_by_role("button", name="On this page", exact=True)
    expect(toggle).to_have_attribute("aria-expanded", "false")
    outline = page.get_by_role("navigation", name="On this page")
    expect(outline.get_by_role("link", name="Example", exact=True)).to_be_hidden()
    toggle.click()
    page.screenshot(path="/tmp/babel-article-outline-mobile-expanded.png")
    outline.get_by_role("link", name="Example", exact=True).click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator(".article-body h3").first).to_be_in_viewport()
    page.screenshot(path="/tmp/babel-article-outline-mobile.png")
    assert page.locator(".article-text").bounding_box()["width"] >= 330
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.fixture
def plain_article(article_factory):
    return article_factory("Plain article")


def test_article_without_sections_has_no_empty_outline(plain_article, anonymous_page, live_server):
    page = anonymous_page
    page.goto(live_server.url + "/plain-article")
    expect(page.get_by_role("navigation", name="On this page")).to_be_hidden()
    expect(page.get_by_role("button", name="On this page", exact=True)).to_be_hidden()


def test_outline_becomes_compact_with_private_notes_open(outlined_article, publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/navigable-notes")
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.get_by_role("button", name="On this page", exact=True)).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_role("complementary", name="My notes")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    article = page.locator(".article-text").bounding_box()
    notes = page.get_by_role("complementary", name="My notes").bounding_box()
    assert article["x"] + article["width"] <= notes["x"]
