import json
from uuid import UUID

from django.core.exceptions import PermissionDenied, RequestDataTooBig
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from study.models import Article
from study.services.content import archive_article
from study.services.graph import CycleError, add_edge, published_graph, remove_edge
from study.views.identity import authenticated_json_write, error


@require_GET
@ensure_csrf_cookie
def galaxy(request):
    return render(request, 'study/galaxy.html', {'articles': Article.objects.filter(archived_at__isnull=True)})


@require_GET
def graph_data(request):
    return JsonResponse(published_graph())


def _write_response(operation):
    try:
        return operation()
    except PermissionDenied:
        return error('publisher_required', 'Publisher access is required.', 403)
    except Article.DoesNotExist:
        return error('not_found', 'Article was not found.', 404)
    except CycleError as exc:
        return error('cycle', str(exc), 409)


@require_POST
@authenticated_json_write
def create_edge(request):
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict) or not all(isinstance(payload.get(key), str) for key in ('source', 'target')):
            raise ValueError
        source, target = UUID(payload['source']), UUID(payload['target'])
    except (ValueError, UnicodeDecodeError, RequestDataTooBig):
        return error('invalid_edge', 'Source and target must be article UUIDs.', 400)

    def create():
        edge = add_edge(request.user, source, target)
        return JsonResponse({'source': str(edge.source_id), 'target': str(edge.target_id)}, status=201)
    return _write_response(create)


@require_http_methods(['DELETE'])
@authenticated_json_write
def delete_edge(request, source_id, target_id):
    def delete():
        remove_edge(request.user, source_id, target_id)
        return JsonResponse({}, status=204)
    return _write_response(delete)


@require_POST
@authenticated_json_write
def archive(request, article_id):
    def perform():
        article = archive_article(request.user, article_id)
        return JsonResponse({'id': str(article.pk), 'archived_at': article.archived_at.isoformat()})
    return _write_response(perform)
