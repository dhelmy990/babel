"""Real-browser review scheduling, lifecycle, and layout regression coverage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.test import Client
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from tests.website.e2e.conftest import _session_cookie

pytestmark = pytest.mark.django_db(transaction=True)


def read_db(query):
    """Read real PostgreSQL outside Playwright's synchronous event-loop thread."""
    def run():
        close_old_connections()
        try:
            return query()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()


@pytest.fixture
def review_browser(request, live_server, publisher, reader, other_reader, monkeypatch):
    from study.models import ReaderProfile, ReviewSchedule
    from study.services.content import archive_article, publish_article
    from study.services.notes import create_note

    options = getattr(request, "param", {})
    clock = SimpleNamespace(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    if options.get("midnight"):
        clock.now = datetime(2026, 9, 14, 15, 59, tzinfo=UTC)
    monkeypatch.setattr("study.views.reviews.timezone.now", lambda: clock.now)
    ReaderProfile.objects.filter(user__in=[reader, other_reader]).update(timezone="UTC")

    def article(title, markdown=None, images=None):
        return publish_article(publisher, title=title, color="#1a5276",
                               markdown=markdown or f"# {title}\n\nA short explanation.",
                               images=images or {}, submission_id=uuid4())

    long_article = article("Long reading", "# Long reading\n\n" + "\n\n".join(
        f"Paragraph {i}. Understanding a useful idea takes time, and reading the entire explanation reaches its final section."
        for i in range(80)
    ))
    short = article("Short reading")
    if options.get("midnight"):
        ReviewSchedule.objects.create(user=reader, article=long_article, interval_days=1,
            next_due_date=clock.now.date() + timedelta(days=1), last_completed_at=clock.now - timedelta(days=1))
    image_bytes = BytesIO()
    Image.new("RGB", (500, 1900), "#214656").save(image_bytes, format="PNG")
    illustrated = article("Illustrated reading", "# Illustrated reading\n\n![Tall diagram](images/tall.png)", {"images/tall.png": image_bytes.getvalue()})
    archived = article("Archived reading")
    archive_article(publisher, archived.pk)
    create_note(reader, long_article.pk, note_id=uuid4(), kind="sticky", text="A separate private note", x=None, y=None)
    due_articles = []
    if options.get("due"):
        due_articles = [article(f"Review item {i}") for i in range(5)]
        for i, item in enumerate(due_articles):
            ReviewSchedule.objects.create(user=reader, article=item, interval_days=1,
                next_due_date=clock.now.date() - timedelta(days=5-i), last_completed_at=clock.now - timedelta(days=7))
        ReviewSchedule.objects.create(user=other_reader, article=long_article, interval_days=1,
            next_due_date=clock.now.date(), last_completed_at=clock.now - timedelta(days=2))
    if options.get("frozen_day"):
        from study.services.reviews import get_review_day
        get_review_day(reader, now=clock.now)
    cookies = {"reader": _session_cookie(live_server, Client(), reader),
               "other": _session_cookie(live_server, Client(), other_reader),
               "owner": _session_cookie(live_server, Client(), publisher, mode="admin")}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="Asia/Singapore")
        context.add_cookies([cookies["reader"]])
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield SimpleNamespace(page=page, browser=browser, clock=clock, cookies=cookies,
            reader=reader, other=other_reader, owner=publisher, long=long_article, short=short,
            illustrated=illustrated, archived=archived, due=due_articles, url=live_server.url)
        browser.close()
        assert errors == []


def schedules(env, article=None):
    from study.models import ReviewSchedule
    def query():
        rows = ReviewSchedule.objects.filter(user=env.reader)
        if article is not None:
            rows = rows.filter(article=article)
        return list(rows.values("article_id", "interval_days", "generation", "next_due_date"))
    return read_db(query)


def api(page, url, method="GET", body=None):
    return page.evaluate("""async ({url, method, body}) => {
      const {requestJSON} = await import('/static/study/site.js');
      return requestJSON(url, {method, ...(body === null ? {} : {body})});
    }""", {"url": url, "method": method, "body": body})


def open_article(env, article=None):
    env.page.goto(f"{env.url}/{(article or env.long).slug}")
    expect(env.page.locator("[data-reading-status]")).to_have_text("")
    expect(env.page.locator("[data-review-link]")).to_contain_text("Review today ·")
    return env.page


def reach_end(page):
    expect(page.locator("[data-reading-pull]")).to_be_enabled()
    page.locator("[data-reading-article]").evaluate("el => window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + scrollY - innerHeight + 90))")
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("End")


def test_article_end_and_partial_pull_do_not_schedule(review_browser):
    env = review_browser
    page = open_article(env)
    page.locator(".article-body").evaluate("el => window.scrollTo(0, el.getBoundingClientRect().bottom + scrollY - innerHeight + 60)")
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    expect(page.locator("[data-reading-pull]")).to_be_visible()
    page.mouse.wheel(0, 180)
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    page.mouse.wheel(0, -180)
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    page.keyboard.press("End")
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    expect(page.locator("[data-reading-article]")).to_have_class(re.compile(".*is-complete.*"))
    assert schedules(env, env.long)[0]["generation"] == 1


def test_short_article_needs_explicit_pull_and_supports_reduced_motion(review_browser):
    env = review_browser
    page = env.page
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{env.url}/{env.short.slug}")
    expect(page.locator("[data-reading-status]")).to_have_text("")
    assert schedules(env, env.short) == []
    page.get_by_role("button", name="Pull down to complete review", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert schedules(env, env.short)[0]["generation"] == 1


def test_restored_bottom_and_resize_need_fresh_completion_intent(review_browser):
    env = review_browser
    page = open_article(env)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true})); window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator("[data-reading-pull]")).to_be_enabled()
    page.set_viewport_size({"width": 1000, "height": 800})
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")


def test_resizing_during_partial_pull_cannot_finish_review(review_browser):
    env = review_browser
    page = open_article(env)
    page.locator("[data-reading-article]").evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - innerHeight + 90)")
    page.mouse.wheel(0, 200)
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []
    page.set_viewport_size({"width": 1280, "height": 6000})
    page.wait_for_timeout(150)
    assert schedules(env, env.long) == []


def test_full_pull_waits_for_acknowledgement_before_glowing(review_browser):
    env = review_browser
    page = open_article(env)
    held = []
    page.route("**/complete", lambda route: held.append(route))
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Recording reading…")
    expect(page.locator("[data-reading-article]")).not_to_have_class(re.compile(".*is-complete.*"))
    assert schedules(env, env.long) == []
    assert len(held) == 1
    held.pop().continue_()
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    page.screenshot(path="/tmp/babel-pull-complete-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[data-reading-status]").scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path="/tmp/babel-pull-complete-mobile.png")


def test_mobile_native_touch_pull_completes_once(review_browser):
    env = review_browser
    context = env.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                      is_mobile=True, timezone_id="Asia/Singapore")
    context.add_cookies([env.cookies["reader"]])
    page = context.new_page()
    page.goto(f"{env.url}/{env.short.slug}")
    expect(page.locator("[data-reading-pull]")).to_be_enabled()
    assert schedules(env, env.short) == []
    cdp = context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 190, "y": 720}]})
    for y in [680, 600, 500, 400, 300, 200, 100]:
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 190, "y": y}]})
        page.wait_for_timeout(30)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert schedules(env, env.short)[0]["generation"] == 1
    context.close()


def test_opening_long_article_does_not_schedule_until_real_empty_successor_end(review_browser):
    env = review_browser
    page = open_article(env)
    assert schedules(env, env.long) == []
    expect(page.locator(".next-links a")).to_have_count(0)
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert schedules(env, env.long)[0]["interval_days"] == 1
    page.reload()
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("This article is not due for review yet.")
    assert schedules(env, env.long)[0]["generation"] == 1


@pytest.mark.parametrize("image_error", [False, True])
def test_short_article_and_failed_image_legitimately_complete(review_browser, image_error):
    env = review_browser
    page = env.page
    article = env.illustrated if image_error else env.short
    if image_error:
        page.route("**/assets/**", lambda route: route.abort())
    page.goto(f"{env.url}/{article.slug}")
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert schedules(env, article)[0]["interval_days"] == 1


def test_delayed_tall_image_cannot_count_an_early_end_intersection(review_browser):
    env = review_browser
    page = env.page
    held = []
    page.route("**/assets/**", lambda route: held.append(route))
    page.goto(f"{env.url}/{env.illustrated.slug}", wait_until="domcontentloaded")
    expect(page.locator("[data-review-link]")).to_contain_text("Review today · 0")
    assert held
    assert schedules(env, env.illustrated) == []
    held.pop().continue_()
    page.wait_for_function("document.querySelector('.article-body img').naturalHeight === 1900")
    expect(page.locator("[data-reading-status]")).to_have_text("")
    assert page.locator(".next").bounding_box()["y"] > 900
    assert schedules(env, env.illustrated) == []
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")


def test_timezone_acknowledgement_precedes_first_day_materialization(review_browser):
    from study.models import ReviewDay
    env = review_browser
    page = env.page
    held, scheduling = [], []
    page.route("**/api/timezone", lambda route: held.append(route))
    page.on("request", lambda req: scheduling.append(req.url) if "/reviews/today" in req.url or "/reading-context" in req.url else None)
    with page.expect_request("**/api/timezone"):
        page.goto(f"{env.url}/{env.long.slug}")
    expect(page.locator("[data-reading-status]")).to_contain_text("Preparing")
    assert len(held) == 1 and scheduling == []
    assert read_db(lambda: ReviewDay.objects.filter(user=env.reader).count()) == 0
    held.pop().continue_()
    expect(page.locator("[data-review-link]")).to_contain_text("Review today · 0")
    assert read_db(lambda: list(ReviewDay.objects.filter(user=env.reader).values_list("timezone", "local_date"))) == [
        ("Asia/Singapore", datetime(2026, 9, 14).date())]


def test_timezone_failure_has_retry_without_freezing_utc_or_blocking_notes(review_browser):
    from study.models import ReviewDay
    env = review_browser
    page = env.page
    page.route("**/api/timezone", lambda route: route.abort())
    page.goto(f"{env.url}/{env.long.slug}")
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    expect(page.locator(".reading-panel")).to_have_css("opacity", "1")
    assert read_db(lambda: ReviewDay.objects.filter(user=env.reader).count()) == 0
    page.get_by_role("button", name="My notes", exact=True).click()
    expect(page.locator("[data-notes-list]")).to_contain_text("A separate private note")
    page.unroute("**/api/timezone")
    page.get_by_role("button", name="Retry reading completion", exact=True).click()
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert read_db(lambda: ReviewDay.objects.get(user=env.reader).timezone) == "Asia/Singapore"


def test_lost_completion_response_retries_same_token_without_new_context(review_browser):
    env = review_browser
    page = open_article(env)
    endpoint = f"**/api/articles/{env.long.pk}/complete"
    tokens, contexts = [], []
    page.on("request", lambda req: contexts.append(req) if "/reading-context" in req.url else None)

    def lose_response(route):
        tokens.append(route.request.post_data_json["token"])
        route.fetch()
        route.abort()

    page.route(endpoint, lose_response)
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    assert schedules(env, env.long)[0]["generation"] == 1
    page.unroute(endpoint)
    page.on("request", lambda req: tokens.append(req.post_data_json["token"]) if req.url.endswith("/complete") else None)
    page.get_by_role("button", name="Retry reading completion", exact=True).click()
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert tokens[0] == tokens[1] and contexts == []
    assert schedules(env, env.long)[0]["generation"] == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_only_expired_tokens_refresh_context(review_browser, invalid):
    env = review_browser
    page = open_article(env)
    contexts = []
    page.on("request", lambda req: contexts.append(req) if "/reading-context" in req.url else None)
    if invalid:
        page.route("**/complete", lambda route: route.fulfill(status=400, json={"error": {"code": "token_invalid", "message": "Invalid reading token"}}))
    else:
        env.clock.now += timedelta(hours=25)
    reach_end(page)
    if invalid:
        expect(page.locator("[data-reading-status]")).to_contain_text("could not")
        assert contexts == [] and schedules(env, env.long) == []
    else:
        expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
        assert len(contexts) == 1
        assert schedules(env, env.long)[0]["interval_days"] == 1


def test_hidden_document_waits_and_sidebar_opening_does_not_duplicate(review_browser):
    env = review_browser
    page = open_article(env)
    page.evaluate("Object.defineProperty(document, 'visibilityState', {configurable:true, get: () => 'hidden'})")
    reach_end(page)
    page.wait_for_timeout(100)
    assert schedules(env, env.long) == []
    page.evaluate("delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    page.get_by_role("button", name="My notes", exact=True).click()
    reach_end(page)
    assert schedules(env, env.long)[0]["generation"] == 1


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_daily_list_max_three_updates_without_refill_after_completion_and_archive(review_browser):
    from study.models import ReviewSlot
    env = review_browser
    page = env.page
    page.goto(env.url + "/")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 3")
    page.screenshot(path="/tmp/babel-r2-home-desktop.png")
    page.locator("[data-review-link]").click()
    details = page.locator("[data-review-list]")
    expect(details).to_have_attribute("open", "")
    details.locator("summary").click()
    expect(details).not_to_have_attribute("open", "")
    page.locator("[data-review-link]").click()
    expect(details).to_have_attribute("open", "")
    links = details.locator("[data-review-items] a")
    expect(links).to_have_count(3)
    page.screenshot(path="/tmp/babel-r2-review-list-desktop.png")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 3")
    selected_ids = read_db(lambda: list(ReviewSlot.objects.filter(day__user=env.reader).values_list("pk", flat=True)))
    links.first.click()
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 2")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 2")
    assert schedules(env, env.due[0])[0]["interval_days"] == 2
    page.screenshot(path="/tmp/babel-r2-completion-desktop.png")
    owner_context = env.browser.new_context(timezone_id="Asia/Singapore")
    owner_context.add_cookies([env.cookies["owner"]])
    owner = owner_context.new_page()
    owner.goto(env.url + "/")
    api(owner, f"/api/articles/{env.due[1].pk}/archive", "POST")
    page.goto(env.url + "/#reviews")
    expect(links).to_have_count(1)
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 1")
    assert read_db(lambda: list(ReviewSlot.objects.filter(day__user=env.reader).values_list("pk", flat=True))) == selected_ids
    owner_context.close()


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_cross_tab_stale_context_cannot_double_review(review_browser):
    env = review_browser
    page = env.page
    page.add_init_script("Object.defineProperty(document, 'visibilityState', {configurable:true, get: () => 'hidden'})")
    page.goto(f"{env.url}/{env.due[0].slug}")
    expect(page.locator("[data-reading-status]")).to_have_text("")
    other = page.context.new_page()
    other.add_init_script("Object.defineProperty(document, 'visibilityState', {configurable:true, get: () => 'hidden'})")
    other.goto(f"{env.url}/{env.due[0].slug}")
    expect(other.locator("[data-reading-status]")).to_have_text("")
    page.evaluate("delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))")
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 2")
    other.evaluate("delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))")
    reach_end(other)
    expect(other.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 2")
    assert schedules(env, env.due[0])[0]["generation"] == 2


@pytest.mark.parametrize("kind", ["anonymous", "archive", "preview"])
def test_excluded_pages_make_no_review_api_requests(review_browser, kind):
    env = review_browser
    context = env.browser.new_context(timezone_id="America/New_York")
    if kind != "anonymous":
        context.add_cookies([env.cookies["owner"]])
    page = context.new_page()
    calls = []
    page.on("request", lambda req: calls.append(req.url) if "/api/reviews/" in req.url or "/reading-context" in req.url or req.url.endswith("/complete") or req.url.endswith("/api/timezone") else None)
    path = f"/{env.archived.slug}" if kind == "archive" else "/publish" if kind == "preview" else f"/{env.short.slug}"
    page.goto(env.url + path)
    if kind == "preview":
        page.get_by_label("Title", exact=True).fill("A preview")
        page.get_by_role("button", name="Markdown", exact=True).click()
        page.get_by_label("Markdown source", exact=True).fill("# Preview\n\nA short preview.")
        page.get_by_role("button", name="Preview", exact=True).click()
        expect(page.locator("[data-preview-body]")).to_contain_text("A short preview")
    page.wait_for_timeout(100)
    assert calls == []
    assert schedules(env) == []
    if kind != "anonymous":
        expect(page.locator("[data-review-link]")).to_have_text("Review today")
    context.close()


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_page_lifecycle_clears_reviews_and_refetches_switched_reader(review_browser):
    env = review_browser
    page = env.page
    page.goto(env.url + "/#reviews")
    expect(page.locator("[data-review-items] a")).to_have_count(3)
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    expect(page.locator("[data-review-items]")).to_be_empty()
    expect(page.locator("[data-review-link]")).not_to_contain_text("3")
    page.context.add_cookies([env.cookies["other"]])
    held = []
    page.route("**/api/reviews/today", lambda route: held.append(route))
    with page.expect_request("**/api/reviews/today"):
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator("[data-review-status]")).to_contain_text("Loading")
    expect(page.locator("[data-review-items]")).to_be_empty()
    assert held
    held.pop().continue_()
    expect(page.locator("[data-review-items] a")).to_have_count(1)
    expect(page.locator("[data-review-items]")).to_contain_text("Long reading")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 1")


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
@pytest.mark.parametrize("identity", ["reader", "owner"])
def test_mobile_navbar_and_single_tap_review_links(review_browser, identity):
    env = review_browser
    context = env.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, timezone_id="Asia/Singapore")
    context.add_cookies([env.cookies[identity]])
    page = context.new_page()
    page.goto(env.url + "/")
    expect(page.locator("[data-review-link]")).to_contain_text("Review today ·")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in page.locator(".site-nav a:visible, .site-nav button:visible").all():
        expect(control).to_be_in_viewport()
    page.screenshot(path=f"/tmp/babel-r2-{identity}-mobile.png")
    if identity == "reader":
        page.locator("[data-review-link]").tap()
        page.locator("[data-review-items] a").first.tap()
        expect(page).to_have_url(f"{env.url}/{env.due[0].slug}")
        reach_end(page)
        expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 2")
    context.close()


def test_repeated_readiness_failure_keeps_retry_usable(review_browser):
    env = review_browser
    page = env.page
    page.route("**/api/timezone", lambda route: route.abort())
    page.goto(f"{env.url}/{env.long.slug}")
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    retry = page.get_by_role("button", name="Retry reading completion", exact=True)
    retry.click()
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    expect(retry).to_be_enabled()
    page.unroute("**/api/timezone")
    retry.click()
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")


def test_completion_body_disconnect_retries_original_token(review_browser):
    env = review_browser
    page = open_article(env)
    tokens = []
    page.on("request", lambda req: tokens.append(req.post_data_json["token"]) if req.url.endswith("/complete") else None)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).endsWith('/complete')) {
          window.fetch = original;
          return {ok: response.ok, status: response.status,
            text: () => Promise.reject(new TypeError('response stream lost'))};
        }
        return response;
      };
    }""")
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    page.get_by_role("button", name="Retry reading completion", exact=True).click()
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")
    assert len(tokens) == 2 and tokens[0] == tokens[1]
    assert schedules(env, env.long)[0]["generation"] == 1


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_late_today_payload_cannot_restore_previous_readers_slots(review_browser):
    env = review_browser
    page = env.page
    page.goto(env.url + "/#reviews")
    expect(page.locator("[data-review-items] a")).to_have_count(3)
    page.evaluate("""() => {
      const original = window.fetch;
      let once = true;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (once && String(args[0]).endsWith('/reviews/today')) {
          once = false;
          const body = await response.text();
          await new Promise(resolve => { window.releaseOldReviews = resolve; });
          return {ok: response.ok, status: response.status, text: async () => body};
        }
        return response;
      };
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    }""")
    page.wait_for_function("typeof window.releaseOldReviews === 'function'")
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}))")
    page.context.add_cookies([env.cookies["other"]])
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator("[data-review-items] a")).to_have_count(1)
    expect(page.locator("[data-review-items]")).to_contain_text("Long reading")
    page.evaluate("window.releaseOldReviews()")
    page.wait_for_timeout(100)
    expect(page.locator("[data-review-items] a")).to_have_count(1)
    expect(page.locator("[data-review-items]")).not_to_contain_text("Review item")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 1")


def test_completion_retry_outside_end_view_remains_usable(review_browser):
    env = review_browser
    page = open_article(env)
    endpoint = f"**/api/articles/{env.long.pk}/complete"
    page.route(endpoint, lambda route: route.abort())
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_contain_text("could not")
    page.unroute(endpoint)
    page.evaluate("scrollTo(0, 0)")
    page.locator("[data-reading-retry]").evaluate("button => button.click()")
    expect(page.locator("[data-reading-retry]")).to_be_enabled()
    assert schedules(env, env.long) == []
    reach_end(page)
    page.get_by_role("button", name="Retry reading completion", exact=True).click()
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 1")


@pytest.mark.parametrize("review_browser", [{"frozen_day": True}], indirect=True)
def test_acknowledged_pending_timezone_does_not_loop_or_rematerialize_day(review_browser):
    from study.models import ReviewDay
    env = review_browser
    page = env.page
    changes = []
    page.on("request", lambda req: changes.append(req) if req.url.endswith("/api/timezone") else None)
    page.goto(env.url + "/#reviews")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 0")
    expect(page.locator("[data-review-day]")).to_have_text("2026-09-13 · UTC")
    assert len(changes) == 1
    session = api(page, "/api/session")
    assert session["timezone"] == "UTC" and session["pending_timezone"] == "Asia/Singapore"
    assert read_db(lambda: ReviewDay.objects.filter(user=env.reader).count()) == 1


def test_session_failure_requires_retry_before_review_day_materializes(review_browser):
    from study.models import ReviewDay
    env = review_browser
    page = env.page
    page.route("**/api/session", lambda route: route.abort())
    page.goto(env.url + "/#reviews")
    expect(page.locator("[data-review-status]")).to_contain_text("could not be loaded")
    assert read_db(lambda: ReviewDay.objects.filter(user=env.reader).count()) == 0
    page.unroute("**/api/session")
    page.get_by_role("button", name="Retry reviews", exact=True).click()
    expect(page.locator("[data-review-status]")).to_have_text("Nothing to review today.")
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 0")
    assert read_db(lambda: ReviewDay.objects.get(user=env.reader).timezone) == "Asia/Singapore"


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_unselected_due_article_does_not_advance_its_schedule(review_browser):
    env = review_browser
    page = env.page
    completions = []
    page.on("request", lambda req: completions.append(req) if req.url.endswith("/complete") else None)
    page.goto(f"{env.url}/{env.due[4].slug}")
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("This article is not in today's review selection.")
    assert len(completions) == 1
    assert schedules(env, env.due[4])[0]["generation"] == 1
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 3")


def test_home_empty_state_and_anonymous_section_visibility(review_browser):
    env = review_browser
    page = env.page
    page.goto(env.url + "/#reviews")
    expect(page.locator("[data-review-status]")).to_have_text("Nothing to review today.")
    expect(page.locator("[data-review-items] a")).to_have_count(0)
    anonymous = env.browser.new_context()
    other = anonymous.new_page()
    calls = []
    other.on("request", lambda req: calls.append(req) if "/api/reviews/" in req.url else None)
    other.goto(env.url + "/#reviews")
    expect(other.locator("[data-review-list]")).to_be_hidden()
    expect(other.locator("[data-review-link]")).to_be_hidden()
    assert calls == []
    anonymous.close()


@pytest.mark.parametrize("review_browser", [{"midnight": True}], indirect=True)
def test_initially_not_due_context_records_current_eligibility_after_midnight(review_browser):
    from study.models import ReviewSlot
    env = review_browser
    page = env.page
    contexts, completions = [], []
    page.on("request", lambda req: contexts.append(req) if req.url.endswith("/reading-context") else None)
    page.on("request", lambda req: completions.append(req.post_data_json) if req.url.endswith("/complete") else None)
    with page.expect_response("**/reading-context") as response:
        page.goto(f"{env.url}/{env.long.slug}")
    original = response.value.json()
    assert original["eligible"] is False and original["status"] == "not_due"
    expect(page.locator("[data-review-link]")).to_have_text("Review today · 0")
    assert completions == []
    assert schedules(env, env.long)[0]["generation"] == 1
    env.clock.now += timedelta(minutes=2)
    reach_end(page)
    expect(page.locator("[data-reading-status]")).to_have_text("Review complete, number of days till the next read: 2")
    assert completions == [{"token": original["token"]}]
    assert len(contexts) == 1
    schedule = schedules(env, env.long)[0]
    assert schedule["interval_days"] == 2 and schedule["generation"] == 2
    assert schedule["next_due_date"].isoformat() == "2026-09-17"
    slots = read_db(lambda: list(ReviewSlot.objects.filter(day__user=env.reader,
        schedule__article=env.long).values("day__local_date", "completed_at")))
    assert len(slots) == 1
    assert slots[0]["day__local_date"].isoformat() == "2026-09-15"
    assert slots[0]["completed_at"] == env.clock.now


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
@pytest.mark.parametrize("repeated_expiry", [False, True])
def test_expiry_refresh_still_requires_completion_and_is_bounded(review_browser, repeated_expiry):
    env = review_browser
    page = env.page
    page.add_init_script("Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});")
    contexts, completions = [], []
    page.on("request", lambda req: contexts.append(req) if req.url.endswith("/reading-context") else None)
    page.on("request", lambda req: completions.append(req.post_data_json) if req.url.endswith("/complete") else None)
    with page.expect_response("**/reading-context") as response:
        page.goto(f"{env.url}/{env.due[4].slug}")
    original = response.value.json()
    assert original["eligible"] is False
    assert completions == []
    env.clock.now += timedelta(hours=25)
    if repeated_expiry:
        page.route("**/complete", lambda route: route.fulfill(status=400,
            json={"error": {"code": "token_expired", "message": "Expired token"}}))
    page.evaluate("Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'}); document.dispatchEvent(new Event('visibilitychange'));")
    reach_end(page)
    if repeated_expiry:
        expect(page.locator("[data-reading-status]")).to_contain_text("could not")
        expect(page.locator("[data-reading-retry]")).to_be_enabled()
    else:
        expect(page.locator("[data-reading-status]")).to_have_text("This article is not in today's review selection.")
    assert len(contexts) == len(completions) == 2
    assert completions[0] == {"token": original["token"]}
    assert completions[1]["token"] != original["token"]
    assert schedules(env, env.due[4])[0]["generation"] == 1
