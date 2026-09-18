"""Frozen daily reviews, calendar scheduling, and generation-bound completions.

Day selection locks Profile -> candidate Articles (UUID order). Reading and
completion finish that preflight first, then lock Profile -> one Article ->
review rows. Archival needs only Article -> review rows, never a Profile lock.
"""
import math
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404

from study.models import Article, ReaderProfile, ReviewDay, ReviewSchedule, ReviewSlot
from study.services.identity import profile_for


TERMINAL_BOUNDARY = datetime.max.replace(tzinfo=UTC)
TOKEN_FIELDS = {"user_id", "article_id", "generation", "issued_at"}


class ReadingTokenError(ValueError):
    code = "token_invalid"


class ReadingTokenExpired(ReadingTokenError):
    code = "token_expired"


def _clock(user, now):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Sign in is required")
    return utc_clock(now)


def utc_clock(now):
    """Normalize an explicit aware service clock without overflowing terminal dates."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("An aware service clock is required")
    try:
        return now.astimezone(UTC)
    except OverflowError:
        return TERMINAL_BOUNDARY if now.year == date.max.year else datetime.min.replace(tzinfo=UTC)


def _article_id(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Invalid article UUID")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid article UUID") from exc


def local_date_for(now, timezone_name):
    try:
        return now.astimezone(ZoneInfo(timezone_name)).date()
    except OverflowError:
        return date.max if now.year == date.max.year else date.min


def next_day_boundary(local_date, timezone_name):
    if local_date == date.max:
        return TERMINAL_BOUNDARY
    midnight = datetime.combine(local_date + timedelta(days=1), time.min, ZoneInfo(timezone_name))
    try:
        return midnight.astimezone(UTC)
    except OverflowError:
        return TERMINAL_BOUNDARY


def _effective_boundary(day, timezone_name):
    # A reused historical day keeps its original snapshot, but the reader's
    # effective timezone determines how long that date now remains active.
    if day.local_date == date.max:
        return TERMINAL_BOUNDARY
    if day.timezone == timezone_name:
        return day.next_boundary_at
    return next_day_boundary(day.local_date, timezone_name)


def review_day_expired(day, timezone_name, now):
    return day.local_date != date.max and now >= _effective_boundary(day, timezone_name)


def _locked_profile(user):
    profile_for(user)  # get_or_create handles simultaneous first profile creation.
    return ReaderProfile.objects.select_for_update().get(user=user)


def _due_schedules(user_id, local_date):
    return ReviewSchedule.objects.filter(
        user_id=user_id, suspended=False, article__archived_at__isnull=True,
        next_due_date__lte=local_date,
    ).order_by("next_due_date", "last_completed_at", "article_id")


def _select_slots(day):
    # Lock every due candidate so an archive before selection can yield the
    # next eligible article; existing days never run this selection again.
    candidates = list(_due_schedules(day.user_id, day.local_date).values("pk", "article_id"))
    # Avoid select_for_update on the joined due query: its due-date ordering
    # must not dictate Article lock order or accidentally lock review rows first.
    list(Article.objects.filter(pk__in=[row["article_id"] for row in candidates]).order_by("pk").select_for_update())
    eligible = _due_schedules(day.user_id, day.local_date).filter(pk__in=[row["pk"] for row in candidates])[:3]
    ReviewSlot.objects.bulk_create([
        ReviewSlot(day=day, ordinal=ordinal, article_id=schedule.article_id, schedule=schedule)
        for ordinal, schedule in enumerate(eligible)
    ])


def _resolve_day(profile, now):
    day = profile.active_day
    if day is not None and not review_day_expired(day, profile.timezone, now):
        return day
    if profile.pending_timezone is not None:
        profile.timezone = profile.pending_timezone
        profile.pending_timezone = None
    local_date = local_date_for(now, profile.timezone)
    day, created = ReviewDay.objects.get_or_create(
        user_id=profile.user_id, local_date=local_date,
        defaults={"timezone": profile.timezone, "next_boundary_at": next_day_boundary(local_date, profile.timezone)},
    )
    if created:
        _select_slots(day)
    profile.active_day = day
    profile.save(update_fields=("timezone", "pending_timezone", "active_day"))
    return day


def get_review_day(user, *, now) -> ReviewDay:
    now = _clock(user, now)
    with transaction.atomic():
        return _resolve_day(_locked_profile(user), now)


def set_timezone(user, timezone_name, *, now) -> ReaderProfile:
    now = _clock(user, now)
    if not isinstance(timezone_name, str) or len(timezone_name) > 63:
        raise ValueError("Timezone must be an IANA timezone name")
    try:
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Timezone must be an IANA timezone name") from exc
    with transaction.atomic():
        profile = _locked_profile(user)
        if profile.active_day_id is None:
            profile.timezone = timezone_name
            profile.pending_timezone = None
        else:
            # Resolve an expired day using the earlier preference, then stage
            # this request. A second preference never shortens the active day.
            _resolve_day(profile, now)
            profile.pending_timezone = None if timezone_name == profile.timezone else timezone_name
        profile.save(update_fields=("timezone", "pending_timezone"))
        return profile


def _with_article(user, article_id, now, operation):
    while True:
        day = get_review_day(user, now=now)  # Completed transaction, no Article lock held.
        with transaction.atomic():
            profile = _locked_profile(user)
            if profile.active_day_id != day.pk or review_day_expired(day, profile.timezone, now):
                continue  # Release locks before any new multi-Article selection.
            try:
                article = Article.objects.select_for_update().get(pk=article_id, archived_at__isnull=True)
            except Article.DoesNotExist as exc:
                raise Http404 from exc
            schedule = ReviewSchedule.objects.select_for_update().filter(user=user, article=article).first()
            return operation(profile, day, article, schedule)


def _eligibility(schedule, day, completed_date):
    if schedule is None:
        return "first_read"
    if schedule.suspended or schedule.next_due_date is None or schedule.next_due_date > completed_date:
        return "not_due"
    if day.slots.filter(schedule=schedule, completed_at__isnull=True, cancelled_at__isnull=True).exists():
        return "review_due"
    return "not_selected"


def reading_context(user, article_id, *, now) -> dict:
    now = _clock(user, now)
    article_id = _article_id(article_id)

    def context(profile, day, article, schedule):
        status = _eligibility(schedule, day, local_date_for(now, profile.timezone))
        token = signing.Signer(salt="study-reading").sign_object({
            "user_id": user.pk, "article_id": str(article.pk),
            "generation": schedule.generation if schedule else 0, "issued_at": now.timestamp(),
        })
        return {"token": token, "eligible": status in ("first_read", "review_due"), "status": status}

    return _with_article(user, article_id, now, context)


def _token_generation(user, article_id, token, now):
    if not isinstance(token, str):
        raise ReadingTokenError("Invalid reading token")
    try:
        data = signing.Signer(salt="study-reading").unsign_object(token)
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise ReadingTokenError("Invalid reading token") from exc
    if (
        not isinstance(data, dict) or set(data) != TOKEN_FIELDS
        or type(data["user_id"]) is not int or data["user_id"] != user.pk
        or data["article_id"] != str(article_id)
        or type(data["generation"]) is not int or not 0 <= data["generation"] <= 2147483647
        or type(data["issued_at"]) not in (int, float)
    ):
        raise ReadingTokenError("Invalid reading token")
    try:
        valid_time = math.isfinite(data["issued_at"])
        age = now.timestamp() - data["issued_at"]
    except OverflowError as exc:
        raise ReadingTokenError("Invalid reading token") from exc
    if not valid_time or age < 0:
        raise ReadingTokenError("Invalid reading token")
    if age > 24 * 60 * 60:
        raise ReadingTokenExpired("Reading token has expired")
    return data["generation"]


def _result(status, schedule, completed_date):
    return {
        "status": status, "interval_days": str(int(schedule.interval_days)),
        "next_due_date": schedule.next_due_date.isoformat() if schedule.next_due_date else None,
        "days_until_due": max(0, (schedule.next_due_date - completed_date).days) if schedule.next_due_date else None,
        "generation": schedule.generation,
    }


def _due_date(completed_date, interval):
    # Decimal multiplication/addition would silently round above 28 digits.
    # Python integers remain exact, and subtraction bounds timedelta safely.
    remaining = (date.max - completed_date).days
    return None if interval > remaining else completed_date + timedelta(days=interval)


def complete_article(user, article_id, *, token, now) -> dict:
    now = _clock(user, now)
    article_id = _article_id(article_id)
    generation = _token_generation(user, article_id, token, now)

    def complete(profile, day, article, schedule):
        completed_date = local_date_for(now, profile.timezone)
        if schedule is not None and generation != schedule.generation:
            return _result("already_processed", schedule, completed_date)
        if schedule is None:
            if generation != 0:
                raise ReadingTokenError("Invalid reading token generation")
            schedule = ReviewSchedule.objects.create(
                user=user, article=article, interval_days=1, generation=1,
                next_due_date=_due_date(completed_date, 1), last_completed_at=now,
            )
            return _result("first_read", schedule, completed_date)
        status = _eligibility(schedule, day, completed_date)
        if status != "review_due":
            return _result(status, schedule, completed_date)
        interval = int(schedule.interval_days) * 2
        if interval >= 10**100:
            raise ValueError("Review interval exceeds the 100-digit storage limit")
        if schedule.generation == 2147483647:
            raise ValueError("Review generation storage limit reached")
        schedule.interval_days = interval
        schedule.generation += 1
        schedule.next_due_date = _due_date(completed_date, interval)
        schedule.last_completed_at = now
        schedule.save(update_fields=("interval_days", "generation", "next_due_date", "last_completed_at"))
        # This review also resolves carried assignments from previously selected
        # days, which can become active again after a timezone change.
        ReviewSlot.objects.filter(schedule=schedule, completed_at__isnull=True, cancelled_at__isnull=True).update(completed_at=now)
        return _result("reviewed", schedule, completed_date)

    return _with_article(user, article_id, now, complete)


def today_payload(user, *, now) -> dict:
    now = _clock(user, now)
    while True:
        day = get_review_day(user, now=now)
        with transaction.atomic():
            profile = _locked_profile(user)
            if profile.active_day_id != day.pk or review_day_expired(day, profile.timezone, now):
                continue
            return {
                "date": day.local_date.isoformat(), "timezone": profile.timezone,
                "slots": [
                    {"ordinal": slot.ordinal, "article_id": str(slot.article_id),
                     "slug": slot.article.slug, "title": slot.article.title,
                     "completed": slot.completed_at is not None, "cancelled": slot.cancelled_at is not None}
                    for slot in day.slots.select_related("article").order_by("ordinal")
                ],
            }


def suspend_article_reviews(article, *, now):
    """Caller holds the Article lock; never acquire a reader's Profile here."""
    ReviewSchedule.objects.filter(article=article).update(suspended=True)
    ReviewSlot.objects.filter(article=article, completed_at__isnull=True, cancelled_at__isnull=True).update(cancelled_at=now)
