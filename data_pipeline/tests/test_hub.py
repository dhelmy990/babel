from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.hub import (  # noqa: E402
    RemoteVerificationError,
    publish_verified_shards,
    verify_remote,
    write_revision_file,
)
from babel_data.cli import main  # noqa: E402
from babel_data.reconcile import split_for  # noqa: E402
from babel_data.shard import write_shards  # noqa: E402


COMMIT = "a" * 40


def row(article_key: str, page_id: int) -> dict[str, object]:
    return {
        "article_key": article_key,
        "page_id": page_id,
        "canonical_title": f"Article {page_id}",
        "wikidata_id": None,
        "lead_text": f"Lead {page_id}",
        "article_text": f"Text {page_id}",
        "teacher_vector": [1.0] + [0.0] * 99,
        "teacher_norm": 1.0,
        "source_revision_id": page_id + 10,
        "snapshot_date": "2016-10-01",
        "split": split_for(article_key),
        "reconciliation_status": "matched",
    }


def rows_for_all_splits() -> list[dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    number = 1
    while len(found) < 3:
        value = row(f"enwiki:2016-10-01:{number}", number)
        found.setdefault(str(value["split"]), value)
        number += 1
    return [found[name] for name in ("train", "validation", "test")]


class FakeApi:
    def __init__(self, *, returned_sha: str = COMMIT, info_sha: str = COMMIT) -> None:
        self.returned_sha = returned_sha
        self.info_sha = info_sha
        self.remote: dict[str, bytes] = {}
        self.operations: list[str] = []
        self.private_calls: list[dict[str, object]] = []

    def create_repo(self, **kwargs: object) -> None:
        self.private_calls.append(kwargs)

    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        if path_in_repo not in self.remote:
            raise FileNotFoundError(path_in_repo)
        return self.remote[path_in_repo]

    def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
        for operation in operations:
            path = str(operation.path_in_repo)
            source = operation.path_or_fileobj
            self.remote[path] = Path(source).read_bytes()
            self.operations.append(path)
        return SimpleNamespace(oid=self.returned_sha)

    def dataset_info(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(sha=self.info_sha)


class EntryNotFoundError(Exception):
    pass


class HubMissingApi(FakeApi):
    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        if path_in_repo not in self.remote:
            raise EntryNotFoundError(path_in_repo)
        return self.remote[path_in_repo]


def prepare(tmp_path: Path):
    values = rows_for_all_splits()
    result = write_shards(values, tmp_path / "prepared", pilot_size=3)
    files = [result.output_root / shard.path for shard in result.shards]
    files.append(result.manifest_path)
    datasets = {split: [next(value for value in values if value["split"] == split)] for split in ("train", "validation", "test")}
    return result, files, datasets


def test_publish_is_private_batched_ordered_and_verified_at_returned_sha(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    load_calls: list[dict[str, object]] = []

    def load_dataset(repo_id: str, **kwargs: object) -> object:
        load_calls.append({"repo_id": repo_id, **kwargs})
        return datasets

    revision = publish_verified_shards(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        files,
        "super-secret",
        root=result.output_root,
        load_dataset_fn=load_dataset,
        sleep=lambda _: None,
    )

    assert revision == COMMIT
    assert api.private_calls == [{
        "repo_id": "dhelmy990/babel-wikipedia-experiment",
        "repo_type": "dataset",
        "private": True,
        "exist_ok": True,
        "token": "super-secret",
    }]
    assert api.operations[-1] == "distillation_2016/manifest.json"
    assert api.operations[:-1] == sorted(api.operations[:-1])
    assert load_calls == [{
        "repo_id": "dhelmy990/babel-wikipedia-experiment",
        "name": "distillation_2016",
        "revision": COMMIT,
        "streaming": True,
        "token": "super-secret",
    }]


def test_publish_treats_hub_entry_not_found_as_an_append(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = HubMissingApi()

    assert publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
        sleep=lambda _: None,
    ) == COMMIT
    assert api.operations


def test_publish_is_idempotent_but_rejects_checksum_conflicts(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()

    assert publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
        sleep=lambda _: None,
    ) == COMMIT
    assert api.operations == []

    api.remote[result.shards[0].path] = b"different"
    with pytest.raises(ValueError, match="refusing to overwrite"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
            sleep=lambda _: None,
        )


def test_publish_rejects_local_shard_changed_after_manifest(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    shard_path = result.output_root / result.shards[0].path
    contents = shard_path.read_bytes()
    shard_path.write_bytes(bytes([contents[0] ^ 1]) + contents[1:])
    api = FakeApi()

    with pytest.raises(ValueError, match="local shard checksum"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
            sleep=lambda _: None,
        )
    assert api.private_calls == []


@pytest.mark.parametrize(
    ("returned", "info", "message"),
    [("bad", "bad", "40-character"), ("a" * 40, "b" * 40, "does not match")],
)
def test_publish_rejects_malformed_or_mismatched_commit_identity(
    tmp_path: Path, returned: str, info: str, message: str
) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(returned_sha=returned, info_sha=info)
    with pytest.raises(RemoteVerificationError, match=message):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
            sleep=lambda _: None,
        )


def test_verify_remote_retries_eventual_consistency_and_rejects_bad_rows(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    attempts = 0

    def eventually(repo_id: str, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FileNotFoundError("not replicated")
        return datasets

    verified = verify_remote(
        api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
        result.manifest_path, "token", load_dataset_fn=eventually,
        retries=3, sleep=lambda _: None,
    )
    assert verified.commit_sha == COMMIT
    assert attempts == 3

    bad = dict(datasets)
    bad.pop("test")
    with pytest.raises(RemoteVerificationError, match="test"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token", load_dataset_fn=lambda *args, **kwargs: bad,
            retries=1, sleep=lambda _: None,
        )

    invalid = {name: list(values) for name, values in datasets.items()}
    invalid["train"] = [dict(invalid["train"][0], teacher_norm=0.0)]
    with pytest.raises(RemoteVerificationError, match="schema-invalid row"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token", load_dataset_fn=lambda *args, **kwargs: invalid,
            retries=1, sleep=lambda _: None,
        )


def test_remote_manifest_mismatch_and_errors_never_leak_token(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    api.remote["distillation_2016/manifest.json"] += b" "

    with pytest.raises(RemoteVerificationError, match="manifest") as error:
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "secret-value", load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )
    assert "secret-value" not in str(error.value)


def test_revision_file_is_atomic_secret_safe_and_no_clobber(tmp_path: Path) -> None:
    target = tmp_path / "revision.txt"
    write_revision_file(target, COMMIT)
    assert target.read_bytes() == (COMMIT + "\n").encode()
    assert target.stat().st_mode & 0o777 == 0o600

    with pytest.raises(FileExistsError):
        write_revision_file(target, "b" * 40)
    assert target.read_bytes() == (COMMIT + "\n").encode()
    assert not list(tmp_path.glob(".revision.txt.*"))


def test_prepare_cli_accepts_jsonl_and_emits_structured_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "accepted.jsonl"
    source.write_text("".join(json.dumps(value) + "\n" for value in rows_for_all_splits()))
    output = tmp_path / "external" / "pilot"

    exit_code = main([
        "prepare-2016",
        "--input", str(source),
        "--output-root", str(output),
        "--pilot-size", "3",
        "--target-shard-bytes", "10000",
    ])

    assert exit_code == 0
    message = json.loads(capsys.readouterr().out)
    assert message == {
        "command": "prepare-2016",
        "manifest": str(output / "distillation_2016" / "manifest.json"),
        "pilot_examples": 3,
        "status": "ok",
    }
    assert (output / "readiness.json").is_file()


def test_cli_failure_is_structured_and_does_not_echo_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "never-print-this-token"
    exit_code = main([
        "verify-remote",
        "--manifest", str(tmp_path / "missing.json"),
        "--revision", COMMIT,
        "--token", token,
    ])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert json.loads(error)["status"] == "error"
    assert token not in error


def test_cli_argument_errors_are_structured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["prepare-2016"]) == 2
    message = json.loads(capsys.readouterr().err)
    assert message["status"] == "error"
    assert message["command"] == "prepare-2016"
    assert message["error"] == "usage"


def test_verify_cli_defaults_manifest_from_external_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    expected = (
        tmp_path
        / "prepared"
        / "2016-pilot"
        / "distillation_2016"
        / "manifest.json"
    )
    calls: list[object] = []

    def verified(api: object, repo: str, revision: str, manifest: str, token: str):
        calls.append(Path(manifest))
        return SimpleNamespace(commit_sha=revision, split_examples={"train": 1})

    monkeypatch.setattr(cli, "_api", lambda: object())
    monkeypatch.setattr(cli, "verify_remote", verified)
    assert main([
        "verify-remote", "--revision", COMMIT, "--token", "token",
        "--data-root", str(tmp_path),
    ]) == 0
    assert calls == [expected]
    assert json.loads(capsys.readouterr().out)["revision"] == COMMIT
