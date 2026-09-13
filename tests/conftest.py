import os

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model

from study.models import PublisherIdentity, ReaderProfile


def pytest_configure():
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
    os.environ.setdefault("DJANGO_DEBUG", "true")
    os.environ.setdefault("DB_NAME", "study_dev")
    os.environ.setdefault("DB_USER", "study_dev")
    os.environ.setdefault("DB_PASSWORD", "study_dev")
    os.environ.setdefault("DB_HOST", "127.0.0.1")
    os.environ.setdefault("DB_PORT", "5433")


@pytest.fixture(autouse=True)
def private_test_media(settings, tmp_path):
    """No test may write into the developer's private media directory."""
    settings.MEDIA_ROOT = tmp_path
    settings.PRIVATE_MEDIA_ROOT = tmp_path


@pytest.fixture
def publisher(db):
    user = get_user_model().objects.create_user(
        username="publisher", email="dhelmy990@gmail.com"
    )
    EmailAddress.objects.create(
        user=user, email="dhelmy990@gmail.com", verified=True, primary=True
    )
    SocialAccount.objects.create(user=user, provider="google", uid="publisher-subject")
    PublisherIdentity.objects.create(user=user, google_subject="publisher-subject")
    ReaderProfile.objects.create(user=user, timezone="Asia/Singapore")
    return user
