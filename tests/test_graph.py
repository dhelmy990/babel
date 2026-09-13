from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

pytestmark = pytest.mark.django_db


def test_edges_are_directed_idempotent_and_keep_explicit_transitive_links(publisher, article_factory):
    from study.models import Edge
    from study.services.graph import CycleError, add_edge, remove_edge
    a, b, c = [article_factory(title) for title in ('A', 'B', 'C')]
    first = add_edge(publisher, a.pk, b.pk)
    assert add_edge(publisher, a.pk, b.pk).pk == first.pk
    add_edge(publisher, b.pk, c.pk)
    add_edge(publisher, a.pk, c.pk)
    assert Edge.objects.count() == 3
    for source, target in [(c, a), (b, a), (a, a)]:
        with pytest.raises(CycleError):
            add_edge(publisher, source.pk, target.pk)
    remove_edge(publisher, a.pk, c.pk)
    remove_edge(publisher, a.pk, c.pk)
    assert set(Edge.objects.values_list('source_id', 'target_id')) == {(a.pk, b.pk), (b.pk, c.pk)}


def test_graph_writes_require_publisher_and_existing_nodes(publisher, article_factory):
    from study.models import Article
    from study.services.graph import add_edge, remove_edge
    a = article_factory('A')
    reader = get_user_model().objects.create_user(username='reader')
    for operation in [add_edge, remove_edge]:
        with pytest.raises(PermissionDenied):
            operation(reader, a.pk, a.pk)
        with pytest.raises(Article.DoesNotExist):
            operation(publisher, a.pk, uuid4())


def test_graph_api_and_article_neighbors_are_immediate(client, publisher, article_factory):
    from study.services.graph import add_edge
    a, b, c = [article_factory(title) for title in ('A', 'B', 'C')]
    add_edge(publisher, a.pk, b.pk)
    add_edge(publisher, b.pk, c.pk)
    data = client.get('/api/graph').json()
    assert {node['id'] for node in data['nodes']} == {str(a.pk), str(b.pk), str(c.pk)}
    assert all(set(node) == {'id', 'slug', 'title', 'excerpt', 'color'} for node in data['nodes'])
    assert {'source': str(a.pk), 'target': str(b.pk)} in data['edges']
    response = client.get('/b')
    assert list(response.context['prerequisites']) == [a]
    assert list(response.context['successors']) == [c]
    assert b'Articles to read next' in client.get('/c').content
    assert b'href="/a"' in response.content
    assert b'href="/c"' in response.content


def test_graph_http_errors_and_mutation(client, publisher, article_factory):
    a, b = [article_factory(title) for title in ('A', 'B')]
    url = '/api/edges'
    payload = {'source': str(a.pk), 'target': str(b.pk)}
    assert client.post(url, payload, content_type='application/json').status_code == 401
    client.force_login(publisher)
    for invalid in [[], {'source': []}, {'source': True, 'target': 8}, {'source': 'bad', 'target': str(b.pk)}]:
        assert client.post(url, invalid, content_type='application/json').status_code == 400
    assert client.post(url, payload, content_type='application/json').status_code == 201
    assert client.post(url, {'source': str(b.pk), 'target': str(a.pk)}, content_type='application/json').status_code == 409
    assert client.post(url, {'source': str(uuid4()), 'target': str(a.pk)}, content_type='application/json').status_code == 404
    assert client.delete(f'/api/edges/{a.pk}/{b.pk}').status_code == 204
    reader = get_user_model().objects.create_user(username='reader')
    client.force_login(reader)
    assert client.post(url, payload, content_type='application/json').status_code == 403


def test_edge_write_checks_csrf(publisher, article_factory):
    from django.test import Client
    a = article_factory('A')
    client = Client(enforce_csrf_checks=True)
    client.force_login(publisher)
    response = client.post('/api/edges', {'source': str(a.pk), 'target': str(a.pk)}, content_type='application/json')
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'csrf_failed'


def test_galaxy_shell_has_public_fallback_and_owner_only_controls(client, publisher, article_factory):
    article_factory('A')
    response = client.get('/galaxy')
    assert response.status_code == 200
    assert b'href="/a"' in response.content
    assert b'website-graph.js' in response.content
    assert b'edge-controls' not in response.content
    client.force_login(publisher)
    session = client.session
    session['study.mode'] = 'admin'
    session.save()
    response = client.get('/galaxy')
    assert b'edge-controls' in response.content
    assert b'href="/publish"' in response.content


def test_static_missing_file_can_render_error_without_auth_middleware():
    from django.test import RequestFactory
    from study.views.pages import not_found
    response = not_found(RequestFactory().get('/static/vendor/missing.js'))
    assert response.status_code == 404
