import json
from functools import wraps

from django.utils import timezone as django_timezone
from django.http import JsonResponse
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import render
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from study.services.identity import is_publisher, profile_for
from study.services.reviews import set_timezone


def error(code, message, status):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def csrf_failure(request, reason=""):
    return error("csrf_failed", "CSRF validation failed.", 403)


@require_GET
def account_login(request):
    return render(request, "account/login.html")


def _csrf_check(request):
    def protected_view(request):
        return None

    return CsrfViewMiddleware(lambda request: None).process_view(
        request, protected_view, (), {}
    )


def authenticated_json_write(view):
    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("authentication_required", "Sign in is required.", 401)
        try:
            if csrf_response := _csrf_check(request):
                return csrf_response
        except RequestDataTooBig:
            return error("request_too_large", "Request is too large.", 400)
        except (TooManyFilesSent, TooManyFieldsSent, MultiPartParserError):
            return error("invalid_request", "Request multipart data is invalid.", 400)
        return view(request, *args, **kwargs)

    return wrapped


def _payload(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _session_payload(request):
    if not request.user.is_authenticated:
        return {
            "authenticated": False,
            "can_publish": False,
            "mode": "reader",
            "timezone": None,
        }
    profile = profile_for(request.user)
    can_publish = is_publisher(request.user)
    mode = "admin" if can_publish and request.session.get("study.mode") == "admin" else "reader"
    return {
        "authenticated": True,
        "can_publish": can_publish,
        "mode": mode,
        "timezone": profile.timezone,
        "pending_timezone": profile.pending_timezone,
    }


@require_GET
def session(request):
    response = JsonResponse(_session_payload(request))
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
@authenticated_json_write
def mode(request):
    payload = _payload(request)
    value = payload.get("mode") if payload else None
    if not isinstance(value, str) or value not in {"reader", "admin"}:
        return error("invalid_mode", "Mode must be reader or admin.", 400)
    if value == "admin" and not is_publisher(request.user):
        return error("publisher_required", "Publisher access is required.", 403)
    request.session["study.mode"] = value
    return JsonResponse(_session_payload(request))


def _private_response(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


@_private_response
@require_POST
@authenticated_json_write
def timezone(request):
    try:
        if request.content_type != "application/json":
            raise ValueError
        payload = _payload(request)
        if payload is None or set(payload) != {"timezone"}:
            raise ValueError
        set_timezone(request.user, payload["timezone"], now=django_timezone.now())
    except (ValueError, RequestDataTooBig):
        return error("invalid_timezone", "Timezone must be an IANA timezone name.", 400)
    return JsonResponse(_session_payload(request))
