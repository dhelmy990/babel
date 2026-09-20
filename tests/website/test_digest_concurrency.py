"""Independent PostgreSQL connections and fake provider acceptance only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import connection, connections

from tests.website.fakes import FakeDelivery
from tests.website.test_digest import NOW, digest_articles

pytestmark = pytest.mark.django_db(transaction=True)


def _pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _parallel(operations):
    assert connection.vendor == "postgresql"
    barrier = Barrier(len(operations))

    def run(operation):
        connections.close_all()
        try:
            pid = _pid()
            barrier.wait(timeout=10)
            return pid, operation()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        results = list(pool.map(run, operations))
    assert len({pid for pid, _ in results}) == len(operations)
    return [value for _, value in results]


def test_concurrent_preparation_and_sending_are_unique(publisher, digest_articles):
    from study.models import Digest
    from study.services.digest import deliver_digest, prepare_owner_digest
    ids = _parallel([lambda: prepare_owner_digest(now=NOW).pk] * 2)
    assert ids[0] == ids[1] and Digest.objects.count() == 1
    fake = FakeDelivery()
    outcomes = _parallel([lambda: deliver_digest(ids[0], now=NOW, delivery=fake)] * 2)
    assert "sent" in outcomes and set(outcomes) <= {"sent", "retry"}
    assert len(fake.calls) == len(fake.messages) == 1


def test_first_send_selection_commits_before_article_locks_and_provider_runs_outside_transactions(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.reviews import set_timezone
    set_timezone(publisher, "Asia/Tokyo", now=NOW)
    digest = prepare_owner_digest(now=NOW)
    transactions = []

    def observe(execute, sql, params, many, context):
        if 'FROM "study_article"' in sql and "FOR UPDATE" in sql:
            with connection.cursor() as cursor:
                cursor.execute("SELECT txid_current()")
                transactions.append(cursor.fetchone()[0])
        return execute(sql, params, many, context)

    class OutsideDelivery(FakeDelivery):
        def send(self, payload, idempotency_key):
            assert not connection.in_atomic_block
            assert connection.get_autocommit()
            return super().send(payload, idempotency_key)

    with connection.execute_wrapper(observe):
        assert deliver_digest(digest.pk, now=NOW + timedelta(hours=14), delivery=OutsideDelivery()) == "sent"
    assert len(transactions) == 2 and transactions[0] != transactions[1]


@pytest.mark.parametrize("stale_result", ["accepted", "permanent", "timeout"])
def test_reclaimed_lease_fences_stale_worker_from_newer_sent_result(publisher, digest_articles, stale_result):
    from study.email_delivery import PermanentDeliveryError
    from study.models import Digest
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    accepted, release = Event(), Event()
    fake = FakeDelivery()
    main_pid = _pid()

    class PausedDelivery:
        def send(self, payload, idempotency_key):
            assert not connection.in_atomic_block
            provider_id = fake.send(payload, idempotency_key)
            accepted.set()
            assert release.wait(timeout=15)
            if stale_result == "permanent":
                raise PermanentDeliveryError("request_rejected")
            if stale_result == "timeout":
                raise TimeoutError("Sensitive body")
            return provider_id

    def first_worker():
        connections.close_all()
        try:
            assert _pid() != main_pid
            return deliver_digest(digest.pk, now=NOW, delivery=PausedDelivery())
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first_worker)
        try:
            assert accepted.wait(timeout=10)
            first_lease = Digest.objects.get(pk=digest.pk).lease_until
            assert deliver_digest(digest.pk, now=NOW + timedelta(minutes=3), delivery=fake) == "sent"
            saved = Digest.objects.get(pk=digest.pk)
            assert saved.lease_until > first_lease
        finally:
            release.set()
        assert pending.result(timeout=15) == "sent"
    digest.refresh_from_db()
    assert digest.provider_id == "fake-1" and digest.last_error == ""
    assert len(fake.calls) == 2 and len(fake.messages) == 1
    assert fake.calls[0] == fake.calls[1]


def test_matching_late_ack_can_record_sent_without_replacement(publisher, digest_articles, monkeypatch):
    from study.services import digest as service
    digest = service.prepare_owner_digest(now=NOW)
    elapsed = [0.0]
    monkeypatch.setattr(service, "monotonic", lambda: elapsed[0])

    class LateDelivery(FakeDelivery):
        def send(self, payload, idempotency_key):
            result = super().send(payload, idempotency_key)
            elapsed[0] = 180.0
            return result

    fake = LateDelivery()
    assert service.deliver_digest(digest.pk, now=NOW, delivery=fake) == "sent"
    digest.refresh_from_db()
    assert digest.lease_until < NOW + timedelta(seconds=elapsed[0])
    assert digest.provider_id == "fake-1"


def test_housekeeping_terminal_unknown_fences_late_accepted_result(publisher, digest_articles):
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    accepted, release = Event(), Event()
    fake = FakeDelivery()
    main_pid = _pid()

    class PausedDelivery:
        def send(self, payload, idempotency_key):
            provider_id = fake.send(payload, idempotency_key)
            accepted.set()
            assert release.wait(timeout=15)
            return provider_id

    def first_worker():
        connections.close_all()
        try:
            assert _pid() != main_pid
            return deliver_digest(digest.pk, now=NOW, delivery=PausedDelivery())
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first_worker)
        try:
            assert accepted.wait(timeout=10)
            assert prepare_owner_digest(now=NOW + timedelta(hours=15)) is None
        finally:
            release.set()
        assert pending.result(timeout=15) == "unknown"
    assert deliver_digest(digest.pk, now=NOW + timedelta(days=1), delivery=fake) == "unknown"
    assert len(fake.calls) == 1


def test_archive_before_first_send_article_lock_is_excluded(publisher, digest_articles):
    from study.services.content import archive_article
    from study.services.digest import deliver_digest, prepare_owner_digest
    digest = prepare_owner_digest(now=NOW)
    waiting, resume = Event(), Event()
    fake = FakeDelivery()
    main_pid = _pid()

    def pause(execute, sql, params, many, context):
        if 'FROM "study_article"' in sql and "FOR UPDATE" in sql:
            waiting.set()
            assert resume.wait(timeout=10)
        return execute(sql, params, many, context)

    def deliver():
        connections.close_all()
        try:
            assert _pid() != main_pid
            with connection.execute_wrapper(pause):
                return deliver_digest(digest.pk, now=NOW, delivery=fake)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(deliver)
        try:
            assert waiting.wait(timeout=10)
            archive_article(publisher, digest_articles[0].pk)
        finally:
            resume.set()
        assert pending.result(timeout=10) == "sent"
    assert digest_articles[0].title not in fake.calls[0][0]["text"]
    assert digest_articles[3].title not in fake.calls[0][0]["text"]


@pytest.mark.parametrize("newly_due", [False, True])
def test_first_send_retries_preflight_when_active_day_changes(publisher, digest_articles, newly_due):
    from study.services.digest import deliver_digest, prepare_owner_digest
    from study.services.reviews import get_review_day, set_timezone
    set_timezone(publisher, "Asia/Tokyo", now=NOW)
    digest = prepare_owner_digest(now=NOW)
    if newly_due:
        from study.models import ReviewSchedule
        from tests.website.test_reviews import finish
        ReviewSchedule.objects.filter(article__in=digest_articles[3:]).update(next_due_date=NOW.date() + timedelta(days=1))
        for article in digest_articles[:3]:
            finish(publisher, article, NOW)
    before_boundary = NOW + timedelta(hours=14) - timedelta(seconds=1)
    waiting, resume = Event(), Event()
    profile_locks = []
    fake = FakeDelivery()
    main_pid = _pid()

    def pause(execute, sql, params, many, context):
        if 'FROM "study_readerprofile"' in sql and "FOR UPDATE" in sql:
            profile_locks.append(sql)
            if len(profile_locks) == 2:
                waiting.set()
                assert resume.wait(timeout=10)
        return execute(sql, params, many, context)

    def deliver():
        connections.close_all()
        try:
            assert _pid() != main_pid
            with connection.execute_wrapper(pause):
                return deliver_digest(digest.pk, now=before_boundary, delivery=fake)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(deliver)
        try:
            assert waiting.wait(timeout=10)
            get_review_day(publisher, now=before_boundary + timedelta(seconds=1))
        finally:
            resume.set()
        assert pending.result(timeout=10) == "sent"
    assert len(profile_locks) >= 4
    assert "2026-09-15" in fake.calls[0][0]["text"]
    if newly_due:
        from study.services.reviews import today_payload
        visible = today_payload(publisher, now=before_boundary + timedelta(seconds=1))["slots"]
        assert len(visible) == 2
        assert all(slot["title"] in fake.calls[0][0]["text"] for slot in visible)
