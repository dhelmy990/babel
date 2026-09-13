import pytest
from playwright.sync_api import expect, sync_playwright


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def published_reader_page(live_server, article_factory, publisher):
    first = article_factory("First")
    second = article_factory("Second")
    from study.services.graph import add_edge
    add_edge(publisher, first.pk, second.pk)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        yield page, first, second
        browser.close()


def test_reader_can_open_article_and_babel_preview_does_not_block_navigation(live_server, published_reader_page):
    page, first, second = published_reader_page
    page.goto(live_server.url + "/second")
    expect(page.get_by_role("link", name="Edit article")).to_have_count(0)
    anchor = page.locator(".babel-anchor").filter(has_text="First")
    anchor.focus()
    expect(anchor.locator(".babel-preview")).to_be_visible()
    anchor.click()
    expect(page).to_have_url(live_server.url + "/first")
    page.go_back()
    expect(page).to_have_url(live_server.url + "/second")
