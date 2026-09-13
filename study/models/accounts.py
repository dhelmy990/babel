from django.conf import settings
from django.db import models


class ReaderProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    timezone = models.CharField(max_length=63, default="UTC")
    pending_timezone = models.CharField(max_length=63, null=True, blank=True)


class PublisherIdentity(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    google_subject = models.CharField(max_length=191, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="publisher_identity_singleton_pk_one"
            )
        ]
