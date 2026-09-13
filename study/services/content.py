"""Authorized, serialized article publication and revision operations."""
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from study.markdown import PreparedArticle, normalize_image_mapping, normalize_image_name, prepare_article, referenced_image_names, render_article
from study.models import ArchiveAccess, Article, Asset, GraphState
from study.services.identity import is_publisher, require_publisher
from study.storage import path_for, remove_asset, write_asset


RESERVED_SLUGS = {"galaxy", "publish", "api", "accounts", "assets", "static", "healthz", "robots.txt"}
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")


class SubmissionConflict(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


def _title_from_markdown(markdown: str) -> str:
    match = re.match(r"^\s*#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def validate_article_metadata(title: str, color: str, markdown: str) -> tuple[str, str]:
    if not isinstance(title, str):
        raise ValueError("Invalid title")
    title = title.strip() or _title_from_markdown(markdown)
    if not 1 <= len(title) <= 200:
        raise ValueError("Title must be between 1 and 200 characters")
    if not isinstance(color, str) or not COLOR_RE.fullmatch(color):
        raise ValueError("Color must be a #RRGGBB value")
    return title, color.lower()


def _submission_digest(title: str, color: str, markdown: str, images: dict[str, bytes]) -> str:
    payload = {
        "title": title,
        "color": color,
        "markdown": markdown,
        "images": [
            (normalize_image_name(name), hashlib.sha256(value).hexdigest())
            for name, value in sorted(images.items())
        ],
    }
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _new_assets(article: Article, prepared: PreparedArticle, names: set[str], written: list[str]) -> dict[str, Asset]:
    assets = {}
    for name in sorted(names):
        if name not in prepared.images:
            continue
        asset = Asset(
            article=article,
            logical_name=name,
            storage_key="pending",
            media_type=prepared.media_types[name],
            sha256=hashlib.sha256(prepared.images[name]).hexdigest(),
        )
        asset.storage_key = write_asset(prepared.images[name])
        written.append(asset.storage_key)
        assets[name] = asset
    return assets


def _render_with_assets(prepared: PreparedArticle, assets: dict[str, Asset]) -> str:
    return render_article(prepared, {name: f"/assets/{asset.pk}" for name, asset in assets.items()})


def _slug_for(title: str) -> str:
    slug = slugify(title)
    if not slug or slug in RESERVED_SLUGS:
        raise ValueError("Invalid or reserved slug")
    return slug


def publish_article(user, *, title, color, markdown, images, submission_id) -> Article:
    require_publisher(user)
    if not isinstance(submission_id, UUID):
        try:
            submission_id = UUID(str(submission_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid submission id") from exc
    title, color = validate_article_metadata(title, color, markdown)
    uploads = normalize_image_mapping(images)
    prepared = prepare_article(markdown, uploads)
    digest = _submission_digest(title, color, markdown, uploads)
    written: list[str] = []
    try:
        with transaction.atomic():
            GraphState.objects.select_for_update().get(pk=1)
            previous = Article.objects.filter(submission_id=submission_id).first()
            if previous:
                if previous.submission_hash == digest:
                    return previous
                raise SubmissionConflict("Submission id was already used for different content")
            slug = _slug_for(title)
            if Article.objects.filter(slug=slug).exists():
                raise ValueError("Duplicate slug")
            now = timezone.now()
            article = Article(
                slug=slug, title=title, color=color, markdown=markdown,
                rendered_html="", excerpt=prepared.excerpt, sources=list(prepared.sources),
                published_at=now, updated_at=now, submission_id=submission_id, submission_hash=digest,
            )
            assets = _new_assets(article, prepared, set(prepared.images), written)
            article.rendered_html = _render_with_assets(prepared, assets)
            article.save()
            Asset.objects.bulk_create(assets.values())
            return article
    except Exception:
        for key in written:
            remove_asset(key)
        raise


def update_article(user, article_id, *, expected_revision, title, color, markdown, images) -> Article:
    require_publisher(user)
    title, color = validate_article_metadata(title, color, markdown)
    written: list[str] = []
    try:
        with transaction.atomic():
            GraphState.objects.select_for_update().get(pk=1)
            article = Article.objects.select_for_update().get(pk=article_id)
            if article.archived_at is not None:
                raise ValueError("Archived articles cannot be edited")
            if article.revision != int(expected_revision):
                raise RevisionConflict("Article has changed")
            existing = {asset.logical_name: asset for asset in article.assets.all().order_by("created_at")}
            uploads = normalize_image_mapping(images)
            required_existing = referenced_image_names(markdown) - set(uploads)
            combined = dict(uploads)
            for name in required_existing:
                asset = existing.get(name)
                if asset is None:
                    continue
                try:
                    combined[name] = path_for(asset.storage_key).read_bytes()
                except OSError as exc:
                    raise ValueError("Stored asset is unavailable") from exc
            prepared = prepare_article(markdown, combined)
            replacements = set(uploads)
            created = _new_assets(article, prepared, replacements, written)
            resolved = {**existing, **created}
            article.title = title
            article.color = color
            article.markdown = markdown
            article.rendered_html = _render_with_assets(prepared, resolved)
            article.excerpt = prepared.excerpt
            article.sources = list(prepared.sources)
            article.updated_at = timezone.now()
            article.revision += 1
            article.save(update_fields=["title", "color", "markdown", "rendered_html", "excerpt", "sources", "updated_at", "revision"])
            Asset.objects.bulk_create(created.values())
            return article
    except Exception:
        for key in written:
            remove_asset(key)
        raise


def can_read_article(user, article: Article) -> bool:
    if article.archived_at is None:
        return True
    if is_publisher(user):
        return True
    return bool(getattr(user, "is_authenticated", False) and ArchiveAccess.objects.filter(user=user, article=article).exists())


def stored_images_for_article(article: Article, names: set[str] | None = None) -> dict[str, bytes]:
    """Read named latest assets for that article only; leave obsolete revisions cold."""
    assets = {asset.logical_name: asset for asset in article.assets.all().order_by("created_at")}
    images = {}
    for name in names if names is not None else assets:
        asset = assets.get(name)
        if asset is None:
            continue
        try:
            images[asset.logical_name] = path_for(asset.storage_key).read_bytes()
        except OSError as exc:
            raise ValueError("Stored asset is unavailable") from exc
    return images


@transaction.atomic
def archive_article(user, article_id) -> Article:
    """Central archive transaction; future note grants/review suspension belong here."""
    require_publisher(user)
    GraphState.objects.select_for_update().get(pk=1)
    article = Article.objects.select_for_update().get(pk=article_id)
    if article.archived_at is None:
        article.archived_at = timezone.now()
        article.save(update_fields=['archived_at'])
    return article
