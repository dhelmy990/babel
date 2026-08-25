from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data import sources  # noqa: E402
from babel_data.sources import (  # noqa: E402
    ChecksumMismatch,
    DownloadError,
    ExistingFileInvalid,
    InvalidSourceSpec,
    SizeMismatch,
    SourceError,
    SourceSpec,
    download_source,
    verify_file,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


class InterruptedResponse(FakeResponse):
    def __init__(self, first_chunk: bytes) -> None:
        super().__init__(b"")
        self._first_chunk = first_chunk

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self._first_chunk:
            chunk, self._first_chunk = self._first_chunk, b""
            return chunk
        raise OSError("connection dropped")


class FakeOpener:
    def __init__(self, *responses: FakeResponse | BaseException) -> None:
        self.responses = list(responses)
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def source_spec(
    payload: bytes,
    *,
    filename: str = "source.bin",
    sha1: str | None = None,
) -> SourceSpec:
    return SourceSpec(
        name="test source",
        url="https://example.test/source.bin",
        filename=filename,
        size=len(payload),
        md5=hashlib.md5(payload).hexdigest(),
        sha1=sha1,
    )


def request_headers(request: Request) -> dict[str, str]:
    return dict(request.header_items())


def test_verify_file_accepts_valid_size_and_digests(tmp_path: Path) -> None:
    payload = b"authoritative bytes"
    path = tmp_path / "source.bin"
    path.write_bytes(payload)
    spec = source_spec(payload, sha1=hashlib.sha1(payload).hexdigest())

    assert verify_file(path, spec) is None


def test_verify_file_rejects_wrong_size_before_digesting(tmp_path: Path) -> None:
    payload = b"short"
    path = tmp_path / "source.bin"
    path.write_bytes(payload)
    spec = SourceSpec(
        name="test source",
        url="https://example.test/source.bin",
        filename="source.bin",
        size=len(payload) + 1,
        md5=hashlib.md5(payload).hexdigest(),
    )

    with pytest.raises(SizeMismatch, match=r"source\.bin.*expected 6 bytes.*found 5"):
        verify_file(path, spec)


def test_verify_file_rejects_wrong_md5(tmp_path: Path) -> None:
    path = tmp_path / "source.bin"
    path.write_bytes(b"corrupt")
    spec = SourceSpec(
        name="test source",
        url="https://example.test/source.bin",
        filename="source.bin",
        size=7,
        md5="0" * 32,
    )

    with pytest.raises(ChecksumMismatch, match=r"source\.bin.*MD5"):
        verify_file(path, spec)

    assert path.read_bytes() == b"corrupt"


def test_verify_file_rejects_wrong_optional_sha1(tmp_path: Path) -> None:
    payload = b"correct md5 but wrong sha1"
    path = tmp_path / "source.bin"
    path.write_bytes(payload)
    spec = source_spec(payload, sha1="0" * 40)

    with pytest.raises(ChecksumMismatch, match=r"source\.bin.*SHA-1"):
        verify_file(path, spec)


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"url": "not-a-url"},
        {"size": 0},
        {"size": True},
        {"md5": "not-an-md5"},
        {"sha1": "not-a-sha1"},
        {"filename": "../escape.bin"},
        {"filename": "/tmp/escape.bin"},
        {"filename": r"..\escape.bin"},
    ],
)
def test_source_spec_rejects_obviously_invalid_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "name": "test source",
        "url": "https://example.test/source.bin",
        "filename": "source.bin",
        "size": 1,
        "md5": "0" * 32,
        "sha1": None,
    }
    values.update(overrides)

    with pytest.raises(InvalidSourceSpec):
        SourceSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "https://example.test:invalid/source.bin"),
        ("url", "https://example.test:70000/source.bin"),
        ("url", "https://example.test:/source.bin"),
        ("url", "https://exa mple.test/source.bin"),
        ("url", "https://example.test/source\n.bin"),
        ("url", "https://[::1/source.bin"),
        ("filename", " source.bin"),
        ("filename", "source\t.bin"),
        ("name", " test source"),
    ],
)
def test_source_spec_rejects_whitespace_controls_and_malformed_authorities(
    field: str, value: str
) -> None:
    values = {
        "name": "test source",
        "url": "https://example.test/source.bin",
        "filename": "source.bin",
        "size": 1,
        "md5": "0" * 32,
    }
    values[field] = value

    with pytest.raises(InvalidSourceSpec):
        SourceSpec(**values)  # type: ignore[arg-type]


def test_download_uses_part_file_until_verified_and_promotes_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"complete response body"
    response = FakeResponse(payload)
    opener = FakeOpener(response)
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path)

    assert result == tmp_path / "source.bin"
    assert result.read_bytes() == payload
    assert not (tmp_path / "source.bin.part").exists()
    assert opener.timeouts == [30.0]
    assert response.read_sizes
    assert all(0 < size <= 8 * 1024 * 1024 for size in response.read_sizes)


def test_download_resumes_only_from_a_confirmed_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"abcdef"
    (tmp_path / "source.bin.part").write_bytes(payload[:3])
    response = FakeResponse(
        payload[3:], status=206, headers={"Content-Range": "bytes 3-5/6"}
    )
    opener = FakeOpener(response)
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path, resume=True)

    assert result.read_bytes() == payload
    assert request_headers(opener.requests[0])["Range"] == "bytes=3-"


def test_download_restarts_partial_when_server_ignores_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"abcdef"
    (tmp_path / "source.bin.part").write_bytes(payload[:3])
    opener = FakeOpener(FakeResponse(payload, status=200))
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path, resume=True)

    assert result.read_bytes() == payload
    assert request_headers(opener.requests[0])["Range"] == "bytes=3-"
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    "content_range",
    [
        None,
        "not-a-range",
        "bytes 0-5/6",
        "bytes 3-2/6",
        "bytes 3-5/*",
        "bytes 3-5/7",
        "bytes 3-5/3",
        "bytes 3-7/6",
    ],
)
def test_download_restarts_with_a_new_request_for_malformed_range_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content_range: str | None,
) -> None:
    payload = b"abcdef"
    (tmp_path / "source.bin.part").write_bytes(payload[:3])
    headers = {} if content_range is None else {"Content-Range": content_range}
    ranged = FakeResponse(payload[3:], status=206, headers=headers)
    full = FakeResponse(payload, status=200)
    opener = FakeOpener(ranged, full)
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path, resume=True)

    assert result.read_bytes() == payload
    assert request_headers(opener.requests[0])["Range"] == "bytes=3-"
    assert "Range" not in request_headers(opener.requests[1])
    assert ranged.closed


@pytest.mark.parametrize(
    ("content_range", "content_length", "ranged_body"),
    [
        ("bytes 3-4/6", None, b"de"),
        ("bytes 3-5/6", "2", b"def"),
        ("bytes 3-5/6", "invalid", b"def"),
        ("bytes 3-5/6", None, b"de"),
        ("bytes 3-5/6", None, b"defX"),
    ],
)
def test_download_restarts_when_206_extent_length_or_body_lies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content_range: str,
    content_length: str | None,
    ranged_body: bytes,
) -> None:
    payload = b"abcdef"
    (tmp_path / "source.bin.part").write_bytes(payload[:3])
    headers = {"Content-Range": content_range}
    if content_length is not None:
        headers["Content-Length"] = content_length
    ranged = FakeResponse(ranged_body, status=206, headers=headers)
    opener = FakeOpener(ranged, FakeResponse(payload))
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path)

    assert result.read_bytes() == payload
    assert len(opener.requests) == 2
    assert "Range" not in request_headers(opener.requests[1])
    assert ranged.closed


def test_http_error_416_closes_response_and_restarts_full_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"abcdef"
    (tmp_path / "source.bin.part").write_bytes(payload[:3])
    error_body = io.BytesIO(b"range rejected")
    error = HTTPError(
        "https://example.test/source.bin", 416, "Range Not Satisfiable", {}, error_body
    )
    opener = FakeOpener(error, FakeResponse(payload))
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path)

    assert result.read_bytes() == payload
    assert error_body.closed
    assert len(opener.requests) == 2
    assert "Range" not in request_headers(opener.requests[1])


def test_non_416_http_error_is_closed_and_wrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    error_body = io.BytesIO(b"server error")
    error = HTTPError(
        "https://example.test/source.bin", 500, "Server Error", {}, error_body
    )
    monkeypatch.setattr(sources, "urlopen", FakeOpener(error))

    with pytest.raises(DownloadError, match="HTTP 500") as caught:
        download_source(source_spec(b"abcdef"), tmp_path)

    assert caught.value.__cause__ is error
    assert error_body.closed


def test_request_validation_error_is_wrapped_with_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invalid = http.client.InvalidURL("invalid request target")

    def reject_request(*args: object, **kwargs: object) -> Request:
        raise invalid

    monkeypatch.setattr(sources, "Request", reject_request)

    with pytest.raises(DownloadError, match=r"request.*source\.bin") as caught:
        download_source(source_spec(b"abcdef"), tmp_path)

    assert caught.value.__cause__ is invalid


def test_http_protocol_error_is_wrapped_with_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    protocol_error = http.client.HTTPException("broken protocol")
    monkeypatch.setattr(sources, "urlopen", FakeOpener(protocol_error))

    with pytest.raises(DownloadError, match=r"source\.bin") as caught:
        download_source(source_spec(b"abcdef"), tmp_path)

    assert caught.value.__cause__ is protocol_error


def test_incomplete_read_is_wrapped_and_partial_is_not_promoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"abcdef"

    class IncompleteResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise http.client.IncompleteRead(b"abc", 3)

    monkeypatch.setattr(sources, "urlopen", FakeOpener(IncompleteResponse(b"")))

    with pytest.raises(DownloadError, match=r"interrupted.*source\.bin") as caught:
        download_source(source_spec(payload), tmp_path)

    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)
    assert not (tmp_path / "source.bin").exists()


def test_download_reuses_verified_final_without_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"already present"
    final = tmp_path / "source.bin"
    final.write_bytes(payload)
    opener = FakeOpener()
    monkeypatch.setattr(sources, "urlopen", opener)

    assert download_source(source_spec(payload), tmp_path) == final
    assert opener.requests == []


def test_download_fails_safely_when_final_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final = tmp_path / "source.bin"
    final.write_bytes(b"bad")
    opener = FakeOpener()
    monkeypatch.setattr(sources, "urlopen", opener)

    with pytest.raises(ExistingFileInvalid, match="refusing to overwrite"):
        download_source(source_spec(b"good"), tmp_path)

    assert final.read_bytes() == b"bad"
    assert opener.requests == []


def test_symlink_injected_after_request_cannot_redirect_partial_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"authoritative"
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"do not touch")

    def inject_symlink(request: Request, *, timeout: float) -> FakeResponse:
        (tmp_path / "source.bin.part").symlink_to(victim)
        return FakeResponse(payload)

    monkeypatch.setattr(sources, "urlopen", inject_symlink)

    with pytest.raises(SourceError, match=r"source\.bin\.part"):
        download_source(source_spec(payload), tmp_path)

    assert victim.read_bytes() == b"do not touch"
    assert not (tmp_path / "source.bin").exists()


def test_existing_partial_symlink_is_rejected_without_touching_victim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"do not touch")
    (tmp_path / "source.bin.part").symlink_to(victim)
    opener = FakeOpener()
    monkeypatch.setattr(sources, "urlopen", opener)

    with pytest.raises(SourceError, match=r"source\.bin"):
        download_source(source_spec(b"authoritative"), tmp_path)

    assert victim.read_bytes() == b"do not touch"
    assert opener.requests == []


def test_final_symlink_injected_before_promotion_is_never_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"authoritative"
    victim = tmp_path / "victim.bin"
    victim.write_bytes(payload)

    class FinalSymlinkResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            block = super().read(size)
            if not block and not (tmp_path / "source.bin").exists():
                (tmp_path / "source.bin").symlink_to(victim)
            return block

    monkeypatch.setattr(
        sources, "urlopen", FakeOpener(FinalSymlinkResponse(payload))
    )

    with pytest.raises(SourceError, match=r"source\.bin"):
        download_source(source_spec(payload), tmp_path)

    assert victim.read_bytes() == payload
    assert (tmp_path / "source.bin").is_symlink()


def test_atomic_promotion_never_clobbers_a_racing_invalid_final(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"authoritative"
    raced_inode: list[int] = []

    def racing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        final = tmp_path / destination
        final.write_bytes(b"racing writer")
        raced_inode.append(final.stat().st_ino)
        raise FileExistsError(destination)

    monkeypatch.setattr(os, "link", racing_link)
    monkeypatch.setattr(sources, "urlopen", FakeOpener(FakeResponse(payload)))

    with pytest.raises(ExistingFileInvalid, match="refusing to overwrite"):
        download_source(source_spec(payload), tmp_path)

    final = tmp_path / "source.bin"
    assert raced_inode
    assert final.stat().st_ino == raced_inode[0]
    assert final.read_bytes() == b"racing writer"
    assert (tmp_path / "source.bin.part").read_bytes() == payload


def test_concurrent_downloads_of_same_target_are_serialized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"authoritative"
    first_request_started = threading.Event()
    second_request_started = threading.Event()
    release_first_request = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def gated_opener(request: Request, *, timeout: float) -> FakeResponse:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_request_started.set()
            assert release_first_request.wait(timeout=2)
        else:
            second_request_started.set()
        return FakeResponse(payload)

    monkeypatch.setattr(sources, "urlopen", gated_opener)
    spec = source_spec(payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(download_source, spec, tmp_path)
        assert first_request_started.wait(timeout=2)
        second = executor.submit(download_source, spec, tmp_path)
        raced = second_request_started.wait(timeout=0.2)
        release_first_request.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert results == [tmp_path / "source.bin", tmp_path / "source.bin"]
    assert (tmp_path / "source.bin").read_bytes() == payload
    assert not raced
    assert calls == 1


def test_download_restarts_an_oversized_partial_without_a_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"expected"
    (tmp_path / "source.bin.part").write_bytes(payload + b"unverified")
    opener = FakeOpener(FakeResponse(payload))
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path)

    assert result.read_bytes() == payload
    assert "Range" not in request_headers(opener.requests[0])


def test_interrupted_download_leaves_part_and_never_promotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"abcdef"
    opener = FakeOpener(InterruptedResponse(payload[:3]))
    monkeypatch.setattr(sources, "urlopen", opener)

    with pytest.raises(DownloadError, match=r"source\.bin"):
        download_source(source_spec(payload), tmp_path)

    assert (tmp_path / "source.bin.part").read_bytes() == payload[:3]
    assert not (tmp_path / "source.bin").exists()


def test_complete_verified_part_promotes_without_a_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"complete partial"
    (tmp_path / "source.bin.part").write_bytes(payload)
    opener = FakeOpener()
    monkeypatch.setattr(sources, "urlopen", opener)

    result = download_source(source_spec(payload), tmp_path)

    assert result.read_bytes() == payload
    assert opener.requests == []


def test_integrity_hashes_use_fips_compatible_nonsecurity_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"authoritative"
    path = tmp_path / "source.bin"
    path.write_bytes(payload)
    original_md5 = hashlib.md5
    original_sha1 = hashlib.sha1
    spec = source_spec(payload, sha1=original_sha1(payload).hexdigest())
    md5_modes: list[object] = []
    sha1_modes: list[object] = []
    missing = object()

    def fips_md5(data: bytes = b"", **kwargs: object) -> object:
        mode = kwargs.get("usedforsecurity", missing)
        md5_modes.append(mode)
        if mode is False:
            raise TypeError("old hashlib without usedforsecurity")
        return original_md5(data)

    def fips_sha1(data: bytes = b"", **kwargs: object) -> object:
        mode = kwargs.get("usedforsecurity", missing)
        sha1_modes.append(mode)
        if mode is False:
            raise TypeError("old hashlib without usedforsecurity")
        return original_sha1(data)

    monkeypatch.setattr(sources.hashlib, "md5", fips_md5)
    monkeypatch.setattr(sources.hashlib, "sha1", fips_sha1)

    verify_file(path, spec)

    assert md5_modes == [False, missing]
    assert sha1_modes == [False, missing]


def test_manifest_contains_exact_approved_sources_and_constructs_specs() -> None:
    manifest_path = REPOSITORY_ROOT / "data_pipeline" / "manifests" / "2016-sources.json"
    entries = json.loads(manifest_path.read_text())

    assert entries == [
        {
            "name": "Teacher ZIP",
            "url": "https://ndownloader.figshare.com/files/7455673",
            "filename": "2016-09-01_2016-09-30_en_100.zip",
            "size": 727429988,
            "md5": "ac70acfc41aff7a23cc9439e3bb1771f",
        },
        {
            "name": "Wikipedia XML",
            "url": "https://archive.org/download/enwiki-20161001/enwiki-20161001-pages-articles-multistream.xml.bz2",
            "filename": "enwiki-20161001-pages-articles-multistream.xml.bz2",
            "size": 14178624372,
            "md5": "5df8e610829c336138dcb9191071b283",
            "sha1": "86ba305ecc41dafcf03ba3e67c2eacb95724d5ca",
        },
        {
            "name": "Wikipedia multistream index",
            "url": "https://archive.org/download/enwiki-20161001/enwiki-20161001-pages-articles-multistream-index.txt.bz2",
            "filename": "enwiki-20161001-pages-articles-multistream-index.txt.bz2",
            "size": 185177516,
            "md5": "7c9486cde3f9c43ff4e23443dd2323f3",
            "sha1": "f13aebe90c8bea2157d826659e0320157a1978d9",
        },
    ]
    assert [SourceSpec(**entry) for entry in entries]
