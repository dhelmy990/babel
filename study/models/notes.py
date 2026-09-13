"""Private plaintext annotations; coordinates are surface-relative CSS pixels."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Length
from django.db.models.lookups import LessThanOrEqual


class Note(models.Model):
    class Kind(models.TextChoices):
        STICKY = "sticky"
        TEXT = "text"

    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    article = models.ForeignKey("study.Article", on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField(max_length=6, choices=Kind.choices)
    text = models.TextField(max_length=20000)
    x = models.FloatField(null=True, blank=True)
    y = models.FloatField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("user", "article", "created_at", "id"), name="note_owner_article_order")]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=("sticky", "text")), name="note_kind_valid"),
            models.CheckConstraint(condition=Q(version__gte=1), name="note_version_positive"),
            models.CheckConstraint(condition=LessThanOrEqual(Length("text"), 20000), name="note_text_length"),
            models.CheckConstraint(
                # PostgreSQL NaN sorts above every number, so the upper bounds
                # also reject NaN as well as positive infinity.
                condition=Q(x__isnull=True, y__isnull=True) | Q(
                    x__isnull=False, y__isnull=False,
                    x__gte=0, x__lte=1000000, y__gte=0, y__lte=1000000,
                ),
                name="note_coordinates_pair_and_bounds",
            ),
        ]
