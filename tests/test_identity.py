import json

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory

from study.adapters import GoogleAccountAdapter
from study.models import PublisherIdentity, ReaderProfile
from study.services.identity import is_publisher, profile_for


OWNER_EMAIL = "dhelmy990@gmail.com"


def make_request():
    request = RequestFactory().get("/")
    SessionMiddleware(lambda request: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    return request


def google_login(email=OWNER_EMAIL, subject="google-owner-subject", verified=True):
    user = get_user_model()(email=email, username=email.split("@")[0])
    account = SocialAccount(
        provider="google",
        uid=subject,
        extra_data={"sub": subject, "email": email, "email_verified": verified},
    )
    return SocialLogin(
        user=user,
        account=account,
        email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
    )


@pytest.mark.django_db
def test_google_adapter_binds_verified_owner_on_initial_login():
    request = make_request()
    login = google_login()

    GoogleAccountAdapter().save_user(request, login)

    assert PublisherIdentity.objects.get(pk=1).google_subject == "google-owner-subject"
    assert PublisherIdentity.objects.get(pk=1).user == login.user
    assert ReaderProfile.objects.get(user=login.user).timezone == "Asia/Singapore"
    assert is_publisher(login.user) is True


@pytest.mark.django_db
def test_google_adapter_binds_an_existing_verified_owner_on_subsequent_login():
    request = make_request()
    initial_login = google_login()
    GoogleAccountAdapter().save_user(request, initial_login)
    PublisherIdentity.objects.all().delete()

    subsequent_login = google_login()
    subsequent_login.lookup()
    GoogleAccountAdapter().pre_social_login(request, subsequent_login)

    assert subsequent_login.user == initial_login.user
    assert PublisherIdentity.objects.get(pk=1).user == initial_login.user
    assert PublisherIdentity.objects.get(pk=1).google_subject == "google-owner-subject"
    assert ReaderProfile.objects.filter(user=initial_login.user).count() == 1


@pytest.mark.django_db
def test_adapter_does_not_bind_unverified_or_wrong_google_identity():
    request = make_request()
    unverified = google_login(verified=False)
    GoogleAccountAdapter().save_user(request, unverified)
    wrong_email = google_login(email="reader@example.com", subject="other-subject")
    GoogleAccountAdapter().save_user(request, wrong_email)

    assert not PublisherIdentity.objects.exists()
    assert not ReaderProfile.objects.filter(user=unverified.user).exists()
    assert ReaderProfile.objects.filter(user=wrong_email.user).exists()


@pytest.mark.django_db
def test_staff_owner_email_without_persisted_google_binding_is_not_publisher():
    user = get_user_model().objects.create_user(
        username="staff", email=OWNER_EMAIL, is_staff=True
    )

    assert is_publisher(user) is False


@pytest.mark.django_db
def test_publisher_requires_matching_social_subject_and_verified_owner_email():
    user = get_user_model().objects.create_user(username="owner", email=OWNER_EMAIL)
    PublisherIdentity.objects.create(user=user, google_subject="expected-subject")
    SocialAccount.objects.create(user=user, provider="google", uid="other-subject")
    EmailAddress.objects.create(user=user, email=OWNER_EMAIL, verified=True, primary=True)

    assert is_publisher(user) is False

    SocialAccount.objects.filter(user=user).update(uid="expected-subject")
    EmailAddress.objects.filter(user=user).update(verified=False)
    assert is_publisher(user) is False

    EmailAddress.objects.filter(user=user).update(verified=True)
    assert is_publisher(user) is True


@pytest.mark.django_db
def test_profile_for_creates_reader_profile_with_utc_default():
    user = get_user_model().objects.create_user(username="reader", email="reader@example.com")

    profile = profile_for(user)

    assert profile.user == user
    assert profile.timezone == "UTC"
    assert profile.pending_timezone is None


@pytest.mark.django_db
def test_public_session_and_google_login_get_are_safe(client):
    response = client.get("/api/session")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": False,
        "can_publish": False,
        "mode": "reader",
        "timezone": None,
    }

    login_response = client.get("/accounts/google/login/")
    assert login_response.status_code == 200
    assert "Continue with Google" in login_response.content.decode()
    assert client.get("/accounts/signup/").status_code == 404
    assert client.get("/accounts/password/reset/").status_code == 404


@pytest.mark.django_db
def test_mode_and_timezone_json_writes_require_authentication_and_csrf():
    csrf_client = Client(enforce_csrf_checks=True)
    unsigned = csrf_client.post(
        "/api/mode", data=json.dumps({"mode": "reader"}), content_type="application/json"
    )
    assert unsigned.status_code == 401
    assert unsigned.json()["error"]["code"] == "authentication_required"

    user = get_user_model().objects.create_user(username="reader", email="reader@example.com")
    csrf_client.force_login(user)
    missing_csrf = csrf_client.post(
        "/api/timezone",
        data=json.dumps({"timezone": "Asia/Singapore"}),
        content_type="application/json",
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"


@pytest.mark.django_db
def test_reader_cannot_elevate_mode_but_can_set_a_valid_timezone():
    csrf_client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username="reader", email="reader@example.com")
    csrf_client.force_login(user)
    csrf_client.get("/")
    token = csrf_client.cookies["csrftoken"].value

    denied = csrf_client.post(
        "/api/mode",
        data=json.dumps({"mode": "admin"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "publisher_required"

    changed = csrf_client.post(
        "/api/timezone",
        data=json.dumps({"timezone": "America/New_York"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert changed.status_code == 200
    assert changed.json()["timezone"] == "America/New_York"

    invalid = csrf_client.post(
        "/api/timezone",
        data=json.dumps({"timezone": "not/a-timezone"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_timezone"


@pytest.mark.django_db
def test_verified_publisher_can_switch_modes_and_log_out():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username="owner", email=OWNER_EMAIL)
    PublisherIdentity.objects.create(user=user, google_subject="owner-subject")
    SocialAccount.objects.create(user=user, provider="google", uid="owner-subject")
    EmailAddress.objects.create(user=user, email=OWNER_EMAIL, verified=True, primary=True)
    client.force_login(user)
    client.get("/")
    token = client.cookies["csrftoken"].value

    enabled = client.post(
        "/api/mode",
        data=json.dumps({"mode": "admin"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "admin"
    assert client.get("/api/session").json()["can_publish"] is True

    logged_out = client.post("/accounts/logout/", HTTP_X_CSRFTOKEN=token)
    assert logged_out.status_code == 302
    assert client.get("/api/session").json()["authenticated"] is False
