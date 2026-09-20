from django.db import OperationalError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def home(request):
    from study.models import Article

    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    articles = list(Article.objects.filter(archived_at__isnull=True)[(page - 1) * 20 : page * 20 + 1])
    has_next = len(articles) > 20
    return render(request, "study/home.html", {"articles": articles[:20], "next_page": page + 1 if has_next else None})


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def not_found(request, exception=None):
    return render(request, "study/404.html", status=404)


def server_error(request):
    return render(request, "study/500.html", status=500)
