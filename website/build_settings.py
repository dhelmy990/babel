"""Collect static without runtime secrets or database access; never serve requests."""
import os

for name, value in {
    "DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "static-build-only",
    "DB_NAME": "unused", "DB_USER": "unused", "DB_PASSWORD": "unused",
    "DB_HOST": "unused", "DB_PORT": "5432", "REVIEW_EMAIL_DELIVERY": "console",
}.items():
    os.environ[name] = value
from .settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
STATIC_ROOT = os.environ.get("BUILD_STATIC_ROOT", str(BASE_DIR / "staticfiles"))
WHITENOISE_KEEP_ONLY_HASHED_FILES = True
