"""Session-owned JSON API. POST/PATCH return {note: ...}; lists return {notes: ...}."""
import json
import logging
from functools import wraps

from django.core.exceptions import PermissionDenied, RequestDataTooBig
from django.http import Http404, HttpResponse, JsonResponse

from study.services.notes import NoteConflict, create_note, delete_note, list_notes, update_note
from study.views.identity import authenticated_json_write, error


logger = logging.getLogger(__name__)


def _private_api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = error("not_found", "Note or article was not found.", 404)
        except PermissionDenied:
            response = error("authentication_required", "Sign in is required.", 401)
        except NoteConflict as exc:
            response = error("note_conflict", str(exc), 409)
        except (ValueError, UnicodeDecodeError, RequestDataTooBig) as exc:
            response = error("invalid_note", "Invalid note data." if not str(exc) else str(exc), 400)
        except Exception:
            logger.exception("Private notes request failed")
            response = error("server_error", "Unable to process the note request.", 500)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def _payload(request, fields):
    data = json.loads(request.body)
    if not isinstance(data, dict) or set(data) != set(fields):
        raise ValueError("Note data must contain exactly: " + ", ".join(fields))
    return data


def _serialize(note):
    return {
        "id": str(note.pk), "article_id": str(note.article_id), "kind": note.kind,
        "text": note.text, "x": note.x, "y": note.y, "version": note.version,
        "updated_at": note.updated_at.isoformat(),
    }


def _method_not_allowed(methods):
    response = error("method_not_allowed", "Method is not allowed.", 405)
    response["Allow"] = ", ".join(methods)
    return response


@_private_api
@authenticated_json_write
def article_notes(request, article_id):
    if request.method == "GET":
        return JsonResponse({"notes": [_serialize(note) for note in list_notes(request.user, article_id)]})
    if request.method == "POST":
        data = _payload(request, ("id", "kind", "text", "x", "y"))
        note = create_note(request.user, article_id, note_id=data["id"], kind=data["kind"], text=data["text"], x=data["x"], y=data["y"])
        return JsonResponse({"note": _serialize(note)}, status=201)
    return _method_not_allowed(("GET", "POST"))


@_private_api
@authenticated_json_write
def note_detail(request, note_id):
    if request.method == "PATCH":
        data = _payload(request, ("version", "text", "x", "y"))
        note = update_note(request.user, note_id, expected_version=data["version"], text=data["text"], x=data["x"], y=data["y"])
        return JsonResponse({"note": _serialize(note)})
    if request.method == "DELETE":
        delete_note(request.user, note_id)
        return HttpResponse(status=204)
    return _method_not_allowed(("PATCH", "DELETE"))
