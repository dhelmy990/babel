from datetime import UTC, date, datetime

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from study.models import ReaderProfile, ReviewDay, ReviewSchedule

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 9, 30, 17, tzinfo=UTC)


def schedule(user, article, due, **kwargs):
    return ReviewSchedule.objects.create(user=user, article=article, next_due_date=due,
                                         last_completed_at=NOW, **kwargs)


def test_calendar_private_month_and_overdue_without_freezing_day(reader, other_reader, article_factory):
    from study.services.calendar import calendar_payload
    current = article_factory("October reading")
    overdue = article_factory("September reading")
    schedule(reader, current, date(2026, 10, 31))
    schedule(reader, overdue, date(2026, 9, 30))
    schedule(reader, article_factory("November"), date(2026, 11, 1))
    schedule(reader, article_factory("Suspended"), date(2026, 10, 1), suspended=True)
    archived = article_factory("Archived")
    archived.archived_at = NOW
    archived.save(update_fields=["archived_at"])
    schedule(reader, archived, date(2026, 10, 1))
    schedule(other_reader, article_factory("Another reader"), date(2026, 10, 1))
    schedule(reader, article_factory("No later date"), None)
    result = calendar_payload(reader, None, now=NOW)
    assert result["month"] == "2026-10"
    assert result["today"] == "2026-10-01"
    assert result["timezone"] == "Asia/Singapore"
    assert [item["title"] for item in result["items"]] == ["October reading"]
    assert result["items"][0]["due_date"] == "2026-10-31"
    assert result["items"][0]["slug"] == current.slug
    assert [item["title"] for item in result["overdue"]] == ["September reading"]
    assert not ReviewDay.objects.filter(user=reader).exists()


def test_calendar_read_does_not_create_a_profile(reader):
    from study.services.calendar import calendar_payload
    ReaderProfile.objects.filter(user=reader).delete()
    result = calendar_payload(reader, "2028-02", now=NOW)
    assert result["items"] == [] and result["timezone"] == "UTC"
    assert not ReaderProfile.objects.filter(user=reader).exists()


@pytest.mark.parametrize("month", ["2026-00", "2026-13", "0000-01", "2026-1", "not-a-month", "2026-01-01", ""])
def test_calendar_rejects_invalid_month(reader, month):
    from study.services.calendar import calendar_payload
    with pytest.raises(ValueError):
        calendar_payload(reader, month, now=NOW)


def test_calendar_terminal_month_and_leap_day(reader, article_factory):
    from study.services.calendar import calendar_payload
    schedule(reader, article_factory("Leap"), date(2028, 2, 29))
    schedule(reader, article_factory("Last"), date.max)
    assert calendar_payload(reader, "2028-02", now=NOW)["items"][0]["due_date"] == "2028-02-29"
    assert calendar_payload(reader, "9999-12", now=NOW)["items"][0]["due_date"] == "9999-12-31"
    with pytest.raises(PermissionDenied):
        calendar_payload(AnonymousUser(), None, now=NOW)


def test_calendar_http_authentication_private_errors_and_page(client, reader):
    response = client.get("/api/reviews/calendar")
    assert response.status_code == 401
    assert response["Cache-Control"] == "private, no-store"
    assert client.get("/reviews/calendar").status_code == 302
    client.force_login(reader)
    assert client.get("/reviews/calendar").status_code == 200
    response = client.get("/api/reviews/calendar?month=2026-09")
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    response = client.get("/api/reviews/calendar?month=oops")
    assert response.status_code == 400
    assert response["Cache-Control"] == "private, no-store"
    assert client.post("/api/reviews/calendar").status_code == 405
