"""Real-browser fixtures for the owner publishing flow."""
import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import sync_playwright


pytestmark = pytest.mark.django_db(transaction=True)


def _session_cookie(live_server, client, user):
    client.force_login(user)
    session = client.session
    session["study.mode"] = "reader"
    session.save()
    return {"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}


def _page(cookie=None):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        if cookie:
            page.context.add_cookies([cookie])
        yield page
        browser.close()


@pytest.fixture
def publisher_page(live_server, publisher, client):
    """A Chromium page authenticated by the real publisher session cookie."""
    yield from _page(_session_cookie(live_server, client, publisher))


@pytest.fixture
def reader_page(article_factory):
    yield from _page()


@pytest.fixture
def other_reader_page(live_server, client, db):
    reader = get_user_model().objects.create_user(username="browser-reader", email="reader@example.com")
    yield from _page(_session_cookie(live_server, client, reader))
