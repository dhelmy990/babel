import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from uuid import uuid4

import httpx
import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.core.management import call_command

from tests.fakes import FakeDelivery
from tests.test_reviews import due, finish

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 9, 14, 1, tzinfo=UTC)  # 09:00 Singapore
OWNER = "dhelmy990@gmail.com"


@pytest.fixture
def digest_articles(publisher, article_factory, settings):
    settings.PUBLIC_BASE_URL = "https://dhelmy.stream"
    settings.REVIEW_FROM_EMAIL = "Study notes <reviews@dhelmy.stream>"
    articles = [article_factory(f"Digest {i}") for i in range(5)]
    for i, article in enumerate(articles):
        due(publisher, article, due_date=date(2026, 9, 9) + timedelta(days=i))
    return articles


def test_no_digest_before_nine_or_without_verified_owner(publisher, digest_articles):
    from study.models import Digest, PublisherIdentity
    from study.services.digest import prepare_owner_digest
    assert prepare_owner_digest(now=NOW - timedelta(seconds=1)) is None
    assert not Digest.objects.exists()
    PublisherIdentity.objects.all().delete()
    assert prepare_owner_digest(now=NOW) is None
    assert not Digest.objects.exists()


@pytest.mark.parametrize("invalid", ["unverified", "subject", "wrong_user"])
def test_only_bound_verified_owner_is_eligible(publisher, reader, digest_articles, invalid):
    from study.models import Digest, PublisherIdentity
    from study.services.digest import prepare_owner_digest
    if invalid == "unverified":
        EmailAddress.objects.filter(user=publisher).update(verified=False)
    elif invalid == "subject":
        SocialAccount.objects.filter(user=publisher).update(uid="wrong-subject")
    else:
        PublisherIdentity.objects.update(user=reader)
    assert prepare_owner_digest(now=NOW) is None
    assert not Digest.objects.exists()


def test_empty_day_is_terminal_skipped_without_attempt(publisher):
    from study.services.digest import deliver_digest, prepare_owner_digest
    fake = FakeDelivery()
    digest = prepare_owner_digest(now=NOW)
    assert digest.status == "skipped" and digest.first_attempt_at is None and digest.payload == {}
    assert prepare_owner_digest(now=NOW + timedelta(hours=1)).pk == digest.pk
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "skipped"
    assert fake.calls == []


def test_preparation_is_unique_and_first_send_is_owner_only_without_notes(publisher, reader, digest_articles):
    from study.models import Digest
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.notes import create_note
    create_note(publisher, digest_articles[0].pk, note_id=uuid4(), kind="text", text="PRIVATE NOTE SECRET", x=None, y=None)
    fake = FakeDelivery()
    digest = prepare_owner_digest(now=NOW)
    assert digest.payload == {} and digest.first_attempt_at is None
    assert digest.day == date(2026, 9, 14)
    assert prepare_owner_digest(now=NOW).pk == digest.pk
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    assert len(fake.calls) == len(fake.messages) == Digest.objects.count() == 1
    payload, key = fake.calls[0]
    assert payload["to"] == [OWNER]
    assert payload["from"] == "Study notes <reviews@dhelmy.stream>"
    assert key == f"study-review/{publisher.pk}/2026-09-14"
    assert set(payload) == {"from", "to", "subject", "text", "html"}
    for article in digest_articles[:3]:
        assert article.title in payload["text"]
        assert f"https://dhelmy.stream/{article.slug}" in payload["html"]
    assert all(article.title not in payload["text"] for article in digest_articles[3:])
    assert "PRIVATE NOTE SECRET" not in json.dumps(payload)
    digest.refresh_from_db()
    assert digest.first_attempt_at == NOW and digest.retry_until == datetime(2026, 9, 14, 16, tzinfo=UTC)
    assert digest.provider_id == "fake-1" and digest.payload == payload


def test_first_send_rechecks_completion_and_archive_without_refilling(publisher, digest_articles):
    from study.services.content import archive_article
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    finish(publisher, digest_articles[0], NOW)
    archive_article(publisher, digest_articles[1].pk)
    fake = FakeDelivery()
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    text = fake.calls[0][0]["text"]
    assert digest_articles[2].title in text
    assert all(article.title not in text for article in digest_articles if article != digest_articles[2])


def test_all_completed_before_first_send_skips_without_provider(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    for article in digest_articles[:3]:
        finish(publisher, article, NOW)
    fake = FakeDelivery()
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "skipped"
    digest.refresh_from_db()
    assert fake.calls == [] and digest.first_attempt_at is None


def test_retry_payload_is_frozen_after_acceptance_loss_and_content_changes(publisher, digest_articles, settings):
    from study.models import Article
    from study.services.content import archive_article
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    fake = FakeDelivery(fail_after_accept=1)
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "retry"
    digest.refresh_from_db()
    assert "Sensitive" not in digest.last_error
    payload = json.loads(json.dumps(digest.payload))
    finish(publisher, digest_articles[0], NOW)
    archive_article(publisher, digest_articles[1].pk)
    Article.objects.filter(pk=digest_articles[2].pk).update(title="Changed after acceptance")
    settings.PUBLIC_BASE_URL = "https://changed.example"
    settings.REVIEW_FROM_EMAIL = "Changed <changed@example.com>"
    assert deliver_digest(digest.pk, now=NOW + timedelta(minutes=5), delivery=fake) == "sent"
    assert len(fake.calls) == 2 and len(fake.messages) == 1
    assert fake.calls[0] == fake.calls[1]
    assert fake.calls[1][0] == payload


def test_delivery_date_is_singapore_while_content_tracks_active_browser_day(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(publisher, "America/Los_Angeles", now=NOW)
    active = get_review_day(publisher, now=NOW)
    assert active.local_date == date(2026, 9, 13)
    digest = prepare_owner_digest(now=NOW)
    assert digest.day == date(2026, 9, 14)
    fake = FakeDelivery()
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    assert str(active.local_date) in fake.calls[0][0]["text"]


def test_prepared_digest_follows_changed_active_review_day_before_first_attempt(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(publisher, "Asia/Tokyo", now=NOW)
    old_day = get_review_day(publisher, now=NOW)
    digest = prepare_owner_digest(now=NOW)
    finish(publisher, digest_articles[0], NOW)
    later = datetime(2026, 9, 14, 15, tzinfo=UTC)  # Tokyo rollover, same Singapore date.
    new_day = get_review_day(publisher, now=later)
    assert new_day.pk != old_day.pk
    fake = FakeDelivery()
    assert deliver_digest(digest.pk, now=later, delivery=fake) == "sent"
    text = fake.calls[0][0]["text"]
    assert "2026-09-15" in text
    assert digest_articles[3].title in text and digest_articles[0].title not in text


@pytest.mark.parametrize("attempted", [False, True])
def test_expired_records_are_retired_without_old_backlog(publisher, digest_articles, attempted):
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    fake = FakeDelivery(fail_after_accept=1)
    if attempted:
        assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "retry"
    calls = len(fake.calls)
    later = datetime(2026, 9, 14, 16, tzinfo=UTC)
    assert prepare_owner_digest(now=later) is None  # New Singapore date before09:00, but retires yesterday.
    digest.refresh_from_db()
    expected = "unknown" if attempted else "skipped"
    assert digest.status == expected
    assert deliver_digest(digest.pk, now=later + timedelta(days=1), delivery=fake) == expected
    assert len(fake.calls) == calls


def test_permanent_rejection_is_terminal_and_sanitized(publisher, digest_articles):
    from study.email_delivery import PermanentDeliveryError
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    fake = FakeDelivery(failures=[PermanentDeliveryError("authentication_rejected")])
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "failed"
    assert deliver_digest(digest.pk, now=NOW + timedelta(minutes=5), delivery=fake) == "failed"
    assert len(fake.calls) == 1


def test_revoked_owner_cannot_deliver_prepared_digest(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    EmailAddress.objects.filter(user=publisher).update(verified=False)
    fake = FakeDelivery()
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "skipped"
    assert fake.calls == []


def test_delayed_first_claim_does_not_start_provider_after_deadline(publisher, digest_articles, monkeypatch):
    from study.services import digest as service
    digest = service.prepare_owner_digest(now=NOW)
    readings = iter([0.0, 16 * 60 * 60.0])
    monkeypatch.setattr(service, "monotonic", lambda: next(readings))
    fake = FakeDelivery()
    assert service.deliver_digest(digest.pk, now=NOW, delivery=fake) == "skipped"
    assert not fake.calls


def test_dry_run_lists_owner_and_articles_without_digest_attempt(publisher, digest_articles, monkeypatch):
    from study.models import Digest
    from study.management.commands import send_review_digest
    monkeypatch.setattr(send_review_digest.timezone, "now", lambda: NOW)
    fake = FakeDelivery()
    monkeypatch.setattr(send_review_digest, "get_delivery", lambda: fake)
    out = StringIO()
    call_command("send_review_digest", dry_run=True, stdout=out)
    assert OWNER in out.getvalue() and "2026-09-14" in out.getvalue()
    assert all(article.title in out.getvalue() for article in digest_articles[:3])
    assert fake.calls == [] and not Digest.objects.exists()
    call_command("send_review_digest", stdout=StringIO())
    assert len(fake.messages) == 1


@pytest.mark.parametrize("status,name,permanent", [
    (400, "validation_error", True), (401, "missing_api_key", True), (403, "restricted_api_key", True),
    (422, "missing_required_field", True), (409, "invalid_idempotent_request", True),
    (409, "concurrent_idempotent_requests", False), (429, "rate_limit_exceeded", False),
    (500, "application_error", False), (503, "service_unavailable", False),
])
def test_resend_error_contract_uses_sanitized_categories(status, name, permanent):
    from study.email_delivery import PermanentDeliveryError, ResendDelivery, RetryableDeliveryError
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"name": name, "message": "SECRET raw provider body"}))
    delivery = ResendDelivery("fake-key", transport=transport)
    with pytest.raises(PermanentDeliveryError if permanent else RetryableDeliveryError) as caught:
        delivery.send({"to": [OWNER]}, "test-key")
    assert "SECRET" not in str(caught.value) and "fake-key" not in str(caught.value)


def test_resend_sends_canonical_json_exact_headers_and_timeout():
    from study.email_delivery import ResendDelivery
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "provider-123"})

    delivery = ResendDelivery("fake-key", transport=httpx.MockTransport(respond))
    for payload in ({"to": [OWNER], "subject": "A & B"}, {"subject": "A & B", "to": [OWNER]}):
        assert delivery.send(payload, "study-review/1/2026-09-14") == "provider-123"
    request = requests[0]
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == "Bearer fake-key"
    assert request.headers["Idempotency-Key"] == "study-review/1/2026-09-14"
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == requests[1].content
    assert set(request.extensions["timeout"].values()) == {10.0}


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"id":null}', b'{"id":42}', b'{"id":""}'])
def test_resend_success_without_readable_message_id_is_uncertain(body):
    from study.email_delivery import ResendDelivery, RetryableDeliveryError
    delivery = ResendDelivery("fake-key", transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    with pytest.raises(RetryableDeliveryError):
        delivery.send({}, "test-key")


def test_resend_network_error_is_uncertain_without_sensitive_details():
    from study.email_delivery import ResendDelivery, RetryableDeliveryError

    def timeout(request):
        raise httpx.ReadTimeout("SECRET", request=request)

    with pytest.raises(RetryableDeliveryError) as caught:
        ResendDelivery("fake-key", transport=httpx.MockTransport(timeout)).send({}, "test-key")
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("backend,key,valid", [(None, "", True), (None, "fake-key", True), ("disabled", "", True), ("resend", "", False), ("resend", "fake-key", True), ("console", "", True), ("invalid", "", False)])
def test_production_delivery_defaults_and_explicit_console(backend, key, valid):
    environment = {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "test-only-secret", "DB_NAME": "unused", "DB_USER": "unused", "DB_PASSWORD": "unused", "DB_HOST": "localhost", "DB_PORT": "5433", "RESEND_API_KEY": key}
    if backend is not None:
        environment["REVIEW_EMAIL_DELIVERY"] = backend
    result = subprocess.run([sys.executable, "-c", "import website.settings as s; print(s.REVIEW_EMAIL_DELIVERY)"], env=environment, capture_output=True, text=True)
    assert (result.returncode == 0) is valid
    if not valid:
        assert "AttributeError" not in result.stderr
    if valid:
        assert result.stdout.strip() == (backend or "disabled")


def test_uncertain_provider_result_after_retry_deadline_becomes_unknown(publisher, digest_articles, monkeypatch):
    from study.services import digest as service
    digest = service.prepare_owner_digest(now=NOW)
    elapsed = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: elapsed[0])

    class LateTimeout(FakeDelivery):
        def send(self, payload, idempotency_key):
            super().send(payload, idempotency_key)
            elapsed[0] = 16 * 60 * 60.0
            raise TimeoutError("Sensitive lost acceptance")

    assert service.deliver_digest(digest.pk, now=NOW, delivery=LateTimeout()) == "unknown"


def test_same_clock_retry_gets_distinct_fencing_lease(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    fake = FakeDelivery(fail_after_accept=1)
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "retry"
    digest.refresh_from_db()
    first_lease = digest.lease_until
    assert deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    digest.refresh_from_db()
    assert digest.lease_until > first_lease
    assert len(fake.messages) == 1


def test_expired_claim_before_provider_is_released_without_sending(publisher, digest_articles, monkeypatch):
    from study.services import digest as service
    digest = service.prepare_owner_digest(now=NOW)
    elapsed = iter([0.0, 121.0])
    monkeypatch.setattr(service, "monotonic", lambda: next(elapsed))
    fake = FakeDelivery()
    assert service.deliver_digest(digest.pk, now=NOW, delivery=fake) == "retry"
    digest.refresh_from_db()
    assert digest.status == "pending" and digest.last_error == "lease_expired_before_send"
    assert not fake.calls


def test_same_review_day_can_have_different_singapore_delivery_dates(publisher, digest_articles):
    from study.models import Digest
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(publisher, "Pacific/Kiritimati", now=NOW)
    first_day = get_review_day(publisher, now=NOW)
    set_timezone(publisher, "Etc/GMT+12", now=NOW)
    first_digest = prepare_owner_digest(now=NOW)
    fake = FakeDelivery()
    assert deliver_digest(first_digest.pk, now=NOW, delivery=fake) == "sent"
    tomorrow = NOW + timedelta(days=1)
    assert get_review_day(publisher, now=tomorrow).pk == first_day.pk
    next_digest = prepare_owner_digest(now=tomorrow)
    assert next_digest.pk != first_digest.pk
    assert deliver_digest(next_digest.pk, now=tomorrow, delivery=fake) == "sent"
    assert len(fake.messages) == Digest.objects.count() == 2
    assert fake.calls[0][1] != fake.calls[1][1]


def test_digest_database_identity_and_state_constraints(publisher):
    from django.db import IntegrityError, transaction
    from study.models import Digest
    digest = Digest.objects.create(user=publisher, day=NOW.date(), idempotency_key="original")
    with pytest.raises(IntegrityError), transaction.atomic():
        Digest.objects.create(user=publisher, day=NOW.date(), idempotency_key="duplicate")
    for changes in ({"status": "sending"}, {"status": "sent"}, {"status": "invalid"}, {"retry_until": NOW}):
        with pytest.raises(IntegrityError), transaction.atomic():
            Digest.objects.filter(pk=digest.pk).update(**changes)


def test_digest_clocks_must_be_explicit_and_aware():
    from study.services.digest import deliver_digest, prepare_owner_digest, preview_owner_digest
    naive = NOW.replace(tzinfo=None)
    for operation in (lambda: prepare_owner_digest(now=naive), lambda: preview_owner_digest(now=naive), lambda: deliver_digest(1, now=naive, delivery=FakeDelivery())):
        with pytest.raises(ValueError, match="aware"):
            operation()


@pytest.mark.parametrize("field", ["RESEND_API_KEY", "REVIEW_FROM_EMAIL", "PUBLIC_BASE_URL"])
def test_production_rejects_empty_required_delivery_configuration(field):
    environment = {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "test-only-secret", "DB_NAME": "unused", "DB_USER": "unused", "DB_PASSWORD": "unused", "DB_HOST": "localhost", "DB_PORT": "5433", "RESEND_API_KEY": "fake-key", field: " "}
    environment["REVIEW_EMAIL_DELIVERY"] = "resend"
    result = subprocess.run([sys.executable, "-c", "import website.settings"], env=environment, capture_output=True, text=True)
    assert result.returncode != 0 and field in result.stderr


def test_command_resamples_clock_after_preparation_crosses_singapore_midnight(publisher, digest_articles, monkeypatch):
    from study.management.commands import send_review_digest as command
    from study.models import Digest
    clock = [NOW]
    monkeypatch.setattr(command.timezone, "now", lambda: clock[0])
    prepare = command.prepare_owner_digest

    def delayed_preparation(*, now):
        digest = prepare(now=now)
        clock[0] = NOW + timedelta(hours=15)
        return digest

    monkeypatch.setattr(command, "prepare_owner_digest", delayed_preparation)
    fake = FakeDelivery()
    monkeypatch.setattr(command, "get_delivery", lambda: fake)
    call_command("send_review_digest", stdout=StringIO())
    assert fake.calls == []
    digest = Digest.objects.get()
    assert digest.status == "skipped" and digest.first_attempt_at is None


@pytest.mark.parametrize("permanent,category", [(True, "authentication_rejected"), (False, "transport_error")])
def test_command_logs_sanitized_persisted_error_category(publisher, digest_articles, monkeypatch, permanent, category):
    from study.email_delivery import PermanentDeliveryError
    from study.management.commands import send_review_digest as command
    monkeypatch.setattr(command.timezone, "now", lambda: NOW)
    failure = PermanentDeliveryError(category) if permanent else TimeoutError("SECRET provider body and API key")
    fake = FakeDelivery(failures=[failure])
    monkeypatch.setattr(command, "get_delivery", lambda: fake)
    output = StringIO()
    call_command("send_review_digest", stdout=output)
    assert category in output.getvalue()
    assert ("failed" if permanent else "retry") in output.getvalue()
    assert "SECRET" not in output.getvalue() and "API key" not in output.getvalue()
