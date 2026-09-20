"""Read-only recovery evidence and real HTTP checks against a restored Gunicorn.

The snapshot contains hashes, never plaintext annotations. Verification creates
short-lived local sessions to exercise ownership without OAuth or real email.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django
django.setup()
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core import serializers
from study.models import Article, Asset, Note
from study.services.content import can_read_article
from study.storage import path_for, private_media_root


def snapshot():
    models = {}
    for model in apps.get_app_config("study").get_models():
        data = serializers.serialize("json", model.objects.order_by("pk"))
        models[model._meta.label] = hashlib.sha256(data.encode()).hexdigest()
    assets = {}
    for asset in Asset.objects.order_by("pk"):
        digest = hashlib.sha256(path_for(asset.storage_key).read_bytes()).hexdigest()
        assert digest == asset.sha256, "Stored asset checksum mismatch"
        assets[str(asset.pk)] = digest
    return {"models": models, "assets": assets}


def request(path, session=None):
    headers = {"Host": "dhelmy.stream", "X-Forwarded-Proto": "https"}
    if session:
        headers["Cookie"] = "sessionid=" + session.session_key
    try:
        response = urlopen(Request("http://127.0.0.1:8000" + path, headers=headers), timeout=10)
    except HTTPError as response:
        return response.code, response.headers, response.read()
    with response:
        return response.status, response.headers, response.read()


def verify(expected):
    assert snapshot() == expected, "Restored records or image bytes differ"
    assert os.getuid() == 10001
    with tempfile.TemporaryFile(dir=private_media_root()) as output:
        output.write(b"writable")
    assert not os.access(settings.STATIC_ROOT, os.W_OK), "Static files must be read-only"
    for attempt in range(60):
        try:
            if request("/healthz")[0] == 200:
                break
        except (URLError, OSError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Restored web did not become healthy")
    for article in Article.objects.all():
        assert request("/" + article.slug)[0] == (404 if article.archived_at else 200)
    for asset in Asset.objects.select_related("article"):
        status, _, body = request("/assets/" + str(asset.pk))
        assert status == (404 if asset.article.archived_at else 200)
        if status == 200:
            assert hashlib.sha256(body).hexdigest() == expected["assets"][str(asset.pk)]
    for user in get_user_model().objects.filter(is_active=True):
        session = SessionStore()
        session.update({"_auth_user_id": str(user.pk), "_auth_user_backend": "django.contrib.auth.backends.ModelBackend", "_auth_user_hash": user.get_session_auth_hash()})
        session.save()
        try:
            for article in Article.objects.all():
                readable = can_read_article(user, article)
                assert request("/" + article.slug, session)[0] == (200 if readable else 404)
                status, headers, body = request(f"/api/articles/{article.pk}/notes", session)
                assert status == (200 if readable else 404)
                assert headers["Cache-Control"] == "private, no-store"
                if readable:
                    assert {note["id"] for note in json.loads(body)["notes"]} == {str(pk) for pk in Note.objects.filter(user=user, article=article).values_list("pk", flat=True)}
            for asset in Asset.objects.select_related("article"):
                status, headers, body = request("/assets/" + str(asset.pk), session)
                assert status == (200 if can_read_article(user, asset.article) else 404)
                if status == 200:
                    assert headers["Cache-Control"] == "private, no-store"
                    assert hashlib.sha256(body).hexdigest() == expected["assets"][str(asset.pk)]
        finally:
            session.delete()
    print("Recovery verified: records, images, private access, schedules and slot identities.")


if __name__ == "__main__":
    if sys.argv[1:] == ["snapshot"]:
        print(json.dumps(snapshot(), sort_keys=True))
    elif sys.argv[1:] == ["verify"]:
        verify(json.load(sys.stdin))
    else:
        raise SystemExit("Usage: recovery.py snapshot|verify")
