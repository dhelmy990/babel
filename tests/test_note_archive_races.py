"""Exercise actual PostgreSQL locks with independent connections and commits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.http import Http404

pytestmark = pytest.mark.django_db(transaction=True)


def _pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _wait_until_blocked(blocked_pid, blocker_pid):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker_pid, blocked_pid])
            if cursor.fetchone()[0]:
                return
        sleep(0.01)
    pytest.fail("Second transaction did not wait for the first article lock")


@pytest.mark.parametrize("first", ["note", "archive"])
def test_first_note_and_archive_serialize_without_stranded_owner(publisher, reader, article_factory, first):
    from study.models import ArchiveAccess, Article, Note
    from study.services.content import archive_article, can_read_article
    from study.services.notes import create_note
    assert connection.vendor == "postgresql"
    article = article_factory("Article")
    locked, second_started, release = Event(), Event(), Event()
    pids = {}
    note_id = uuid4()

    def perform(operation):
        if operation == "archive":
            archive_article(get_user_model().objects.get(pk=publisher.pk), article.pk)
            return "archived"
        try:
            create_note(get_user_model().objects.get(pk=reader.pk), article.pk,
                        note_id=note_id, kind="sticky", text="Private", x=1, y=2)
            return "created"
        except Http404:
            return "denied"

    def leading_transaction():
        connections.close_all()
        try:
            pids["first"] = _pid()
            with transaction.atomic():
                result = perform(first)
                locked.set()  # Service returned but the outer transaction retains its lock.
                assert release.wait(timeout=15)
            return result
        finally:
            connections.close_all()

    def following_transaction():
        connections.close_all()
        try:
            assert locked.wait(timeout=10)
            pids["second"] = _pid()
            second_started.set()
            return perform("archive" if first == "note" else "note")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        leading = pool.submit(leading_transaction)
        following = pool.submit(following_transaction)
        try:
            assert second_started.wait(timeout=10)
            assert pids["first"] != pids["second"]
            _wait_until_blocked(pids["second"], pids["first"])
        finally:
            release.set()
        outcomes = {leading.result(timeout=15), following.result(timeout=15)}
    article.refresh_from_db()
    assert article.archived_at is not None
    exists = Note.objects.filter(pk=note_id, user=reader).exists()
    granted = ArchiveAccess.objects.filter(article=article, user=reader).exists()
    assert exists == granted == can_read_article(reader, article)
    assert exists is (first == "note")
    assert outcomes == {"archived", "created" if first == "note" else "denied"}
    assert Article.objects.count() == 1


@pytest.mark.parametrize("same_owner", [True, False])
def test_simultaneous_same_uuid_on_different_articles_is_privacy_safe(reader, other_reader, article_factory, same_owner):
    from study.models import Note
    from study.services.notes import NoteConflict, create_note
    assert connection.vendor == "postgresql"
    articles = [article_factory("First"), article_factory("Second")]
    users = [reader, reader if same_owner else other_reader]
    note_id = uuid4()
    inserting = Barrier(2)

    def insert_together(execute, sql, params, many, context):
        if sql.startswith('INSERT INTO "study_note"'):
            inserting.wait(timeout=10)
        return execute(sql, params, many, context)

    def create(index):
        connections.close_all()
        try:
            pid = _pid()
            user = get_user_model().objects.get(pk=users[index].pk)
            with connection.execute_wrapper(insert_together):
                try:
                    note = create_note(user, articles[index].pk, note_id=note_id, kind="text", text="Private", x=None, y=None)
                    assert note.article_id == articles[index].pk and note.user_id == user.pk
                    outcome = "created"
                except NoteConflict:
                    outcome = "conflict"
                except Http404:
                    outcome = "hidden"
            assert Note.objects.count() == 1
            return pid, outcome
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert len({pid for pid, _ in results}) == 2
    assert sorted(outcome for _, outcome in results) == sorted(["created", "conflict" if same_owner else "hidden"])
    assert Note.objects.count() == 1


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_recreated_uuid_on_another_article_is_not_changed_under_old_article_lock(reader, article_factory, operation):
    from study.models import Note
    from study.services.notes import create_note, delete_note, update_note
    articles = [article_factory("Original"), article_factory("Replacement")]
    note_id = uuid4()
    create_note(reader, articles[0].pk, note_id=note_id, kind="text", text="Original", x=None, y=None)
    resolved, resume = Event(), Event()
    main_pid = _pid()

    def pause_before_lock(execute, sql, params, many, context):
        if 'FROM "study_article"' in sql and "FOR UPDATE" in sql:
            resolved.set()
            assert resume.wait(timeout=10)
        return execute(sql, params, many, context)

    def mutate_original():
        connections.close_all()
        try:
            worker_pid = _pid()
            assert worker_pid != main_pid
            user = get_user_model().objects.get(pk=reader.pk)
            with connection.execute_wrapper(pause_before_lock):
                try:
                    if operation == "update":
                        update_note(user, note_id, expected_version=1, text="Unwanted", x=1, y=2)
                    else:
                        delete_note(user, note_id)
                except Http404:
                    return "missing"
            return "changed"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(mutate_original)
        try:
            assert resolved.wait(timeout=10)
            delete_note(reader, note_id)
            create_note(reader, articles[1].pk, note_id=note_id, kind="text", text="Replacement", x=None, y=None)
        finally:
            resume.set()
        assert pending.result(timeout=10) == "missing"
    replacement = Note.objects.get(pk=note_id)
    assert replacement.article_id == articles[1].pk
    assert replacement.text == "Replacement" and replacement.version == 1
