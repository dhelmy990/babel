"""Private review JSON endpoints; the session and server clock own all state."""
import json
import logging
from functools import wraps

from django.core.exceptions import PermissionDenied, RequestDataTooBig
from django.http import Http404, JsonResponse
from django.utils import timezone

from study.services.reviews import ReadingTokenError, complete_article, reading_context, today_payload
from study.views.identity import authenticated_json_write, error

logger = logging.getLogger(__name__)


def _private_api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = error("not_found", "Article was not found.", 404)
        except PermissionDenied:
            response = error("authentication_required", "Sign in is required.", 401)
        except ReadingTokenError as exc:
            response = error(exc.code, str(exc), 400)
        except (ValueError, RequestDataTooBig) as exc:
            response = error("invalid_review", str(exc) or "Invalid review data.", 400)
        except Exception:
            logger.exception("Private review request failed")
            response = error("server_error", "Unable to process the review request.", 500)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def _payload(request, fields):
    if request.content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
        if fields or request.POST or request.FILES:
            raise ValueError("Review data must be JSON with the required fields")
        return {}
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict) or set(data) != set(fields):
        raise ValueError("Review data must contain exactly: " + ", ".join(fields))
    return data


def _wrong_method(method):
    response = error("method_not_allowed", "Method is not allowed.", 405)
    response["Allow"] = method
    return response


@_private_api
@authenticated_json_write
def today(request):
    if request.method != "POST":
        return _wrong_method("POST")
    _payload(request, ())
    return JsonResponse(today_payload(request.user, now=timezone.now()))


@_private_api
@authenticated_json_write
def context(request, article_id):
    if request.method != "GET":
        return _wrong_method("GET")
    return JsonResponse(reading_context(request.user, article_id, now=timezone.now()))


@_private_api
@authenticated_json_write
def complete(request, article_id):
    if request.method != "POST":
        return _wrong_method("POST")
    data = _payload(request, ("token",))
    return JsonResponse(complete_article(request.user, article_id, token=data["token"], now=timezone.now()))
