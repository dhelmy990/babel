"""Private notes exercised through the real Django server and Chromium."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.django_db(transaction=True)


def open_notes(env):
    page = env.page
    page.goto(f"{env.url}/{env.article.slug}")
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.first.text)
    return page


def card(page, note_id):
    return page.locator(f'[data-notes-list] [data-note-id="{note_id}"]')


def api(page, url, method="GET", body=None):
    return page.evaluate("""async ({url, method, body}) => {
      const {requestJSON} = await import('/static/study/site.js');
      return requestJSON(url, {method, ...(body === null ? {} : {body})});
    }""", {"url": url, "method": method, "body": body})


def saved(item):
    expect(item.get_by_role("status")).to_have_text("Saved")


@pytest.mark.parametrize("kind", ["sticky note", "text box"])
def test_notes_survive_reload_and_remain_plain_text(notes_browser, kind):
    env = notes_browser
    page = open_notes(env)
    page.get_by_role("button", name=f"New {kind}", exact=True).click()
    item = page.locator("[data-notes-list] [data-note-id]").last
    text = '<img src=x onerror="alert(1)"> A private thought.'
    item.get_by_label("Note text", exact=True).fill(text)
    item.get_by_role("button", name="Save note", exact=True).click()
    saved(item)
    page.reload()
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(text)
    expect(page.locator("[data-notes-list] img")).to_have_count(0)


def test_pointer_coordinates_are_scrolled_grip_relative_and_saved_once(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    copy = page.locator(f'[data-note-copy="{env.first.pk}"]')
    copy.scroll_into_view_if_needed()
    grip = copy.get_by_role("button", name="Move note", exact=True)
    before = copy.bounding_box()
    box = grip.bounding_box()
    writes = []
    page.on("request", lambda req: writes.append(req.post_data_json) if req.method == "PATCH" else None)
    page.mouse.move(box["x"] + 19, box["y"] + 11)
    page.mouse.down()
    page.mouse.move(box["x"] + 79, box["y"] + 51, steps=4)
    during = copy.bounding_box()
    assert during["x"] == pytest.approx(before["x"] + 60, abs=1)
    assert during["y"] == pytest.approx(before["y"] + 40, abs=1)
    assert writes == []
    page.mouse.up()
    saved(item)
    assert len(writes) == 1
    assert writes[0]["x"] == pytest.approx(100)
    assert writes[0]["y"] == pytest.approx(1640)
    page.reload()
    expect(copy).to_have_css("left", "100px")
    expect(copy).to_have_css("top", "1640px")
    grip.focus()
    grip.press("ArrowRight")
    grip.press("Shift+ArrowDown")
    page.get_by_role("button", name="My notes", exact=True).click()
    saved(item)
    result = api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]
    assert (result["x"], result["y"]) == (110, 1641)
    grip.scroll_into_view_if_needed()
    box = grip.bounding_box()
    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    grip.dispatch_event("pointercancel", {"pointerId": 1})
    page.mouse.up()
    expect(copy).to_have_css("left", "110px")
    expect(copy).to_have_css("top", "1641px")
    assert len(writes) == 3


def test_return_to_sidebar_and_recover_after_real_article_shortening(notes_browser):
    env = notes_browser
    page = open_notes(env)
    owner_context = env.browser.new_context()
    owner_context.add_cookies([env.cookies["owner"]])
    owner = owner_context.new_page()
    owner.goto(f"{env.url}/publish/{env.article.pk}")
    owner.get_by_label("Markdown file", exact=True).set_input_files({
        "name": "short.md", "mimeType": "text/markdown", "buffer": b"# Private reading\n\nShortened author body.",
    })
    owner.get_by_role("button", name="Save", exact=True).click()
    owner.wait_for_url("**/private-reading")
    page.reload()
    page.get_by_role("button", name="My notes", exact=True).click()
    item = card(page, env.first.pk)
    expect(item).to_contain_text(env.first.text)
    copy = page.locator(f'[data-note-copy="{env.first.pk}"]')
    expect(copy).to_be_hidden()
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]["y"] == 1600
    item.get_by_role("button", name="Place here", exact=True).click()
    saved(item)
    expect(copy).to_be_visible()
    item.get_by_role("button", name="Return to sidebar", exact=True).click()
    saved(item)
    expect(copy).to_be_hidden()
    note = api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]
    assert note["x"] is None and note["y"] is None and note["text"] == env.first.text
    owner_context.close()


def test_network_failure_retains_current_tab_input_and_exact_create_retry(notes_browser):
    env = notes_browser
    page = open_notes(env)
    page.get_by_role("button", name="New text box", exact=True).click()
    item = page.locator("[data-notes-list] [data-note-id]").last
    item.get_by_label("Note text", exact=True).fill("Original attempted creation")
    payloads = []

    def lose_response(route):
        payloads.append(route.request.post_data_json)
        route.fetch()
        route.abort()

    endpoint = f"**/api/articles/{env.article.pk}/notes"
    page.route(endpoint, lambda route: lose_response(route) if route.request.method == "POST" else route.continue_())
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("could not")
    item.get_by_label("Note text", exact=True).fill("Newer typing after failure")
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("Newer typing after failure")
    page.unroute(endpoint)
    page.on("request", lambda req: payloads.append(req.post_data_json) if req.method == "POST" and req.url.endswith("/notes") else None)
    item.get_by_role("button", name="Retry", exact=True).click()
    saved(item)
    assert payloads[0] == payloads[1]
    notes = api(page, f"/api/articles/{env.article.pk}/notes")["notes"]
    assert len(notes) == 2
    assert notes[1]["text"] == "Newer typing after failure"


def test_conflict_shows_server_copy_without_losing_local_text(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("My unsaved conflicting text")
    api(page, f"/api/notes/{env.first.pk}", "PATCH", {"version": 1, "text": "New server text", "x": 40, "y": 1600})
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("changed elsewhere")
    expect(item.locator("[data-server-copy]")).to_contain_text("New server text")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("My unsaved conflicting text")
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]["text"] == "New server text"
    item.get_by_role("button", name="Retry", exact=True).click()
    saved(item)
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]["text"] == "My unsaved conflicting text"


def test_pagehide_clears_private_state_and_pageshow_refetches_new_reader(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("A private unsaved draft")
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    expect(page.locator("[data-notes-list]")).to_be_empty()
    expect(page.locator("[data-note-copy]")).to_have_count(0)
    expect(page.get_by_label("Note text", exact=True)).to_have_count(0)
    page.context.add_cookies([env.cookies["other"]])
    held = []
    page.route(f"**/api/articles/{env.article.pk}/notes", lambda route: held.append(route))
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator("[data-notes-status]")).to_contain_text("Loading")
    page.wait_for_timeout(150)
    assert held
    assert "Reader one's private note" not in page.content()
    held.pop().continue_()
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.second.text)
    expect(page.locator("[data-notes-list]")).not_to_contain_text(env.first.text)
    page.unroute(f"**/api/articles/{env.article.pk}/notes")
    page.goto(env.url + "/")
    page.context.add_cookies([env.cookies["reader"]])
    page.go_back()
    print(page.evaluate("""async () => ({state: document.readyState, scripts: [...document.scripts].map(s => s.src),
      toggle: document.querySelector('[data-notes-toggle]')?.outerHTML,
      session: await (await fetch('/api/session')).text(), notesScript: (await fetch('/static/study/notes.js')).status})"""))
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.first.text)
    expect(page.locator("[data-notes-list]")).not_to_contain_text(env.second.text)


def test_mobile_drawer_keyboard_focus_and_explicit_placement(notes_browser):
    env = notes_browser
    context = env.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    context.add_cookies([env.cookies["reader"]])
    page = env.page = context.new_page()
    open_notes(env)
    drawer = page.locator("[data-notes-sidebar]")
    expect(drawer).to_be_visible()
    expect(card(page, env.first.pk).locator(".note-location")).to_have_text("In sidebar on this screen")
    page.get_by_role("button", name="New text box", exact=True).tap()
    mobile_note = page.locator("[data-notes-list] [data-note-id]").last
    mobile_note.get_by_label("Note text", exact=True).tap()
    mobile_note.get_by_label("Note text", exact=True).fill("A touch-edited note")
    mobile_note.get_by_role("button", name="Save note", exact=True).tap()
    saved(mobile_note)
    page.screenshot(path="/tmp/babel-n2-notes-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.locator(f'[data-note-copy="{env.first.pk}"]')).to_be_hidden()
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(page.get_by_role("button", name="My notes", exact=True)).to_be_focused()
    expect(page.get_by_role("button", name="My notes", exact=True)).to_have_attribute("aria-expanded", "false")
    page.get_by_role("button", name="My notes", exact=True).click()
    card(page, env.first.pk).get_by_role("button", name="Place here", exact=True).click()
    expect(drawer).to_be_hidden()
    expect(page.locator(f'[data-note-copy="{env.first.pk}"]')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_archived_article_keeps_reader_notes_and_hides_frozen_owner_actions(notes_browser):
    env = notes_browser
    page = open_notes(env)
    owner_context = env.browser.new_context()
    owner_context.add_cookies([env.cookies["owner"]])
    owner = owner_context.new_page()
    owner.goto(f"{env.url}/{env.article.slug}")
    api(owner, f"/api/articles/{env.article.pk}/archive", "POST")
    owner.reload()
    expect(owner.get_by_text("Archived", exact=True)).to_be_visible()
    expect(owner.get_by_role("link", name="Edit article", exact=True)).to_have_count(0)
    expect(owner.get_by_role("button", name="Archive", exact=True)).to_have_count(0)
    page.reload()
    page.get_by_role("button", name="My notes", exact=True).click()
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("Still my archived note")
    item.get_by_role("button", name="Save note", exact=True).click()
    saved(item)
    owner_context.close()


def test_delayed_ack_preserves_newer_unsubmitted_typing_and_serializes_saves(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("First submitted edit")
    held = []
    endpoint = f"**/api/notes/{env.first.pk}"
    page.route(endpoint, lambda route: held.append(route))
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_have_text("Saving…")
    item.get_by_label("Note text", exact=True).fill("Newer unsubmitted typing")
    assert len(held) == 1
    first = held.pop()
    first.fulfill(response=first.fetch())
    expect(item.get_by_role("status")).to_have_text("Not saved")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("Newer unsubmitted typing")
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_have_text("Saving…")
    item.get_by_label("Note text", exact=True).fill("Last queued edit")
    item.get_by_role("button", name="Save note", exact=True).click()
    assert len(held) == 1
    second = held.pop()
    assert second.request.post_data_json["version"] == 2
    second.fulfill(response=second.fetch())
    page.wait_for_timeout(100)
    assert len(held) == 1
    third = held.pop()
    assert third.request.post_data_json["version"] == 3
    assert third.request.post_data_json["text"] == "Last queued edit"
    third.fulfill(response=third.fetch())
    saved(item)


def test_csrf_failure_preserves_draft_and_delete_retries_lost_reply(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("Preserved through CSRF failure")
    endpoint = f"**/api/notes/{env.first.pk}"
    page.route(endpoint, lambda route: route.fulfill(status=403, json={"error": {"code": "csrf_failed", "message": "CSRF validation failed."}}))
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("could not")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("Preserved through CSRF failure")
    page.unroute(endpoint)
    item.get_by_role("button", name="Retry", exact=True).click()
    saved(item)

    def lose_delete(route):
        route.fetch()
        route.abort()

    page.route(endpoint, lose_delete)
    item.get_by_role("button", name="Delete", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("could not")
    page.unroute(endpoint)
    item.get_by_role("button", name="Retry", exact=True).click()
    expect(item).to_have_count(0)
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"] == []


def test_late_previous_reader_response_cannot_repopulate_after_pagehide(notes_browser):
    env = notes_browser
    page = open_notes(env)
    # Hold an already fetched response in JavaScript to exercise generation guards
    # even when a response arrives despite AbortController cancellation.
    page.evaluate("""() => {
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      const original = window.fetch;
      let once = true;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (once && String(args[0]).endsWith('/notes')) {
          once = false;
          await new Promise(resolve => { window.releaseOldNotes = resolve; });
        }
        return response;
      };
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    }""")
    page.wait_for_function("typeof window.releaseOldNotes === 'function'")
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    page.context.add_cookies([env.cookies["other"]])
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.second.text)
    page.evaluate("window.releaseOldNotes()")
    page.wait_for_timeout(100)
    expect(page.locator("[data-notes-list]")).not_to_contain_text(env.first.text)
    expect(page.locator("[data-notes-list]")).to_contain_text(env.second.text)


def test_mid_article_edit_and_recovery_preserve_visible_reading_position(notes_browser):
    env = notes_browser
    page = open_notes(env)
    page.screenshot(path="/tmp/babel-n2-notes-desktop-top.png")
    page.get_by_role("button", name="Close notes", exact=True).click()
    copy = page.locator(f'[data-note-copy="{env.first.pk}"]')
    copy.scroll_into_view_if_needed()
    before = page.evaluate("scrollY")
    copy.get_by_role("button", name="Edit note", exact=True).click()
    assert page.evaluate("scrollY") == pytest.approx(before, abs=2)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Place here", exact=True).click()
    position = copy.bounding_box()
    assert 0 <= position["y"] < 100
    assert position["y"] + position["height"] <= page.viewport_size["height"]
    saved(item)
    page.screenshot(path="/tmp/babel-n2-notes-desktop.png")


def test_large_sidebar_inventory_does_not_lengthen_article(notes_browser):
    env = notes_browser
    page = open_notes(env)
    before = page.evaluate("document.documentElement.scrollHeight")
    for i in range(8):
        page.get_by_role("button", name="New text box", exact=True).click()
        page.locator("[data-notes-list] [data-note-id]").last.get_by_label("Note text", exact=True).fill(f"Draft {i}")
    assert page.evaluate("document.documentElement.scrollHeight") == before
    sidebar_box = page.locator("[data-notes-sidebar]").bounding_box()
    assert sidebar_box["y"] >= 0
    assert sidebar_box["y"] + sidebar_box["height"] <= page.viewport_size["height"]


def test_anonymous_notes_affordance_and_failed_load_retry(notes_browser):
    env = notes_browser
    context = env.browser.new_context()
    anonymous = context.new_page()
    anonymous.goto(f"{env.url}/{env.article.slug}")
    expect(anonymous.get_by_role("link", name="Sign in with Google to keep private notes", exact=True)).to_be_visible()
    expect(anonymous.get_by_role("button", name="My notes", exact=True)).to_have_count(0)
    expect(anonymous.locator("[data-notes-list]")).to_be_empty()
    context.close()
    page = env.page
    endpoint = f"**/api/articles/{env.article.pk}/notes"
    page.route(endpoint, lambda route: route.abort())
    page.goto(f"{env.url}/{env.article.slug}")
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-status]")).to_contain_text("could not be loaded")
    expect(page.locator("[data-notes-list]")).to_be_empty()
    page.unroute(endpoint)
    page.get_by_role("button", name="Retry loading notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.first.text)


def test_pagehide_cancels_queued_writes_and_ignores_late_acknowledgement(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (args[1]?.method === 'PATCH') {
          await new Promise(resolve => { window.releaseOldSave = resolve; });
        }
        return response;
      };
    }""")
    writes = []
    page.on("request", lambda req: writes.append(req) if req.method == "PATCH" else None)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("Old reader pending write")
    item.get_by_role("button", name="Save note", exact=True).click()
    page.wait_for_function("typeof window.releaseOldSave === 'function'")
    item.get_by_label("Note text", exact=True).fill("Old reader queued write")
    item.get_by_role("button", name="Save note", exact=True).click()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    expect(page.locator("[data-notes-list]")).to_be_empty()
    page.context.add_cookies([env.cookies["other"]])
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.second.text)
    page.evaluate("window.releaseOldSave()")
    page.wait_for_timeout(100)
    assert len(writes) == 1
    assert "Old reader" not in page.locator("[data-notes-list]").inner_text()
    expect(page.locator("[data-notes-list]")).to_contain_text(env.second.text)


def test_editor_closes_only_after_its_current_draft_is_confirmed(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("A confirmed compact card")
    item.get_by_role("button", name="Save note", exact=True).click()
    saved(item)
    expect(item.get_by_label("Note text", exact=True)).to_be_hidden()
    expect(item.locator(".note-text")).to_have_text("A confirmed compact card")
    item.get_by_role("button", name="Edit", exact=True).click()
    expect(item.get_by_label("Note text", exact=True)).to_have_value("A confirmed compact card")


def test_delete_can_discard_an_edit_after_save_failure(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("An edit I chose to discard")
    endpoint = f"**/api/notes/{env.first.pk}"
    page.route(endpoint, lambda route: route.abort() if route.request.method == "PATCH" else route.continue_())
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("could not")
    item.get_by_role("button", name="Delete", exact=True).click()
    expect(item).to_have_count(0)
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"] == []


@pytest.mark.parametrize("retry_refresh", [False, True])
def test_logout_during_conflict_refresh_clears_all_private_state(notes_browser, retry_refresh):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("A private conflicting draft")
    api(page, f"/api/notes/{env.first.pk}", "PATCH", {
        "version": 1, "text": "A newer server copy", "x": 40, "y": 1600,
    })
    endpoint = f"**/api/articles/{env.article.pk}/notes"
    held = []
    if retry_refresh:
        page.route(endpoint, lambda route: route.abort())
        item.get_by_role("button", name="Save note", exact=True).click()
        expect(item.get_by_role("status")).to_contain_text("server copy could not be loaded")
        expect(item.get_by_label("Note text", exact=True)).to_have_value("A private conflicting draft")
        page.unroute(endpoint)
    page.route(endpoint, lambda route: held.append(route))
    with page.expect_request(lambda req: req.method == "GET" and req.url.endswith(f"/api/articles/{env.article.pk}/notes")):
        item.get_by_role("button", name="Retry" if retry_refresh else "Save note", exact=True).click()
    # A real logout invalidates the held request's session without navigating this
    # tab, so only the conflict-refresh 401 can clear its private DOM.
    api(page, "/accounts/logout/", "POST")
    assert api(page, "/api/session")["authenticated"] is False
    assert len(held) == 1
    held.pop().continue_()
    expect(page.locator("[data-notes-list]")).to_be_empty()
    expect(page.locator("[data-note-copy]")).to_have_count(0)
    expect(page.get_by_label("Note text", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="Sign in with Google to keep private notes", exact=True)).to_be_visible()


def test_csrf_failure_during_conflict_refresh_retains_draft_for_comparison(notes_browser):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("A draft retained after forbidden refresh")
    api(page, f"/api/notes/{env.first.pk}", "PATCH", {
        "version": 1, "text": "Newer server copy", "x": 40, "y": 1600,
    })
    endpoint = f"**/api/articles/{env.article.pk}/notes"
    page.route(endpoint, lambda route: route.fulfill(status=403, json={
        "error": {"code": "csrf_failed", "message": "CSRF validation failed."},
    }))
    item.get_by_role("button", name="Save note", exact=True).click()
    expect(item.get_by_role("status")).to_contain_text("server copy could not be loaded")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("A draft retained after forbidden refresh")
    page.unroute(endpoint)
    item.get_by_role("button", name="Retry", exact=True).click()
    expect(item.locator("[data-server-copy]")).to_contain_text("Newer server copy")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("A draft retained after forbidden refresh")


@pytest.mark.parametrize("new_text", [None, "Non-drag unsaved text"])
def test_pointer_cancel_uses_confirmation_received_after_drag_started(notes_browser, new_text):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    copy = page.locator(f'[data-note-copy="{env.first.pk}"]')
    grip = copy.get_by_role("button", name="Move note", exact=True)
    copy.scroll_into_view_if_needed()
    held = []
    endpoint = f"**/api/notes/{env.first.pk}"
    page.route(endpoint, lambda route: held.append(route))
    with page.expect_request(lambda req: req.method == "PATCH"):
        grip.press("ArrowRight")
    expect(copy).to_have_css("left", "50px")
    if new_text is not None:
        item.get_by_role("button", name="Edit", exact=True).click()
        item.get_by_label("Note text", exact=True).fill(new_text)
    assert len(held) == 1
    pending = held.pop()
    response = pending.fetch()
    assert response.json()["note"]["x"] == 50
    grip.scroll_into_view_if_needed()
    box = grip.bounding_box()
    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 30, box["y"] + 10)
    expect(copy).to_have_css("left", "70px")
    pending.fulfill(response=response)
    expect(item.get_by_role("status")).to_have_text("Not saved")
    grip.dispatch_event("pointercancel", {"pointerId": 1})
    page.mouse.up()
    expect(copy).to_have_css("left", "50px")
    if new_text is not None:
        expect(item.get_by_label("Note text", exact=True)).to_have_value(new_text)
        expect(item.get_by_role("status")).to_have_text("Not saved")
    else:
        saved(item)
    assert held == []
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"][0]["x"] == 50


@pytest.mark.parametrize("retry_refresh", [False, True])
def test_delete_during_conflict_refresh_resumes_after_response(notes_browser, retry_refresh):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("A stale edit I chose to delete")
    api(page, f"/api/notes/{env.first.pk}", "PATCH", {
        "version": 1, "text": "Newer server note", "x": 40, "y": 1600,
    })
    writes = []
    page.on("request", lambda req: writes.append(req.method) if req.method in {"PATCH", "DELETE"} else None)
    endpoint = f"**/api/articles/{env.article.pk}/notes"
    held = []
    if retry_refresh:
        page.route(endpoint, lambda route: route.abort())
        item.get_by_role("button", name="Save note", exact=True).click()
        expect(item.get_by_role("status")).to_contain_text("server copy could not be loaded")
        page.unroute(endpoint)
    page.route(endpoint, lambda route: held.append(route))
    with page.expect_request(lambda req: req.method == "GET" and req.url.endswith(f"/api/articles/{env.article.pk}/notes")):
        item.get_by_role("button", name="Retry" if retry_refresh else "Save note", exact=True).click()
    item.get_by_role("button", name="Delete", exact=True).click()
    assert len(held) == 1
    held.pop().continue_()
    expect(item).to_have_count(0)
    expect(page.locator("[data-note-copy]")).to_have_count(0)
    expect(page.get_by_role("button", name="New sticky note", exact=True)).to_be_enabled()
    assert writes == ["PATCH", "DELETE"]
    page.unroute(endpoint)
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"] == []


@pytest.mark.parametrize("refresh_status", [0, 403])
def test_pending_delete_keeps_draft_when_conflict_refresh_fails(notes_browser, refresh_status):
    env = notes_browser
    page = open_notes(env)
    item = card(page, env.first.pk)
    item.get_by_role("button", name="Edit", exact=True).click()
    item.get_by_label("Note text", exact=True).fill("Retained until deletion can finish")
    api(page, f"/api/notes/{env.first.pk}", "PATCH", {
        "version": 1, "text": "Newer server note", "x": 40, "y": 1600,
    })
    endpoint = f"**/api/articles/{env.article.pk}/notes"
    held = []
    page.route(endpoint, lambda route: held.append(route))
    with page.expect_request(lambda req: req.method == "GET" and req.url.endswith(f"/api/articles/{env.article.pk}/notes")):
        item.get_by_role("button", name="Save note", exact=True).click()
    item.get_by_role("button", name="Delete", exact=True).click()
    assert len(held) == 1
    route = held.pop()
    if refresh_status:
        route.fulfill(status=refresh_status, json={"error": {"code": "csrf_failed", "message": "CSRF validation failed."}})
    else:
        route.abort()
    expect(item.get_by_role("status")).to_contain_text("server copy could not be loaded")
    expect(item.get_by_label("Note text", exact=True)).to_have_value("Retained until deletion can finish")
    expect(item.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    page.unroute(endpoint)
    assert len(api(page, f"/api/articles/{env.article.pk}/notes")["notes"]) == 1
    item.get_by_role("button", name="Retry", exact=True).click()
    expect(item).to_have_count(0)
    assert api(page, f"/api/articles/{env.article.pk}/notes")["notes"] == []
