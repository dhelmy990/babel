from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from tests.e2e.test_reviews import read_db, review_browser

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("review_browser", [{"due": True}], indirect=True)
def test_calendar_dates_navigation_privacy_and_mobile(review_browser):
    from study.models import ReviewDay, ReviewSchedule
    env = review_browser
    read_db(lambda: ReviewSchedule.objects.filter(user=env.reader, article=env.due[0]).update(next_due_date=date(2026, 9, 20)))
    read_db(lambda: ReviewSchedule.objects.filter(user=env.reader, article=env.due[1]).update(next_due_date=date(2026, 10, 1)))
    page = env.page
    page.goto(env.url + "/reviews/calendar")
    expect(page.locator("[data-calendar-month]")).to_have_text("September 2026")
    expect(page.locator("[data-calendar-timezone]")).to_contain_text("UTC")
    expect(page.locator("[data-calendar-overdue] a")).to_have_count(3)
    page.get_by_role("button", name="20 September 2026, 1 scheduled read", exact=True).click()
    expect(page.locator("[data-calendar-items]")).to_contain_text("Review item 0")
    expect(page.locator("[data-calendar-items] a")).to_have_attribute("href", f"/{env.due[0].slug}")
    page.get_by_role("button", name="Next month", exact=True).click()
    expect(page.locator("[data-calendar-month]")).to_have_text("October 2026")
    page.get_by_role("button", name="1 October 2026, 1 scheduled read", exact=True).click()
    expect(page.locator("[data-calendar-items]")).to_contain_text("Review item 1")
    page.get_by_role("button", name="This month", exact=True).click()
    expect(page.locator("[data-calendar-month]")).to_have_text("September 2026")
    assert not read_db(lambda: ReviewDay.objects.filter(user=env.reader).exists())
    page.screenshot(path="/tmp/babel-calendar-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path="/tmp/babel-calendar-mobile.png")
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    expect(page.locator("[data-calendar-content]")).to_be_hidden()
    page.context.add_cookies([env.cookies["other"]])
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator("[data-calendar-items] a")).to_have_count(1)
    expect(page.locator("[data-calendar-items]")).to_contain_text("Long reading")
    expect(page.locator("[data-calendar-overdue] a")).to_have_count(0)
    expect(page.locator("[data-calendar-overdue]")).not_to_contain_text("Review item")


def test_calendar_failure_can_retry_and_has_empty_state(review_browser):
    page = review_browser.page
    page.route("**/api/reviews/calendar*", lambda route: route.abort())
    page.goto(review_browser.url + "/reviews/calendar")
    expect(page.locator("[data-calendar-status]")).to_contain_text("could not")
    page.unroute("**/api/reviews/calendar*")
    page.get_by_role("button", name="Retry calendar", exact=True).click()
    expect(page.locator("[data-calendar-status]")).to_have_text("")
    expect(page.locator("[data-calendar-empty]")).to_be_visible()
    expect(page.locator("[data-calendar-overdue-empty]")).to_be_visible()


@pytest.mark.parametrize("review_browser", [{"frozen_day": True}], indirect=True)
def test_calendar_with_expired_day_does_not_synchronize_timezone_or_freeze_another_day(review_browser):
    from study.models import ReaderProfile, ReviewDay
    env = review_browser
    before = read_db(lambda: list(ReviewDay.objects.filter(user=env.reader).values_list("pk", flat=True)))
    env.clock.now += timedelta(days=2)
    writes = []
    env.page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
    env.page.goto(env.url + "/reviews/calendar")
    expect(env.page.locator("[data-calendar-month]")).to_have_text("September 2026")
    assert read_db(lambda: list(ReviewDay.objects.filter(user=env.reader).values_list("pk", flat=True))) == before
    assert read_db(lambda: ReaderProfile.objects.get(user=env.reader).timezone) == "UTC"
    assert writes == []
