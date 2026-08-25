"""Safely stream and inventory Word2Vec navigation-teacher archives.

Duplicate detection retains only normalized title keys and their first row
numbers, never vectors or full records.  The verified artifact's index is
expected to occupy about 279 MiB and grows linearly with normalized title-key
bytes; that cost is intentional for deterministic duplicate reporting.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

import numpy as np


DEFAULT_DIMENSION = 100
MAX_DECLARED_COUNT = 10_000_000
MAX_LINE_BYTES = 64 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200.0
MAX_ARCHIVE_MEMBERS = 1024
MAX_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_EXCLUSION_RECORDS = 100
HASH_CHUNK_BYTES = 1024 * 1024
DUPLICATE_POLICY = (
    "NFC, underscores-to-spaces, Unicode-whitespace-collapse, "
    "MediaWiki-first-letter-capitalization"
)

_HEADER_PATTERN = re.compile(r"([0-9]+) ([0-9]+)", re.ASCII)
_NUMBER_PATTERN = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?",
    re.ASCII,
)
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_EOCD = struct.Struct("<4s4H2LH")
_ZIP64_EOCD = struct.Struct("<4sQ2H2L4Q")
_ZIP64_LOCATOR = struct.Struct("<4sLQL")


class TeacherError(ValueError):
    """Base class for invalid teacher input."""


class InvalidTeacherArchive(TeacherError):
    """Raised when the ZIP container is invalid or unsafe."""


class UnsafeTeacherOutput(TeacherError):
    """Raised when inventory output cannot be published without path races."""


class InvalidTeacherHeader(TeacherError):
    """Raised when the Word2Vec header or declared row count is invalid."""

    def __init__(self, reason: str, *, row: int = 1) -> None:
        self.row = row
        super().__init__(f"invalid teacher header at row {row}: {reason}")


class InvalidTeacherVector(TeacherError):
    """Raised when a vector row cannot be represented safely."""

    def __init__(
        self, reason: str, *, row: int | None = None, title: str | None = None
    ) -> None:
        self.row = row
        self.title = title
        context = "teacher vector"
        if row is not None:
            context += f" at row {row}"
        if title is not None:
            context += f" (title {title!r})"
        super().__init__(f"invalid {context}: {reason}")


class DuplicateTeacherTitle(TeacherError):
    """Raised when two titles have the same deterministic teacher key."""

    def __init__(
        self, *, title: str, row: int, normalized_title: str, first_row: int
    ) -> None:
        self.title = title
        self.row = row
        self.normalized_title = normalized_title
        self.first_row = first_row
        super().__init__(
            "duplicate teacher title "
            f"{title!r} at row {row}; normalized title {normalized_title!r} "
            f"was first seen at row {first_row}"
        )


@dataclass(frozen=True, slots=True)
class TeacherRecord:
    """One frozen title and owned, advisory-read-only float32 vector.

    NumPy permits an owner to re-enable its write flag.  Preventing that would
    require a copy or non-owned backing storage, contrary to the streaming and
    ownership contract, so callers must treat vectors as immutable.
    """

    title: str
    vector: np.ndarray


@dataclass(frozen=True, slots=True)
class TeacherExclusion:
    """One reversible, narrowly classified source-row exclusion."""

    row: int
    reason: str
    raw_title_hex: str
    detail: str


@dataclass(slots=True)
class TeacherAudit:
    """Bounded audit state populated while raw teacher records are streamed."""

    declared_count: int | None = None
    dimension: int | None = None
    raw_record_count: int = 0
    emitted_count: int = 0
    exclusion_count: int = 0
    exclusions_by_reason: dict[str, int] = field(default_factory=dict)
    exclusions: list[TeacherExclusion] = field(default_factory=list)
    exclusions_truncated: bool = False

    def _reset(self, *, declared_count: int, dimension: int) -> None:
        self.declared_count = declared_count
        self.dimension = dimension
        self.raw_record_count = 0
        self.emitted_count = 0
        self.exclusion_count = 0
        self.exclusions_by_reason.clear()
        self.exclusions.clear()
        self.exclusions_truncated = False

    def _exclude(self, exclusion: TeacherExclusion) -> None:
        self.exclusion_count += 1
        self.exclusions_by_reason[exclusion.reason] = (
            self.exclusions_by_reason.get(exclusion.reason, 0) + 1
        )
        if len(self.exclusions) < MAX_EXCLUSION_RECORDS:
            self.exclusions.append(exclusion)
        else:
            self.exclusions_truncated = True


def _without_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def normalize_teacher_title(title: str) -> str:
    """Return the MediaWiki-compatible key shared with reconciliation."""
    normalized = " ".join(
        unicodedata.normalize("NFC", title).replace("_", " ").split()
    )
    if not normalized:
        return ""
    return unicodedata.normalize("NFC", normalized[0].upper() + normalized[1:])


def _stable_norm(vector: np.ndarray) -> float:
    return math.sqrt(math.fsum(float(value) * float(value) for value in vector))


def _parse_values(
    raw_values: list[str], dimension: int, *, row: int | None, title: str | None
) -> np.ndarray:
    if len(raw_values) != dimension or any(value == "" for value in raw_values):
        raise InvalidTeacherVector(
            f"expected exactly {dimension} numeric values, found {len(raw_values)}",
            row=row,
            title=title,
        )

    values: list[float] = []
    for column, raw_value in enumerate(raw_values, start=1):
        if _NUMBER_PATTERN.fullmatch(raw_value) is None:
            raise InvalidTeacherVector(
                f"value {column} is not a decimal number: {raw_value!r}",
                row=row,
                title=title,
            )
        try:
            value = float(raw_value)
        except (OverflowError, ValueError) as error:
            raise InvalidTeacherVector(
                f"value {column} is outside the numeric range: {raw_value!r}",
                row=row,
                title=title,
            ) from error
        if not math.isfinite(value):
            raise InvalidTeacherVector(
                f"value {column} is not finite: {raw_value!r}",
                row=row,
                title=title,
            )
        values.append(value)

    with np.errstate(over="ignore", invalid="ignore"):
        vector = np.array(values, dtype=np.float32, order="C", copy=True)
    if vector.shape != (dimension,) or not np.isfinite(vector).all():
        raise InvalidTeacherVector(
            "one or more values overflow float32", row=row, title=title
        )
    if _stable_norm(vector) == 0.0:
        raise InvalidTeacherVector("vector has zero norm", row=row, title=title)
    vector.setflags(write=False)
    return vector


def _parse_vector_text(
    line: str, dimension: int, *, row: int | None
) -> TeacherRecord:
    title_hint = line.split(" ", 1)[0] or None
    if (
        not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension != DEFAULT_DIMENSION
    ):
        raise InvalidTeacherVector(
            f"dimension must be exactly {DEFAULT_DIMENSION}, found {dimension!r}",
            row=row,
            title=title_hint,
        )
    try:
        encoded_length = len(line.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise InvalidTeacherVector(
            "line is not valid UTF-8 text", row=row, title=title_hint
        ) from error
    if encoded_length > MAX_LINE_BYTES:
        raise InvalidTeacherVector("line is too long", row=row, title=title_hint)

    text = _without_line_ending(line)
    if text.endswith(" "):
        text = text[:-1]
    if "\n" in text or "\r" in text:
        raise InvalidTeacherVector(
            "embedded newline or carriage return", row=row, title=title_hint
        )
    if _contains_control(text):
        raise InvalidTeacherVector(
            "line contains a control character", row=row, title=title_hint
        )

    fields = text.split(" ")
    title = fields[0] if fields else ""
    raw_values = fields[1:]
    if not title or title.isspace() or not normalize_teacher_title(title):
        raise InvalidTeacherVector(
            "title must be nonblank", row=row, title=title or None
        )
    vector = _parse_values(raw_values, dimension, row=row, title=title)
    return TeacherRecord(title=title, vector=vector)


def _without_byte_line_ending(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _parse_vector_bytes(
    raw: bytes,
    dimension: int,
    *,
    row: int,
    record_row: int,
    audit: TeacherAudit,
) -> TeacherRecord | None:
    content = _without_byte_line_ending(raw)
    if content.endswith(b" "):
        content = content[:-1]
    if b"\n" in content or b"\r" in content:
        raise InvalidTeacherVector("embedded newline or carriage return", row=row)

    fields = content.split(b" ")
    raw_title = fields[0] if fields else b""
    raw_value_bytes = fields[1:]
    if len(raw_value_bytes) != dimension or any(
        value == b"" for value in raw_value_bytes
    ):
        raise InvalidTeacherVector(
            f"expected exactly {dimension} numeric values, "
            f"found {len(raw_value_bytes)}",
            row=row,
        )
    try:
        raw_values = [value.decode("ascii", errors="strict") for value in raw_value_bytes]
    except UnicodeDecodeError as error:
        raise InvalidTeacherVector(
            "numeric payload is not strict ASCII", row=row
        ) from error
    vector = _parse_values(raw_values, dimension, row=row, title=None)

    try:
        title = raw_title.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        audit._exclude(
            TeacherExclusion(
                row=record_row,
                reason="invalid_title_utf8",
                raw_title_hex=raw_title.hex(),
                detail=f"{error.reason} at title byte offset {error.start}",
            )
        )
        return None
    if not title or title.isspace() or not normalize_teacher_title(title):
        raise InvalidTeacherVector("title must be nonblank", row=row, title=title or None)
    if _contains_control(title):
        raise InvalidTeacherVector(
            "title contains a control character", row=row, title=title
        )
    return TeacherRecord(title=title, vector=vector)


def parse_vector_line(line: str, dimension: int = DEFAULT_DIMENSION) -> TeacherRecord:
    """Parse one Word2Vec text row into an immutable validated record."""
    if not isinstance(line, str):
        raise InvalidTeacherVector("line must be text")
    return _parse_vector_text(line, dimension, row=None)


def _read_at(file_descriptor: int, size: int, offset: int) -> bytes:
    try:
        payload = os.pread(file_descriptor, size, offset)
    except (AttributeError, OSError) as error:
        raise InvalidTeacherArchive(
            f"cannot safely preflight ZIP metadata: {error}"
        ) from error
    if len(payload) != size:
        raise InvalidTeacherArchive("ZIP metadata exceeds source bounds")
    return payload


def _find_eocd(file_descriptor: int, archive_size: int) -> tuple[int, bytes]:
    tail_size = min(archive_size, _EOCD.size + 65_535)
    tail_offset = archive_size - tail_size
    tail = _read_at(file_descriptor, tail_size, tail_offset)
    search_end = len(tail)
    while True:
        position = tail.rfind(_EOCD_SIGNATURE, 0, search_end)
        if position < 0:
            raise InvalidTeacherArchive("ZIP end-of-central-directory record is missing")
        if position + _EOCD.size <= len(tail):
            comment_size = struct.unpack_from("<H", tail, position + 20)[0]
            if position + _EOCD.size + comment_size == len(tail):
                return tail_offset + position, tail[position : position + _EOCD.size]
        search_end = position


def _preflight_zip(file_descriptor: int, archive_size: int) -> None:
    eocd_offset, raw_eocd = _find_eocd(file_descriptor, archive_size)
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        _comment_size,
    ) = _EOCD.unpack(raw_eocd)
    if signature != _EOCD_SIGNATURE or disk_number != 0 or central_disk != 0:
        raise InvalidTeacherArchive("multi-disk ZIP archives are not supported")

    central_end_limit = eocd_offset
    uses_zip64 = (
        disk_entries == 0xFFFF
        or total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    )
    if uses_zip64:
        locator_offset = eocd_offset - _ZIP64_LOCATOR.size
        if locator_offset < 0:
            raise InvalidTeacherArchive("ZIP64 locator is outside source bounds")
        locator = _ZIP64_LOCATOR.unpack(
            _read_at(file_descriptor, _ZIP64_LOCATOR.size, locator_offset)
        )
        locator_signature, locator_disk, zip64_offset, disk_count = locator
        if (
            locator_signature != _ZIP64_LOCATOR_SIGNATURE
            or locator_disk != 0
            or disk_count != 1
        ):
            raise InvalidTeacherArchive("invalid ZIP64 locator")
        if zip64_offset + _ZIP64_EOCD.size > locator_offset:
            raise InvalidTeacherArchive("ZIP64 directory metadata exceeds source bounds")
        zip64 = _ZIP64_EOCD.unpack(
            _read_at(file_descriptor, _ZIP64_EOCD.size, zip64_offset)
        )
        (
            zip64_signature,
            zip64_record_size,
            _made_by,
            _needed,
            zip64_disk,
            zip64_central_disk,
            zip64_disk_entries,
            total_entries,
            central_size,
            central_offset,
        ) = zip64
        if (
            zip64_signature != _ZIP64_EOCD_SIGNATURE
            or zip64_record_size < 44
            or zip64_disk != 0
            or zip64_central_disk != 0
            or zip64_disk_entries != total_entries
        ):
            raise InvalidTeacherArchive("invalid ZIP64 end-of-directory record")
        central_end_limit = zip64_offset
    elif disk_entries != total_entries:
        raise InvalidTeacherArchive("multi-disk ZIP entry counts do not match")

    if total_entries > MAX_ARCHIVE_MEMBERS:
        raise InvalidTeacherArchive(
            f"archive has too many members: {total_entries} > {MAX_ARCHIVE_MEMBERS}"
        )
    if central_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise InvalidTeacherArchive(
            f"central directory is too large: {central_size} bytes"
        )
    if central_size < total_entries * 46:
        raise InvalidTeacherArchive("central directory entry bounds are invalid")
    if central_offset + central_size != central_end_limit:
        raise InvalidTeacherArchive("central directory exceeds source bounds")


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


@dataclass(frozen=True, slots=True)
class _TeacherSource:
    path: Path
    parent_descriptor: int
    file_descriptor: int
    filename: str
    initial_stat: os.stat_result


def _source_entry_stat(source: _TeacherSource) -> os.stat_result:
    try:
        entry = os.stat(
            source.filename,
            dir_fd=source.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise InvalidTeacherArchive(
            f"teacher source identity changed: {source.path}: {error}"
        ) from error
    return entry


def _validate_source_identity(source: _TeacherSource) -> os.stat_result:
    try:
        current = os.fstat(source.file_descriptor)
    except OSError as error:
        raise InvalidTeacherArchive(f"cannot inspect teacher source: {error}") from error
    entry = _source_entry_stat(source)
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
        raise InvalidTeacherArchive(
            f"teacher source identity, size, or single-link invariant changed: {source.path}"
        )
    return current


@contextmanager
def _open_teacher_source(path: Path) -> Iterator[_TeacherSource]:
    if not _O_DIRECTORY or not _O_NOFOLLOW:
        raise InvalidTeacherArchive(
            "secure teacher parsing requires O_DIRECTORY and O_NOFOLLOW"
        )
    source = Path(path)
    parent = source.parent
    filename = source.name
    if not filename or filename in {".", ".."}:
        raise InvalidTeacherArchive(f"unsafe teacher source path: {source}")
    try:
        parent_descriptor = os.open(
            parent, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        )
    except OSError as error:
        raise InvalidTeacherArchive(
            f"cannot safely open teacher source parent {parent}: {error}"
        ) from error
    file_descriptor: int | None = None
    try:
        try:
            file_descriptor = os.open(
                filename,
                os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            raise InvalidTeacherArchive(
                f"cannot safely open teacher archive {source}: {error}"
            ) from error
        source_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise InvalidTeacherArchive(
                f"teacher archive is not a regular file: {source}"
            )
        if source_stat.st_nlink != 1:
            raise InvalidTeacherArchive(
                f"teacher archive must have a single link: {source}"
            )
        if source_stat.st_size <= 0 or source_stat.st_size > MAX_ARCHIVE_BYTES:
            raise InvalidTeacherArchive(
                f"teacher archive size is unsafe: {source_stat.st_size} bytes"
            )
        opened = _TeacherSource(
            path=source,
            parent_descriptor=parent_descriptor,
            file_descriptor=file_descriptor,
            filename=filename,
            initial_stat=source_stat,
        )
        _validate_source_identity(opened)
        yield opened
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(parent_descriptor)


def _validate_member_name(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or _contains_control(name)
    ):
        raise InvalidTeacherArchive(f"unsafe member name: {name!r}")
    parts = name.split("/")
    checked_parts = parts[:-1] if info.is_dir() and parts[-1] == "" else parts
    if (
        not checked_parts
        or any(part in {"", ".", ".."} for part in checked_parts)
        or (len(checked_parts[0]) >= 2 and checked_parts[0][1] == ":")
        or PurePosixPath(name).is_absolute()
    ):
        raise InvalidTeacherArchive(f"unsafe member name: {name!r}")


def _is_regular_member(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG}:
        raise InvalidTeacherArchive(
            f"ZIP member is not a regular file: {info.filename!r}"
        )
    return True


def _select_member(archive: zipfile.ZipFile, archive_size: int) -> zipfile.ZipInfo:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise InvalidTeacherArchive(
            f"archive has too many members: {len(members)} > {MAX_ARCHIVE_MEMBERS}"
        )

    regular: list[zipfile.ZipInfo] = []
    for info in members:
        _validate_member_name(info)
        if info.flag_bits & 0x1:
            raise InvalidTeacherArchive(f"encrypted ZIP member: {info.filename!r}")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise InvalidTeacherArchive(
                f"unsupported ZIP compression for member {info.filename!r}"
            )
        if not _is_regular_member(info):
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            raise InvalidTeacherArchive(
                f"declared member size is unsafe for {info.filename!r}: "
                f"{info.file_size}"
            )
        if info.compress_size <= 0 or info.compress_size > archive_size:
            raise InvalidTeacherArchive(
                f"declared compressed size is unsafe for {info.filename!r}"
            )
        ratio = info.file_size / info.compress_size
        if ratio > MAX_COMPRESSION_RATIO:
            raise InvalidTeacherArchive(
                f"unsafe compression ratio for {info.filename!r}: {ratio:.1f}"
            )
        regular.append(info)

    if not regular:
        raise InvalidTeacherArchive("archive has no regular data member")
    if len(regular) != 1:
        raise InvalidTeacherArchive(
            f"archive has multiple regular data members: {len(regular)}"
        )
    return regular[0]


def _readline(
    member: BinaryIO,
    *,
    row: int,
    header: bool = False,
) -> tuple[bytes, int]:
    raw = member.readline(MAX_LINE_BYTES + 1)
    if len(raw) > MAX_LINE_BYTES:
        if header:
            raise InvalidTeacherHeader("header line is too long", row=row)
        raise InvalidTeacherVector("line is too long", row=row)
    return raw, len(raw)


def _decode_line(raw: bytes, *, row: int, header: bool = False) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        if header:
            raise InvalidTeacherHeader("header is not valid UTF-8", row=row) from error
        raise InvalidTeacherVector("line is not valid UTF-8", row=row) from error


def _parse_header(raw: bytes) -> tuple[int, int]:
    if not raw:
        raise InvalidTeacherHeader("missing header")
    text = _without_line_ending(_decode_line(raw, row=1, header=True))
    match = _HEADER_PATTERN.fullmatch(text)
    if match is None:
        raise InvalidTeacherHeader(
            "header must contain exactly two positive decimal integers"
        )
    raw_count, raw_dimension = match.groups()
    if len(raw_count) > 9 or len(raw_dimension) > 9:
        raise InvalidTeacherHeader("declared count or dimension is absurd")
    declared_count = int(raw_count)
    dimension = int(raw_dimension)
    if declared_count <= 0 or declared_count > MAX_DECLARED_COUNT:
        raise InvalidTeacherHeader(
            f"declared count must be between 1 and {MAX_DECLARED_COUNT}, "
            f"found {declared_count}"
        )
    if dimension != DEFAULT_DIMENSION:
        raise InvalidTeacherHeader(
            f"dimension must be exactly {DEFAULT_DIMENSION}, found {dimension}"
        )
    return declared_count, dimension


def _iter_member(
    member: BinaryIO, info: zipfile.ZipInfo, audit: TeacherAudit
) -> Iterator[TeacherRecord]:
    raw_header, consumed = _readline(member, row=1, header=True)
    declared_count, dimension = _parse_header(raw_header)
    audit._reset(declared_count=declared_count, dimension=dimension)
    normalized_rows: dict[str, int] = {}

    for record_index in range(declared_count):
        row = record_index + 2
        raw, byte_count = _readline(member, row=row)
        consumed += byte_count
        if not raw:
            raise InvalidTeacherHeader(
                f"declared {declared_count} records but found {record_index}",
                row=row,
            )
        audit.raw_record_count += 1
        record = _parse_vector_bytes(
            raw,
            dimension,
            row=row,
            record_row=record_index + 1,
            audit=audit,
        )
        if record is None:
            continue
        normalized = normalize_teacher_title(record.title)
        first_row = normalized_rows.get(normalized)
        if first_row is not None:
            raise DuplicateTeacherTitle(
                title=record.title,
                row=row,
                normalized_title=normalized,
                first_row=first_row,
            )
        normalized_rows[normalized] = row
        audit.emitted_count += 1
        yield record

    extra_row = declared_count + 2
    while True:
        raw, byte_count = _readline(member, row=extra_row)
        consumed += byte_count
        if not raw:
            break
        text = _without_line_ending(_decode_line(raw, row=extra_row))
        if text.strip(" "):
            raise InvalidTeacherHeader(
                f"extra nonblank record at row {extra_row} beyond declared "
                f"count {declared_count}",
                row=extra_row,
            )
        extra_row += 1

    if consumed != info.file_size:
        raise InvalidTeacherArchive(
            f"member size mismatch for {info.filename!r}: "
            f"declared {info.file_size}, read {consumed}"
        )


def _iter_teacher_descriptor(
    source: _TeacherSource, audit: TeacherAudit
) -> Iterator[TeacherRecord]:
    _preflight_zip(source.file_descriptor, source.initial_stat.st_size)
    duplicate = os.dup(source.file_descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as archive_file:
            duplicate = -1
            with zipfile.ZipFile(archive_file, mode="r") as archive:
                info = _select_member(archive, source.initial_stat.st_size)
                with archive.open(info, mode="r") as member:
                    yield from _iter_member(member, info, audit)
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _translate_archive_error(source: Path, error: BaseException) -> None:
    raise InvalidTeacherArchive(
        f"cannot read teacher archive {source}: {error}"
    ) from error


def iter_teacher(
    path: str | os.PathLike[str], *, audit: TeacherAudit | None = None
) -> Iterator[TeacherRecord]:
    """Stream records from one validated Word2Vec text member in a ZIP.

    Invalid UTF-8 confined to a title is skipped deterministically.  Pass a
    :class:`TeacherAudit` to retain bounded reversible exclusion provenance.
    All other malformed bytes remain fatal.  The generator owns both ZIP
    descriptors; calling ``close()`` releases them immediately.
    """
    source = Path(path)
    try:
        with _open_teacher_source(source) as opened:
            yield from _iter_teacher_descriptor(opened, audit or TeacherAudit())
            _validate_source_identity(opened)
    except TeacherError:
        raise
    except (
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        UnicodeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as error:
        _translate_archive_error(source, error)


def _sha256_descriptor(file_descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    while chunk := os.read(file_descriptor, HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _entry_at(parent_descriptor: int, filename: str) -> os.stat_result | None:
    try:
        return os.stat(
            filename, dir_fd=parent_descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        return None


def _unlink_at_if_inode(
    parent_descriptor: int, filename: str, expected: os.stat_result
) -> bool:
    entry = _entry_at(parent_descriptor, filename)
    if entry is None or not _same_inode(entry, expected):
        return False
    os.unlink(filename, dir_fd=parent_descriptor)
    return True


@contextmanager
def _open_output_parent(output: Path) -> Iterator[tuple[int, os.stat_result, str]]:
    if not _O_DIRECTORY or not _O_NOFOLLOW:
        raise UnsafeTeacherOutput(
            "secure inventory publication requires O_DIRECTORY and O_NOFOLLOW"
        )
    filename = output.name
    if not filename or filename in {".", ".."}:
        raise UnsafeTeacherOutput(f"unsafe inventory output name: {filename!r}")
    try:
        parent_descriptor = os.open(
            output.parent,
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
        )
    except OSError as error:
        raise UnsafeTeacherOutput(
            f"cannot safely open output parent {output.parent}: {error}"
        ) from error
    try:
        parent_stat = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise UnsafeTeacherOutput(f"output parent is not a directory: {output.parent}")
        yield parent_descriptor, parent_stat, filename
    finally:
        os.close(parent_descriptor)


def _validate_output_parent(
    output: Path, parent_stat: os.stat_result
) -> None:
    try:
        current = os.stat(output.parent, follow_symlinks=False)
    except OSError as error:
        raise UnsafeTeacherOutput(
            f"output parent identity changed: {output.parent}: {error}"
        ) from error
    if not stat.S_ISDIR(current.st_mode) or not _same_inode(current, parent_stat):
        raise UnsafeTeacherOutput(f"output parent identity changed: {output.parent}")


def _open_inventory_temp(parent_descriptor: int, output_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    for counter in range(100):
        temporary_name = f".{output_name}.{os.getpid()}.{counter}.tmp"
        try:
            descriptor = os.open(
                temporary_name, flags, 0o600, dir_fd=parent_descriptor
            )
            return descriptor, temporary_name
        except FileExistsError:
            continue
        except OSError as error:
            raise UnsafeTeacherOutput(
                f"cannot safely create inventory temporary file: {error}"
            ) from error
    raise UnsafeTeacherOutput("cannot allocate a unique inventory temporary file")


def _write_all(file_descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(file_descriptor, payload[offset:])
        if written <= 0:
            raise UnsafeTeacherOutput("short write while creating inventory")
        offset += written


def _publish_inventory_at(
    output: Path,
    document: dict[str, object],
    *,
    parent_descriptor: int,
    parent_stat: os.stat_result,
    output_name: str,
) -> None:
    payload = (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    _validate_output_parent(output, parent_stat)
    if _entry_at(parent_descriptor, output_name) is not None:
        raise FileExistsError(f"inventory output already exists: {output}")
    descriptor, temporary_name = _open_inventory_temp(parent_descriptor, output_name)
    temporary_stat = os.fstat(descriptor)
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        temporary_stat = os.fstat(descriptor)
        if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
            raise UnsafeTeacherOutput(
                "inventory temporary file violated the single-link invariant"
            )
        try:
            os.link(
                temporary_name,
                output_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise FileExistsError(
                f"inventory output already exists: {output}"
            ) from error
        except OSError as error:
            raise UnsafeTeacherOutput(
                f"cannot atomically publish inventory: {error}"
            ) from error

        linked = _entry_at(parent_descriptor, output_name)
        after_link = os.fstat(descriptor)
        if (
            linked is None
            or not _same_inode(linked, after_link)
            or not _same_inode(temporary_stat, after_link)
            or linked.st_nlink != 2
            or after_link.st_nlink != 2
        ):
            if linked is not None and _same_inode(linked, after_link):
                _unlink_at_if_inode(parent_descriptor, output_name, after_link)
            raise UnsafeTeacherOutput(
                "inventory publication hard-link invariant failed"
            )

        if not _unlink_at_if_inode(parent_descriptor, temporary_name, after_link):
            _unlink_at_if_inode(parent_descriptor, output_name, after_link)
            raise UnsafeTeacherOutput("inventory temporary file changed before cleanup")
        final_stat = os.fstat(descriptor)
        final_entry = _entry_at(parent_descriptor, output_name)
        if (
            final_entry is None
            or not _same_inode(final_entry, final_stat)
            or final_stat.st_nlink != 1
            or final_entry.st_nlink != 1
        ):
            if final_entry is not None and _same_inode(final_entry, final_stat):
                _unlink_at_if_inode(parent_descriptor, output_name, final_stat)
            raise UnsafeTeacherOutput("published inventory identity invariant failed")
        try:
            _validate_output_parent(output, parent_stat)
        except UnsafeTeacherOutput:
            _unlink_at_if_inode(parent_descriptor, output_name, final_stat)
            raise
        os.fsync(parent_descriptor)
    finally:
        os.close(descriptor)
        _unlink_at_if_inode(parent_descriptor, temporary_name, temporary_stat)


def build_teacher_inventory(
    path: str | os.PathLike[str], output: str | os.PathLike[str]
) -> Path:
    """Validate a teacher once and atomically write deterministic JSON metadata."""
    source = Path(path)
    destination = Path(output)
    audit = TeacherAudit()
    actual_count = 0
    minimum_norm = math.inf
    maximum_norm = -math.inf
    norm_sum = 0.0
    compensation = 0.0
    with _open_output_parent(destination) as (
        parent_descriptor,
        parent_stat,
        output_name,
    ):
        if _entry_at(parent_descriptor, output_name) is not None:
            raise FileExistsError(f"inventory output already exists: {destination}")
        try:
            with _open_teacher_source(source) as opened:
                try:
                    source_sha256 = _sha256_descriptor(opened.file_descriptor)
                except OSError as error:
                    raise InvalidTeacherArchive(
                        f"cannot hash teacher archive {source}: {error}"
                    ) from error
                for record in _iter_teacher_descriptor(opened, audit):
                    norm = _stable_norm(record.vector)
                    minimum_norm = min(minimum_norm, norm)
                    maximum_norm = max(maximum_norm, norm)
                    adjusted = norm - compensation
                    updated = norm_sum + adjusted
                    compensation = (updated - norm_sum) - adjusted
                    norm_sum = updated
                    actual_count += 1
                try:
                    verification_sha256 = _sha256_descriptor(opened.file_descriptor)
                except OSError as error:
                    raise InvalidTeacherArchive(
                        f"cannot rehash teacher archive {source}: {error}"
                    ) from error
                if source_sha256 != verification_sha256:
                    raise InvalidTeacherArchive(
                        "teacher archive changed while inventory was built"
                    )
                before = _validate_source_identity(opened)
        except TeacherError:
            raise
        except (
            OSError,
            EOFError,
            RuntimeError,
            NotImplementedError,
            UnicodeError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
        ) as error:
            _translate_archive_error(source, error)

        if (
            audit.declared_count is None
            or audit.dimension is None
            or actual_count <= 0
        ):
            raise InvalidTeacherHeader("teacher emitted no valid records")

        document: dict[str, object] = {
            "schema_version": "teacher-inventory-v1",
            "source_filename": source.name,
            "source_byte_size": before.st_size,
            "source_sha256": source_sha256,
            "declared_count": audit.declared_count,
            "raw_record_count": audit.raw_record_count,
            "emitted_count": audit.emitted_count,
            "valid_count": audit.emitted_count,
            "actual_count": actual_count,
            "exclusion_count": audit.exclusion_count,
            "exclusions_by_reason": dict(sorted(audit.exclusions_by_reason.items())),
            "exclusions": [
                {
                    "row": exclusion.row,
                    "reason": exclusion.reason,
                    "raw_title_hex": exclusion.raw_title_hex,
                    "detail": exclusion.detail,
                }
                for exclusion in audit.exclusions
            ],
            "exclusions_truncated": audit.exclusions_truncated,
            "dimension": audit.dimension,
            "vector_dtype": "float32",
            "duplicate_policy": DUPLICATE_POLICY,
            "norm_statistics": {
                "min": minimum_norm,
                "max": maximum_norm,
                "mean": norm_sum / actual_count,
            },
        }
        _publish_inventory_at(
            destination,
            document,
            parent_descriptor=parent_descriptor,
            parent_stat=parent_stat,
            output_name=output_name,
        )
    return destination


__all__ = [
    "DuplicateTeacherTitle",
    "InvalidTeacherArchive",
    "InvalidTeacherHeader",
    "InvalidTeacherVector",
    "UnsafeTeacherOutput",
    "TeacherError",
    "TeacherAudit",
    "TeacherExclusion",
    "TeacherRecord",
    "build_teacher_inventory",
    "iter_teacher",
    "normalize_teacher_title",
    "parse_vector_line",
]
