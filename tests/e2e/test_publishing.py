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
    page.get_by_label("Title", exact=True).fill("Ownership")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "ownership.md", "mimeType": "text/markdown",
        "buffer": b"# Ownership\n\nA clear lifetime.\n\n## Sources\n\n[The source](https://example.com/source)",
    })
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body]")).to_contain_text("A clear lifetime.")
    expect(page.locator("[data-preview-sources]")).to_contain_text("The source")
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
