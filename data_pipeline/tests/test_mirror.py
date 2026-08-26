from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.cli import main  # noqa: E402
from babel_data.hub import DEFAULT_REPO_ID  # noqa: E402
from babel_data.mirror import (  # noqa: E402
    MirrorVerificationError,
    SourceMirrorReceiptV1,
    mirror_source,
    open_processing_source,
    receipt_path,
)
from babel_data.sources import SourcePolicyError, SourceSpec  # noqa: E402


COMMIT = "a" * 40
PAYLOAD = b"authoritative bytes for the private mirror"


class FakeHub:
    def __init__(
        self,
        *,
        commit: str = COMMIT,
        private: bool = True,
        corrupt_remote: bool = False,
    ) -> None:
        self.commit = commit
        self.private = private
        self.corrupt_remote = corrupt_remote
        self.remote: dict[str, bytes] = {}
        self.create_repo_calls: list[dict[str, object]] = []
        self.commit_calls: list[dict[str, object]] = []
        self.info_calls: list[dict[str, object]] = []
        self.download_calls: list[dict[str, object]] = []

    def create_repo(self, **kwargs: object) -> None:
        self.create_repo_calls.append(kwargs)

    def dataset_info(self, repo_id: str, **kwargs: object) -> object:
        self.info_calls.append({"repo_id": repo_id, **kwargs})
        revision = kwargs.get("revision")
        sha = self.commit if revision in (None, "main") else revision
        return SimpleNamespace(sha=sha, private=self.private)

    def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
        self.commit_calls.append(kwargs)
        for operation in operations:
            path = str(operation.path_in_repo)
            content = Path(operation.path_or_fileobj).read_bytes()
            self.remote[path] = content
        return SimpleNamespace(oid=self.commit)

    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        self.download_calls.append({"path_in_repo": path_in_repo, **kwargs})
        content = self.remote[path_in_repo]
        if self.corrupt_remote:
            return content + b"corrupt"
        return content


def source_spec(payload: bytes = PAYLOAD) -> SourceSpec:
    return SourceSpec(
        name="Wikipedia multistream index",
        url="https://archive.org/download/enwiki/source.txt.bz2",
        filename="source.txt.bz2",
        size=len(payload),
        md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        sha1=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
    )


def install_authoritative_download(
    monkeypatch: pytest.MonkeyPatch, payload: bytes = PAYLOAD
) -> list[Path]:
    import babel_data.mirror as mirror

    destinations: list[Path] = []

    def download(spec: SourceSpec, destination: Path) -> Path:
        destinations.append(destination)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / spec.filename
        path.write_bytes(payload)
        return path

    monkeypatch.setattr(mirror, "download_source", download)
    return destinations


def test_successful_mirror_is_remote_verified_and_sha256_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destinations = install_authoritative_download(monkeypatch)
    api = FakeHub()

    receipt = mirror_source(
        source_spec(),
        api,
        repository=DEFAULT_REPO_ID,
        token="secret-token",
        data_root=tmp_path,
    )

    digest = hashlib.sha256(PAYLOAD).hexdigest()
    assert receipt.state == "remote_verified"
    assert receipt.local_sha256 == receipt.remote_sha256 == digest
    assert receipt.expected_sha256 == digest
    assert receipt.path_in_repo == (
        "sources/wikipedia-multistream-index/source.txt.bz2"
    )
    assert receipt.remote_commit_sha == COMMIT
    assert destinations == [tmp_path / "raw-mirror-staging"]
    assert api.create_repo_calls == [
        {
            "repo_id": DEFAULT_REPO_ID,
            "repo_type": "dataset",
            "private": True,
            "exist_ok": True,
            "token": "secret-token",
        }
    ]
    assert api.commit_calls == [
        {
            "repo_id": DEFAULT_REPO_ID,
            "repo_type": "dataset",
            "revision": "main",
            "parent_commit": COMMIT,
            "commit_message": "Mirror authoritative source wikipedia-multistream-index",
            "token": "secret-token",
        }
    ]
    saved = receipt_path(tmp_path / "hf-cache", receipt)
    assert saved.read_bytes() == receipt.to_json_bytes()
    assert "secret-token" not in saved.read_text()


def test_receipt_json_is_deterministic_closed_and_immutable() -> None:
    receipt = SourceMirrorReceiptV1(
        source_id="wikipedia-index",
        authoritative_url="https://example.test/index.bz2",
        expected_sha256="1" * 64,
        bytes=123,
        repository=DEFAULT_REPO_ID,
        path_in_repo="sources/wikipedia-index/index.bz2",
        remote_commit_sha=COMMIT,
        remote_sha256="1" * 64,
    )

    assert receipt.to_json_bytes() == (
        b'{"authoritative_url":"https://example.test/index.bz2","bytes":123,'
        b'"expected_sha256":"1111111111111111111111111111111111111111111111111111111111111111",'
        b'"path_in_repo":"sources/wikipedia-index/index.bz2",'
        b'"remote_commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"remote_sha256":"1111111111111111111111111111111111111111111111111111111111111111",'
        b'"repository":"dhelmy990/babel-wikipedia-experiment",'
        b'"source_id":"wikipedia-index","state":"remote_verified"}\n'
    )
    assert SourceMirrorReceiptV1.from_json_bytes(receipt.to_json_bytes()) == receipt
    document = json.loads(receipt.to_json_bytes())
    document["extra"] = True
    with pytest.raises(ValueError, match="exactly"):
        SourceMirrorReceiptV1.from_json_bytes(
            (json.dumps(document) + "\n").encode()
        )
    with pytest.raises((AttributeError, TypeError)):
        receipt.state = "unchecked"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_sha256", None),
        ("path_in_repo", 7),
        ("remote_commit_sha", None),
        ("remote_sha256", None),
    ],
)
def test_receipt_closed_validation_rejects_wrong_field_types(
    field: str, value: object
) -> None:
    document = {
        "source_id": "wikipedia-index",
        "authoritative_url": "https://example.test/index.bz2",
        "expected_sha256": "1" * 64,
        "bytes": 123,
        "repository": DEFAULT_REPO_ID,
        "path_in_repo": "sources/wikipedia-index/index.bz2",
        "remote_commit_sha": COMMIT,
        "remote_sha256": "1" * 64,
        "state": "remote_verified",
    }
    document[field] = value

    with pytest.raises(ValueError):
        SourceMirrorReceiptV1.from_json_bytes(
            (json.dumps(document) + "\n").encode()
        )


def test_direct_authoritative_url_is_never_a_processing_source() -> None:
    with pytest.raises(SourcePolicyError, match="pinned Hugging Face mirror"):
        open_processing_source(
            "https://archive.org/download/enwiki-20161001/"
            "enwiki-20161001-pages-articles-multistream.xml.bz2"
        )


@pytest.mark.parametrize(
    "revision",
    ["a" * 39, "a" * 41, "A" * 40, "main", "0" * 39 + "g"],
)
def test_processing_requires_exact_lowercase_commit(
    tmp_path: Path, revision: str
) -> None:
    with pytest.raises(SourcePolicyError, match="40-character lowercase"):
        open_processing_source(
            DEFAULT_REPO_ID,
            revision,
            "sources/wikipedia-index/index.bz2",
            "token",
            tmp_path / "hf-cache",
            api=FakeHub(),
        )


def test_processing_requires_the_fixed_private_repository(tmp_path: Path) -> None:
    with pytest.raises(SourcePolicyError, match="private Hugging Face repository"):
        open_processing_source(
            "someone/public-repository",
            COMMIT,
            "sources/wikipedia-index/index.bz2",
            "token",
            tmp_path / "hf-cache",
            api=FakeHub(),
        )


def test_processing_cache_root_must_be_absolute() -> None:
    with pytest.raises(SourcePolicyError, match="cache root must be absolute"):
        open_processing_source(
            DEFAULT_REPO_ID,
            COMMIT,
            "sources/wikipedia-index/index.bz2",
            "token",
            Path("relative/hf-cache"),
            api=FakeHub(),
        )


def test_processing_authenticates_private_exact_revision_and_caches_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_authoritative_download(monkeypatch)
    api = FakeHub()
    receipt = mirror_source(
        source_spec(), api, repository=DEFAULT_REPO_ID,
        token="private-token", data_root=tmp_path,
    )

    first = open_processing_source(
        receipt.repository,
        receipt.remote_commit_sha,
        receipt.path_in_repo,
        "private-token",
        tmp_path / "hf-cache",
        api=api,
    )
    second = open_processing_source(
        receipt.repository,
        receipt.remote_commit_sha,
        receipt.path_in_repo,
        "private-token",
        tmp_path / "hf-cache",
        api=api,
    )

    expected = tmp_path / "hf-cache" / COMMIT / receipt.path_in_repo
    assert first == second == expected
    assert first.read_bytes() == PAYLOAD
    assert all(call["token"] == "private-token" for call in api.info_calls)
    assert all(call["revision"] == COMMIT for call in api.download_calls)
    assert not str(first).startswith(str(tmp_path / "raw-mirror-staging"))


def test_processing_rejects_unproved_private_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_authoritative_download(monkeypatch)
    api = FakeHub(private=True)
    receipt = mirror_source(
        source_spec(), api, repository=DEFAULT_REPO_ID,
        token="token", data_root=tmp_path,
    )
    api.private = False

    with pytest.raises(SourcePolicyError, match="proved private"):
        open_processing_source(
            receipt.repository, receipt.remote_commit_sha, receipt.path_in_repo,
            "token", tmp_path / "hf-cache", api=api,
        )


def test_mirror_rejects_remote_checksum_or_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_authoritative_download(monkeypatch)
    with pytest.raises(MirrorVerificationError, match="remote.*bytes"):
        mirror_source(
            source_spec(), FakeHub(corrupt_remote=True),
            repository=DEFAULT_REPO_ID, token="token", data_root=tmp_path,
        )


def test_processing_rejects_receipt_checksum_and_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_authoritative_download(monkeypatch)
    api = FakeHub()
    receipt = mirror_source(
        source_spec(), api, repository=DEFAULT_REPO_ID,
        token="token", data_root=tmp_path,
    )
    saved = receipt_path(tmp_path / "hf-cache", receipt)
    document = json.loads(saved.read_text())
    document["bytes"] += 1
    saved.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(MirrorVerificationError, match="size"):
        open_processing_source(
            receipt.repository, receipt.remote_commit_sha, receipt.path_in_repo,
            "token", tmp_path / "hf-cache", api=api,
        )


@pytest.mark.parametrize(
    "data_root",
    [None, "relative/data", str(REPOSITORY_ROOT / "data")],
)
def test_mirror_cli_rejects_missing_relative_or_repository_contained_data_root(
    data_root: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("BABEL_DATA_ROOT", raising=False)
    argv = ["mirror-source", "--source-id", "wikipedia-multistream-index"]
    if data_root is not None:
        argv.extend(["--data-root", data_root])

    assert main(argv) == 1
    message = json.loads(capsys.readouterr().err)
    assert message["status"] == "error"
    assert "data root" in message["message"]


def test_mirror_cli_uses_environment_root_and_never_exposes_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    secret = "never-expose-hf-token"
    monkeypatch.setenv("BABEL_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", secret)
    monkeypatch.setattr(cli, "_api", lambda: object())

    def fail(*args: object, **kwargs: object) -> object:
        assert kwargs["data_root"] == tmp_path
        assert kwargs["token"] == secret
        raise RuntimeError(f"Hub rejected {secret}")

    monkeypatch.setattr(cli, "mirror_source", fail)
    assert main([
        "mirror-source", "--source-id", "wikipedia-multistream-index"
    ]) == 1
    error = capsys.readouterr().err
    assert secret not in error
    assert "[REDACTED]" in error


def test_mirror_cli_writes_non_secret_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    receipt = SourceMirrorReceiptV1(
        source_id="wikipedia-multistream-index",
        authoritative_url="https://example.test/index.bz2",
        expected_sha256="1" * 64,
        bytes=123,
        repository=DEFAULT_REPO_ID,
        path_in_repo="sources/wikipedia-multistream-index/index.bz2",
        remote_commit_sha=COMMIT,
        remote_sha256="1" * 64,
    )
    monkeypatch.setenv("HF_TOKEN", "secret")
    monkeypatch.setattr(cli, "_api", lambda: object())
    monkeypatch.setattr(cli, "mirror_source", lambda *args, **kwargs: receipt)

    assert main([
        "mirror-source", "--source-id", "wikipedia-multistream-index",
        "--data-root", str(tmp_path),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    persisted = Path(result["receipt"])
    assert persisted.read_bytes() == receipt.to_json_bytes()
    assert "secret" not in persisted.read_text()
    assert result["revision"] == COMMIT
    assert result["state"] == "remote_verified"
