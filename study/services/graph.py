"""Directed prerequisites. Every graph writer takes the singleton lock first."""
from collections import defaultdict

from django.db import transaction

from study.models import Article, Edge, GraphState
from study.services.identity import require_publisher


class CycleError(ValueError):
    pass


def _lock_endpoints(source_id, target_id):
    articles = list(Article.objects.select_for_update().filter(
        pk__in=[source_id, target_id], archived_at__isnull=True,
    ).order_by('pk'))
    if {article.pk for article in articles} != {source_id, target_id}:
        raise Article.DoesNotExist


def _path_exists(edges, source, target):
    adjacency = defaultdict(list)
    for start, end in edges:
        adjacency[start].append(end)
    pending, visited = [source], set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node not in visited:
            visited.add(node)
            pending.extend(adjacency[node])
    return False


@transaction.atomic
def add_edge(user, source_id, target_id) -> Edge:
    require_publisher(user)
    GraphState.objects.select_for_update().get(pk=1)
    _lock_endpoints(source_id, target_id)
    if source_id == target_id or _path_exists(
        Edge.objects.values_list('source_id', 'target_id'), target_id, source_id,
    ):
        raise CycleError('Read-before links cannot form a cycle.')
    edge, _ = Edge.objects.get_or_create(source_id=source_id, target_id=target_id)
    return edge


@transaction.atomic
def remove_edge(user, source_id, target_id) -> None:
    require_publisher(user)
    GraphState.objects.select_for_update().get(pk=1)
    _lock_endpoints(source_id, target_id)
    Edge.objects.filter(source_id=source_id, target_id=target_id).delete()


def published_graph() -> dict:
    # Derive the edge set from the same node snapshot, including when an archive
    # commits between these reads. No loading operation changes stored edges.
    nodes = list(Article.objects.filter(archived_at__isnull=True).values('id', 'slug', 'title', 'excerpt', 'color'))
    ids = [node['id'] for node in nodes]
    edges = list(Edge.objects.filter(source_id__in=ids, target_id__in=ids).values('source', 'target'))
    return {
        'nodes': [{**node, 'id': str(node['id'])} for node in nodes],
        'edges': [{'source': str(edge['source']), 'target': str(edge['target'])} for edge in edges],
    }
