from django.urls import path

from study.views import pages


urlpatterns = [
    path("", pages.home, name="home"),
    path("healthz", pages.healthz, name="healthz"),
]

handler404 = pages.not_found
handler500 = pages.server_error
