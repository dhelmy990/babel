from allauth.account import views as account_views
from allauth.socialaccount.providers.google import urls as google_urls
from django.urls import include, path

from study.views import content, graph, identity, notes, pages, reviews


urlpatterns = [
    path("", pages.home, name="home"),
    path("healthz", pages.healthz, name="healthz"),
    path("galaxy", graph.galaxy, name="galaxy"),
    path("api/articles/<str:article_id>/notes", notes.article_notes, name="article_notes"),
    path("api/notes/<str:note_id>", notes.note_detail, name="note_detail"),
    path("api/reviews/today", reviews.today, name="reviews_today"),
    path("api/reviews/calendar", reviews.calendar_data, name="reviews_calendar_data"),
    path("reviews/calendar", reviews.calendar_page, name="reviews_calendar"),
    path("api/articles/<str:article_id>/reading-context", reviews.context, name="reading_context"),
    path("api/articles/<str:article_id>/complete", reviews.complete, name="article_complete"),
    path("api/graph", graph.graph_data, name="graph_data"),
    path("api/edges", graph.create_edge, name="edge_create"),
    path("api/edges/<uuid:source_id>/<uuid:target_id>", graph.delete_edge, name="edge_delete"),
    path("api/articles/<uuid:article_id>/archive", graph.archive, name="article_archive"),
    path("robots.txt", pages.not_found, name="robots"),
    path("assets/<uuid:asset_id>", content.asset_response, name="asset"),
    path("api/articles/preview", content.preview, name="article_preview"),
    path("api/articles", content.publish, name="article_publish"),
    path("api/articles/<uuid:article_id>", content.update, name="article_update"),
    path("publish", content.publishing, name="article_new"),
    path("publish/<uuid:article_id>", content.publishing, name="article_edit"),
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
