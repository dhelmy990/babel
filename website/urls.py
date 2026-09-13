from allauth.account import views as account_views
from allauth.socialaccount.providers.google import urls as google_urls
from django.urls import include, path

from study.views import content, graph, identity, pages


urlpatterns = [
    path("", pages.home, name="home"),
    path("healthz", pages.healthz, name="healthz"),
    path("galaxy", graph.galaxy, name="galaxy"),
    path("api/graph", graph.graph_data, name="graph_data"),
    path("api/edges", graph.create_edge, name="edge_create"),
    path("api/edges/<uuid:source_id>/<uuid:target_id>", graph.delete_edge, name="edge_delete"),
    path("api/articles/<uuid:article_id>/archive", graph.archive, name="article_archive"),
    path("robots.txt", pages.not_found, name="robots"),
    path("assets/<uuid:asset_id>", content.asset_response, name="asset"),
    path("api/articles/preview", content.preview, name="article_preview"),
    path("api/articles", content.publish, name="article_publish"),
    path("api/articles/<uuid:article_id>", content.update, name="article_update"),
    path("api/session", identity.session, name="session"),
    path("api/mode", identity.mode, name="mode"),
    path("api/timezone", identity.timezone, name="timezone"),
    path("accounts/login/", identity.account_login, name="account_login"),
    path("accounts/logout/", account_views.logout, name="account_logout"),
    path("accounts/", include(google_urls)),
    path("<slug:slug>", content.article_detail, name="article_detail"),
]

handler404 = pages.not_found
handler500 = pages.server_error
