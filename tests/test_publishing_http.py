from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client


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
