from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.test import override_settings


@pytest.mark.django_db
def test_home_is_public_and_contains_the_approved_shell(client):
    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Diego Helmy's study notes" in content
    assert "dhelmy.stream" in content
    assert 'href="/galaxy"' in content
    assert (
        "A compilation of my various learnings about C++, finance, and model "
        "serving and model development."
    ) in content
    assert 'href="https://github.com/dhelmy990"' in content
    assert 'href="https://www.linkedin.com/in/dhelmy990/"' in content


@pytest.mark.django_db
def test_home_has_no_sample_content_or_anonymous_personal_controls(client):
    response = client.get("/")

    content = response.content.decode()
    assert "Choose this layout" not in content
    assert "Who owns this memory?" not in content
    assert "My notes" not in content
    assert "Review today" not in content
    assert "sample" not in content.lower()


@pytest.mark.django_db
def test_home_explains_when_no_articles_are_published(client):
    response = client.get("/")

    assert "No articles published yet." in response.content.decode()


@pytest.mark.django_db
def test_healthz_queries_the_database(client, django_assert_num_queries):
    with django_assert_num_queries(1):
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_healthz_reports_database_unavailability(client):
    with patch(
        "study.views.pages.connection.cursor",
        side_effect=OperationalError("database unavailable"),
    ):
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db
def test_routes_do_not_append_slashes(client):
    response = client.get("/healthz/")

    assert response.status_code == 404


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_not_found_page_uses_the_site_shell(client):
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert "dhelmy.stream" in response.content.decode()
