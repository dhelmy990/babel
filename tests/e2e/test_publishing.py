import re

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.django_db(transaction=True)


def test_owner_publishes_previewed_markdown_and_returns_to_real_article(publisher_page, live_server):
    page = publisher_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_server.url + "/galaxy")
    page.get_by_role("button", name="Admin mode", exact=True).click()
    expect(page.get_by_role("link", name="New article", exact=True)).to_be_visible()
    page.get_by_role("link", name="New article", exact=True).click()
    page.screenshot(path="/tmp/babel-p5-owner-form-desktop.png", full_page=True)
    expect(page.locator("[data-preview-sources]")).to_be_hidden()
    page.get_by_label("Title", exact=True).fill("Ownership")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "source-free.md", "mimeType": "text/markdown",
        "buffer": b"# Ownership\n\nA clear lifetime.",
    })
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body]")).to_contain_text("A clear lifetime.")
    expect(page.locator("[data-preview-sources]")).to_be_hidden()
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "ownership.md", "mimeType": "text/markdown",
        "buffer": b"# Ownership\n\nA clear lifetime.\n\n## Sources\n\n[The source](https://example.com/source)",
    })
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-sources]")).to_contain_text("The source")
    expect(page.locator("[data-preview-sources]")).to_be_visible()
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/ownership")
    expect(page.get_by_role("heading", name="Ownership", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="The source", exact=True)).to_be_visible()
    page.screenshot(path="/tmp/babel-p5-article-desktop.png", full_page=True)
    page.goto(live_server.url + "/")
    page.screenshot(path="/tmp/babel-p5-home-desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path="/tmp/babel-p5-home-mobile.png", full_page=True)
    page.goto(live_server.url + "/ownership")
    page.screenshot(path="/tmp/babel-p5-article-mobile.png", full_page=True)
    page.goto(live_server.url + "/publish")
    page.screenshot(path="/tmp/babel-p5-owner-form-mobile.png", full_page=True)
    assert errors == []


def test_owner_form_keeps_text_and_files_when_save_fails(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Retry article")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "retry.md", "mimeType": "text/markdown", "buffer": b"# Retry article\n\nStill here.",
    })
    page.route("**/api/articles", lambda route: route.abort())
    page.get_by_role("button", name="Publish", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("could not")
    assert page.get_by_label("Title", exact=True).input_value() == "Retry article"
    assert page.get_by_label("Markdown file", exact=True).evaluate("input => input.files.length") == 1


def test_owner_uploads_an_image_at_an_editable_logical_path(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Diagram")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "diagram.md", "mimeType": "text/markdown", "buffer": b"# Diagram\n\n![A diagram](images/diagram.png)",
    })
    page.get_by_label("Image files", exact=True).set_input_files({
        "name": "diagram.png", "mimeType": "image/png",
        "buffer": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82",
    })
    page.get_by_label("Logical path for diagram.png", exact=True).fill("images/diagram.png")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body] img")).to_have_attribute("src", re.compile(r"data:image/png;base64,"))
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/diagram")
    expect(page.locator(".article-body img")).to_have_attribute("src", re.compile(r"/assets/"))
    assert page.locator(".article-body img").evaluate("image => image.naturalWidth") == 1


def test_owner_archives_article_and_anonymous_reading_is_removed(published_owner_page, live_server):
    page, article = published_owner_page
    page.goto(live_server.url + "/archive-me")
    page.get_by_role("button", name="Archive", exact=True).click()
    page.wait_for_url(live_server.url + "/")
    anonymous = page.context.browser.new_context()
    try:
        assert anonymous.request.get(live_server.url + "/archive-me").status == 404
    finally:
        anonymous.close()


def test_request_json_normalizes_a_response_body_disconnect(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/")
    result = page.evaluate("""async () => {
        const originalFetch = window.fetch;
        window.fetch = async () => ({ok: true, status: 200, text: () => Promise.reject(new TypeError('body lost'))});
        try {
          const {requestJSON} = await import('/static/study/site.js?body-stream-test');
          await requestJSON('/lost-response');
          return null;
        } catch (error) {
          return {name: error.name, status: error.status, code: error.code, body: error.body?.message};
        } finally {
          window.fetch = originalFetch;
        }
    }""")
    assert result == {"name": "RequestError", "status": 200, "code": "response_body_error", "body": "body lost"}


def test_publish_form_denies_anonymous(anonymous_page, live_server):
    assert anonymous_page.goto(live_server.url + "/publish").status == 403


def test_publish_form_denies_normal_reader(other_reader_page, live_server):
    assert other_reader_page.goto(live_server.url + "/publish").status == 403


def test_owner_edits_metadata_while_retaining_existing_markdown_and_image(editable_owner_page, live_server):
    page, article = editable_owner_page
    page.goto(live_server.url + "/editable")
    original_image = page.locator(".article-body img")
    original_url = original_image.get_attribute("src")
    original_date = page.locator(".article-date").inner_text()
    page.goto(f"{live_server.url}/publish/{article.pk}")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body] img")).to_have_attribute("src", re.compile(r"data:image/png;base64,"))
    page.get_by_label("Title", exact=True).fill("Edited title")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/editable")
    expect(page.get_by_role("heading", name="Edited title", exact=True)).to_be_visible()
    retained_image = page.locator(".article-body img")
    assert retained_image.get_attribute("src") == original_url
    assert retained_image.evaluate("image => image.naturalWidth") == 1
    assert page.locator(".article-date").inner_text() == original_date
    expect(page.locator(".article-body")).to_contain_text("Original body.")


def test_stale_edit_keeps_typed_title_and_selected_files(editable_owner_page, live_server):
    page, article = editable_owner_page
    page.goto(f"{live_server.url}/publish/{article.pk}")
    newer_context = page.context.browser.new_context()
    newer_context.add_cookies(page.context.cookies())
    newer = newer_context.new_page()
    newer.goto(f"{live_server.url}/publish/{article.pk}")
    newer.get_by_label("Title", exact=True).fill("Newer server content")
    newer.get_by_role("button", name="Save", exact=True).click()
    newer.wait_for_url("**/editable")
    page.get_by_label("Title", exact=True).fill("My conflicting edit")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "edit.md", "mimeType": "text/markdown", "buffer": b"# My conflicting edit\n\nKept.",
    })
    page.get_by_label("Image files", exact=True).set_input_files({
        "name": "new.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82",
    })
    page.get_by_label("Logical path for new.png", exact=True).fill("images/new.png")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("changed elsewhere")
    assert page.get_by_label("Title", exact=True).input_value() == "My conflicting edit"
    assert page.get_by_label("Markdown file", exact=True).evaluate("input => input.files.length") == 1
    assert page.get_by_label("Image files", exact=True).evaluate("input => input.files.length") == 1
    newer.reload()
    expect(newer.get_by_role("heading", name="Newer server content", exact=True)).to_be_visible()
    expect(newer.locator(".article-body")).to_contain_text("Original body.")
    newer_context.close()


def test_lost_publish_response_retries_the_same_submission_without_duplicate(retry_publish_page, live_server):
    page = retry_publish_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Retry on lost response")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "retry.md", "mimeType": "text/markdown", "buffer": b"# Retry on lost response\n\nOnly once.",
    })

    def lose_response(route):
        route.fetch()
        route.abort()

    page.route("**/api/articles", lose_response)
    page.get_by_role("button", name="Publish", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("could not be saved")
    page.unroute("**/api/articles")
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/retry-on-lost-response")


def test_reference_layout_screenshots_have_no_mobile_overflow(visual_owner_page, live_server):
    desktop, mobile, article = visual_owner_page
    desktop.goto(live_server.url + "/")
    desktop.screenshot(path="/tmp/babel-p5-followup-home-desktop.png", full_page=True)
    desktop.goto(live_server.url + "/foundations")
    desktop.screenshot(path="/tmp/babel-p5-followup-article-desktop.png", full_page=True)
    desktop.goto(live_server.url + f"/publish/{article.pk}")
    desktop.screenshot(path="/tmp/babel-p5-followup-form-desktop.png", full_page=True)
    for path, screenshot in [
        ("/", "/tmp/babel-p5-followup-home-mobile.png"),
        ("/foundations", "/tmp/babel-p5-followup-article-mobile.png"),
        (f"/publish/{article.pk}", "/tmp/babel-p5-followup-form-mobile.png"),
    ]:
        mobile.goto(live_server.url + path)
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        mobile.screenshot(path=screenshot, full_page=True)
