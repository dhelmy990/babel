import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Article(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=220, unique=True)
    title = models.CharField(max_length=200)
    color = models.CharField(max_length=7)
    markdown = models.TextField()
    rendered_html = models.TextField()
    excerpt = models.CharField(max_length=240)
    sources = models.JSONField(default=list)
    published_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    archived_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    submission_id = models.UUIDField(unique=True)
    submission_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ("-published_at", "-id")
        constraints = [models.CheckConstraint(condition=Q(revision__gte=1), name="article_revision_positive")]


class Asset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="assets")
    logical_name = models.CharField(max_length=500)
    storage_key = models.CharField(max_length=500, unique=True)
    media_type = models.CharField(max_length=40)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class GraphState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name="graph_state_singleton_pk_one")]


class Edge(models.Model):
    source = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="incoming_edges")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("source", "target"), name="edge_source_target_unique"),
            models.CheckConstraint(condition=~Q(source=F("target")), name="edge_no_self_reference"),
        ]


class ArchiveAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "article"), name="archive_access_user_article_unique")]
