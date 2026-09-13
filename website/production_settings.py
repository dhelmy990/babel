"""Runtime-only configuration. The image never falls back to development mode."""
import os
from pathlib import Path

os.environ.setdefault("DJANGO_DEBUG", "false")
from .settings import *  # noqa: E402,F403

if DEBUG:
    raise RuntimeError("DJANGO_DEBUG must be false in production")
for required in ("DJANGO_SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    env(required)

ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "dhelmy.stream").split(",")
CSRF_TRUSTED_ORIGINS = ["https://dhelmy.stream"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # The same-origin request helper reads this cookie.
MEDIA_ROOT = Path(env("MEDIA_ROOT", "/app/media"))
PRIVATE_MEDIA_ROOT = MEDIA_ROOT
WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
WHITENOISE_KEEP_ONLY_HASHED_FILES = True
