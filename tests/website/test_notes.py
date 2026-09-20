import json
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client

pytestmark = pytest.mark.django_db


def payload(**changes):
    return {"id": str(uuid4()), "kind": "sticky", "text": "Private <b>plain text</b>", "x": 42.5, "y": 70, **changes}


def post(client, article, data):
    return client.post(f"/api/articles/{article.pk}/notes", data=json.dumps(data), content_type="application/json")


def patch(client, note_id, **changes):
    return client.patch(f"/api/notes/{note_id}", data=json.dumps({"version": 1, "text": "Changed", "x": None, "y": None, **changes}), content_type="application/json")


def private(response, status):
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    if status >= 400:
        assert set(response.json()["error"]) == {"code", "message"}
    return response


def test_anonymous_reads_and_writes_require_session(client, article_factory):
    article = article_factory("Article")
    private(client.get(f"/api/articles/{article.pk}/notes"), 401)
    private(post(client, article, payload()), 401)
    private(patch(client, uuid4()), 401)
    private(client.delete(f"/api/notes/{uuid4()}"), 401)


def test_owned_notes_roundtrip_versions_and_plaintext(client, reader, article_factory):
    from study.models import Note
    article = article_factory("Article")
    client.force_login(reader)
    data = payload()
    note = private(post(client, article, data), 201).json()["note"]
    assert note == {**data, "article_id": str(article.pk), "version": 1, "updated_at": note["updated_at"]}
    assert private(client.get(f"/api/articles/{article.pk}/notes"), 200).json() == {"notes": [note]}
    changed = private(patch(client, note["id"]), 200).json()["note"]
    assert changed["version"] == 2
    assert changed["text"] == "Changed"
    assert changed["x"] is changed["y"] is None
    private(patch(client, note["id"]), 409)
    assert Note.objects.get(pk=note["id"]).version == 2
    private(client.delete(f'/api/notes/{note["id"]}'), 204)
    private(client.delete(f'/api/notes/{note["id"]}'), 404)


@pytest.mark.parametrize("actor", ["other_reader", "publisher"])
def test_other_users_including_publisher_cannot_access_notes(client, reader, request, article_factory, actor):
    from study.models import Note
    article = article_factory("Article")
    client.force_login(reader)
    data = payload()
    private(post(client, article, data), 201)
    client.force_login(request.getfixturevalue(actor))
    session = client.session
    session["study.mode"] = "admin"
    session.save()
    private(client.get(f"/api/articles/{article.pk}/notes?mode=admin"), 200)
    assert client.get(f"/api/articles/{article.pk}/notes").json() == {"notes": []}
    private(patch(client, data["id"]), 404)
    private(client.delete(f'/api/notes/{data["id"]}'), 404)
    private(post(client, article, data), 404)
    original = Note.objects.get(pk=data["id"])
    assert original.user == reader and original.text == data["text"] and original.version == 1


@pytest.mark.parametrize("extra", [{"user_id": 999}, {"article_id": str(uuid4())}, {"mode": "admin"}, {"user": 999}])
def test_forged_or_immutable_fields_are_rejected(client, reader, article_factory, extra):
    article = article_factory("Article")
    client.force_login(reader)
    private(post(client, article, payload(**extra)), 400)
    data = payload()
    private(post(client, article, data), 201)
    private(patch(client, data["id"], **extra), 400)


def test_create_id_retry_is_exact_and_conflicts_are_private(client, reader, article_factory):
    from study.models import Note
    article, second = article_factory("Article"), article_factory("Second")
    client.force_login(reader)
    data = payload()
    first = private(post(client, article, data), 201).json()
    assert private(post(client, article, data), 201).json() == first
    for changes in ({"text": "different"}, {"kind": "text"}, {"x": 43}, {"y": 71}):
        private(post(client, article, {**data, **changes}), 409)
    private(post(client, second, data), 409)
    assert Note.objects.count() == 1


@pytest.mark.parametrize("changes", [
    {"x": True}, {"y": False}, {"x": "12"}, {"x": None}, {"y": None},
    {"x": -1}, {"y": 1000001}, {"x": float("nan")}, {"y": float("inf")},
    {"x": -float("inf")}, {"x": 10**400}, {"text": "x" * 20001}, {"text": None},
    {"text": "bad\x00text"}, {"text": "bad\ud800text"}, {"kind": "html"}, {"id": "invalid"},
])
def test_create_validates_payload(client, reader, article_factory, changes):
    article = article_factory("Article")
    client.force_login(reader)
    private(post(client, article, payload(**changes)), 400)


@pytest.mark.parametrize("version", [True, False, 1.5, "1", 0, -1, None, 10**100])
def test_update_rejects_invalid_versions(client, reader, article_factory, version):
    article = article_factory("Article")
    client.force_login(reader)
    data = payload()
    private(post(client, article, data), 201)
    private(patch(client, data["id"], version=version), 400)


@pytest.mark.parametrize("body", ["{", "[]", "null", '{}', '{"kind":"sticky"}'])
def test_malformed_requests_are_private_json(client, reader, article_factory, body):
    article = article_factory("Article")
    client.force_login(reader)
    private(client.post(f"/api/articles/{article.pk}/notes", data=body, content_type="application/json"), 400)
    private(client.patch(f"/api/notes/{uuid4()}", data=body, content_type="application/json"), 400)


def test_csrf_failures_and_method_errors_are_private(reader, article_factory):
    article = article_factory("Article")
    client = Client(enforce_csrf_checks=True)
    client.force_login(reader)
    private(post(client, article, payload()), 403)
    private(patch(client, uuid4()), 403)
    private(client.delete(f"/api/notes/{uuid4()}"), 403)
    client.get("/")
    private(client.put(f"/api/articles/{article.pk}/notes", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value), 405)
    private(client.get(f"/api/notes/{uuid4()}"), 405)
    client.get("/")
    data = payload()
    response = client.post(f"/api/articles/{article.pk}/notes", data=json.dumps(data), content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    private(response, 201)


def test_missing_and_archived_articles_are_hidden(client, reader, publisher, article_factory):
    from study.services.content import archive_article
    article = article_factory("Article")
    archive_article(publisher, article.pk)
    client.force_login(reader)
    for article_id in (article.pk, uuid4()):
        private(client.get(f"/api/articles/{article_id}/notes"), 404)
        private(client.post(f"/api/articles/{article_id}/notes", data=json.dumps(payload()), content_type="application/json"), 404)
    private(patch(client, uuid4()), 404)


def test_service_enforces_auth_ownership_and_validation(reader, other_reader, publisher, article_factory):
    from study.services.notes import create_note, delete_note, list_notes, update_note
    article = article_factory("Article")
    values = dict(note_id=uuid4(), kind="text", text="Private", x=None, y=None)
    for operation in (
        lambda: list_notes(AnonymousUser(), article.pk),
        lambda: create_note(AnonymousUser(), article.pk, **values),
        lambda: update_note(AnonymousUser(), values["note_id"], expected_version=1, text="x", x=None, y=None),
        lambda: delete_note(AnonymousUser(), values["note_id"]),
    ):
        with pytest.raises(PermissionDenied):
            operation()
    for invalid in ({"x": True, "y": 1}, {"x": float("nan"), "y": 1}, {"text": "x" * 20001}, {"text": "\x00"}):
        with pytest.raises(ValueError):
            create_note(reader, article.pk, **{**values, **invalid})
    note = create_note(reader, article.pk, **values)
    for user in (other_reader, publisher):
        assert list_notes(user, article.pk) == []
        with pytest.raises(Http404):
            update_note(user, note.pk, expected_version=1, text="x", x=None, y=None)
        with pytest.raises(Http404):
            delete_note(user, note.pk)
    with pytest.raises(ValueError):
        update_note(reader, note.pk, expected_version=True, text="x", x=None, y=None)
    with pytest.raises(ValueError):
        update_note(reader, note.pk, expected_version=1, text="x", x=1, y=None)


def test_notes_order_by_created_at_then_id(reader, article_factory):
    from study.models import Note
    from study.services.notes import create_note, list_notes
    article = article_factory("Article")
    notes = [create_note(reader, article.pk, note_id=uuid4(), kind="text", text="", x=0, y=1000000) for _ in range(3)]
    Note.objects.update(created_at=notes[0].created_at)
    assert [note.pk for note in list_notes(reader, article.pk)] == sorted(note.pk for note in notes)


@pytest.mark.parametrize("values", [
    {"x": None, "y": 1}, {"x": 1, "y": None}, {"x": float("nan"), "y": 1},
    {"x": 1, "y": float("nan")}, {"x": float("inf"), "y": 1},
    {"x": 1, "y": -1}, {"x": 1000001, "y": 1}, {"version": 0},
    {"kind": "html"}, {"text": "x" * 20001},
])
def test_database_constraints_reject_invalid_notes(reader, article_factory, values):
    from study.models import Note
    article = article_factory("Article")
    with pytest.raises(IntegrityError), transaction.atomic():
        Note.objects.create(id=uuid4(), user=reader, article=article, **{"kind": "text", **values})


def test_archive_grants_note_owners_and_keeps_access_after_deletion(reader, other_reader, publisher, client):
    from study.models import ArchiveAccess
    from study.services.content import archive_article, publish_article, update_article
    from study.services.notes import create_note, delete_note, update_note
    from tests.website.test_assets import png_bytes
    article = publish_article(publisher, title="Article", color="#112233", markdown="# Article\n\n![image](a.png)", images={"a.png": png_bytes()}, submission_id=uuid4())
    asset = article.assets.get()
    note = create_note(reader, article.pk, note_id=uuid4(), kind="sticky", text="Private", x=42, y=83)
    create_note(reader, article.pk, note_id=uuid4(), kind="text", text="Second", x=None, y=None)
    update_article(publisher, article.pk, expected_revision=1, title="Article", color="#112233", markdown="# Article\n\nEdited\n\n![image](a.png)", images={})
    note.refresh_from_db()
    assert (note.x, note.y) == (42, 83)
    archive_article(publisher, article.pk)
    archive_article(publisher, article.pk)
    assert ArchiveAccess.objects.filter(user=reader, article=article).count() == 1
    for actor, status in ((reader, 200), (publisher, 200), (other_reader, 404)):
        client.force_login(actor)
        assert client.get(f"/{article.slug}").status_code == status
        response = client.get(f"/assets/{asset.pk}")
        assert response.status_code == status
        if status == 200:
            assert response.headers["Cache-Control"] == "private, no-store"
        if response.streaming:
            assert b"".join(response.streaming_content)
    changed = update_note(reader, note.pk, expected_version=1, text="Archive note", x=42, y=83)
    assert changed.version == 2
    create_note(reader, article.pk, note_id=uuid4(), kind="text", text="Later", x=None, y=None)
    with pytest.raises(ValueError, match="Archived"):
        update_article(publisher, article.pk, expected_revision=2, title="Article", color="#112233", markdown="# Replaced", images={})
    from study.services.notes import list_notes
    for owned in list_notes(reader, article.pk):
        delete_note(reader, owned.pk)
    assert ArchiveAccess.objects.filter(user=reader, article=article).exists()
    client.force_login(reader)
    assert client.get(f"/{article.slug}").status_code == 200
    assert client.get(f"/api/articles/{article.pk}/notes").json() == {"notes": []}


@pytest.mark.parametrize("path", ["/api/articles/not-a-uuid/notes", "/api/notes/not-a-uuid"])
def test_malformed_url_ids_return_private_json(client, reader, path):
    client.force_login(reader)
    response = client.get(path) if "/articles/" in path else patch(client, "not-a-uuid")
    private(response, 400)


def test_oversized_request_is_private(client, reader, article_factory, settings):
    article = article_factory("Article")
    client.force_login(reader)
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10
    private(post(client, article, payload()), 400)


def test_unexpected_api_error_is_private_and_does_not_expose_details(client, reader, article_factory, monkeypatch):
    from study.views import notes
    article = article_factory("Article")
    client.force_login(reader)

    def broken(*args):
        raise RuntimeError("Internal sensitive detail")

    monkeypatch.setattr(notes, "list_notes", broken)
    response = private(client.get(f"/api/articles/{article.pk}/notes"), 500)
    assert "Internal sensitive detail" not in response.content.decode()
