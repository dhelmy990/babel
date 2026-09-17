"""The one Markdown parser used by previews and durable publications."""
from dataclasses import dataclass
from html import escape
from io import BytesIO
import re
import warnings
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt
from PIL import Image, ImageFile


MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 20
MAX_IMAGE_PIXELS = 30_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = False


@dataclass(frozen=True)
class PreparedArticle:
    html: str
    excerpt: str
    sources: tuple[tuple[str, str], ...]
    images: dict[str, bytes]
    media_types: dict[str, str]
    tokens: tuple


def markdown_parser():
    return MarkdownIt("js-default", {"html": False}).enable("table")


def normalize_image_name(name: str) -> str:
    decoded = unquote(name)
    parsed = urlparse(decoded)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or decoded.startswith(("/", "\\")):
        raise ValueError("Invalid image path")
    path = PurePosixPath(decoded.replace("\\", "/"))
    if not decoded or not path.parts or ".." in path.parts:
        raise ValueError("Invalid image path")
    normalized = path.as_posix()
    if normalized.lower().endswith((".svg", ".html", ".htm")):
        raise ValueError("Unsupported image type")
    return normalized


def normalize_image_mapping(images: dict[str, bytes]) -> dict[str, bytes]:
    """Canonicalize upload names before combining them with stored assets."""
    normalized = {}
    for supplied_name, data in images.items():
        name = normalize_image_name(supplied_name)
        if name in normalized:
            raise ValueError("Duplicate image")
        normalized[name] = data
    return normalized


def _validated_image(data: bytes) -> tuple[bytes, str]:
    if not isinstance(data, bytes) or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(data))
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image is too large")
            image.load()
        formats = {"PNG": ("PNG", "image/png"), "JPEG": ("JPEG", "image/jpeg"), "WEBP": ("WEBP", "image/webp")}
        output_format, media_type = formats[image.format]
        if output_format == "JPEG" and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, output_format)
        return output.getvalue(), media_type
    except (OSError, KeyError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Invalid image") from exc


def _split_sources(tokens) -> tuple[list, tuple[tuple[str, str], ...]]:
    """Partition one parsed document, retaining its global reference environment."""
    headings = [
        index for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.level == 0
    ]
    if not headings:
        return tokens, ()
    index = headings[-1]
    heading = tokens[index]
    label_token = tokens[index + 1] if index + 1 < len(tokens) else None
    if heading.tag != "h2" or not label_token or label_token.type != "inline" or label_token.content.strip().lower() != "sources":
        return tokens, ()
    source_tokens = tokens[index:]
    links = []
    for token in source_tokens:
        if token.type != "inline":
            continue
        label_parts, url = [], None
        for child in token.children or []:
            if child.type == "link_open":
                url = child.attrGet("href") or ""
                parsed = urlparse(url)
                if parsed.scheme.lower() not in {"http", "https"}:
                    raise ValueError("Unsafe link")
                label_parts = []
            elif child.type == "link_close" and url:
                label = "".join(label_parts).strip()
                if label:
                    links.append((label, url))
                url = None
            elif url and child.type in {"text", "code_inline"}:
                label_parts.append(child.content)
    return (tokens[:index], tuple(links)) if links else (tokens, ())


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "mailto"}:
        raise ValueError("Unsafe link")


def _reject_unsafe_link_syntax(tokens) -> None:
    """Catch disabled executable links without inspecting literal code tokens."""
    for token in tokens:
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type != "text":
                continue
            for scheme in re.findall(r"\]\(\s*<?([A-Za-z][A-Za-z0-9+.-]*):", child.content):
                if scheme.lower() not in {"http", "https", "mailto"}:
                    raise ValueError("Unsafe link")


def referenced_image_names(markdown: str) -> set[str]:
    """Return canonical image names in the article body, excluding Sources."""
    body, _ = _split_sources(markdown_parser().parse(markdown))
    references = set()
    for token in body:
        for child in token.children or []:
            if child.type == "image":
                references.add(normalize_image_name(child.attrGet("src") or ""))
    return references


def _excerpt(tokens) -> str:
    in_paragraph = False
    for token in tokens:
        if token.type == "paragraph_open":
            in_paragraph = True
        elif token.type == "paragraph_close":
            in_paragraph = False
        elif in_paragraph and token.type == "inline":
            text = "".join(
                child.content if child.type in {"text", "code_inline"} else " " if child.type in {"softbreak", "hardbreak"} else ""
                for child in token.children or []
            )
            text = " ".join(text.split())
            if text:
                return text[:240]
    return ""


def render_article(prepared: PreparedArticle, image_urls: dict[str, str]) -> str:
    """Render image tokens through a callback, never by replacing HTML text."""
    parser = markdown_parser()

    def image_alt_text(tokens):
        return "".join(
            token.content if token.type in {"text", "text_special", "code_inline"}
            else " " if token.type in {"softbreak", "hardbreak"}
            else image_alt_text(token.children or []) if token.type == "image"
            else ""
            for token in tokens
        )

    def render_image(tokens, idx, options, env):
        token = tokens[idx]
        name = token.attrGet("src") or ""
        src = image_urls.get(name, "asset:" + name)
        alt = image_alt_text(token.children or [])
        return f'<img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}">'

    parser.renderer.rules["image"] = render_image
    return parser.renderer.render(list(prepared.tokens), parser.options, {})


def prepare_article(markdown: str, images: dict[str, bytes]) -> PreparedArticle:
    if not isinstance(markdown, str):
        raise ValueError("Invalid Markdown")
    try:
        if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
            raise ValueError("Markdown is too large")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid Markdown") from exc
    parser = markdown_parser()
    document_tokens = parser.parse(markdown)
    _reject_unsafe_link_syntax(document_tokens)
    if len(images) > MAX_IMAGES:
        raise ValueError("Too many images")
    normal_images: dict[str, bytes] = {}
    media_types: dict[str, str] = {}
    for name, data in normalize_image_mapping(images).items():
        normal_images[name], media_types[name] = _validated_image(data)

    tokens, sources = _split_sources(document_tokens)
    for token in tokens:
        for child in token.children or []:
            if child.type == "image":
                source = child.attrGet("src") or ""
                name = normalize_image_name(source)
                if name not in normal_images:
                    raise ValueError("Missing image")
                child.attrSet("src", name)
            elif child.type == "link_open":
                _validate_url(child.attrGet("href") or "")
    # Do not duplicate a leading title that the page already renders.
    if len(tokens) >= 3 and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
        tokens = tokens[3:]
    prepared = PreparedArticle(
        html="", excerpt=_excerpt(tokens), sources=sources, images=normal_images,
        media_types=media_types, tokens=tuple(tokens),
    )
    return PreparedArticle(
        html=render_article(prepared, {}), excerpt=prepared.excerpt, sources=prepared.sources,
        images=prepared.images, media_types=prepared.media_types, tokens=prepared.tokens,
    )
