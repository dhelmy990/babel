"""Verified, resumable acquisition of immutable source files.

Security requirements and threat model
--------------------------------------
Downloads require Linux/POSIX ``dir_fd`` operations, ``O_NOFOLLOW``, ``flock``,
hard links, and ``/proc/self/fd``. Unsupported systems fail closed. The caller
must provide a trusted, local writable ``data_root``: the code defends against
symlink/hard-link substitution and cooperating concurrent writers, but cannot
make security guarantees for hostile FUSE/network filesystems or an attacker
that can replace the mounted directory itself while acquisition is running.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported platforms
    fcntl = None  # type: ignore[assignment]


DIGEST_CHUNK_SIZE = 8 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
_MD5_PATTERN = re.compile(r"[0-9a-f]{32}")
_SHA1_PATTERN = re.compile(r"[0-9a-f]{40}")
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (\d+)-(\d+)/(\d+)", re.IGNORECASE)
_DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_HAS_REQUIRED_DIR_FD = all(
    function in os.supports_dir_fd for function in (os.open, os.stat, os.unlink, os.link)
)


class SourceError(Exception):
    """Base exception for source verification and acquisition failures."""


class InvalidSourceSpec(ValueError, SourceError):
    """Raised when a source specification is structurally unsafe or invalid."""


class UnsafeSourcePath(SourceError):
    """Raised when a source path cannot be accessed without following links."""


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


class _RangeBodyMismatch(SourceError):
    """Internal signal that a ranged response must be discarded."""


def _contains_control_or_whitespace(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    )


def _valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return False
    return bool(ascii_hostname) and len(ascii_hostname) <= 253 and all(
        _DNS_LABEL_PATTERN.fullmatch(label) is not None
        for label in ascii_hostname.split(".")
    )


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
        if (
            not isinstance(self.name, str)
            or not self.name
            or self.name != self.name.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.name
            )
        ):
            raise InvalidSourceSpec(
                "source name must be a non-empty string without surrounding whitespace"
            )

        if not isinstance(self.url, str) or _contains_control_or_whitespace(self.url):
            raise InvalidSourceSpec("source URL must be a well-formed HTTP(S) URL")
        try:
            parsed_url = urlsplit(self.url)
            hostname = parsed_url.hostname
            parsed_url.port
        except ValueError as exc:
            raise InvalidSourceSpec(
                "source URL must be a well-formed HTTP(S) URL"
            ) from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.netloc.rsplit("@", 1)[-1].endswith(":")
            or hostname is None
            or not _valid_hostname(hostname)
        ):
            raise InvalidSourceSpec("source URL must be a well-formed HTTP(S) URL")

        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size <= 0
        ):
            raise InvalidSourceSpec("source size must be a positive integer")

        if (
            not isinstance(self.md5, str)
            or _MD5_PATTERN.fullmatch(self.md5) is None
        ):
            raise InvalidSourceSpec(
                "source MD5 must be 32 lowercase hexadecimal characters"
            )

        if self.sha1 is not None and (
            not isinstance(self.sha1, str)
            or _SHA1_PATTERN.fullmatch(self.sha1) is None
        ):
            raise InvalidSourceSpec(
                "source SHA-1 must be 40 lowercase hexadecimal characters"
            )

        if (
            not isinstance(self.filename, str)
            or not self.filename
            or _contains_control_or_whitespace(self.filename)
        ):
            raise InvalidSourceSpec(
                "source filename must be a non-empty basename without whitespace"
            )
        if (
            self.filename in {".", ".."}
            or Path(self.filename).name != self.filename
            or "/" in self.filename
            or "\\" in self.filename
            or "\x00" in self.filename
        ):
            raise InvalidSourceSpec("source filename must be a safe basename")


def _new_md5() -> object:
    try:
        return hashlib.md5(usedforsecurity=False)
    except TypeError:  # Python/OpenSSL implementations predating this keyword
        return hashlib.md5()


def _new_sha1() -> object:
    try:
        return hashlib.sha1(usedforsecurity=False)
    except TypeError:  # Python/OpenSSL implementations predating this keyword
        return hashlib.sha1()


def _verify_fd(file_descriptor: int, spec: SourceSpec) -> None:
    file_stat = os.fstat(file_descriptor)
    if not stat.S_ISREG(file_stat.st_mode):
        raise UnsafeSourcePath(f"{spec.filename} is not a regular file")
    if file_stat.st_size != spec.size:
        raise SizeMismatch(spec.filename, spec.size, file_stat.st_size)

    md5_digest = _new_md5()
    sha1_digest = _new_sha1() if spec.sha1 is not None else None
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    while True:
        block = os.read(file_descriptor, DIGEST_CHUNK_SIZE)
        if not block:
            break
        md5_digest.update(block)  # type: ignore[attr-defined]
        if sha1_digest is not None:
            sha1_digest.update(block)  # type: ignore[attr-defined]

    actual_md5 = md5_digest.hexdigest()  # type: ignore[attr-defined]
    if not hmac.compare_digest(actual_md5, spec.md5):
        raise ChecksumMismatch(spec.filename, "MD5", spec.md5, actual_md5)

    if sha1_digest is not None and spec.sha1 is not None:
        actual_sha1 = sha1_digest.hexdigest()  # type: ignore[attr-defined]
        if not hmac.compare_digest(actual_sha1, spec.sha1):
            raise ChecksumMismatch(spec.filename, "SHA-1", spec.sha1, actual_sha1)


def verify_file(path: Path, spec: SourceSpec) -> None:
    """Verify exact size and all declared digests without changing *path*."""

    try:
        file_descriptor = os.open(
            Path(path), os.O_RDONLY | _O_CLOEXEC | _O_NOFOLLOW
        )
    except OSError as exc:
        raise UnsafeSourcePath(
            f"cannot safely open {spec.filename} for verification: {exc}"
        ) from exc
    try:
        _verify_fd(file_descriptor, spec)
    finally:
        os.close(file_descriptor)


def _require_secure_platform() -> None:
    if (
        fcntl is None
        or not _O_DIRECTORY
        or not _O_NOFOLLOW
        or not _HAS_REQUIRED_DIR_FD
        or not Path("/proc/self/fd").is_dir()
    ):
        raise DownloadError(
            "secure source acquisition requires POSIX dir-fd, no-follow, flock, "
            "and /proc/self/fd support"
        )


@contextmanager
def _open_data_root(data_root: Path) -> Iterator[int]:
    _require_secure_platform()
    root = Path(data_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        root_descriptor = os.open(
            root, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        )
    except OSError as exc:
        raise DownloadError(f"cannot safely open data root {root}: {exc}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise DownloadError(f"data root is not a directory: {root}")
        yield root_descriptor
    finally:
        os.close(root_descriptor)


def _open_regular_at(
    root_descriptor: int,
    filename: str,
    flags: int,
    *,
    create: bool = False,
    require_single_link: bool = False,
) -> int:
    open_flags = flags | _O_NOFOLLOW | _O_CLOEXEC
    if create:
        open_flags |= os.O_CREAT
    try:
        file_descriptor = os.open(
            filename, open_flags, 0o600, dir_fd=root_descriptor
        )
    except OSError as exc:
        raise UnsafeSourcePath(f"cannot safely open {filename}: {exc}") from exc
    try:
        file_stat = os.fstat(file_descriptor)
    except OSError:
        os.close(file_descriptor)
        raise
    if stat.S_ISREG(file_stat.st_mode) and (
        not require_single_link or file_stat.st_nlink == 1
    ):
        return file_descriptor
    os.close(file_descriptor)
    raise UnsafeSourcePath(
        f"{filename} must be a regular file with no external hard links"
    )


@contextmanager
def _target_lock(root_descriptor: int, filename: str) -> Iterator[None]:
    lock_name = f".{filename}.lock"
    lock_descriptor = _open_regular_at(
        root_descriptor, lock_name, os.O_RDWR, create=True
    )
    locked = False
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)  # type: ignore[union-attr]
        locked = True
        yield
    finally:
        try:
            if locked:
                fcntl.flock(  # type: ignore[union-attr]
                    lock_descriptor, fcntl.LOCK_UN
                )
        finally:
            os.close(lock_descriptor)


def _entry_stat(root_descriptor: int, filename: str) -> os.stat_result | None:
    try:
        entry = os.stat(filename, dir_fd=root_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(entry.st_mode):
        raise UnsafeSourcePath(f"{filename} is not a regular file")
    return entry


def _open_partial(root_descriptor: int, filename: str) -> int:
    try:
        return _open_regular_at(
            root_descriptor,
            filename,
            os.O_RDWR | os.O_EXCL,
            create=True,
            require_single_link=True,
        )
    except UnsafeSourcePath as exc:
        if not isinstance(exc.__cause__, FileExistsError):
            raise
    return _open_regular_at(
        root_descriptor,
        filename,
        os.O_RDWR,
        require_single_link=True,
    )


def _verify_existing_final(
    root_descriptor: int, filename: str, spec: SourceSpec
) -> bool:
    try:
        if _entry_stat(root_descriptor, filename) is None:
            return False
        final_descriptor = _open_regular_at(
            root_descriptor,
            filename,
            os.O_RDONLY,
            require_single_link=True,
        )
        try:
            _require_named_inode(
                root_descriptor,
                filename,
                final_descriptor,
                expected_links=1,
            )
            _verify_fd(final_descriptor, spec)
            _require_named_inode(
                root_descriptor,
                filename,
                final_descriptor,
                expected_links=1,
            )
        finally:
            os.close(final_descriptor)
    except (OSError, SourceError) as exc:
        raise ExistingFileInvalid(
            f"existing final file {filename} is invalid; refusing to overwrite it"
        ) from exc
    return True


def _make_request(spec: SourceSpec, headers: dict[str, str] | None = None) -> Request:
    try:
        return Request(spec.url, headers=headers or {})
    except (ValueError, http.client.HTTPException) as exc:
        raise DownloadError(f"could not create request for {spec.filename}: {exc}") from exc


def _open_http(
    request: Request, spec: SourceSpec, *, allow_range_416: bool = False
) -> object | None:
    try:
        return urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except HTTPError as exc:
        try:
            if allow_range_416 and exc.code == 416:
                return None
            raise DownloadError(
                f"HTTP {exc.code} while downloading {spec.filename}: {exc.reason}"
            ) from exc
        finally:
            exc.close()
    except (ValueError, http.client.HTTPException, OSError) as exc:
        raise DownloadError(f"could not download {spec.filename}: {exc}") from exc


@contextmanager
def _close_response(response: object) -> Iterator[object]:
    try:
        yield response
    finally:
        response.close()  # type: ignore[attr-defined]


def _status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()  # type: ignore[attr-defined]
    return int(status)


def _confirmed_range_length(
    response: object, expected_start: int, expected_total: int
) -> int | None:
    if _status(response) != 206:
        return None
    content_range = response.headers.get("Content-Range")  # type: ignore[attr-defined]
    if not isinstance(content_range, str):
        return None
    match = _CONTENT_RANGE_PATTERN.fullmatch(content_range.strip())
    if match is None:
        return None
    start, end, total = (int(value) for value in match.groups())
    if not (
        start == expected_start
        and end >= start
        and total == expected_total
        and end == total - 1
    ):
        return None
    extent = end - start + 1
    content_length = response.headers.get("Content-Length")  # type: ignore[attr-defined]
    if content_length is not None:
        if not isinstance(content_length, str) or not content_length.isdigit():
            return None
        if int(content_length) != extent:
            return None
    return extent


def _write_all(file_descriptor: int, block: bytes) -> None:
    view = memoryview(block)
    while view:
        written = os.write(file_descriptor, view)
        if written <= 0:
            raise OSError("write made no progress")
        view = view[written:]


def _stream_response(
    response: object,
    partial_descriptor: int,
    spec: SourceSpec,
    *,
    initial_size: int,
    expected_response_bytes: int | None = None,
) -> None:
    total = initial_size
    received = 0
    os.lseek(partial_descriptor, initial_size, os.SEEK_SET)
    try:
        while True:
            remaining_file_probe = max(spec.size - total + 1, 1)
            read_size = min(DOWNLOAD_CHUNK_SIZE, remaining_file_probe)
            if expected_response_bytes is not None:
                range_probe = max(expected_response_bytes - received + 1, 1)
                read_size = min(read_size, range_probe)
            block = response.read(read_size)  # type: ignore[attr-defined]
            if not block:
                break
            if expected_response_bytes is not None and (
                received + len(block) > expected_response_bytes
            ):
                raise _RangeBodyMismatch(
                    f"ranged response body is longer than declared for {spec.filename}"
                )
            next_total = total + len(block)
            if next_total > spec.size:
                raise SizeMismatch(spec.filename, spec.size, next_total)
            _write_all(partial_descriptor, block)
            total = next_total
            received += len(block)
    except _RangeBodyMismatch:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise DownloadError(f"download interrupted for {spec.filename}: {exc}") from exc
    if expected_response_bytes is not None and received != expected_response_bytes:
        raise _RangeBodyMismatch(
            f"ranged response body is shorter than declared for {spec.filename}"
        )


def _download_full(spec: SourceSpec, partial_descriptor: int) -> None:
    request = _make_request(spec)
    response = _open_http(request, spec)
    assert response is not None
    with _close_response(response):
        if _status(response) != 200:
            raise DownloadError(
                f"unexpected HTTP status {_status(response)} for full download "
                f"of {spec.filename}"
            )
        os.ftruncate(partial_descriptor, 0)
        _stream_response(response, partial_descriptor, spec, initial_size=0)


def _download_or_resume(
    spec: SourceSpec,
    partial_descriptor: int,
    partial_size: int,
    resume: bool,
) -> None:
    resume_start = partial_size if resume and 0 < partial_size < spec.size else 0
    if not resume_start:
        _download_full(spec, partial_descriptor)
        return

    ranged_request = _make_request(
        spec, headers={"Range": f"bytes={resume_start}-"}
    )
    response = _open_http(ranged_request, spec, allow_range_416=True)
    if response is None:
        _download_full(spec, partial_descriptor)
        return

    restart_full = False
    with _close_response(response):
        range_length = _confirmed_range_length(response, resume_start, spec.size)
        if range_length is not None:
            try:
                _stream_response(
                    response,
                    partial_descriptor,
                    spec,
                    initial_size=resume_start,
                    expected_response_bytes=range_length,
                )
            except _RangeBodyMismatch:
                restart_full = True
        elif _status(response) == 200:
            os.ftruncate(partial_descriptor, 0)
            _stream_response(response, partial_descriptor, spec, initial_size=0)
        else:
            restart_full = True
    if restart_full:
        _download_full(spec, partial_descriptor)


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _require_named_inode(
    root_descriptor: int,
    filename: str,
    file_descriptor: int,
    *,
    expected_links: int,
) -> os.stat_result:
    descriptor_stat = os.fstat(file_descriptor)
    entry_stat = _entry_stat(root_descriptor, filename)
    if (
        entry_stat is None
        or not _same_inode(entry_stat, descriptor_stat)
        or descriptor_stat.st_nlink != expected_links
        or entry_stat.st_nlink != expected_links
    ):
        raise UnsafeSourcePath(
            f"{filename} hard-link/identity invariant failed: "
            f"expected {expected_links} link(s)"
        )
    return descriptor_stat


def _unlink_if_same_inode(
    root_descriptor: int, filename: str, expected: os.stat_result
) -> bool:
    try:
        entry_stat = os.stat(
            filename, dir_fd=root_descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(entry_stat.st_mode) or not _same_inode(entry_stat, expected):
        return False
    os.unlink(filename, dir_fd=root_descriptor)
    return True


def _atomic_promote(
    root_descriptor: int,
    partial_descriptor: int,
    partial_name: str,
    final_name: str,
    spec: SourceSpec,
) -> None:
    _verify_fd(partial_descriptor, spec)
    partial_stat = _require_named_inode(
        root_descriptor,
        partial_name,
        partial_descriptor,
        expected_links=1,
    )
    source_fd_path = f"/proc/self/fd/{partial_descriptor}"
    try:
        os.link(
            source_fd_path,
            final_name,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
            follow_symlinks=True,
        )
    except FileExistsError:
        if not _verify_existing_final(root_descriptor, final_name, spec):
            raise DownloadError(f"racing final file disappeared: {final_name}")
        _require_named_inode(
            root_descriptor,
            partial_name,
            partial_descriptor,
            expected_links=1,
        )
        if not _unlink_if_same_inode(root_descriptor, partial_name, partial_stat):
            raise UnsafeSourcePath(
                f"{partial_name} changed before safe cleanup"
            )
        if os.fstat(partial_descriptor).st_nlink != 0:
            raise UnsafeSourcePath(
                f"{partial_name} retained an unexpected external hard link"
            )
        return
    except OSError as exc:
        raise DownloadError(
            f"could not atomically promote verified {partial_name}: {exc}"
        ) from exc

    linked_stat = os.stat(
        final_name, dir_fd=root_descriptor, follow_symlinks=False
    )
    current_partial_stat = os.fstat(partial_descriptor)
    if not _same_inode(linked_stat, current_partial_stat):
        raise DownloadError(f"atomic promotion identity mismatch for {final_name}")

    _require_named_inode(
        root_descriptor,
        partial_name,
        partial_descriptor,
        expected_links=2,
    )
    if linked_stat.st_nlink != 2 or current_partial_stat.st_nlink != 2:
        raise UnsafeSourcePath(
            f"{partial_name} hard-link invariant failed during promotion"
        )

    if not _unlink_if_same_inode(root_descriptor, partial_name, current_partial_stat):
        raise UnsafeSourcePath(f"{partial_name} changed before safe cleanup")

    final_inode = os.fstat(partial_descriptor)
    if final_inode.st_nlink != 1:
        raise UnsafeSourcePath(
            f"{final_name} retained an unexpected external hard link"
        )
    final_entry = os.stat(
        final_name, dir_fd=root_descriptor, follow_symlinks=False
    )
    if not _same_inode(final_entry, final_inode) or final_entry.st_nlink != 1:
        raise DownloadError(f"atomic promotion identity mismatch for {final_name}")


def download_source(
    spec: SourceSpec, data_root: Path, resume: bool = True
) -> Path:
    """Download *spec* via a locked partial file and return its verified path."""

    final_name = spec.filename
    partial_name = f"{spec.filename}.part"
    result = Path(data_root) / final_name

    with _open_data_root(Path(data_root)) as root_descriptor:
        with _target_lock(root_descriptor, final_name):
            if _verify_existing_final(root_descriptor, final_name, spec):
                return result

            partial_descriptor = _open_partial(root_descriptor, partial_name)
            try:
                partial_size = os.fstat(partial_descriptor).st_size
                if partial_size == spec.size:
                    try:
                        _verify_fd(partial_descriptor, spec)
                    except VerificationError:
                        partial_size = 0
                    else:
                        _atomic_promote(
                            root_descriptor,
                            partial_descriptor,
                            partial_name,
                            final_name,
                            spec,
                        )
                        return result
                elif partial_size > spec.size:
                    partial_size = 0

                _download_or_resume(spec, partial_descriptor, partial_size, resume)
                _atomic_promote(
                    root_descriptor,
                    partial_descriptor,
                    partial_name,
                    final_name,
                    spec,
                )
            finally:
                os.close(partial_descriptor)
    return result


__all__ = [
    "ChecksumMismatch",
    "DownloadError",
    "ExistingFileInvalid",
    "InvalidSourceSpec",
    "SizeMismatch",
    "SourceError",
    "SourceSpec",
    "UnsafeSourcePath",
    "VerificationError",
    "download_source",
    "verify_file",
]
