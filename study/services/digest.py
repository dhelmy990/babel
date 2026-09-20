"""One owner digest per Singapore date; frozen, leased, bounded provider retries."""
from datetime import datetime, time, timedelta
from time import monotonic
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Case, CharField, Q, Value, When
from django.template.loader import render_to_string

from study.email_delivery import PermanentDeliveryError, RetryableDeliveryError, valid_message_id
from study.models import Article, Digest, PublisherIdentity, ReaderProfile
from study.services.identity import OWNER_EMAIL, is_publisher
from study.services.reviews import (
    TERMINAL_BOUNDARY, get_review_day, local_date_for, next_day_boundary,
    review_day_expired, utc_clock,
)


SINGAPORE = "Asia/Singapore"
TERMINAL = {"sent", "skipped", "failed", "unknown"}


def _after(now, interval):
    try:
        return now + interval
    except OverflowError:
        return TERMINAL_BOUNDARY


def _delivery_day(now):
    day = local_date_for(now, SINGAPORE)
    opening = datetime.combine(day, time(9), ZoneInfo(SINGAPORE))
    return day if opening <= now < next_day_boundary(day, SINGAPORE) else None


def _owner():
    binding = PublisherIdentity.objects.select_related("user").filter(pk=1).first()
    return binding.user if binding and is_publisher(binding.user) else None


def _retire_expired(now):
    # Standalone housekeeping takes Digest locks only; it never requests Profile
    # or Article locks afterward, and never starts a provider request.
    Digest.objects.filter(status__in=("pending", "sending")).filter(
        Q(day__lt=local_date_for(now, SINGAPORE)) | Q(retry_until__lte=now)
    ).update(
        status=Case(When(first_attempt_at__isnull=True, then=Value("skipped")), default=Value("unknown"), output_field=CharField()),
        last_error="delivery_window_expired",
    )


def _with_current_slots(user, now, operation):
    while True:
        day = get_review_day(user, now=now)  # Its selection transaction finishes here.
        with transaction.atomic():
            profile = ReaderProfile.objects.select_for_update().get(user=user)
            if profile.active_day_id != day.pk or review_day_expired(day, profile.timezone, now):
                continue
            article_ids = list(day.slots.values_list("article_id", flat=True))
            list(Article.objects.filter(pk__in=article_ids).order_by("pk").select_for_update())
            slots = list(day.slots.filter(
                completed_at__isnull=True, cancelled_at__isnull=True,
                article__archived_at__isnull=True, schedule__suspended=False,
            ).select_related("article").order_by("ordinal"))
            return operation(profile, day, slots)


def prepare_owner_digest(*, now) -> Digest | None:
    now = utc_clock(now)
    _retire_expired(now)
    delivery_day = _delivery_day(now)
    user = _owner()
    if delivery_day is None or user is None:
        return None
    previous = Digest.objects.filter(user=user, day=delivery_day).first()
    if previous is not None:
        return previous

    def prepare(profile, day, slots):
        if not is_publisher(user):
            return None
        digest, _ = Digest.objects.select_for_update().get_or_create(
            user=user, day=delivery_day,
            defaults={"idempotency_key": f"study-review/{user.pk}/{delivery_day.isoformat()}",
                      "status": "pending" if slots else "skipped"},
        )
        return digest

    return _with_current_slots(user, now, prepare)


def _articles(slots):
    base = settings.PUBLIC_BASE_URL
    sender = settings.REVIEW_FROM_EMAIL
    if not isinstance(base, str) or not isinstance(sender, str) or not sender.strip() or any(c in sender for c in "\r\n\x00"):
        raise ImproperlyConfigured("Invalid digest sender or public base URL")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ImproperlyConfigured("Invalid PUBLIC_BASE_URL")
    return [{"title": slot.article.title, "url": base.rstrip("/") + "/" + slot.article.slug} for slot in slots]


def preview_owner_digest(*, now):
    """Dry run may freeze a ReviewDay, but creates no Digest or delivery attempt."""
    now = utc_clock(now)
    delivery_day, user = _delivery_day(now), _owner()
    if delivery_day is None or user is None:
        return None

    def preview(profile, day, slots):
        if not is_publisher(user):
            return None
        return {"owner": OWNER_EMAIL, "day": delivery_day.isoformat(), "review_date": day.local_date.isoformat(), "articles": _articles(slots)}

    return _with_current_slots(user, now, preview)


def _freeze(profile, day, slots, delivery_day):
    context = {"articles": _articles(slots), "review_date": day.local_date.isoformat(), "timezone": profile.timezone}
    return {"from": settings.REVIEW_FROM_EMAIL, "to": [OWNER_EMAIL],
            "subject": f"Study review — {delivery_day.isoformat()}",
            "text": render_to_string("study/digest.txt", context),
            "html": render_to_string("study/digest.html", context)}


def _result(digest):
    return digest.status if digest.status in TERMINAL else "retry"


def _close(digest, status, code):
    digest.status, digest.last_error = status, code
    digest.save(update_fields=("status", "last_error"))
    return _result(digest)


def _claim(digest, user, now, build_payload=None):
    if digest.status in TERMINAL:
        return _result(digest)
    if digest.day != local_date_for(now, SINGAPORE) or (digest.retry_until is not None and now >= digest.retry_until):
        return _close(digest, "unknown" if digest.first_attempt_at else "skipped", "delivery_window_expired")
    if not is_publisher(user):
        return _close(digest, "unknown" if digest.first_attempt_at else "skipped", "owner_ineligible")
    if _delivery_day(now) is None:
        return "retry"
    if digest.status == "sending" and digest.lease_until > now:
        return "retry"
    first = digest.first_attempt_at is None
    if first:
        payload = build_payload()
        if payload is None:
            return _close(digest, "skipped", "no_outstanding_reviews")
        digest.payload = payload
        digest.first_attempt_at = now
        digest.retry_until = min(_after(now, timedelta(hours=23)), next_day_boundary(digest.day, SINGAPORE))
    lease = _after(now, timedelta(minutes=2))
    if digest.lease_until is not None and lease <= digest.lease_until:
        lease = _after(digest.lease_until, timedelta(microseconds=1))
    digest.lease_until, digest.status, digest.last_error = lease, "sending", ""
    digest.save(update_fields=("payload", "first_attempt_at", "retry_until", "lease_until", "status", "last_error"))
    return digest, first


def _settle(digest_id, lease, *, status, code="", provider_id=None):
    with transaction.atomic():
        digest = Digest.objects.select_for_update().get(pk=digest_id)
        # A matching late acknowledgment can confirm delivery after lease expiry.
        # A replaced claim or any terminal transition always fences stale results.
        if digest.status == "sending" and digest.lease_until == lease:
            digest.status, digest.last_error = status, code
            fields = ["status", "last_error"]
            if provider_id is not None:
                digest.provider_id = provider_id
                fields.append("provider_id")
            digest.save(update_fields=fields)
        return _result(digest)


def deliver_digest(digest_id, *, now, delivery) -> str:
    started = monotonic()
    now = utc_clock(now)
    initial = Digest.objects.select_related("user").get(pk=digest_id)
    user = initial.user
    if initial.first_attempt_at is None and initial.status not in TERMINAL and _delivery_day(now) == initial.day and is_publisher(user):
        def first_claim(profile, day, slots):
            digest = Digest.objects.select_for_update().get(pk=digest_id)
            return _claim(digest, user, now, lambda: _freeze(profile, day, slots, digest.day) if slots else None)
        claimed = _with_current_slots(user, now, first_claim)
    else:
        with transaction.atomic():
            claimed = _claim(Digest.objects.select_for_update().get(pk=digest_id), user, now)
    if isinstance(claimed, str):
        return claimed
    digest, first = claimed
    send_now = _after(now, timedelta(seconds=max(0, monotonic() - started)))
    if send_now >= digest.retry_until or local_date_for(send_now, SINGAPORE) != digest.day:
        return _settle(digest.pk, digest.lease_until, status="skipped" if first else "unknown", code="expired_before_send")
    if send_now >= digest.lease_until:
        return _settle(digest.pk, digest.lease_until, status="pending", code="lease_expired_before_send")
    try:
        provider_id = delivery.send(digest.payload, digest.idempotency_key)
        if not valid_message_id(provider_id):
            raise RetryableDeliveryError("invalid_response")
    except PermanentDeliveryError as exc:
        return _settle(digest.pk, digest.lease_until, status="failed", code=exc.code)
    except Exception as exc:
        code = exc.code if isinstance(exc, RetryableDeliveryError) else "transport_error"
        finished_now = _after(now, timedelta(seconds=max(0, monotonic() - started)))
        expired = finished_now >= digest.retry_until or local_date_for(finished_now, SINGAPORE) != digest.day
        return _settle(digest.pk, digest.lease_until, status="unknown" if expired else "pending", code=code)
    return _settle(digest.pk, digest.lease_until, status="sent", provider_id=provider_id)
