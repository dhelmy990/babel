"""Owner-scoped notes, serialized against article editing and archival."""
import math
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from study.models import ArchiveAccess, Article, Note
from study.services.content import can_read_article


class NoteConflict(ValueError):
    pass


def _require_user(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Sign in is required")


def _uuid(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Invalid UUID")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc


def _validate_content(text, x, y):
    if not isinstance(text, str) or len(text) > 20000 or "\x00" in text:
        raise ValueError("Text must contain at most 20000 plaintext characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Text must be valid Unicode") from exc
    if x is None and y is None:
        return
    for value in (x, y):
        # Check bounds before isfinite to avoid overflowing a huge JSON integer.
        if type(value) not in (int, float) or not 0 <= value <= 1000000 or not math.isfinite(value):
            raise ValueError("Coordinates must both be null or finite numbers from 0 to 1000000")


def _readable_article(user, article_id, *, lock=False):
    articles = Article.objects.select_for_update() if lock else Article.objects
    try:
        article = articles.get(pk=article_id)
    except Article.DoesNotExist as exc:
        raise Http404 from exc
    if not can_read_article(user, article):
        raise Http404
    return article


def list_notes(user, article_id) -> list[Note]:
    _require_user(user)
    article = _readable_article(user, _uuid(article_id))
    return list(Note.objects.filter(user=user, article=article).order_by("created_at", "id"))


def _retry_note(user, note_id, article, kind, text, x, y):
    note = Note.objects.filter(pk=note_id).first()
    if note is None or note.user_id != user.pk:
        raise Http404
    if (note.article_id, note.kind, note.text, note.x, note.y) != (article.pk, kind, text, x, y):
        raise NoteConflict("Note id was already used for different content")
    return note


def create_note(user, article_id, *, note_id, kind, text, x, y) -> Note:
    _require_user(user)
    article_id, note_id = _uuid(article_id), _uuid(note_id)
    _validate_content(text, x, y)
    if not isinstance(kind, str) or kind not in Note.Kind.values:
        raise ValueError("Kind must be sticky or text")
    with transaction.atomic():
        article = _readable_article(user, article_id, lock=True)
        if Note.objects.filter(pk=note_id).exists():
            return _retry_note(user, note_id, article, kind, text, x, y)
        try:
            # Different articles have different locks: a simultaneous UUID insert
            # can still win. Roll back only this savepoint before reading it.
            with transaction.atomic():
                return Note.objects.create(id=note_id, user=user, article=article, kind=kind, text=text, x=x, y=y)
        except IntegrityError:
            return _retry_note(user, note_id, article, kind, text, x, y)


def _owned_article_id(user, note_id):
    article_id = Note.objects.filter(pk=note_id, user=user).values_list("article_id", flat=True).first()
    if article_id is None:
        raise Http404
    return article_id


def update_note(user, note_id, *, expected_version, text, x, y) -> Note:
    _require_user(user)
    note_id = _uuid(note_id)
    _validate_content(text, x, y)
    if type(expected_version) is not int or not 1 <= expected_version <= 2147483647:
        raise ValueError("Version must be a positive integer within the storage range")
    article_id = _owned_article_id(user, note_id)
    with transaction.atomic():
        _readable_article(user, article_id, lock=True)
        owned = Note.objects.filter(pk=note_id, user=user, article_id=article_id)
        if not owned.exists():
            raise Http404
        if expected_version == 2147483647:
            raise NoteConflict("Note version limit reached")
        changed = owned.filter(version=expected_version).update(
            text=text, x=x, y=y, version=F("version") + 1, updated_at=timezone.now(),
        )
        if not changed:
            raise NoteConflict("Note has changed")
        return owned.get()


def delete_note(user, note_id) -> None:
    _require_user(user)
    note_id = _uuid(note_id)
    article_id = _owned_article_id(user, note_id)
    with transaction.atomic():
        _readable_article(user, article_id, lock=True)
        deleted, _ = Note.objects.filter(pk=note_id, user=user, article_id=article_id).delete()
        if not deleted:
            raise Http404


def grant_archive_access(article):
    """Snapshot note owners while the caller holds this article's row lock."""
    owners = Note.objects.filter(article=article).order_by().values_list("user_id", flat=True).distinct()
    ArchiveAccess.objects.bulk_create(
        [ArchiveAccess(user_id=user_id, article=article) for user_id in owners],
        ignore_conflicts=True,
    )
