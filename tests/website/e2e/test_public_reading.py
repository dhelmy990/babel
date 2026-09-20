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
        desktop = browser.new_page(viewport={"width": 1280, "height": 900})
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        mobile = mobile_context.new_page()
        yield desktop, mobile, first, second
        mobile_context.close()
        browser.close()


def test_reader_can_open_article_and_babel_preview_does_not_block_navigation(live_server, published_reader_page):
    desktop, mobile, first, second = published_reader_page
    desktop.goto(live_server.url + "/second")
    expect(desktop.get_by_role("link", name="Edit article")).to_have_count(0)
    anchor = desktop.locator(".babel-anchor").filter(has_text="First")
    anchor.hover()
    expect(anchor.locator(".babel-preview")).to_be_visible()
    anchor.focus()
    expect(anchor.locator(".babel-preview")).to_be_visible()
    mobile.goto(live_server.url + "/second")
    mobile.locator(".babel-anchor").filter(has_text="First").tap()
    expect(mobile).to_have_url(live_server.url + "/first")
    mobile.go_back()
    expect(mobile).to_have_url(live_server.url + "/second")
    mobile.go_forward()
    expect(mobile).to_have_url(live_server.url + "/first")
