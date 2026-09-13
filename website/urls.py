from allauth.account import views as account_views
from allauth.socialaccount.providers.google import urls as google_urls
from django.urls import include, path

from study.views import identity, pages


urlpatterns = [
    path("", pages.home, name="home"),
    path("healthz", pages.healthz, name="healthz"),
    path("api/session", identity.session, name="session"),
    path("api/mode", identity.mode, name="mode"),
    path("api/timezone", identity.timezone, name="timezone"),
    path("accounts/login/", identity.account_login, name="account_login"),
    path("accounts/logout/", account_views.logout, name="account_logout"),
    path("accounts/", include(google_urls)),
]

handler404 = pages.not_found
handler500 = pages.server_error
