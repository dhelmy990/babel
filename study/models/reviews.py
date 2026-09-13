"""Durable per-reader schedules and a frozen daily selection of at most three."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class ReviewSchedule(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    article = models.ForeignKey("study.Article", on_delete=models.CASCADE)
    interval_days = models.DecimalField(max_digits=100, decimal_places=0, default=1)
    next_due_date = models.DateField(null=True, blank=True)
    last_completed_at = models.DateTimeField()
    generation = models.PositiveIntegerField(default=1)
    suspended = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "article"), name="review_schedule_user_article_unique"),
            models.CheckConstraint(condition=Q(generation__gte=1), name="review_generation_positive"),
            models.CheckConstraint(condition=Q(interval_days__gte=1, interval_days__lte=Decimal("9" * 100)), name="review_interval_bounds"),
        ]
        indexes = [models.Index(fields=("user", "suspended", "next_due_date"), name="review_user_suspended_due")]


class ReviewDay(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    local_date = models.DateField()
    timezone = models.CharField(max_length=63)
    created_at = models.DateTimeField(auto_now_add=True)
    next_boundary_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "local_date"), name="review_day_user_date_unique")]


class ReviewSlot(models.Model):
    day = models.ForeignKey(ReviewDay, on_delete=models.CASCADE, related_name="slots")
    ordinal = models.PositiveSmallIntegerField()
    article = models.ForeignKey("study.Article", on_delete=models.CASCADE)
    schedule = models.ForeignKey(ReviewSchedule, on_delete=models.CASCADE)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("ordinal",)
        constraints = [
            models.UniqueConstraint(fields=("day", "ordinal"), name="review_slot_day_ordinal_unique"),
            models.UniqueConstraint(fields=("day", "article"), name="review_slot_day_article_unique"),
            models.CheckConstraint(condition=Q(ordinal__gte=0, ordinal__lte=2), name="review_slot_ordinal_bounds"),
        ]
