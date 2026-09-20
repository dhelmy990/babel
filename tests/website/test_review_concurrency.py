"""PostgreSQL serialization checks using independent real connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.http import Http404

from tests.website.test_reviews import NOW, due

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
    return [result for _, result in results]


def _wait_blocked(blocked_pid, blocker_pid):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker_pid, blocked_pid])
            if cursor.fetchone()[0]:
                return
        sleep(0.01)
    pytest.fail("Expected the second transaction to wait on the first")


def test_same_day_materialization_creates_one_day_and_three_slots(reader, article_factory):
    from study.models import ReviewDay, ReviewSlot
    from study.services.reviews import get_review_day
    for i in range(5):
        due(reader, article_factory(f"Article {i}"))
    results = _parallel([lambda: get_review_day(reader, now=NOW).pk] * 2)
    assert len(set(results)) == 1
    assert ReviewDay.objects.count() == 1 and ReviewSlot.objects.count() == 3


def test_concurrent_initial_profile_creation_is_unique():
    from study.models import ReaderProfile, ReviewDay
    from study.services.reviews import get_review_day
    user = get_user_model().objects.create_user(username="new-concurrent-reader")
    ids = _parallel([lambda: get_review_day(user, now=NOW).pk] * 2)
    assert len(set(ids)) == 1
    assert ReaderProfile.objects.filter(user=user).count() == 1
    assert ReviewDay.objects.count() == 1


@pytest.mark.parametrize("existing", [False, True])
def test_same_generation_can_only_complete_once(reader, article_factory, existing):
    from study.models import ReviewSchedule
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    if existing:
        due(reader, article)
    token = reading_context(reader, article.pk, now=NOW)["token"]
    results = _parallel([lambda: complete_article(reader, article.pk, token=token, now=NOW)] * 2)
    assert sorted(result["status"] for result in results) == sorted(["already_processed", "reviewed" if existing else "first_read"])
    schedule = ReviewSchedule.objects.get()
    assert schedule.generation == (2 if existing else 1)
    assert int(schedule.interval_days) == (2 if existing else 1)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("first", ["archive", "complete"])
def test_archive_and_completion_never_leave_active_archived_schedule(reader, publisher, article_factory, existing, first):
    from study.models import ReviewSchedule, ReviewSlot
    from study.services.content import archive_article
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    if existing:
        due(reader, article)
    token = reading_context(reader, article.pk, now=NOW)["token"]
    locked, started, release = Event(), Event(), Event()
    pids = {}

    def perform(operation):
        if operation == "archive":
            archive_article(publisher, article.pk)
            return "archived"
        try:
            return complete_article(reader, article.pk, token=token, now=NOW)["status"]
        except Http404:
            return "denied"

    def leading():
        connections.close_all()
        try:
            pids["first"] = _pid()
            with transaction.atomic():
                result = perform(first)
                locked.set()
                assert release.wait(timeout=15)
            return result
        finally:
            connections.close_all()

    def following():
        connections.close_all()
        try:
            assert locked.wait(timeout=10)
            pids["second"] = _pid()
            started.set()
            return perform("complete" if first == "archive" else "archive")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(leading), pool.submit(following)
        try:
            assert started.wait(timeout=10)
            assert pids["first"] != pids["second"]
            _wait_blocked(pids["second"], pids["first"])
        finally:
            release.set()
        results = {a.result(timeout=15), b.result(timeout=15)}
    expected = "denied" if first == "archive" else "reviewed" if existing else "first_read"
    assert results == {"archived", expected}
    assert not ReviewSchedule.objects.filter(article=article, suspended=False).exists()
    assert ReviewSchedule.objects.filter(article=article).count() == int(existing or first == "complete")
    if existing:
        slot = ReviewSlot.objects.get()
        assert (slot.completed_at is not None) == (first == "complete")
        assert (slot.cancelled_at is not None) == (first == "archive")


def test_timezone_and_rollover_share_profile_lock(reader):
    from study.models import ReaderProfile, ReviewDay
    from study.services.reviews import get_review_day, set_timezone
    day = get_review_day(reader, now=NOW)
    set_timezone(reader, "UTC", now=NOW)
    boundary = day.next_boundary_at
    _parallel([lambda: get_review_day(reader, now=boundary), lambda: set_timezone(reader, "America/Los_Angeles", now=boundary)])
    profile = ReaderProfile.objects.get(user=reader)
    assert profile.active_day_id == day.pk
    assert profile.timezone == "UTC" and profile.pending_timezone == "America/Los_Angeles"
    assert ReviewDay.objects.count() == 1


def test_archive_before_selection_lock_replaces_candidate_before_day_is_frozen(reader, publisher, article_factory):
    from study.services.content import archive_article
    from study.services.reviews import get_review_day
    articles = [article_factory(f"Article {i}") for i in range(4)]
    for i, article in enumerate(articles):
        due(reader, article, due_date=NOW.date() - timedelta(days=4-i))
    candidates_chosen, resume = Event(), Event()
    main_pid = _pid()

    def pause_before_articles(execute, sql, params, many, context):
        if 'FROM "study_article"' in sql and "FOR UPDATE" in sql:
            candidates_chosen.set()
            assert resume.wait(timeout=10)
        return execute(sql, params, many, context)

    def materialize():
        connections.close_all()
        try:
            assert _pid() != main_pid
            with connection.execute_wrapper(pause_before_articles):
                day = get_review_day(reader, now=NOW)
            return list(day.slots.order_by("ordinal").values_list("article_id", flat=True))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(materialize)
        try:
            assert candidates_chosen.wait(timeout=10)
            archive_article(publisher, articles[0].pk)
        finally:
            resume.set()
        assert pending.result(timeout=10) == [article.pk for article in articles[1:]]


@pytest.mark.parametrize("operation", ["context", "complete"])
def test_preflight_article_selection_commits_before_target_article_lock(reader, article_factory, operation):
    from django.core import signing
    from study.services.reviews import complete_article, reading_context
    article = article_factory("Article")
    due(reader, article)
    transactions = []

    def observe(execute, sql, params, many, context):
        if 'FROM "study_article"' in sql and "FOR UPDATE" in sql:
            with connection.cursor() as cursor:
                cursor.execute("SELECT txid_current()")
                transactions.append(cursor.fetchone()[0])
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        if operation == "context":
            assert reading_context(reader, article.pk, now=NOW)["eligible"]
        else:
            token = signing.Signer(salt="study-reading").sign_object({
                "user_id": reader.pk, "article_id": str(article.pk), "generation": 1, "issued_at": NOW.timestamp(),
            })
            assert complete_article(reader, article.pk, token=token, now=NOW)["status"] == "reviewed"
    assert len(transactions) == 2
    assert transactions[0] != transactions[1]


def test_completion_retries_preflight_if_active_day_changes_before_profile_lock(reader, article_factory):
    from study.models import ReviewSchedule
    from study.services.reviews import complete_article, get_review_day, reading_context
    article = article_factory("Article")
    due(reader, article)
    token = reading_context(reader, article.pk, now=NOW)["token"]
    preflight_done, resume = Event(), Event()
    main_pid = _pid()
    profile_lock_queries = []

    def pause_second_profile_lock(execute, sql, params, many, context):
        if 'FROM "study_readerprofile"' in sql and "FOR UPDATE" in sql:
            profile_lock_queries.append(sql)
            if len(profile_lock_queries) == 2:
                preflight_done.set()
                assert resume.wait(timeout=10)
        return execute(sql, params, many, context)

    def finish():
        connections.close_all()
        try:
            assert _pid() != main_pid
            with connection.execute_wrapper(pause_second_profile_lock):
                return complete_article(reader, article.pk, token=token, now=NOW)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(finish)
        try:
            assert preflight_done.wait(timeout=10)
            newer_day = get_review_day(reader, now=NOW + timedelta(days=1))
        finally:
            resume.set()
        assert pending.result(timeout=10)["status"] == "reviewed"
    assert len(profile_lock_queries) >= 4  # Repeated preflight and then a fresh profile lock.
    assert newer_day.slots.get().completed_at == NOW
    assert ReviewSchedule.objects.get().generation == 2
