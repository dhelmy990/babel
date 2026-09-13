import os


def pytest_configure():
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
    os.environ.setdefault("DJANGO_DEBUG", "true")
    os.environ.setdefault("DB_NAME", "study_dev")
    os.environ.setdefault("DB_USER", "study_dev")
    os.environ.setdefault("DB_PASSWORD", "study_dev")
    os.environ.setdefault("DB_HOST", "127.0.0.1")
    os.environ.setdefault("DB_PORT", "5433")
