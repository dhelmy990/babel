from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

pytestmark = pytest.mark.django_db


def test_archive_hides_incident_edges_without_rewiring_and_freezes_content(publisher, article_factory, client):
    from study.models import Article, Edge
    from study.services.content import archive_article, update_article
    from study.services.graph import add_edge, published_graph
    a, b, c = [article_factory(title) for title in ('A', 'B', 'C')]
    add_edge(publisher, a.pk, b.pk)
    add_edge(publisher, b.pk, c.pk)
    archived = archive_article(publisher, b.pk)
    assert archive_article(publisher, b.pk).archived_at == archived.archived_at
    assert Edge.objects.count() == 2
    assert published_graph()['edges'] == []
    assert {node['id'] for node in published_graph()['nodes']} == {str(a.pk), str(c.pk)}
    assert client.get('/b').status_code == 404
    assert b'href="/b"' not in client.get('/').content
    assert list(client.get('/a').context['successors']) == []
    assert list(client.get('/c').context['prerequisites']) == []
    with pytest.raises(Article.DoesNotExist):
        add_edge(publisher, a.pk, b.pk)
    with pytest.raises(ValueError, match='Archived'):
        update_article(publisher, b.pk, expected_revision=1, title='B', color='#1a5276', markdown='# B', images={})


def test_archive_permissions_and_access_grant(publisher, article_factory, client):
    from study.models import ArchiveAccess
    from study.services.content import archive_article
    a = article_factory('A')
    reader = get_user_model().objects.create_user(username='reader')
    with pytest.raises(PermissionDenied):
        archive_article(reader, a.pk)
    assert client.post(f'/api/articles/{a.pk}/archive').status_code == 401
    client.force_login(reader)
    assert client.post(f'/api/articles/{a.pk}/archive').status_code == 403
    client.force_login(publisher)
    assert client.post(f'/api/articles/{a.pk}/archive').status_code == 200
    assert client.post(f'/api/articles/{a.pk}/archive').status_code == 200
    assert client.post(f'/api/articles/{uuid4()}/archive').status_code == 404
    assert client.get('/a').status_code == 200
    client.force_login(reader)
    assert client.get('/a').status_code == 404
    ArchiveAccess.objects.create(user=reader, article=a)
    assert client.get('/a').status_code == 200


def test_publish_slug_is_reserved(publisher, article_factory):
    with pytest.raises(ValueError, match='reserved'):
        article_factory('Publish')
