"""Bounded, offline extraction of article text from a Wikipedia XML dump.

Passing ``title_filter`` performs a metadata pass followed by a content pass.
Only exact normalized teacher titles and the forward closure of their redirect
targets retain article bodies.  This intentionally trades a second sequential
read for bounded article-text memory on full Wikipedia snapshots.
"""

from __future__ import annotations

import bz2
import codecs
import html
import io
import os
import re
import sqlite3
import stat
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .teacher import normalize_teacher_title


READ_CHUNK_BYTES = 64 * 1024
MAX_XML_PROLOG_BYTES = 1024 * 1024
MAX_WIKITEXT_BYTES = 16 * 1024 * 1024
MAX_XML_PAGE_BYTES = 20 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024 * 1024
MAX_XML_DEPTH = 64
MAX_PAGE_ELEMENTS = 100_000
MAX_NON_PAGE_ELEMENTS = 100_000
MAX_XML_MARKUP_BYTES = 20 * 1024 * 1024
MAX_XML_TEXT_RUN_BYTES = 20 * 1024 * 1024
MAX_MARKUP_DEPTH = 64
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024 * 1024
MAX_ID_DIGITS = 19
MAX_ID_VALUE = (1 << 63) - 1
DEFAULT_REDIRECT_DEPTH = 16
MEDIAWIKI_EXPORT_NAMESPACE = "http://www.mediawiki.org/xml/export-0.10/"

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DECLARATION = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_ROOT = re.compile(br"<(?:[A-Za-z_][\w.-]*:)?mediawiki(?:\s|>)")
_TAG_NAME = re.compile(br"<(/?)([A-Za-z_][\w.:-]*)")
_REDIRECT = re.compile(
    r"^\s*#\s*redirect\s*:?[\t ]*\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^\s*(={2,6})\s*(.*?)\s*\1\s*$")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_REF = re.compile(
    r"<ref\b[^>]*?/>|<ref\b[^>]*>.*?(?:</ref\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_EXTERNAL_LINK = re.compile(r"\[(?:https?|ftp)://[^\s\]]+(?:\s+([^\]]+))?\]", re.I)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_NON_ARTICLE_PREFIXES = frozenset(
    {
        "Talk",
        "User",
        "User talk",
        "Wikipedia",
        "Wikipedia talk",
        "File",
        "File talk",
        "MediaWiki",
        "MediaWiki talk",
        "Template",
        "Template talk",
        "Help",
        "Help talk",
        "Category",
        "Category talk",
        "Portal",
        "Portal talk",
        "Book",
        "Book talk",
        "Draft",
        "Draft talk",
        "Education Program",
        "Education Program talk",
        "TimedText",
        "TimedText talk",
        "Module",
        "Module talk",
        "Gadget",
        "Gadget talk",
        "Gadget definition",
        "Gadget definition talk",
        "Topic",
        "Special",
        "Media",
    }
)


class WikipediaError(ValueError):
    """Base class for deterministic snapshot extraction failures."""


class InvalidWikipediaSource(WikipediaError):
    """The source path or its open-file identity is unsafe."""


class CorruptWikipediaBzip(WikipediaError):
    """The BZ2 stream is corrupt or truncated."""


class InvalidWikipediaXml(WikipediaError):
    """The decompressed XML or bounded wikitext is structurally invalid."""


class InvalidWikipediaUtf8(WikipediaError):
    """The decompressed XML is not strict UTF-8."""


class WikipediaLimitExceeded(InvalidWikipediaXml):
    """A bounded decompression, XML, page, or wikitext limit was exceeded."""


class InvalidWikipediaPage(WikipediaError):
    """A retained page has an invalid identity."""


class DuplicateWikipediaPageId(InvalidWikipediaPage):
    """Two retained dump pages declare the same positive page-level ID."""


class DuplicateWikipediaTitle(InvalidWikipediaPage):
    """Two retained dump pages have the same normalized title."""


@dataclass(frozen=True, slots=True)
class WikipediaPage:
    page_id: int
    canonical_title: str
    revision_id: int | None
    article_text: str
    lead_text: str
    redirect_target: str | None

    @property
    def model_text(self) -> str:
        return self.canonical_title + "\n\n" + self.lead_text


@dataclass(frozen=True, slots=True)
class RedirectResolution:
    status: str
    page: WikipediaPage | None
    chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Source:
    path: Path
    parent_descriptor: int
    descriptor: int
    filename: str
    initial_stat: os.stat_result


@dataclass(frozen=True, slots=True)
class _RawPage:
    page_id: int
    canonical_title: str
    normalized_title: str
    revision_id: int | None
    raw_text: str
    redirect_target: str | None


normalize_title = normalize_teacher_title


def is_non_article_title(title: str) -> bool:
    """Return whether a missing normalized title names a known namespace."""
    normalized = normalize_title(title)
    prefix, separator, _rest = normalized.partition(":")
    return bool(separator) and prefix in _NON_ARTICLE_PREFIXES


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _validate_source(source: _Source) -> None:
    try:
        current = os.fstat(source.descriptor)
        entry = os.stat(
            source.filename,
            dir_fd=source.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise InvalidWikipediaSource(
            f"Wikipedia source identity changed: {source.path}: {error}"
        ) from error
    initial = source.initial_stat
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or entry.st_nlink != 1
        or not _same_inode(current, entry)
        or not _same_inode(current, initial)
        or current.st_size != initial.st_size
        or current.st_mtime_ns != initial.st_mtime_ns
        or current.st_ctime_ns != initial.st_ctime_ns
    ):
        raise InvalidWikipediaSource(
            f"Wikipedia source identity, size, or single-link invariant changed: {source.path}"
        )


@contextmanager
def _open_wikipedia_source(path: Path) -> Iterator[_Source]:
    if not _O_DIRECTORY or not _O_NOFOLLOW:
        raise InvalidWikipediaSource(
            "secure Wikipedia parsing requires O_DIRECTORY and O_NOFOLLOW"
        )
    source_path = Path(path)
    if not source_path.name or source_path.name in {".", ".."}:
        raise InvalidWikipediaSource(f"unsafe Wikipedia source path: {source_path}")
    try:
        parent_descriptor = os.open(
            source_path.parent,
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
        )
    except OSError as error:
        raise InvalidWikipediaSource(
            f"cannot safely open Wikipedia source parent {source_path.parent}: {error}"
        ) from error
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                source_path.name,
                os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            raise InvalidWikipediaSource(
                f"cannot safely open Wikipedia dump {source_path}: {error}"
            ) from error
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise InvalidWikipediaSource(
                f"Wikipedia dump is not a regular file: {source_path}"
            )
        if source_stat.st_nlink != 1:
            raise InvalidWikipediaSource(
                f"Wikipedia dump must have a single link: {source_path}"
            )
        if source_stat.st_size <= 0 or source_stat.st_size > MAX_ARCHIVE_BYTES:
            raise InvalidWikipediaSource(
                f"Wikipedia dump size is unsafe: {source_stat.st_size} bytes"
            )
        opened = _Source(
            source_path,
            parent_descriptor,
            descriptor,
            source_path.name,
            source_stat,
        )
        _validate_source(opened)
        yield opened
        _validate_source(opened)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _read_compressed(source: BinaryIO, size: int) -> bytes:
    return source.read(size)


class _StrictBz2Reader(io.RawIOBase):
    """Validate every byte of a bounded concatenated BZ2 stream."""

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._decompressor = bz2.BZ2Decompressor()
        self._output = bytearray()
        self._pending = b""
        self._raw_eof = False
        self._finished = False
        self._decompressed_bytes = 0

    def readable(self) -> bool:
        return True

    def _next_input(self) -> bytes:
        if self._pending:
            payload = self._pending
            self._pending = b""
            return payload
        if self._raw_eof:
            return b""
        try:
            payload = _read_compressed(self._source, READ_CHUNK_BYTES)
        except OSError as error:
            raise CorruptWikipediaBzip(
                f"cannot read Wikipedia BZ2 stream: {error}"
            ) from error
        if not payload:
            self._raw_eof = True
        return payload

    def _fill(self, target: int) -> None:
        while len(self._output) < target and not self._finished:
            if self._decompressor.eof:
                trailing = self._decompressor.unused_data
                next_member = trailing or self._next_input()
                if not next_member:
                    self._finished = True
                    break
                self._decompressor = bz2.BZ2Decompressor()
                self._pending = next_member

            payload = self._next_input() if self._decompressor.needs_input else b""
            if not payload and self._decompressor.needs_input:
                raise CorruptWikipediaBzip(
                    "Wikipedia BZ2 stream is truncated before end-of-member"
                )
            try:
                block = self._decompressor.decompress(
                    payload,
                    max_length=min(READ_CHUNK_BYTES, target - len(self._output)),
                )
            except (EOFError, OSError) as error:
                raise CorruptWikipediaBzip(
                    f"corrupt Wikipedia BZ2 stream: {error}"
                ) from error
            self._decompressed_bytes += len(block)
            if self._decompressed_bytes > MAX_DECOMPRESSED_BYTES:
                raise WikipediaLimitExceeded(
                    "Wikipedia decompressed XML exceeds the configured byte limit"
                )
            self._output.extend(block)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = READ_CHUNK_BYTES
        size = min(size, READ_CHUNK_BYTES)
        self._fill(size)
        amount = min(size, len(self._output))
        payload = bytes(self._output[:amount])
        del self._output[:amount]
        return payload


class _GuardedReader(io.RawIOBase):
    """Validate UTF-8, declarations, and raw page size before parser intake."""

    def __init__(self, source: BinaryIO, prefix: bytes = b"") -> None:
        self._source = source
        self._buffer = bytearray(prefix)
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._finished = False
        self._decoder_finalized = False
        self._page_bound = _XmlPageBoundScanner()

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = READ_CHUNK_BYTES
        size = min(size, READ_CHUNK_BYTES)
        try:
            while len(self._buffer) < size + 16 and not self._finished:
                block = self._source.read(READ_CHUNK_BYTES)
                if block:
                    self._buffer.extend(block)
                else:
                    self._finished = True
        except InvalidWikipediaUtf8:
            raise
        except (EOFError, OSError) as error:
            raise CorruptWikipediaBzip(f"corrupt Wikipedia BZ2 stream: {error}") from error

        inspected = bytes(self._buffer)
        if _DECLARATION.search(inspected):
            raise InvalidWikipediaXml("Wikipedia XML contains a forbidden DTD/entity declaration")
        amount = min(size, len(self._buffer))
        payload = bytes(self._buffer[:amount])
        del self._buffer[:amount]
        self._page_bound.feed(payload)
        try:
            self._decoder.decode(payload, final=False)
            if (
                self._finished
                and not self._buffer
                and not payload
                and not self._decoder_finalized
            ):
                self._decoder.decode(b"", final=True)
                self._decoder_finalized = True
        except UnicodeDecodeError as error:
            raise InvalidWikipediaUtf8(
                f"Wikipedia XML is not valid UTF-8: {error}"
            ) from error
        return payload


class _XmlPageBoundScanner:
    """Incrementally locate real XML page tags before bytes reach ElementTree."""

    _SPECIAL_PREFIXES = (b"<!--", b"<![CDATA[", b"<?")
    _SPECIAL_END = {
        "comment": b"-->",
        "cdata": b"]]>",
        "pi": b"?>",
    }

    def __init__(self) -> None:
        self._state = "text"
        self._token = bytearray()
        self._quote: int | None = None
        self._inside_page = False
        self._page_bytes = 0
        self._text_run_bytes = 0

    def feed(self, payload: bytes) -> None:
        for byte in payload:
            if self._inside_page:
                self._page_bytes += 1
                if self._page_bytes > MAX_XML_PAGE_BYTES:
                    raise WikipediaLimitExceeded(
                        "Wikipedia XML page exceeds the pre-DOM byte limit"
                    )

            if self._state == "text":
                if byte == ord("<"):
                    self._state = "prefix"
                    self._token = bytearray((byte,))
                    self._text_run_bytes = 0
                elif not self._inside_page:
                    self._text_run_bytes += 1
                    if self._text_run_bytes > MAX_XML_TEXT_RUN_BYTES:
                        raise WikipediaLimitExceeded(
                            "Wikipedia XML text run exceeds the configured limit"
                        )
                continue

            self._token.append(byte)
            if len(self._token) > MAX_XML_MARKUP_BYTES:
                raise WikipediaLimitExceeded(
                    "Wikipedia XML markup construct exceeds the configured limit"
                )

            if self._state == "prefix":
                token = bytes(self._token)
                if token == b"<!--":
                    self._state = "comment"
                    continue
                if token == b"<![CDATA[":
                    self._state = "cdata"
                    continue
                if token == b"<?":
                    self._state = "pi"
                    continue
                if any(prefix.startswith(token) for prefix in self._SPECIAL_PREFIXES):
                    continue
                self._state = "declaration" if token.startswith(b"<!") else "tag"

            if self._state in self._SPECIAL_END:
                if self._token.endswith(self._SPECIAL_END[self._state]):
                    self._finish_special()
                continue

            if self._state in {"tag", "declaration"}:
                if self._quote is None and byte in {ord("'"), ord('"')}:
                    self._quote = byte
                elif self._quote == byte:
                    self._quote = None
                elif self._quote is None and byte == ord(">"):
                    self._finish_tag()

    def _finish_special(self) -> None:
        self._state = "text"
        self._token.clear()
        self._text_run_bytes = 0

    def _finish_tag(self) -> None:
        token = bytes(self._token)
        if self._state == "tag":
            match = _TAG_NAME.match(token)
            if match is not None:
                closing, qualified_name = match.groups()
                local_name = qualified_name.rsplit(b":", 1)[-1]
                self_closing = token.rstrip().endswith(b"/>")
                if local_name == b"page":
                    if closing:
                        self._inside_page = False
                        self._page_bytes = 0
                    elif not self_closing:
                        if self._inside_page:
                            raise InvalidWikipediaXml(
                                "Wikipedia XML nests page elements"
                            )
                        self._inside_page = True
                        self._page_bytes = len(token)
                        if self._page_bytes > MAX_XML_PAGE_BYTES:
                            raise WikipediaLimitExceeded(
                                "Wikipedia XML page exceeds the pre-DOM byte limit"
                            )
        self._state = "text"
        self._token.clear()
        self._quote = None
        self._text_run_bytes = 0


def _preflight_xml(decompressed: BinaryIO) -> bytes:
    prefix = bytearray()
    try:
        while len(prefix) <= MAX_XML_PROLOG_BYTES:
            block = decompressed.read(READ_CHUNK_BYTES)
            if not block:
                break
            prefix.extend(block)
            if _DECLARATION.search(prefix):
                raise InvalidWikipediaXml(
                    "Wikipedia XML contains a forbidden DTD/entity declaration"
                )
            if _ROOT.search(prefix):
                try:
                    decoder = codecs.getincrementaldecoder("utf-8")("strict")
                    decoder.decode(bytes(prefix), final=False)
                except UnicodeDecodeError as error:
                    raise InvalidWikipediaUtf8(
                        f"Wikipedia XML is not valid UTF-8: {error}"
                    ) from error
                return bytes(prefix)
    except InvalidWikipediaXml:
        raise
    except (EOFError, OSError) as error:
        raise CorruptWikipediaBzip(f"corrupt Wikipedia BZ2 stream: {error}") from error
    raise InvalidWikipediaXml(
        f"Wikipedia XML root is absent from the first {MAX_XML_PROLOG_BYTES} bytes"
    )


def _tag_parts(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, separator, local_name = tag[1:].partition("}")
        if separator:
            return namespace, local_name
    return "", tag


def _local_name(tag: str) -> str:
    return _tag_parts(tag)[1]


def _direct_child(
    element: ET.Element, name: str, namespace: str
) -> ET.Element | None:
    return next(
        (
            child
            for child in element
            if _tag_parts(child.tag) == (namespace, name)
        ),
        None,
    )


def _parse_positive_int(value: str | None) -> int | None:
    if (
        value is None
        or not value
        or len(value) > MAX_ID_DIGITS
        or not value.isascii()
        or not value.isdecimal()
    ):
        return None
    try:
        parsed = int(value)
    except (ValueError, OverflowError):
        return None
    return parsed if 0 < parsed <= MAX_ID_VALUE else None


def _parse_namespace(value: str | None) -> int | None:
    if value is None or not value or len(value) > 10 or not value.isascii():
        return None
    if value.startswith("-"):
        digits = value[1:]
    else:
        digits = value
    if not digits.isdecimal():
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def _redirect_from_text(raw_text: str) -> str | None:
    match = _REDIRECT.match(raw_text)
    if match is None:
        return None
    target = normalize_title(match.group(1))
    return target or None


def _raw_page(element: ET.Element) -> _RawPage | None:
    element_namespace, _element_name = _tag_parts(element.tag)
    title_element = _direct_child(element, "title", element_namespace)
    namespace_element = _direct_child(element, "ns", element_namespace)
    page_id_element = _direct_child(element, "id", element_namespace)
    if namespace_element is None:
        raise InvalidWikipediaPage("Wikipedia page is missing required namespace")
    namespace = _parse_namespace((namespace_element.text or "").strip())
    if namespace is None:
        raise InvalidWikipediaPage("Wikipedia page has a malformed namespace")
    if namespace != 0:
        return None
    if title_element is None:
        raise InvalidWikipediaPage("namespace-zero page is missing required title")
    if page_id_element is None:
        raise InvalidWikipediaPage(
            f"namespace-zero page {title_element.text!r} is missing required page ID"
        )
    title = title_element.text or ""
    normalized = normalize_title(title)
    page_id = _parse_positive_int((page_id_element.text or "").strip())
    if page_id is None:
        raise InvalidWikipediaPage(f"nonpositive or malformed page ID for {title!r}")
    if not normalized:
        raise InvalidWikipediaPage(f"blank normalized title for page ID {page_id}")

    revisions = [
        child
        for child in element
        if _tag_parts(child.tag) == (element_namespace, "revision")
    ]
    if not revisions:
        return None
    revision = revisions[-1]
    text_element = _direct_child(revision, "text", element_namespace)
    if text_element is None:
        return None
    revision_id_element = _direct_child(revision, "id", element_namespace)
    revision_id = None
    if revision_id_element is not None:
        revision_id = _parse_positive_int((revision_id_element.text or "").strip())
        if revision_id is None:
            raise InvalidWikipediaPage(
                f"malformed revision ID for namespace-zero page {title!r}"
            )
    raw_text = text_element.text or ""
    redirect_element = _direct_child(element, "redirect", element_namespace)
    redirect_target = None
    if redirect_element is not None:
        redirect_target = normalize_title(redirect_element.attrib.get("title", "")) or None
    fallback = _redirect_from_text(raw_text)
    if redirect_target is None:
        redirect_target = fallback
    elif fallback is not None and fallback != redirect_target:
        raise InvalidWikipediaPage(
            f"redirect metadata disagrees with wikitext for {title!r}"
        )
    return _RawPage(
        page_id,
        title,
        normalized,
        revision_id,
        raw_text,
        redirect_target,
    )


def _rewind_source(source: _Source) -> None:
    _validate_source(source)
    try:
        os.lseek(source.descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise InvalidWikipediaSource(
            f"cannot rewind Wikipedia source {source.path}: {error}"
        ) from error


def _iter_raw_pages_source(
    source: _Source,
    *,
    duplicate_scope: set[str] | None = None,
    check_duplicates: bool = True,
) -> Iterator[_RawPage]:
    _rewind_source(source)
    duplicate = os.dup(source.descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as source_file:
            duplicate = -1
            decompressed = _StrictBz2Reader(source_file)
            prefix = _preflight_xml(decompressed)
            guarded = _GuardedReader(decompressed, prefix)
            seen_ids: set[int] = set()
            seen_titles: dict[str, str] = {}
            root: ET.Element | None = None
            xml_depth = 0
            inside_page = False
            page_elements = 0
            non_page_elements = 0
            try:
                for event, element in ET.iterparse(guarded, events=("start", "end")):
                    element_namespace, local_name = _tag_parts(element.tag)
                    if event == "start":
                        xml_depth += 1
                        if xml_depth > MAX_XML_DEPTH:
                            raise WikipediaLimitExceeded(
                                f"Wikipedia XML depth exceeds {MAX_XML_DEPTH}"
                            )
                        if root is None:
                            if (
                                local_name != "mediawiki"
                                or element_namespace != MEDIAWIKI_EXPORT_NAMESPACE
                            ):
                                raise InvalidWikipediaXml(
                                    "Wikipedia XML root namespace is not the expected "
                                    "MediaWiki export namespace"
                                )
                            root = element
                        if local_name == "page":
                            if (
                                xml_depth != 2
                                or element_namespace != MEDIAWIKI_EXPORT_NAMESPACE
                            ):
                                raise InvalidWikipediaXml(
                                    "Wikipedia page must be a direct root child in the "
                                    "MediaWiki export namespace"
                                )
                            if inside_page:
                                raise InvalidWikipediaXml("Wikipedia XML nests page elements")
                            inside_page = True
                            page_elements = 1
                        elif inside_page:
                            page_elements += 1
                            if page_elements > MAX_PAGE_ELEMENTS:
                                raise WikipediaLimitExceeded(
                                    "Wikipedia XML page has too many elements"
                                )
                        else:
                            non_page_elements += 1
                            if non_page_elements > MAX_NON_PAGE_ELEMENTS:
                                raise WikipediaLimitExceeded(
                                    "Wikipedia XML has too many non-page elements"
                                )
                        continue

                    xml_depth -= 1
                    if local_name != "page":
                        if not inside_page and element is not root:
                            element.clear()
                            if xml_depth == 1 and root is not None:
                                root.remove(element)
                        continue
                    page = _raw_page(element)
                    inside_page = False
                    element.clear()
                    if root is not None:
                        root.clear()
                    if page is None:
                        continue
                    should_check = (
                        check_duplicates
                        and (
                            duplicate_scope is None
                            or page.normalized_title in duplicate_scope
                        )
                    )
                    if should_check:
                        if page.page_id in seen_ids:
                            raise DuplicateWikipediaPageId(
                                f"duplicate Wikipedia page ID: {page.page_id}"
                            )
                        previous = seen_titles.get(page.normalized_title)
                        if previous is not None:
                            raise DuplicateWikipediaTitle(
                                "duplicate normalized Wikipedia title "
                                f"{page.normalized_title!r}: {previous!r} and "
                                f"{page.canonical_title!r}"
                            )
                        seen_ids.add(page.page_id)
                        seen_titles[page.normalized_title] = page.canonical_title
                    yield page
            except (InvalidWikipediaUtf8, CorruptWikipediaBzip, WikipediaLimitExceeded):
                raise
            except ET.ParseError as error:
                raise InvalidWikipediaXml(f"malformed Wikipedia XML: {error}") from error
            finally:
                _validate_source(source)
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _iter_raw_pages(path: Path) -> Iterator[_RawPage]:
    with _open_wikipedia_source(path) as source:
        yield from _iter_raw_pages_source(source)


def _remove_balanced(text: str, opening: str, closing: str) -> str:
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        start = text.find(opening, cursor)
        if start < 0:
            output.append(text[cursor:])
            break
        output.append(text[cursor:start])
        position = start
        depth = 0
        while position < len(text):
            next_open = text.find(opening, position)
            next_close = text.find(closing, position)
            if next_close < 0:
                position = len(text)
                break
            if next_open >= 0 and next_open < next_close:
                depth += 1
                if depth > MAX_MARKUP_DEPTH:
                    raise InvalidWikipediaXml(
                        f"wikitext nesting exceeds {MAX_MARKUP_DEPTH} levels"
                    )
                position = next_open + len(opening)
            else:
                depth -= 1
                position = next_close + len(closing)
                if depth == 0:
                    output.append(" ")
                    break
        cursor = position
    return "".join(output)


def _internal_link_label(match: re.Match[str]) -> str:
    content = match.group(1)
    target, separator, label = content.partition("|")
    namespace = normalize_title(target).partition(":")[0]
    if namespace in {"File", "Image", "Category"}:
        return ""
    return label.rsplit("|", 1)[-1] if separator else target.split("#", 1)[0]


def _normalize_plain_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"^[\t ]*[*#:;]+[\t ]*", "", line)
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _remove_structural_markup(text: str) -> str:
    text = _COMMENT.sub(" ", text)
    text = _REF.sub(" ", text)
    text = _remove_balanced(text, "{{", "}}")
    return _remove_balanced(text, "{|", "|}")


def wikitext_to_plain_text(wikitext: str) -> str:
    """Convert bounded wikitext without network or template expansion."""
    if not isinstance(wikitext, str):
        raise TypeError("wikitext must be text")
    if len(wikitext.encode("utf-8", errors="strict")) > MAX_WIKITEXT_BYTES:
        raise WikipediaLimitExceeded("wikitext article is too large")
    text = wikitext.replace("\r\n", "\n").replace("\r", "\n")
    text = _remove_structural_markup(text)
    text = _INTERNAL_LINK.sub(_internal_link_label, text)
    text = _EXTERNAL_LINK.sub(lambda match: match.group(1) or "", text)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("'''", "").replace("''", "")
    lines: list[str] = []
    for line in text.split("\n"):
        heading = _HEADING.match(line)
        lines.append(heading.group(2) if heading is not None else line)
    return _normalize_plain_text("\n".join(lines))


def extract_lead(wikitext: str) -> str:
    """Return useful prose before the first actual section heading."""
    if _redirect_from_text(wikitext) is not None:
        return ""
    if len(wikitext.encode("utf-8", errors="strict")) > MAX_WIKITEXT_BYTES:
        raise WikipediaLimitExceeded("wikitext article is too large")
    structural_text = _remove_structural_markup(
        wikitext.replace("\r\n", "\n").replace("\r", "\n")
    )
    prefix: list[str] = []
    for line in structural_text.split("\n"):
        if _HEADING.match(line):
            break
        prefix.append(line)
    lead = wikitext_to_plain_text("\n".join(prefix))
    lowered = lead.casefold()
    if (
        "may refer to:" in lowered
        or "may also refer to:" in lowered
        or "can refer to:" in lowered
        or "{{disambiguation" in wikitext.casefold()
    ):
        return ""
    return lead


def _to_page(raw: _RawPage) -> WikipediaPage:
    if raw.redirect_target is not None:
        article_text = ""
        lead_text = ""
    else:
        article_text = wikitext_to_plain_text(raw.raw_text)
        lead_text = extract_lead(raw.raw_text)
    return WikipediaPage(
        raw.page_id,
        raw.canonical_title,
        raw.revision_id,
        article_text,
        lead_text,
        raw.redirect_target,
    )


def iter_wikipedia_pages(
    xml_bz2_path: Path,
    *,
    title_filter: Iterable[str] | None = None,
    max_redirect_depth: int = DEFAULT_REDIRECT_DEPTH,
) -> Iterator[WikipediaPage]:
    """Stream namespace-zero pages, optionally retaining redirect closure only."""
    if (
        not isinstance(max_redirect_depth, int)
        or isinstance(max_redirect_depth, bool)
        or max_redirect_depth < 0
    ):
        raise ValueError("max_redirect_depth must be a nonnegative integer")
    with _open_wikipedia_source(Path(xml_bz2_path)) as source:
        if title_filter is None:
            for raw in _iter_raw_pages_source(source):
                yield _to_page(raw)
            return

        candidates = {normalize_title(title) for title in title_filter}
        candidates.discard("")
        wanted = _candidate_closure(
            source,
            candidates,
            max_redirect_depth=max_redirect_depth,
        )
        for raw in _iter_raw_pages_source(
            source,
            duplicate_scope=wanted,
            check_duplicates=False,
        ):
            if raw.normalized_title in wanted:
                yield _to_page(raw)


def _candidate_closure(
    source: _Source,
    candidates: set[str],
    *,
    max_redirect_depth: int,
) -> set[str]:
    """Spill full identity metadata to disk; retain only closure keys in RAM."""
    database = sqlite3.connect("")
    try:
        database.execute("PRAGMA journal_mode=OFF")
        database.execute("PRAGMA synchronous=OFF")
        database.execute("PRAGMA temp_store=FILE")
        database.execute(
            "CREATE TABLE page_identity ("
            "normalized_title TEXT PRIMARY KEY, "
            "canonical_title TEXT NOT NULL, "
            "page_id INTEGER NOT NULL UNIQUE, "
            "redirect_target TEXT)"
        )
        for raw in _iter_raw_pages_source(source, check_duplicates=False):
            try:
                database.execute(
                    "INSERT INTO page_identity VALUES (?, ?, ?, ?)",
                    (
                        raw.normalized_title,
                        raw.canonical_title,
                        raw.page_id,
                        raw.redirect_target,
                    ),
                )
            except sqlite3.IntegrityError as error:
                title_duplicate = database.execute(
                    "SELECT canonical_title FROM page_identity "
                    "WHERE normalized_title = ?",
                    (raw.normalized_title,),
                ).fetchone()
                if title_duplicate is not None:
                    raise DuplicateWikipediaTitle(
                        "duplicate normalized Wikipedia title "
                        f"{raw.normalized_title!r}: {title_duplicate[0]!r} and "
                        f"{raw.canonical_title!r}"
                    ) from error
                raise DuplicateWikipediaPageId(
                    f"duplicate Wikipedia page ID: {raw.page_id}"
                ) from error
        database.commit()

        wanted = set(candidates)
        for candidate in tuple(candidates):
            current = candidate
            visited: set[str] = set()
            for _depth in range(max_redirect_depth):
                if current in visited:
                    break
                visited.add(current)
                row = database.execute(
                    "SELECT redirect_target FROM page_identity "
                    "WHERE normalized_title = ?",
                    (current,),
                ).fetchone()
                if row is None or row[0] is None:
                    break
                current = str(row[0])
                wanted.add(current)
        return wanted
    finally:
        database.close()


def _page_index(
    pages: Mapping[str, WikipediaPage] | Iterable[WikipediaPage],
) -> tuple[dict[str, WikipediaPage], set[str]]:
    values = pages.values() if isinstance(pages, Mapping) else pages
    index: dict[str, WikipediaPage] = {}
    ambiguous: set[str] = set()
    ids: dict[int, str] = {}
    for page in values:
        key = normalize_title(page.canonical_title)
        if key in index:
            ambiguous.add(key)
        else:
            index[key] = page
        previous_key = ids.get(page.page_id)
        if previous_key is not None:
            ambiguous.add(previous_key)
            ambiguous.add(key)
        else:
            ids[page.page_id] = key
    return index, ambiguous


def resolve_redirect(
    title: str,
    pages: Mapping[str, WikipediaPage] | Iterable[WikipediaPage],
    *,
    max_depth: int = DEFAULT_REDIRECT_DEPTH,
) -> RedirectResolution:
    """Resolve exact normalized identities with explicit bounded outcomes."""
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0:
        raise ValueError("max_depth must be a nonnegative integer")
    index, ambiguous = _page_index(pages)
    current = normalize_title(title)
    chain: list[str] = []
    visited: set[str] = set()
    depth = 0
    while True:
        if current in ambiguous:
            return RedirectResolution("duplicate/ambiguous_title", None, tuple(chain))
        page = index.get(current)
        if page is None:
            status = "title_not_found" if not chain else "redirect_target_missing"
            return RedirectResolution(status, None, tuple(chain))
        display = normalize_title(page.canonical_title)
        chain.append(display)
        if page.redirect_target is None:
            return RedirectResolution("resolved", page, tuple(chain))
        if current in visited:
            return RedirectResolution("redirect_cycle", None, tuple(chain))
        visited.add(current)
        if depth >= max_depth:
            return RedirectResolution("redirect_depth_exceeded", None, tuple(chain))
        current = normalize_title(page.redirect_target)
        depth += 1


__all__ = [
    "CorruptWikipediaBzip",
    "DuplicateWikipediaPageId",
    "DuplicateWikipediaTitle",
    "InvalidWikipediaPage",
    "InvalidWikipediaSource",
    "InvalidWikipediaUtf8",
    "InvalidWikipediaXml",
    "RedirectResolution",
    "WikipediaError",
    "WikipediaLimitExceeded",
    "WikipediaPage",
    "extract_lead",
    "is_non_article_title",
    "iter_wikipedia_pages",
    "normalize_title",
    "resolve_redirect",
    "wikitext_to_plain_text",
]
