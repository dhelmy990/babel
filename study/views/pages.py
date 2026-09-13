from django.db import OperationalError, connection
from django.http import JsonResponse
from django.shortcuts import render


def home(request):
    return render(request, "study/home.html", {"articles": []})


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def not_found(request, exception):
    return render(request, "study/404.html", status=404)


def server_error(request):
    return render(request, "study/500.html", status=500)
