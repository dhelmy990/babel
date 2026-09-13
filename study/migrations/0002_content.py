# Generated manually to keep the data migration adjacent to the schema it needs.
import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


def create_graph_state(apps, schema_editor):
    apps.get_model("study", "GraphState").objects.get_or_create(id=1)


class Migration(migrations.Migration):
    dependencies = [("study", "0001_accounts"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(name="Article", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("slug", models.SlugField(max_length=220, unique=True)), ("title", models.CharField(max_length=200)),
            ("color", models.CharField(max_length=7)), ("markdown", models.TextField()),
            ("rendered_html", models.TextField()), ("excerpt", models.CharField(max_length=240)),
            ("sources", models.JSONField(default=list)), ("published_at", models.DateTimeField()),
            ("updated_at", models.DateTimeField()), ("archived_at", models.DateTimeField(blank=True, null=True)),
            ("revision", models.PositiveIntegerField(default=1)), ("submission_id", models.UUIDField(unique=True)),
            ("submission_hash", models.CharField(max_length=64)),
        ], options={"ordering": ("-published_at", "-id")}),
        migrations.AddConstraint(model_name="article", constraint=models.CheckConstraint(condition=Q(("revision__gte", 1)), name="article_revision_positive")),
        migrations.CreateModel(name="GraphState", fields=[("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False))]),
        migrations.AddConstraint(model_name="graphstate", constraint=models.CheckConstraint(condition=Q(("id", 1)), name="graph_state_singleton_pk_one")),
        migrations.CreateModel(name="Asset", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("logical_name", models.CharField(max_length=500)), ("storage_key", models.CharField(max_length=500, unique=True)),
            ("media_type", models.CharField(max_length=40)), ("sha256", models.CharField(max_length=64)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("article", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="assets", to="study.article")),
        ]),
        migrations.CreateModel(name="Edge", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("source", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="outgoing_edges", to="study.article")),
            ("target", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="incoming_edges", to="study.article")),
        ]),
        migrations.AddConstraint(model_name="edge", constraint=models.UniqueConstraint(fields=("source", "target"), name="edge_source_target_unique")),
        migrations.AddConstraint(model_name="edge", constraint=models.CheckConstraint(condition=~Q(source=F("target")), name="edge_no_self_reference")),
        migrations.CreateModel(name="ArchiveAccess", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("granted_at", models.DateTimeField(auto_now_add=True)),
            ("article", models.ForeignKey(on_delete=models.deletion.CASCADE, to="study.article")),
            ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name="archiveaccess", constraint=models.UniqueConstraint(fields=("user", "article"), name="archive_access_user_article_unique")),
        migrations.RunPython(create_graph_state, migrations.RunPython.noop),
    ]
