"""Read-only calendar projections of a reader's existing schedules."""
import calendar
from datetime import date
import re

from django.core.exceptions import PermissionDenied

from study.models import ReaderProfile, ReviewSchedule
from study.services.reviews import local_date_for, utc_clock


def calendar_payload(user, month, *, now):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Sign in is required")
    now = utc_clock(now)
    timezone = ReaderProfile.objects.filter(user=user).values_list("timezone", flat=True).first() or "UTC"
    today = local_date_for(now, timezone)
    if month is None:
        month = f"{today.year:04d}-{today.month:02d}"
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("Month must use YYYY-MM")
    year, number = map(int, month.split("-"))
    start = date(year, number, 1)
    end = date(year, number, calendar.monthrange(year, number)[1])
    schedules = ReviewSchedule.objects.filter(
        user=user, suspended=False, article__archived_at__isnull=True,
        next_due_date__isnull=False,
    ).select_related("article").order_by("next_due_date", "article__title", "article_id")

    def items(rows):
        return [{"article_id": str(row.article_id), "title": row.article.title,
                 "slug": row.article.slug, "color": row.article.color,
                 "due_date": row.next_due_date.isoformat()} for row in rows]

    return {
        "month": month, "today": today.isoformat(), "timezone": timezone,
        "items": items(schedules.filter(next_due_date__range=(start, end))),
        "overdue": items(schedules.filter(next_due_date__lt=today)),
    }
