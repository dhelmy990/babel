import base64
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from study.forms import ArticleSubmissionForm
from study.markdown import prepare_article, render_article
from study.models import Article, Asset
from study.services.content import RevisionConflict, SubmissionConflict, can_read_article, publish_article, stored_images_for_article, update_article, validate_article_metadata
from study.services.identity import require_publisher
from study.storage import path_for
from study.views.identity import authenticated_json_write, error


MAX_REQUEST_BYTES = 64 * 1024 * 1024


def _form_error(form):
    message = next(iter(form.non_field_errors() or form.errors.values()), ["Invalid article."])[0]
    return error("invalid_article", str(message), 400)


def _check_request_size(request):
    try:
        return int(request.META.get("CONTENT_LENGTH", "0")) <= MAX_REQUEST_BYTES
    except ValueError:
        return False


def _preview_html(prepared):
    return render_article(prepared, {
        name: f"data:{prepared.media_types[name]};base64,{base64.b64encode(data).decode('ascii')}"
        for name, data in prepared.images.items()
    })


@require_POST
@authenticated_json_write
def preview(request):
    if not _check_request_size(request):
        return error("request_too_large", "Request is too large.", 400)
    try:
        require_publisher(request.user)
    except PermissionDenied:
        return error("publisher_required", "Publisher access is required.", 403)
    form = ArticleSubmissionForm(request.POST, request.FILES)
    if not form.is_valid():
        return _form_error(form)
    try:
        validate_article_metadata(form.cleaned_data["title"], form.cleaned_data["color"], form.cleaned_data["markdown"])
        images = form.cleaned_data["images"]
        article_id = form.cleaned_data.get("article_id")
        if article_id:
            article = Article.objects.get(pk=article_id)
            if article.archived_at is not None:
                raise ValueError("Archived articles cannot be edited")
            images = {**stored_images_for_article(article), **images}
        prepared = prepare_article(form.cleaned_data["markdown"], images)
    except Article.DoesNotExist:
        return error("not_found", "Article was not found.", 404)
    except ValueError as exc:
        return error("invalid_article", str(exc), 400)
    return JsonResponse({"html": _preview_html(prepared), "excerpt": prepared.excerpt, "sources": prepared.sources})


@require_POST
@authenticated_json_write
def publish(request):
    if not _check_request_size(request):
        return error("request_too_large", "Request is too large.", 400)
    form = ArticleSubmissionForm(request.POST, request.FILES)
    if not form.is_valid() or not form.cleaned_data.get("submission_id"):
        return _form_error(form)
    try:
        article = publish_article(
            request.user, title=form.cleaned_data["title"], color=form.cleaned_data["color"],
            markdown=form.cleaned_data["markdown"], images=form.cleaned_data["images"],
            submission_id=form.cleaned_data["submission_id"],
        )
    except PermissionDenied:
        return error("publisher_required", "Publisher access is required.", 403)
    except SubmissionConflict as exc:
        return error("submission_conflict", str(exc), 409)
    except ValueError as exc:
        return error("invalid_article", str(exc), 400)
    return JsonResponse({"id": str(article.pk), "slug": article.slug, "revision": article.revision}, status=201)


@require_POST
@authenticated_json_write
def update(request, article_id):
    if not _check_request_size(request):
        return error("request_too_large", "Request is too large.", 400)
    form = ArticleSubmissionForm(request.POST, request.FILES)
    if not form.is_valid() or form.cleaned_data.get("revision") is None:
        return _form_error(form)
    try:
        article = update_article(
            request.user, article_id, expected_revision=form.cleaned_data["revision"],
            title=form.cleaned_data["title"], color=form.cleaned_data["color"],
            markdown=form.cleaned_data["markdown"], images=form.cleaned_data["images"],
        )
    except PermissionDenied:
        return error("publisher_required", "Publisher access is required.", 403)
    except Article.DoesNotExist:
        return error("not_found", "Article was not found.", 404)
    except RevisionConflict as exc:
        return error("revision_conflict", str(exc), 409)
    except ValueError as exc:
        return error("invalid_article", str(exc), 400)
    return JsonResponse({"id": str(article.pk), "slug": article.slug, "revision": article.revision})


@require_GET
def asset_response(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related("article"), pk=asset_id)
    if not can_read_article(request.user, asset.article):
        raise Http404
    try:
        response = FileResponse(path_for(asset.storage_key).open("rb"), content_type=asset.media_type)
    except OSError:
        raise Http404
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def article_detail(request, slug):
    article = get_object_or_404(Article, slug=slug)
    if not can_read_article(request.user, article):
        raise Http404
    response = render(request, "study/article.html", {"article": article})
    if article.archived_at is not None:
        response["Cache-Control"] = "private, no-store"
    return response
