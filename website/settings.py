import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or (default is None and not value.strip()):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DEBUG = env("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes", "on"}
SECRET_KEY = env("DJANGO_SECRET_KEY", "study-dev-secret-key" if DEBUG else None)
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

INSTALLED_APPS = [
    "django.contrib.sites",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "study",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "website.urls"
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "study.context_processors.identity",
            ],
        },
    },
]

WSGI_APPLICATION = "website.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "study_dev" if DEBUG else None),
        "USER": env("DB_USER", "study_dev" if DEBUG else None),
        "PASSWORD": env("DB_PASSWORD", "study_dev" if DEBUG else None),
        "HOST": env("DB_HOST", "127.0.0.1" if DEBUG else None),
        "PORT": env("DB_PORT", "5433" if DEBUG else None),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "study.staticfiles.WebsiteModuleFinder",
]
# Deliberately absent from URL routing: article images are authorized through /assets/<uuid>.
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "private-media")))
PRIVATE_MEDIA_ROOT = MEDIA_ROOT
# Authenticated multipart writers impose the 64 MiB request cap themselves so
# the CSRF middleware can parse valid requests before article validation.
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024 * 1024
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "study.staticfiles.WebsiteStaticStorage"
        ),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_LOGIN_METHODS = {"email"}
LOGIN_REDIRECT_URL = "/"
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_ADAPTER = "study.adapters.GoogleAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": [
            {
                "name": "dhelmy.stream",
                "client_id": env("GOOGLE_CLIENT_ID", ""),
                "secret": env("GOOGLE_CLIENT_SECRET", ""),
                "settings": {
                    "scope": ["profile", "email"],
                    "auth_params": {"access_type": "online"},
                    "oauth_pkce_enabled": True,
                },
            }
        ]
    }
}
CSRF_FAILURE_VIEW = "study.views.identity.csrf_failure"

# Console is local-only by default. A production console override must be explicit.
REVIEW_EMAIL_DELIVERY = env("REVIEW_EMAIL_DELIVERY", "console" if DEBUG else "resend")
RESEND_API_KEY = env("RESEND_API_KEY", "")
REVIEW_FROM_EMAIL = env("REVIEW_FROM_EMAIL", "Study notes <reviews@dhelmy.stream>")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://127.0.0.1:8000" if DEBUG else "https://dhelmy.stream")
if REVIEW_EMAIL_DELIVERY not in {"console", "resend"}:
    raise RuntimeError("REVIEW_EMAIL_DELIVERY must be console or resend")
if not REVIEW_FROM_EMAIL.strip() or not PUBLIC_BASE_URL.strip():
    raise RuntimeError("REVIEW_FROM_EMAIL and PUBLIC_BASE_URL must not be empty")
if not DEBUG and REVIEW_EMAIL_DELIVERY == "resend" and not RESEND_API_KEY.strip():
    raise RuntimeError("RESEND_API_KEY is required for production Resend delivery")
