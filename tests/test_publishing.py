from uuid import uuid4

import pytest


@pytest.mark.django_db
def test_authored_first_heading_with_image_survives_repeated_saves(publisher):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article, update_article

    image = BytesIO()
    Image.new("RGB", (1, 1)).save(image, "PNG")
    article = publish_article(
        publisher, title="Illustrated notes", color="#1a5276", submission_id=uuid4(),
        markdown="# A diagram ![A & B](images/diagram.png)\n\nExplanation.",
        images={"images/diagram.png": image.getvalue()},
    )
    for revision in (1, 2):
        article = update_article(
            publisher, article.pk, expected_revision=revision, title="Renamed notes", color=article.color,
            markdown=article.markdown, images={},
        )
        assert '<h1>A diagram <img src="/assets/' in article.rendered_html
        assert 'alt="A &amp; B"' in article.rendered_html


@pytest.mark.django_db
def test_failed_image_reference_does_not_publish(publisher):
    from study.models import Article
    from study.services.content import publish_article

    with pytest.raises(ValueError, match="Missing image"):
        publish_article(
            publisher,
            title="Ownership",
            color="#1a5276",
            markdown="# Ownership\n\n![Sketch](images/missing.png)",
            images={},
            submission_id=uuid4(),
        )
    assert Article.objects.count() == 0


@pytest.mark.django_db
def test_publish_is_idempotent_and_keeps_initial_url_and_date(publisher):
    from study.services.content import publish_article

    submission_id = uuid4()
    first = publish_article(
        publisher, title="Ownership", color="#1a5276", markdown="# Ownership\n\nBody.",
        images={}, submission_id=submission_id,
    )
    retry = publish_article(
        publisher, title="Ownership", color="#1a5276", markdown="# Ownership\n\nBody.",
        images={}, submission_id=submission_id,
    )

    assert retry.pk == first.pk
    assert retry.slug == "ownership"
    assert retry.published_at == first.published_at


@pytest.mark.django_db
def test_reusing_submission_id_for_different_content_conflicts(publisher):
    from study.services.content import SubmissionConflict, publish_article

    submission_id = uuid4()
    publish_article(publisher, title="Ownership", color="#1a5276", markdown="# Ownership", images={}, submission_id=submission_id)

    with pytest.raises(SubmissionConflict):
        publish_article(publisher, title="Other", color="#1a5276", markdown="# Other", images={}, submission_id=submission_id)


@pytest.mark.django_db
def test_submission_id_rejects_distinct_original_images_that_normalize_identically(publisher):
    from io import BytesIO
    from PIL import Image, PngImagePlugin
    from study.services.content import SubmissionConflict, publish_article

    plain, annotated = BytesIO(), BytesIO()
    Image.new("RGB", (2, 2), "blue").save(plain, "PNG")
    info = PngImagePlugin.PngInfo()
    info.add_text("comment", "different upload bytes")
    Image.new("RGB", (2, 2), "blue").save(annotated, "PNG", pnginfo=info)
    submission_id = uuid4()
    kwargs = dict(title="Images", color="#1a5276", markdown="# Images\n\n![x](images/x.png)", submission_id=submission_id)
    publish_article(publisher, images={"images/x.png": plain.getvalue()}, **kwargs)

    with pytest.raises(SubmissionConflict):
        publish_article(publisher, images={"images/x.png": annotated.getvalue()}, **kwargs)


@pytest.mark.django_db
def test_update_uses_revision_cas_and_preserves_stable_article_identity(publisher):
    from study.services.content import RevisionConflict, publish_article, update_article

    article = publish_article(
        publisher, title="Ownership", color="#1a5276", markdown="# Ownership\n\nOriginal.",
        images={}, submission_id=uuid4(),
    )
    published_at, slug = article.published_at, article.slug
    revised = update_article(
        publisher, article.pk, expected_revision=1, title="Renamed", color="#112233",
        markdown="# Renamed\n\nUpdated.", images={},
    )

    assert (revised.slug, revised.published_at, revised.revision) == (slug, published_at, 2)
    with pytest.raises(RevisionConflict):
        update_article(
            publisher, article.pk, expected_revision=1, title="Stale", color="#112233",
            markdown="# Stale", images={},
        )


@pytest.mark.django_db
@pytest.mark.parametrize("title", ["", "Galaxy", "***"])
def test_publish_rejects_empty_reserved_and_empty_slug_titles(publisher, title):
    from study.services.content import publish_article

    with pytest.raises(ValueError):
        publish_article(
            publisher, title=title, color="#1a5276", markdown="Body without a heading", images={}, submission_id=uuid4(),
        )


@pytest.mark.django_db
def test_storage_files_are_removed_when_database_write_fails(publisher, monkeypatch, settings, tmp_path):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article

    settings.PRIVATE_MEDIA_ROOT = tmp_path
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    monkeypatch.setattr("study.services.content.Article.save", lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database failure")))

    with pytest.raises(RuntimeError, match="database failure"):
        publish_article(
            publisher, title="Storage", color="#1a5276", markdown="# Storage\n\n![x](images/x.png)",
            images={"images/x.png": output.getvalue()}, submission_id=uuid4(),
        )
    assert not list(tmp_path.rglob("*"))


@pytest.mark.django_db
def test_update_can_reuse_its_own_existing_image_without_writing_another_asset(publisher):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article, update_article

    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    article = publish_article(
        publisher, title="Images", color="#1a5276", markdown="# Images\n\n![blue](images/blue.png)",
        images={"images/blue.png": output.getvalue()}, submission_id=uuid4(),
    )
    existing_asset = article.assets.get()
    revised = update_article(
        publisher, article.pk, expected_revision=1, title="Images edited", color="#1a5276",
        markdown="# Images edited\n\n![blue](images/blue.png)\n\nMore.", images={},
    )

    assert revised.assets.count() == 1
    assert str(existing_asset.pk) in revised.rendered_html


@pytest.mark.django_db
def test_archived_article_is_hidden_from_public_but_visible_to_publisher(publisher, client):
    from django.utils import timezone
    from study.services.content import publish_article

    article = publish_article(
        publisher, title="Archived", color="#1a5276", markdown="# Archived", images={}, submission_id=uuid4(),
    )
    article.archived_at = timezone.now()
    article.save(update_fields=["archived_at"])

    assert client.get("/archived").status_code == 404
    client.force_login(publisher)
    publisher_response = client.get("/archived")
    assert publisher_response.status_code == 200
    assert publisher_response["Cache-Control"] == "private, no-store"


@pytest.mark.django_db
def test_publish_and_update_normalized_upload_paths_write_and_render_the_replacement(publisher):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article, update_article

    first, second = BytesIO(), BytesIO()
    Image.new("RGB", (2, 2), "blue").save(first, "PNG")
    Image.new("RGB", (2, 2), "red").save(second, "PNG")
    article = publish_article(
        publisher, title="Normalized", color="#1a5276", markdown="# Normalized\n\n![x](./images/x.png)",
        images={"./images/x.png": first.getvalue()}, submission_id=uuid4(),
    )
    original = article.assets.get()
    assert original.logical_name == "images/x.png"
    assert str(original.pk) in article.rendered_html

    revised = update_article(
        publisher, article.pk, expected_revision=1, title="Normalized", color="#1a5276",
        markdown="# Normalized\n\n![x](images/x.png)", images={"./images/x.png": second.getvalue()},
    )
    newest = revised.assets.order_by("created_at").last()
    assert revised.assets.count() == 2
    assert newest.logical_name == "images/x.png"
    assert newest.sha256 != original.sha256
    assert str(newest.pk) in revised.rendered_html


@pytest.mark.django_db
def test_update_only_loads_images_referenced_by_the_new_body(publisher):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article, update_article

    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    data = output.getvalue()
    article = publish_article(
        publisher, title="History", color="#1a5276", markdown="# History\n\n![0](images/0.png)",
        images={"images/0.png": data}, submission_id=uuid4(),
    )
    for number in range(1, 21):
        article = update_article(
            publisher, article.pk, expected_revision=number, title="History", color="#1a5276",
            markdown=f"# History\n\n![{number}](images/{number}.png)", images={f"images/{number}.png": data},
        )

    revised = update_article(
        publisher, article.pk, expected_revision=21, title="History", color="#1a5276",
        markdown="# History\n\n![new](images/new.png)", images={"images/new.png": data},
    )
    assert revised.revision == 22
    assert "images/new.png" == revised.assets.order_by("created_at").last().logical_name
