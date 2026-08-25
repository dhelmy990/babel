"""Verified, resumable acquisition of immutable source files."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DIGEST_CHUNK_SIZE = 8 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
_MD5_PATTERN = re.compile(r"[0-9a-f]{32}")
_SHA1_PATTERN = re.compile(r"[0-9a-f]{40}")
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)", re.IGNORECASE)


class SourceError(Exception):
    """Base exception for source verification and acquisition failures."""


class InvalidSourceSpec(ValueError, SourceError):
    """Raised when a source specification is structurally unsafe or invalid."""


class VerificationError(SourceError):
    """Raised when bytes do not match their source specification."""


class SizeMismatch(VerificationError):
    """Raised when a source has an unexpected byte size."""

    def __init__(self, filename: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{filename} size mismatch: expected {expected} bytes, found {actual}"
        )
        self.filename = filename
        self.expected = expected
        self.actual = actual


class ChecksumMismatch(VerificationError):
    """Raised when a source digest differs from its authoritative digest."""

    def __init__(
        self, filename: str, algorithm: str, expected: str, actual: str
    ) -> None:
        super().__init__(
            f"{filename} {algorithm} checksum mismatch: "
            f"expected {expected}, found {actual}"
        )
        self.filename = filename
        self.algorithm = algorithm
        self.expected = expected
        self.actual = actual


class ExistingFileInvalid(SourceError):
    """Raised rather than replacing an invalid existing final file."""


class DownloadError(SourceError):
    """Raised when a source cannot be downloaded safely."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Immutable identity and integrity facts for one source file."""

    name: str
    url: str
    size: int
    md5: str
    filename: str
    sha1: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidSourceSpec("source name must be a non-empty string")

        if not isinstance(self.url, str):
            raise InvalidSourceSpec("source URL must be an HTTP(S) URL")
        parsed_url = urlsplit(self.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise InvalidSourceSpec("source URL must be an HTTP(S) URL")

        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size <= 0:
            raise InvalidSourceSpec("source size must be a positive integer")

        if not isinstance(self.md5, str) or _MD5_PATTERN.fullmatch(self.md5) is None:
            raise InvalidSourceSpec("source MD5 must be 32 lowercase hexadecimal characters")

        if self.sha1 is not None and (
            not isinstance(self.sha1, str)
            or _SHA1_PATTERN.fullmatch(self.sha1) is None
        ):
            raise InvalidSourceSpec(
                "source SHA-1 must be 40 lowercase hexadecimal characters"
            )

        if not isinstance(self.filename, str) or not self.filename:
            raise InvalidSourceSpec("source filename must be a non-empty basename")
        if (
            self.filename in {".", ".."}
            or Path(self.filename).name != self.filename
            or "/" in self.filename
            or "\\" in self.filename
            or "\x00" in self.filename
        ):
            raise InvalidSourceSpec("source filename must be a safe basename")


def verify_file(path: Path, spec: SourceSpec) -> None:
    """Verify exact size and all declared digests without changing *path*."""

    source_path = Path(path)
    actual_size = source_path.stat().st_size
    if actual_size != spec.size:
        raise SizeMismatch(spec.filename, spec.size, actual_size)

    md5_digest = hashlib.md5()
    sha1_digest = hashlib.sha1() if spec.sha1 is not None else None
    with source_path.open("rb") as stream:
        for block in iter(lambda: stream.read(DIGEST_CHUNK_SIZE), b""):
            md5_digest.update(block)
            if sha1_digest is not None:
                sha1_digest.update(block)

    actual_md5 = md5_digest.hexdigest()
    if not hmac.compare_digest(actual_md5, spec.md5):
        raise ChecksumMismatch(spec.filename, "MD5", spec.md5, actual_md5)

    if sha1_digest is not None and spec.sha1 is not None:
        actual_sha1 = sha1_digest.hexdigest()
        if not hmac.compare_digest(actual_sha1, spec.sha1):
            raise ChecksumMismatch(spec.filename, "SHA-1", spec.sha1, actual_sha1)


def _target_paths(data_root: Path, spec: SourceSpec) -> tuple[Path, Path]:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    final = root / spec.filename
    partial = root / f"{spec.filename}.part"

    for candidate in (final, partial):
        if candidate.parent.resolve() != resolved_root or candidate.is_symlink():
            raise InvalidSourceSpec(
                f"source filename escapes data root: {spec.filename!r}"
            )
    return final, partial


def _status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()  # type: ignore[attr-defined]
    return int(status)


def _confirmed_range(
    response: object, expected_start: int, expected_total: int
) -> bool:
    if _status(response) != 206:
        return False
    content_range = response.headers.get("Content-Range")  # type: ignore[attr-defined]
    if not isinstance(content_range, str):
        return False
    match = _CONTENT_RANGE_PATTERN.fullmatch(content_range.strip())
    if match is None:
        return False
    start, end, total = (int(value) for value in match.groups())
    return (
        start == expected_start
        and end >= start
        and total == expected_total
        and end < total
    )


def _stream_response(
    response: object,
    partial: Path,
    spec: SourceSpec,
    *,
    append: bool,
) -> None:
    initial_size = partial.stat().st_size if append else 0
    total = initial_size
    mode = "ab" if append else "wb"
    try:
        with partial.open(mode) as output:
            while True:
                remaining_with_overflow_probe = max(spec.size - total + 1, 1)
                read_size = min(DOWNLOAD_CHUNK_SIZE, remaining_with_overflow_probe)
                block = response.read(read_size)  # type: ignore[attr-defined]
                if not block:
                    break
                next_total = total + len(block)
                if next_total > spec.size:
                    allowed = spec.size - total
                    if allowed > 0:
                        output.write(block[:allowed])
                    raise SizeMismatch(spec.filename, spec.size, next_total)
                output.write(block)
                total = next_total
    except VerificationError:
        raise
    except OSError as exc:
        raise DownloadError(f"download interrupted for {spec.filename}: {exc}") from exc


def _open(request: Request, spec: SourceSpec) -> object:
    try:
        return urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except OSError as exc:
        raise DownloadError(f"could not download {spec.filename}: {exc}") from exc


def _download_full(spec: SourceSpec, partial: Path) -> None:
    request = Request(spec.url)
    response = _open(request, spec)
    with response:  # type: ignore[attr-defined]
        if _status(response) != 200:
            raise DownloadError(
                f"unexpected HTTP status {_status(response)} for full download "
                f"of {spec.filename}"
            )
        _stream_response(response, partial, spec, append=False)


def _promote_verified_partial(partial: Path, final: Path, spec: SourceSpec) -> Path:
    verify_file(partial, spec)
    if final.exists() or final.is_symlink():
        try:
            verify_file(final, spec)
        except (OSError, VerificationError) as exc:
            raise ExistingFileInvalid(
                f"existing final file {final} is invalid; refusing to overwrite it"
            ) from exc
        partial.unlink()
        return final
    partial.replace(final)
    return final


def download_source(
    spec: SourceSpec, data_root: Path, resume: bool = True
) -> Path:
    """Download *spec* via a partial file and return its verified final path."""

    final, partial = _target_paths(Path(data_root), spec)

    if final.exists():
        try:
            verify_file(final, spec)
        except (OSError, VerificationError) as exc:
            raise ExistingFileInvalid(
                f"existing final file {final} is invalid; refusing to overwrite it"
            ) from exc
        return final

    partial_size = partial.stat().st_size if partial.exists() else 0
    if partial_size == spec.size:
        try:
            return _promote_verified_partial(partial, final, spec)
        except VerificationError:
            partial_size = 0
    elif partial_size > spec.size:
        partial_size = 0

    resume_start = partial_size if resume and 0 < partial_size < spec.size else 0
    if resume_start:
        ranged_request = Request(
            spec.url, headers={"Range": f"bytes={resume_start}-"}
        )
        response = _open(ranged_request, spec)
        with response:  # type: ignore[attr-defined]
            if _confirmed_range(response, resume_start, spec.size):
                _stream_response(response, partial, spec, append=True)
            elif _status(response) == 200:
                _stream_response(response, partial, spec, append=False)
            else:
                response = None
        if response is None:
            _download_full(spec, partial)
    else:
        _download_full(spec, partial)

    return _promote_verified_partial(partial, final, spec)


__all__ = [
    "ChecksumMismatch",
    "DownloadError",
    "ExistingFileInvalid",
    "InvalidSourceSpec",
    "SizeMismatch",
    "SourceError",
    "SourceSpec",
    "VerificationError",
    "download_source",
    "verify_file",
]
