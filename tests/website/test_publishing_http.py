from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client


@pytest.mark.django_db
def test_preview_and_save_retain_distinct_body_h1_and_suppress_legacy_title_on_rename(publisher, client):
    from study.services.content import publish_article, update_article

    client.force_login(publisher)
    for first_heading in ("Opening section", "Original title"):
        article = publish_article(
            publisher, title="Original title" if first_heading == "Original title" else "Structured notes",
            color="#1a5276", markdown=f"# {first_heading}\n\nBody.", images={}, submission_id=uuid4(),
        )
        expected = "<h1>Opening section</h1>" if first_heading == "Opening section" else "<p>Body.</p>"
        assert expected in article.rendered_html
        preview = client.post("/api/articles/preview", {
            "article_id": str(article.pk), "title": "Renamed notes", "color": article.color, "markdown": article.markdown,
        })
        assert preview.status_code == 200
        assert expected in preview.json()["html"]
        assert "<h1>Original title</h1>" not in preview.json()["html"]
        revised = update_article(
            publisher, article.pk, expected_revision=article.revision, title="Renamed notes", color=article.color,
            markdown=article.markdown, images={},
        )
        assert expected in revised.rendered_html
        assert "<h1>Original title</h1>" not in revised.rendered_html
        revised_again = update_article(
            publisher, article.pk, expected_revision=revised.revision, title="Renamed once more", color=article.color,
            markdown=article.markdown, images={},
        )
        assert expected in revised_again.rendered_html
        assert "<h1>Original title</h1>" not in revised_again.rendered_html


@pytest.mark.django_db
def test_article_write_api_requires_authentication_csrf_and_publisher(publisher):
    client = Client(enforce_csrf_checks=True)
    payload = {"title": "HTTP", "color": "#1a5276", "markdown": "# HTTP", "submission_id": str(uuid4())}
    assert client.post("/api/articles", payload).status_code == 401

    reader = get_user_model().objects.create_user(username="reader-http", email="reader@example.com")
    client.force_login(reader)
    client.get("/")
    token = client.cookies["csrftoken"].value
    denied = client.post("/api/articles", payload, HTTP_X_CSRFTOKEN=token)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "publisher_required"

    client.force_login(publisher)
    missing_csrf = client.post("/api/articles", payload)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"


@pytest.mark.django_db
def test_article_api_publishes_and_public_page_refreshes(publisher):
    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    response = client.post(
        "/api/articles", {"title": "HTTP", "color": "#1a5276", "markdown": "# HTTP\n\nVisible", "submission_id": str(uuid4())},
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert response.status_code == 201
    public = Client().get("/http")
    assert public.status_code == 200
    assert "Visible" in public.content.decode()


@pytest.mark.django_db
def test_preview_is_ephemeral_and_rejects_invalid_utf8_upload(publisher):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from study.models import Article

    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    token = client.cookies["csrftoken"].value
    preview = client.post(
        "/api/articles/preview", {"title": "Preview", "color": "#1a5276", "markdown": "# Preview\n\nText"},
        HTTP_X_CSRFTOKEN=token,
    )
    assert preview.status_code == 200
    assert Article.objects.count() == 0
    invalid = client.post(
        "/api/articles/preview", {"title": "Preview", "color": "#1a5276", "markdown_file": SimpleUploadedFile("bad.md", b"\xff")},
        HTTP_X_CSRFTOKEN=token,
    )
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_preview_validates_metadata_and_can_reuse_the_article_image(publisher):
    from io import BytesIO
    from PIL import Image
    from study.services.content import publish_article

    image = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, "PNG")
    article = publish_article(
        publisher, title="Images", color="#1a5276", markdown="# Images\n\n![x](images/x.png)",
        images={"images/x.png": image.getvalue()}, submission_id=uuid4(),
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    token = client.cookies["csrftoken"].value
    reused = client.post(
        "/api/articles/preview", {"article_id": str(article.pk), "title": "Edited", "color": "#112233", "markdown": "# Edited\n\n![x](images/x.png)"},
        HTTP_X_CSRFTOKEN=token,
    )
    assert reused.status_code == 200
    assert "data:image/png;base64" in reused.json()["html"]
    invalid = client.post(
        "/api/articles/preview", {"title": "", "color": "orange", "markdown": "No title"},
        HTTP_X_CSRFTOKEN=token,
    )
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_oversized_markdown_multipart_is_a_json_validation_error_after_csrf(publisher):
    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    response = client.post(
        "/api/articles/preview",
        {"title": "Large", "color": "#1a5276", "markdown": "x" * (3 * 1024 * 1024)},
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_article"


@pytest.mark.django_db
def test_too_many_multipart_files_returns_json_after_authentication_and_csrf(publisher):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    files = [SimpleUploadedFile(f"{number}.png", b"x", content_type="image/png") for number in range(101)]
    response = client.post(
        "/api/articles/preview",
        {
            "title": "Many", "color": "#1a5276", "markdown": "# Many",
            "images": files,
            "image_path": [f"images/{number}.png" for number in range(101)],
        },
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.django_db
def test_preview_does_not_load_unreferenced_historical_assets(publisher):
    from io import BytesIO
    from PIL import Image
    from study.models import Asset
    from study.services.content import publish_article

    image = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, "PNG")
    article = publish_article(
        publisher, title="Preview history", color="#1a5276", markdown="# Preview history\n\n![x](images/x.png)",
        images={"images/x.png": image.getvalue()}, submission_id=uuid4(),
    )
    Asset.objects.create(
        article=article, logical_name="images/obsolete.png", storage_key="assets/missing",
        media_type="image/png", sha256="0" * 64,
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    client.get("/")
    response = client.post(
        "/api/articles/preview",
        {"article_id": str(article.pk), "title": "Preview history", "color": "#1a5276", "markdown": "# Preview history\n\n![x](images/x.png)"},
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert response.status_code == 200
