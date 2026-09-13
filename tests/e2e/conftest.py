"""Real-browser fixtures for the owner publishing flow."""
import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import sync_playwright


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
)


pytestmark = pytest.mark.django_db(transaction=True)


def _session_cookie(live_server, client, user, mode="reader"):
    client.force_login(user)
    session = client.session
    session["study.mode"] = mode
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
def published_owner_page(live_server, article_factory, publisher, client):
    """An admin-mode owner page seeded before Playwright starts its sync loop."""
    article = article_factory("Archive me")
    cookie = _session_cookie(live_server, client, publisher, mode="admin")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.add_cookies([cookie])
        yield page, article
        browser.close()
    from study.models import Article
    assert Article.objects.get(pk=article.pk).archived_at is not None


@pytest.fixture
def editable_owner_page(live_server, article_factory, publisher, client):
    """An existing article and admin session, both prepared before browser work."""
    from uuid import uuid4
    from study.services.content import publish_article
    article = publish_article(
        publisher, title="Editable", color="#1a5276",
        markdown="# Editable\n\n![Kept image](images/kept.png)\n\nOriginal body.",
        images={"images/kept.png": PNG}, submission_id=uuid4(),
    )
    cookie = _session_cookie(live_server, client, publisher, mode="admin")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.add_cookies([cookie])
        yield page, article
        browser.close()


@pytest.fixture
def visual_owner_page(live_server, article_factory, publisher, client):
    """Multi-entry, long-form content for screenshot review only; test DB/media stay disposable."""
    from study.services.content import update_article
    articles = [article_factory(title) for title in ("Foundations", "Ownership", "Model serving")]
    update_article(
        publisher, articles[0].pk, expected_revision=articles[0].revision,
        title="Foundations", color="#1a5276",
        markdown=("# Foundations\n\nA moderately long opening paragraph keeps the article proportions visible "
                  "while explaining how the pieces fit together over time.\n\n## A useful boundary\n\n"
                  "The body uses real Markdown rendering, paragraph spacing, and a code sample.\n\n"
                  "```cpp\nstd::unique_ptr<Node> root;\n```\n\n## Sources\n\n"
                  "[Reference](https://example.com/reference)\n[Further reading](https://example.com/further)\n[Design note](https://example.com/design)"),
        images={},
    )
    cookie = _session_cookie(live_server, client, publisher, mode="admin")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        desktop = browser.new_page(viewport={"width": 1440, "height": 1100})
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        mobile = mobile_context.new_page()
        desktop.context.add_cookies([cookie])
        mobile.context.add_cookies([cookie])
        yield desktop, mobile, articles[0]
        mobile_context.close()
        browser.close()


@pytest.fixture
def retry_publish_page(live_server, publisher, client):
    """A fresh owner form whose teardown proves retry idempotency in PostgreSQL."""
    cookie = _session_cookie(live_server, client, publisher, mode="admin")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.add_cookies([cookie])
        yield page
        browser.close()
    from study.models import Article
    assert Article.objects.filter(slug="retry-on-lost-response").count() == 1


@pytest.fixture
def anonymous_page():
    yield from _page()


@pytest.fixture
def reader_page(live_server, client, db):
    reader = get_user_model().objects.create_user(username="browser-reader", email="reader@example.com")
    from study.services.identity import profile_for
    profile_for(reader)
    yield from _page(_session_cookie(live_server, client, reader))


@pytest.fixture
def other_reader_page(live_server, client, db):
    reader = get_user_model().objects.create_user(username="other-browser-reader", email="other-reader@example.com")
    from study.services.identity import profile_for
    profile_for(reader)
    yield from _page(_session_cookie(live_server, client, reader))
