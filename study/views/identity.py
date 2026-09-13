import json
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.http import JsonResponse
from django.shortcuts import render
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from study.services.identity import is_publisher, profile_for


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
        if csrf_response := _csrf_check(request):
            return csrf_response
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
    }


@require_GET
def session(request):
    return JsonResponse(_session_payload(request))


@require_POST
@authenticated_json_write
def mode(request):
    payload = _payload(request)
    if payload is None or payload.get("mode") not in {"reader", "admin"}:
        return error("invalid_mode", "Mode must be reader or admin.", 400)
    if payload["mode"] == "admin" and not is_publisher(request.user):
        return error("publisher_required", "Publisher access is required.", 403)
    request.session["study.mode"] = payload["mode"]
    return JsonResponse(_session_payload(request))


@require_POST
@authenticated_json_write
def timezone(request):
    payload = _payload(request)
    value = payload.get("timezone") if payload else None
    if not isinstance(value, str):
        return error("invalid_timezone", "Timezone must be an IANA timezone name.", 400)
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return error("invalid_timezone", "Timezone must be an IANA timezone name.", 400)
    profile = profile_for(request.user)
    profile.timezone = value
    profile.save(update_fields=["timezone"])
    return JsonResponse(_session_payload(request))
