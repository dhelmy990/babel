"""Production settings load independently of developer environment and test overrides."""
import json
import os
import subprocess
import sys

import pytest


def production_env(**changes):
    return {"PATH": os.environ["PATH"], "DJANGO_SETTINGS_MODULE": "website.production_settings",
            "DJANGO_SECRET_KEY": "deployment-test-secret-" * 4,
            "DB_NAME": "study", "DB_USER": "study", "DB_PASSWORD": "disposable",
            "DB_HOST": "db", "DB_PORT": "5432", "GOOGLE_CLIENT_ID": "dummy",
            "GOOGLE_CLIENT_SECRET": "dummy", "REVIEW_EMAIL_DELIVERY": "console", **changes}


@pytest.mark.parametrize("name", ["DJANGO_SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_production_rejects_missing_or_empty_configuration(name, value):
    environment = production_env()
    if value is None:
        environment.pop(name)
    else:
        environment[name] = value
    result = subprocess.run([sys.executable, "-c", "import django; django.setup()"], env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert name in result.stderr


def test_production_defaults_are_secure_and_callback_registered():
    script = '''
import django, json
django.setup()
from django.conf import settings as s
from django.urls import resolve
assert resolve('/accounts/google/login/callback/')
print(json.dumps([s.DEBUG, s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS,
    s.SESSION_COOKIE_SECURE, s.SESSION_COOKIE_HTTPONLY, s.CSRF_COOKIE_SECURE,
    s.CSRF_COOKIE_HTTPONLY, s.SECURE_SSL_REDIRECT, s.SECURE_PROXY_SSL_HEADER,
    str(s.MEDIA_ROOT), str(s.PRIVATE_MEDIA_ROOT)]))
'''
    result = subprocess.run([sys.executable, "-c", script], env=production_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [False, ["dhelmy.stream"], ["https://dhelmy.stream"], True, True, True, False, True, ["HTTP_X_FORWARDED_PROTO", "https"], "/app/media", "/app/media"]


def test_production_image_settings_refuse_debug():
    result = subprocess.run([sys.executable, "-c", "import django; django.setup()"], env=production_env(DJANGO_DEBUG="true"), capture_output=True, text=True)
    assert result.returncode != 0 and "DJANGO_DEBUG" in result.stderr


def test_build_settings_collect_without_runtime_configuration(tmp_path):
    result = subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], env={"PATH": os.environ["PATH"], "DJANGO_SETTINGS_MODULE": "website.build_settings", "BUILD_STATIC_ROOT": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "staticfiles.json").read_text())["paths"]
    publishing = (tmp_path / manifest["study/publishing.js"]).read_text()
    assert manifest["study/site.js"].split("/")[-1] in publishing


@pytest.mark.django_db
def test_repository_and_private_media_paths_are_not_public(client):
    for path in ("/.env", "/.env.save", "/prompts/website.md", "/build/", "/media/assets/anything", "/private-media/anything"):
        assert client.get(path).status_code == 404
