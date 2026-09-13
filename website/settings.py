import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
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
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
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
