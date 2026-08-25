"""Safely stream and inventory Word2Vec navigation-teacher archives.

Duplicate detection retains only normalized title keys and their first row
numbers, never vectors or full records.  That bounded-per-record index is an
intentional trade-off for deterministic duplicate reporting at inventory scale.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
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
HASH_CHUNK_BYTES = 1024 * 1024
DUPLICATE_POLICY = (
    "NFC, underscores-to-spaces, whitespace-collapse, Unicode-casefold"
)

_HEADER_PATTERN = re.compile(r"([0-9]+) ([0-9]+)", re.ASCII)
_NUMBER_PATTERN = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?",
    re.ASCII,
)
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class TeacherError(ValueError):
    """Base class for invalid teacher input."""


class InvalidTeacherArchive(TeacherError):
    """Raised when the ZIP container is invalid or unsafe."""


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
    """One immutable title and owned, read-only float32 teacher vector."""

    title: str
    vector: np.ndarray


def _without_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _normalize_title(title: str) -> str:
    return " ".join(
        unicodedata.normalize("NFC", title).replace("_", " ").split()
    ).casefold()


def _stable_norm(vector: np.ndarray) -> float:
    return math.sqrt(math.fsum(float(value) * float(value) for value in vector))


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
    if not title or title.isspace() or not _normalize_title(title):
        raise InvalidTeacherVector(
            "title must be nonblank", row=row, title=title or None
        )
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
    return TeacherRecord(title=title, vector=vector)


def parse_vector_line(line: str, dimension: int = DEFAULT_DIMENSION) -> TeacherRecord:
    """Parse one Word2Vec text row into an immutable validated record."""
    if not isinstance(line, str):
        raise InvalidTeacherVector("line must be text")
    return _parse_vector_text(line, dimension, row=None)


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
    member: BinaryIO, info: zipfile.ZipInfo
) -> Iterator[TeacherRecord]:
    raw_header, consumed = _readline(member, row=1, header=True)
    declared_count, dimension = _parse_header(raw_header)
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
        record = _parse_vector_text(_decode_line(raw, row=row), dimension, row=row)
        normalized = _normalize_title(record.title)
        first_row = normalized_rows.get(normalized)
        if first_row is not None:
            raise DuplicateTeacherTitle(
                title=record.title,
                row=row,
                normalized_title=normalized,
                first_row=first_row,
            )
        normalized_rows[normalized] = row
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


def _validated_source_stat(source: Path) -> os.stat_result:
    try:
        source_stat = source.stat()
    except OSError as error:
        raise InvalidTeacherArchive(
            f"cannot stat teacher archive {source}: {error}"
        ) from error
    if not stat.S_ISREG(source_stat.st_mode):
        raise InvalidTeacherArchive(
            f"teacher archive is not a regular file: {source}"
        )
    if source_stat.st_size <= 0 or source_stat.st_size > MAX_ARCHIVE_BYTES:
        raise InvalidTeacherArchive(
            f"teacher archive size is unsafe: {source_stat.st_size} bytes"
        )
    return source_stat


def iter_teacher(path: str | os.PathLike[str]) -> Iterator[TeacherRecord]:
    """Stream records from one validated Word2Vec text member in a ZIP.

    The generator owns both ZIP descriptors.  Calling ``close()`` before
    exhaustion releases the member and archive immediately.
    """
    source = Path(path)
    try:
        source_stat = _validated_source_stat(source)
        with zipfile.ZipFile(source, mode="r") as archive:
            info = _select_member(archive, source_stat.st_size)
            with archive.open(info, mode="r") as member:
                yield from _iter_member(member, info)
    except TeacherError:
        raise
    except (
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as error:
        raise InvalidTeacherArchive(
            f"cannot read teacher archive {source}: {error}"
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_inventory(output: Path, document: dict[str, object]) -> None:
    if os.path.lexists(output):
        raise FileExistsError(f"inventory output already exists: {output}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(
                document,
                target,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise FileExistsError(
                f"inventory output already exists: {output}"
            ) from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_teacher_inventory(
    path: str | os.PathLike[str], output: str | os.PathLike[str]
) -> Path:
    """Validate a teacher once and atomically write deterministic JSON metadata."""
    source = Path(path)
    destination = Path(output)
    if os.path.lexists(destination):
        raise FileExistsError(f"inventory output already exists: {destination}")

    before = _validated_source_stat(source)
    try:
        source_sha256 = _sha256(source)
    except OSError as error:
        raise InvalidTeacherArchive(
            f"cannot hash teacher archive {source}: {error}"
        ) from error

    actual_count = 0
    minimum_norm = math.inf
    maximum_norm = -math.inf
    norm_sum = 0.0
    compensation = 0.0
    for record in iter_teacher(source):
        norm = _stable_norm(record.vector)
        minimum_norm = min(minimum_norm, norm)
        maximum_norm = max(maximum_norm, norm)
        adjusted = norm - compensation
        updated = norm_sum + adjusted
        compensation = (updated - norm_sum) - adjusted
        norm_sum = updated
        actual_count += 1

    try:
        after = source.stat()
    except OSError as error:
        raise InvalidTeacherArchive(
            f"cannot restat teacher archive {source}: {error}"
        ) from error
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise InvalidTeacherArchive("teacher archive changed while inventory was built")

    document: dict[str, object] = {
        "schema_version": "teacher-inventory-v1",
        "source_filename": source.name,
        "source_byte_size": before.st_size,
        "source_sha256": source_sha256,
        "declared_count": actual_count,
        "actual_count": actual_count,
        "dimension": DEFAULT_DIMENSION,
        "vector_dtype": "float32",
        "duplicate_policy": DUPLICATE_POLICY,
        "norm_statistics": {
            "min": minimum_norm,
            "max": maximum_norm,
            "mean": norm_sum / actual_count,
        },
    }
    _publish_inventory(destination, document)
    return destination


__all__ = [
    "DuplicateTeacherTitle",
    "InvalidTeacherArchive",
    "InvalidTeacherHeader",
    "InvalidTeacherVector",
    "TeacherError",
    "TeacherRecord",
    "build_teacher_inventory",
    "iter_teacher",
    "parse_vector_line",
]
