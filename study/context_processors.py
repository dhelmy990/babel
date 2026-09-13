from study.services.identity import is_publisher


def identity(request):
    can_publish = is_publisher(request.user)
    mode = "admin" if can_publish and request.session.get("study.mode") == "admin" else "reader"
    return {"can_publish": can_publish, "identity_mode": mode}
