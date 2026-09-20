from study.services.identity import is_publisher


def identity(request):
    user = getattr(request, "user", None)
    can_publish = user is not None and is_publisher(user)
    mode = "admin" if can_publish and request.session.get("study.mode") == "admin" else "reader"
    return {"can_publish": can_publish, "identity_mode": mode}
