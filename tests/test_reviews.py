import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 9, 14, 2, tzinfo=UTC)


def finish(user, article, now=NOW):
    from study.services.reviews import complete_article, reading_context
    context = reading_context(user, article.pk, now=now)
    return complete_article(user, article.pk, token=context["token"], now=now)


def due(user, article, *, due_date=date(2026, 9, 14), interval=1, completed=None):
    from study.models import ReviewSchedule
    return ReviewSchedule.objects.create(user=user, article=article, interval_days=Decimal(interval), next_due_date=due_date,
                                         last_completed_at=completed or NOW - timedelta(days=2))


def assert_private(response, status):
    assert response.status_code == status
    assert response["Cache-Control"] == "private, no-store"
    if status >= 400:
        assert set(response.json()["error"]) == {"code", "message"}
    return response


def test_first_completion_then_selected_review_doubles_from_completion_date(reader, article_factory):
    from study.models import ReviewSchedule
    from study.services.reviews import complete_article, get_review_day, reading_context
    article = article_factory("Article")
    context = reading_context(reader, article.pk, now=NOW)
    assert context["eligible"] and context["status"] == "first_read"
    assert not ReviewSchedule.objects.exists()
    first = complete_article(reader, article.pk, token=context["token"], now=NOW)
    assert first == {"status": "first_read", "interval_days": "1", "generation": 1, "next_due_date": "2026-09-15"}
    assert get_review_day(reader, now=NOW).slots.count() == 0
    tomorrow = NOW + timedelta(days=1)
    assert get_review_day(reader, now=tomorrow).slots.count() == 1
    context = reading_context(reader, article.pk, now=tomorrow)
    assert context["eligible"] and context["status"] == "review_due"
    reviewed = complete_article(reader, article.pk, token=context["token"], now=tomorrow)
    assert reviewed == {"status": "reviewed", "interval_days": "2", "generation": 2, "next_due_date": "2026-09-17"}
    assert get_review_day(reader, now=tomorrow).slots.get().completed_at == tomorrow
    assert complete_article(reader, article.pk, token=context["token"], now=tomorrow)["status"] == "already_processed"
    assert finish(reader, article, tomorrow)["status"] == "not_due"
    assert ReviewSchedule.objects.get().generation == 2


@pytest.mark.parametrize("count", [0, 1, 2, 5])
def test_daily_cap_is_frozen_without_refilling_and_unfinished_remain_oldest(reader, article_factory, count):
    from study.services.reviews import get_review_day, today_payload
    articles = [article_factory(f"Article {index}") for index in range(count)]
    for index, article in enumerate(articles):
        due(reader, article, due_date=date(2026, 9, 9) + timedelta(days=index))
    day = get_review_day(reader, now=NOW)
    slots = list(day.slots.order_by("ordinal"))
    assert [slot.article_id for slot in slots] == [article.pk for article in articles[:3]]
    if slots:
        assert finish(reader, articles[0])["status"] == "reviewed"
    assert list(get_review_day(reader, now=NOW).slots.order_by("ordinal").values_list("pk", flat=True)) == [slot.pk for slot in slots]
    payload = today_payload(reader, now=NOW)
    assert payload["date"] == "2026-09-14" and payload["timezone"] == "Asia/Singapore"
    assert len(payload["slots"]) == min(count, 3)
    if count == 5:
        assert payload["slots"][0] == {"ordinal": 0, "article_id": str(articles[0].pk), "slug": articles[0].slug,
                                        "title": articles[0].title, "completed": True, "cancelled": False}
        assert finish(reader, articles[3])["status"] == "not_selected"
        tomorrow = get_review_day(reader, now=NOW + timedelta(days=1))
        assert list(tomorrow.slots.order_by("ordinal").values_list("article_id", flat=True)) == [a.pk for a in articles[1:4]]


def test_due_order_uses_last_completed_then_article_uuid(reader, article_factory):
    from study.services.reviews import get_review_day
    articles = [article_factory(f"Article {i}") for i in range(4)]
    for article in articles:
        due(reader, article)
    from study.models import ReviewSchedule
    ReviewSchedule.objects.filter(article=articles[3]).update(last_completed_at=NOW - timedelta(days=10))
    expected = [articles[3].pk] + sorted(a.pk for a in articles[:3])[:2]
    assert list(get_review_day(reader, now=NOW).slots.order_by("ordinal").values_list("article_id", flat=True)) == expected


def test_first_read_duplicate_and_early_reread_do_not_advance(reader, article_factory):
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    token = reading_context(reader, article.pk, now=NOW)["token"]
    assert complete_article(reader, article.pk, token=token, now=NOW)["status"] == "first_read"
    assert complete_article(reader, article.pk, token=token, now=NOW)["status"] == "already_processed"
    assert finish(reader, article)["status"] == "not_due"


@pytest.mark.parametrize("change", ["expired", "future", "wrong_user", "wrong_article", "bool_generation", "fraction_generation", "string_time", "nan_time", "infinite_time", "bool_time", "missing", "unsigned"])
def test_tokens_are_bound_typed_and_expire_against_service_clock(reader, other_reader, article_factory, change):
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    token = reading_context(reader, article.pk, now=NOW)["token"]
    signer = signing.Signer(salt="study-reading")
    data = signer.unsign_object(token)
    user, target, now = reader, article.pk, NOW
    if change == "expired":
        now += timedelta(hours=24, seconds=1)
    elif change == "future":
        now -= timedelta(seconds=1)
    elif change == "wrong_user":
        user = other_reader
    elif change == "wrong_article":
        target = article_factory("Another").pk
    elif change == "unsigned":
        token = "forged"
    else:
        if change == "bool_generation":
            data["generation"] = True
        elif change == "fraction_generation":
            data["generation"] = 1.5
        elif change == "string_time":
            data["issued_at"] = str(NOW.timestamp())
        elif change == "nan_time":
            data["issued_at"] = float("nan")
        elif change == "infinite_time":
            data["issued_at"] = float("inf")
        elif change == "bool_time":
            data["issued_at"] = True
        elif change == "missing":
            del data["generation"]
        token = signer.sign_object(data)
    with pytest.raises(ValueError) as caught:
        complete_article(user, target, token=token, now=now)
    assert caught.value.code == ("token_expired" if change == "expired" else "token_invalid")


def test_token_24_hour_boundary_and_first_read_before_midnight(reader, article_factory):
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    before = datetime(2026, 9, 14, 15, 59, 59, tzinfo=UTC)
    token = reading_context(reader, article.pk, now=before)["token"]
    result = complete_article(reader, article.pk, token=token, now=before + timedelta(hours=24))
    assert result["next_due_date"] == "2026-09-16"


@pytest.mark.parametrize("now,expected", [
    (datetime(2028, 2, 28, 2, tzinfo=UTC), "2028-02-29"),
    (datetime(2028, 2, 29, 2, tzinfo=UTC), "2028-03-01"),
    (datetime(2026, 9, 14, 15, 59, 59, tzinfo=UTC), "2026-09-15"),
])
def test_first_read_calendar_dates(reader, article_factory, now, expected):
    assert finish(reader, article_factory("Article"), now)["next_due_date"] == expected


@pytest.mark.parametrize("now,boundary", [
    (datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 9, 4, tzinfo=UTC)),
    (datetime(2026, 11, 1, 4, tzinfo=UTC), datetime(2026, 11, 2, 5, tzinfo=UTC)),
])
def test_day_boundary_follows_dst_midnight(reader, now, boundary):
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(reader, "America/New_York", now=now)
    day = get_review_day(reader, now=now)
    assert day.next_boundary_at == boundary


def test_large_interval_is_exact_and_overflow_has_no_due_date(reader, article_factory):
    from study.models import ReviewSchedule
    article = article_factory("Article")
    value = 10**99 + 123456789012345678901234567891
    due(reader, article, interval=value)
    result = finish(reader, article)
    assert result["interval_days"] == str(value * 2)
    assert result["next_due_date"] is None
    schedule = ReviewSchedule.objects.get()
    assert int(schedule.interval_days) == value * 2
    assert finish(reader, article)["status"] == "not_due"


def test_terminal_date_with_positive_timezone_never_converts_sentinel(reader, article_factory):
    from study.services.reviews import get_review_day, set_timezone, today_payload
    final = datetime.max.replace(tzinfo=UTC)
    article = article_factory("Article")
    assert finish(reader, article, final)["next_due_date"] is None
    day = get_review_day(reader, now=final)
    assert day.local_date == date.max and day.next_boundary_at == final
    set_timezone(reader, "Asia/Tokyo", now=final)
    assert get_review_day(reader, now=final).pk == day.pk
    assert today_payload(reader, now=final)["date"] == "9999-12-31"


def test_pending_timezone_last_request_wins_and_can_be_cancelled(reader):
    from study.models import ReaderProfile
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(reader, "UTC", now=NOW)
    assert ReaderProfile.objects.get(user=reader).active_day_id is None
    day = get_review_day(reader, now=NOW)
    set_timezone(reader, "Asia/Tokyo", now=NOW)
    set_timezone(reader, "America/Los_Angeles", now=NOW)
    profile = ReaderProfile.objects.get(user=reader)
    assert profile.timezone == "UTC" and profile.pending_timezone == "America/Los_Angeles"
    assert get_review_day(reader, now=NOW).pk == day.pk
    set_timezone(reader, "UTC", now=NOW)
    assert ReaderProfile.objects.get(user=reader).pending_timezone is None


def test_backward_timezone_reuses_day_with_effective_boundary_then_applies_pending(reader):
    from study.models import ReaderProfile, ReviewDay
    from study.services.reviews import get_review_day, set_timezone, today_payload
    set_timezone(reader, "UTC", now=NOW)
    original = get_review_day(reader, now=NOW)
    set_timezone(reader, "America/Los_Angeles", now=NOW)
    midnight = datetime(2026, 9, 15, tzinfo=UTC)
    reused = get_review_day(reader, now=midnight)
    assert reused.pk == original.pk
    assert reused.timezone == "UTC" and reused.next_boundary_at == midnight
    assert today_payload(reader, now=midnight)["timezone"] == "America/Los_Angeles"
    set_timezone(reader, "Asia/Tokyo", now=midnight + timedelta(hours=1))
    assert get_review_day(reader, now=midnight + timedelta(hours=6)).pk == original.pk
    assert ReaderProfile.objects.get(user=reader).pending_timezone == "Asia/Tokyo"
    new = get_review_day(reader, now=midnight + timedelta(hours=7))
    assert new.local_date == date(2026, 9, 15) and new.timezone == "Asia/Tokyo"
    assert new.next_boundary_at == midnight + timedelta(hours=15)
    assert ReviewDay.objects.count() == 2
    assert ReaderProfile.objects.get(user=reader).pending_timezone is None


def test_timezone_update_after_expiry_resolves_existing_pending_before_staging(reader):
    from study.models import ReaderProfile
    from study.services.reviews import get_review_day, set_timezone
    get_review_day(reader, now=NOW)
    set_timezone(reader, "UTC", now=NOW)
    boundary = datetime(2026, 9, 14, 16, tzinfo=UTC)
    set_timezone(reader, "Asia/Tokyo", now=boundary)
    profile = ReaderProfile.objects.get(user=reader)
    assert profile.timezone == "UTC" and profile.pending_timezone == "Asia/Tokyo"
    assert profile.active_day.timezone == "Asia/Singapore"  # Existing Sep14 is preserved.
    assert get_review_day(reader, now=boundary).pk == profile.active_day_id


def test_archive_suspends_schedules_and_cancels_only_uncompleted_slots(reader, publisher, article_factory):
    from study.models import ReviewSchedule
    from study.services.content import archive_article
    from study.services.reviews import complete_article, get_review_day, reading_context
    a, b, fresh = [article_factory(title) for title in ("A", "B", "Fresh")]
    for article in (a, b):
        due(reader, article)
    day = get_review_day(reader, now=NOW)
    token = reading_context(reader, b.pk, now=NOW)["token"]
    fresh_token = reading_context(reader, fresh.pk, now=NOW)["token"]
    finish(reader, a)
    for article in (a, b, fresh):
        archive_article(publisher, article.pk)
    assert ReviewSchedule.objects.filter(suspended=True).count() == 2
    assert day.slots.get(article=a).completed_at is not None
    assert day.slots.get(article=a).cancelled_at is None
    assert day.slots.get(article=b).cancelled_at is not None
    for actor in (reader, publisher):
        with pytest.raises(Http404):
            reading_context(actor, b.pk, now=NOW)
    for article, signed in ((b, token), (fresh, fresh_token)):
        with pytest.raises(Http404):
            complete_article(reader, article.pk, token=signed, now=NOW)
    assert not ReviewSchedule.objects.filter(article=fresh).exists()


def test_services_require_authentication_and_aware_clock(reader, article_factory):
    from study.services.reviews import complete_article, get_review_day, reading_context, set_timezone, today_payload
    article = article_factory("Article")
    for operation in (
        lambda user, now: get_review_day(user, now=now),
        lambda user, now: reading_context(user, article.pk, now=now),
        lambda user, now: complete_article(user, article.pk, token="fake", now=now),
        lambda user, now: today_payload(user, now=now),
        lambda user, now: set_timezone(user, "UTC", now=now),
    ):
        with pytest.raises(PermissionDenied):
            operation(AnonymousUser(), NOW)
        with pytest.raises(ValueError):
            operation(reader, NOW.replace(tzinfo=None))


def test_initial_profile_is_created_with_utc_default():
    from study.models import ReaderProfile
    from study.services.reviews import get_review_day
    user = get_user_model().objects.create_user(username="new-reader")
    day = get_review_day(user, now=NOW)
    assert day.timezone == "UTC"
    assert ReaderProfile.objects.get(user=user).active_day_id == day.pk


def test_http_identity_strict_fields_csrf_and_private_errors(client, reader, other_reader, publisher, article_factory):
    article = article_factory("Article")
    context_url = f"/api/articles/{article.pk}/reading-context"
    complete_url = f"/api/articles/{article.pk}/complete"
    assert_private(client.post("/api/reviews/today"), 401)
    assert_private(client.get(context_url), 401)
    assert_private(client.post(complete_url, data='{"token":"fake"}', content_type="application/json"), 401)
    client.force_login(reader)
    token = assert_private(client.get(context_url), 200).json()["token"]
    for actor in (other_reader, publisher):
        client.force_login(actor)
        response = client.post(complete_url, data=json.dumps({"token": token}), content_type="application/json")
        assert assert_private(response, 400).json()["error"]["code"] == "token_invalid"
    client.force_login(reader)
    for body in ("{", "[]", "{}", '{"token":1}', json.dumps({"token": token, "interval_days": 100}), json.dumps({"token": token, "user_id": reader.pk})):
        assert_private(client.post(complete_url, data=body, content_type="application/json"), 400)
    assert_private(client.get("/api/articles/not-a-uuid/reading-context"), 400)
    assert_private(client.get(f"/api/articles/{uuid4()}/reading-context"), 404)
    assert_private(client.get("/api/reviews/today"), 405)
    assert_private(client.post("/api/reviews/today", data='{"timezone":"UTC"}', content_type="application/json"), 400)
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(reader)
    assert_private(csrf.post("/api/reviews/today"), 403)
    assert_private(csrf.post(complete_url, data=json.dumps({"token": token}), content_type="application/json"), 403)
    csrf.get("/")
    assert_private(csrf.post("/api/reviews/today", HTTP_X_CSRFTOKEN=csrf.cookies["csrftoken"].value), 200)
    response = client.post(complete_url, data=json.dumps({"token": token}), content_type="application/json")
    assert assert_private(response, 200).json()["status"] == "first_read"
    assert assert_private(client.post("/api/reviews/today"), 200).json()["slots"] == []


def test_timezone_endpoint_stages_changes_and_is_private(client, reader):
    from study.services.reviews import get_review_day
    from django.utils import timezone
    get_review_day(reader, now=timezone.now())
    client.force_login(reader)
    response = client.post("/api/timezone", data=json.dumps({"timezone": "UTC"}), content_type="application/json")
    assert assert_private(response, 200).json()["timezone"] == "Asia/Singapore"
    assert response.json()["pending_timezone"] == "UTC"
    for value in ("../UTC", "", "invalid", 1, True):
        assert_private(client.post("/api/timezone", data=json.dumps({"timezone": value}), content_type="application/json"), 400)


def test_unstoreable_doubled_interval_fails_without_mutation(reader, article_factory):
    from study.models import ReviewSchedule
    article = article_factory("Article")
    schedule = due(reader, article, interval=10**100 - 1)
    with pytest.raises(ValueError, match="100"):
        finish(reader, article)
    schedule.refresh_from_db()
    assert int(schedule.interval_days) == 10**100 - 1
    assert schedule.generation == 1
    assert schedule.next_due_date == date(2026, 9, 14)


def test_database_review_uniqueness_and_bounds(reader, article_factory):
    from study.models import ReviewDay, ReviewSchedule, ReviewSlot
    article = article_factory("Article")
    schedule = due(reader, article)
    with pytest.raises(IntegrityError), transaction.atomic():
        due(reader, article)
    for fields in ({"interval_days": 0}, {"generation": 0}):
        with pytest.raises(IntegrityError), transaction.atomic():
            ReviewSchedule.objects.filter(pk=schedule.pk).update(**fields)
    day = ReviewDay.objects.create(user=reader, local_date=NOW.date(), timezone="UTC", next_boundary_at=NOW + timedelta(days=1))
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewDay.objects.create(user=reader, local_date=NOW.date(), timezone="UTC", next_boundary_at=NOW + timedelta(days=1))
    ReviewSlot.objects.create(day=day, schedule=schedule, article=article, ordinal=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewSlot.objects.create(day=day, schedule=schedule, article=article, ordinal=1)
    other = article_factory("Other")
    other_schedule = due(reader, other)
    for ordinal in (0, 3, -1):
        with pytest.raises(IntegrityError), transaction.atomic():
            ReviewSlot.objects.create(day=day, schedule=other_schedule, article=other, ordinal=ordinal)


def test_http_expiry_size_and_unexpected_errors_are_private(client, reader, article_factory, monkeypatch, settings):
    from study.services.reviews import reading_context
    from study.views import reviews
    article = article_factory("Article")
    token = reading_context(reader, article.pk, now=NOW)["token"]
    client.force_login(reader)
    monkeypatch.setattr(reviews.timezone, "now", lambda: NOW + timedelta(hours=24, seconds=1))
    response = client.post(f"/api/articles/{article.pk}/complete", data=json.dumps({"token": token}), content_type="application/json")
    assert assert_private(response, 400).json()["error"]["code"] == "token_expired"
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10
    assert_private(client.post(f"/api/articles/{article.pk}/complete", data=json.dumps({"token": token}), content_type="application/json"), 400)

    def broken(*args, **kwargs):
        raise RuntimeError("Internal detail")

    monkeypatch.setattr(reviews, "reading_context", broken)
    response = assert_private(client.get(f"/api/articles/{article.pk}/reading-context"), 500)
    assert "Internal detail" not in response.content.decode()


def test_suspended_schedules_are_excluded_and_timezone_never_changes_due_dates(reader, article_factory):
    from study.models import ReviewSchedule
    from study.services.reviews import get_review_day, set_timezone
    a, b = article_factory("Active"), article_factory("Suspended")
    active, suspended = due(reader, a), due(reader, b)
    ReviewSchedule.objects.filter(pk=suspended.pk).update(suspended=True)
    day = get_review_day(reader, now=NOW)
    assert list(day.slots.values_list("article_id", flat=True)) == [a.pk]
    set_timezone(reader, "UTC", now=NOW)
    get_review_day(reader, now=day.next_boundary_at)
    active.refresh_from_db()
    assert active.next_due_date == date(2026, 9, 14)


def test_due_review_on_final_date_has_no_later_due_date(reader, article_factory):
    from study.services.reviews import get_review_day
    article = article_factory("Article")
    due(reader, article, due_date=date.max)
    final = datetime.max.replace(tzinfo=UTC)
    assert get_review_day(reader, now=final).slots.count() == 1
    result = finish(reader, article, final)
    assert result["status"] == "reviewed" and result["interval_days"] == "2"
    assert result["next_due_date"] is None


def test_timezone_form_body_after_csrf_is_rejected_as_private_json(reader):
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(reader)
    csrf.get("/")
    response = csrf.post("/api/timezone", data={"timezone": "UTC"}, HTTP_X_CSRFTOKEN=csrf.cookies["csrftoken"].value)
    assert_private(response, 400)
