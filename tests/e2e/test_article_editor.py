"""Exercise the editor through actual browser input and durable article saves."""
import base64
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import PNG


pytestmark = pytest.mark.django_db(transaction=True)


def paste_image(editor, data=PNG, mime="image/png"):
    editor.evaluate("""(element, {data, mime}) => {
      const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
      const clipboardData = new DataTransfer();
      clipboardData.items.add(new File([bytes], 'clipboard.png', {type: mime}));
      element.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
    }""", {"data": base64.b64encode(data).decode(), "mime": mime})


def test_edit_existing_body_and_retain_image(editable_owner_page, live_server):
    page, article = editable_owner_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{live_server.url}/publish/{article.pk}")
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    expect(editor).to_contain_text("Original body.")
    expect(editor.locator("img[data-image-path]")).to_have_attribute("src", re.compile("/assets/"))
    editor.get_by_text("Original body.", exact=True).click()
    expect(editor).to_be_focused()
    editor.press("End", delay=50)
    editor.press("Enter")
    editor.press_sequentially("An explanation added directly.")
    expect(editor.locator("p").filter(has_text="An explanation added directly.")).to_be_visible()
    assert errors == []
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/editable")
    expect(page.locator(".article-body")).to_contain_text("An explanation added directly.")
    expect(page.locator(".article-body img")).to_have_attribute("src", re.compile("/assets/"))
    assert errors == []


def test_pasted_images_preview_save_and_reopen(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Pasted illustrations")
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    editor.fill("Before the illustrations.")
    page.keyboard.press("End", delay=50)
    paste_image(editor)
    paste_image(editor)
    expect(editor.locator("img[data-image-path]")).to_have_count(2)
    for image in editor.locator("img[data-image-path]").all():
        expect(image).to_have_attribute("src", re.compile("blob:"))
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body] img")).to_have_count(2)
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/pasted-illustrations")
    expect(page.locator(".article-body img")).to_have_count(2)
    for image in page.locator(".article-body img").all():
        expect(image).to_have_attribute("src", re.compile("/assets/"))
        assert image.evaluate("image => image.naturalWidth") == 1
    page.get_by_role("button", name="Admin mode", exact=True).click()
    page.get_by_role("link", name="Edit article", exact=True).click()
    expect(page.get_by_role("textbox", name="Article body", exact=True).locator("img[data-image-path]")).to_have_count(2)


def test_source_mode_preserves_tables_code_and_sources(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Source round trip")
    page.get_by_role("button", name="Markdown", exact=True).click()
    source = page.get_by_role("textbox", name="Markdown source", exact=True)
    source.fill("## Details\n\n| Name | Value |\n| --- | --- |\n| one | two |\n\n```python\nprint('hello')\n```\n\n## Sources\n\n[Reference](https://example.com/reference)")
    page.get_by_role("button", name="Write", exact=True).click()
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    expect(editor.locator("table")).to_contain_text("two")
    editor.click()
    page.keyboard.press("Control+Home", delay=50)
    page.keyboard.type("Updated ")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body] table")).to_contain_text("two")
    expect(page.locator("[data-preview-body] pre")).to_contain_text("print('hello')")
    expect(page.locator("[data-preview-sources]")).to_contain_text("Reference")


def test_failed_save_keeps_text_and_clipboard_file_for_retry(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Retained draft")
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    editor.fill("My unsaved paragraph.")
    page.keyboard.press("End", delay=50)
    paste_image(editor)
    page.route("**/api/articles", lambda route: route.abort())
    page.get_by_role("button", name="Publish", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("could not")
    expect(editor).to_contain_text("My unsaved paragraph.")
    expect(editor.locator("img[data-image-path]")).to_have_count(1)
    page.unroute("**/api/articles")
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/retained-draft")
    expect(page.locator(".article-body img")).to_have_attribute("src", re.compile("/assets/"))


def test_formatting_and_edits_after_import_win(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Edited import")
    page.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "article.md", "mimeType": "text/markdown", "buffer": b"Original imported text.",
    })
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    expect(editor).to_contain_text("Original imported text.")
    editor.fill("Replacement text")
    page.keyboard.press("Control+a")
    page.get_by_role("button", name="Bold", exact=True).click()
    expect(editor.locator("strong")).to_have_text("Replacement text")
    page.get_by_role("button", name="Publish", exact=True).click()
    page.wait_for_url("**/edited-import")
    expect(page.locator(".article-body strong")).to_have_text("Replacement text")
    expect(page.locator(".article-body")).not_to_contain_text("Original imported text.")


def test_unsupported_paste_leaves_document_untouched(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    editor.fill("Keep this text.")
    paste_image(editor, b"<svg></svg>", "image/svg+xml")
    expect(page.get_by_role("status")).to_contain_text("PNG, JPEG, or WebP")
    expect(editor).to_contain_text("Keep this text.")
    expect(editor.locator("img")).to_have_count(0)


@pytest.mark.parametrize("label, expected_alt", [
    (r"A \[caption\]", "A [caption]"),
    (r"A \*literal\* word", "A *literal* word"),
    ("A *formatted* word", "A formatted word"),
    ("A &amp;amp; B", "A &amp; B"),
])
def test_visual_edit_preserves_image_paths_with_spaces(publisher_page, live_server, label, expected_alt):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Spaced image path")
    page.get_by_role("button", name="Markdown", exact=True).click()
    page.get_by_role("textbox", name="Markdown source", exact=True).fill(
        f'Before ![{label}](<images/my photo.png> "An image") after.'
    )
    page.get_by_label("Image files", exact=True).set_input_files({
        "name": "my photo.png", "mimeType": "image/png", "buffer": PNG,
    })
    page.get_by_role("button", name="Write", exact=True).click()
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    expect(editor.locator("img[data-image-path]")).to_have_attribute("src", re.compile("blob:"))
    editor.click()
    page.keyboard.press("Control+End", delay=50)
    page.keyboard.type(" Still editable.")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body] img")).to_have_attribute("alt", expected_alt)
    expect(page.locator("[data-preview-body]")).to_contain_text("Still editable.")


def test_mode_switch_and_metadata_save_preserve_exact_markdown(editable_owner_page, live_server):
    page, article = editable_owner_page
    page.goto(f"{live_server.url}/publish/{article.pk}")
    page.get_by_role("button", name="Markdown", exact=True).click()
    original = page.get_by_role("textbox", name="Markdown source", exact=True).input_value()
    page.get_by_role("button", name="Write", exact=True).click()
    page.get_by_label("Title", exact=True).fill("New title only")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/editable")
    page.get_by_role("link", name="Edit article", exact=True).click()
    page.get_by_role("button", name="Markdown", exact=True).click()
    assert page.get_by_role("textbox", name="Markdown source", exact=True).input_value() == original


def test_switching_modes_does_not_steal_focus_from_title(publisher_page, live_server):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_role("button", name="Markdown", exact=True).click()
    # Simulate moving straight from the mode switch to another field, before
    # the next animation frame. A delayed editor focus must not take it back.
    assert page.evaluate("""async () => {
      document.querySelector('[data-editor-mode="write"]').click();
      const title = document.querySelector('input[name="title"]');
      title.focus();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return document.activeElement === title;
    }""")


def test_editor_layout_on_mobile_and_desktop(publisher_page, live_server):
    page = publisher_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Notes on paying attention")
    page.get_by_role("button", name="Markdown", exact=True).click()
    page.get_by_role("textbox", name="Markdown source", exact=True).fill(
        "## A place for unfinished thoughts\n\nThe best notes change as our understanding grows. "
        "Return to a sentence, **make it clearer**, and leave a better explanation for next time.\n\n"
        "> Writing is a way of noticing what you think.\n\n"
        "- Keep the useful details\n- Make room for a new connection"
    )
    page.get_by_role("button", name="Write", exact=True).click()
    for width, name in [(1280, "desktop"), (390, "mobile")]:
        page.set_viewport_size({"width": width, "height": 1000})
        page.screenshot(path=f"/tmp/babel-article-editor-{name}.png", full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.get_by_role("button", name="Bold", exact=True)).to_be_visible()
    assert errors == []


@pytest.mark.parametrize("markdown, expected", [
    ("Before <strong>literal tag</strong> after.\n\n<!-- literal comment -->", "<strong>literal tag</strong>"),
    ("- [x] task complete\n- [ ] remaining", "task complete"),
    ("[A link](<https://example.com/a b>)", "A link"),
])
def test_visual_edits_preserve_markdown_content(publisher_page, live_server, markdown, expected):
    page = publisher_page
    page.goto(live_server.url + "/publish")
    page.get_by_label("Title", exact=True).fill("Compatible content")
    page.get_by_role("button", name="Markdown", exact=True).click()
    page.get_by_role("textbox", name="Markdown source", exact=True).fill(markdown)
    page.get_by_role("button", name="Write", exact=True).click()
    editor = page.get_by_role("textbox", name="Article body", exact=True)
    editor.click()
    page.keyboard.press("Control+End", delay=50)
    page.keyboard.type(" Added.")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("[data-preview-body]")).to_contain_text(expected)
    if "<!--" in markdown:
        expect(page.locator("[data-preview-body]")).to_contain_text("<!-- literal comment -->")
    if "[x]" in markdown:
        expect(page.locator("[data-preview-body]")).to_contain_text("[x]")
        expect(page.locator("[data-preview-body]")).to_contain_text("[ ]")
    if "https://" in markdown:
        expect(page.locator("[data-preview-body] a")).to_have_attribute("href", "https://example.com/a%20b")
