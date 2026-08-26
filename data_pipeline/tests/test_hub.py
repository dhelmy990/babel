from __future__ import annotations

import json
import copy
import hashlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from jsonschema import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.hub import (  # noqa: E402
    RemoteVerificationError,
    publish_interview_configuration,
    publish_verified_shards,
    stage_versioned_release_shards,
    verify_remote,
    write_revision_file,
)
from babel_data.cli import main  # noqa: E402
from babel_data.reconcile import split_for  # noqa: E402
from babel_data.release import (  # noqa: E402
    EMPTY_TEST_PATH,
    canonical_json,
    identity_rows_sha256,
    render_dataset_card,
    validate_manifest_extension,
)
from babel_data.shard import (  # noqa: E402
    load_readiness,
    write_complete_shards,
    write_shards,
)
from data_pipeline.tests.test_shard import provenance_document  # noqa: E402
from data_pipeline.tests.test_interview_export import (  # noqa: E402
    SMALL_COUNTS,
    _create_database,
)
from babel_data.interview_export import (  # noqa: E402
    INTERVIEW_CONFIG,
    freeze_frontier,
    select_interview_ids,
    write_interview_release,
)


COMMIT = "a" * 40
PARENT = "c" * 40


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
    def __init__(
        self, *, returned_sha: str = COMMIT, info_sha: str | None = None,
        current_sha: str = PARENT, private: bool | None = True,
    ) -> None:
        self.returned_sha = returned_sha
        self.info_sha = info_sha
        self.current_sha = current_sha
        self.private = private
        self.remote: dict[str, bytes] = {}
        self.operations: list[str] = []
        self.private_calls: list[dict[str, object]] = []
        self.commit_calls: list[dict[str, object]] = []
        self.get_calls: list[tuple[str, object]] = []

    def create_repo(self, **kwargs: object) -> None:
        self.private_calls.append(kwargs)

    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        self.get_calls.append((path_in_repo, kwargs.get("revision")))
        if path_in_repo not in self.remote:
            raise EntryNotFoundError(path_in_repo)
        return self.remote[path_in_repo]

    def iter_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> object:
        if path_in_repo not in self.remote:
            raise EntryNotFoundError(path_in_repo)
        value = self.remote[path_in_repo]
        return (
            value[offset : offset + 1024 * 1024]
            for offset in range(0, len(value), 1024 * 1024)
        )

    def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
        self.commit_calls.append(kwargs)
        for operation in operations:
            path = str(operation.path_in_repo)
            source = operation.path_or_fileobj
            self.remote[path] = Path(source).read_bytes()
            self.operations.append(path)
        self.current_sha = self.returned_sha
        return SimpleNamespace(oid=self.returned_sha)

    def dataset_info(self, *args: object, **kwargs: object) -> object:
        revision = kwargs.get("revision")
        sha = self.current_sha if revision in (None, "main") else self.info_sha or revision
        return SimpleNamespace(sha=sha, private=self.private)


class EntryNotFoundError(Exception):
    pass


class LocalEntryNotFoundError(FileNotFoundError):
    pass


class ParentCommitConflictError(Exception):
    def __init__(self) -> None:
        super().__init__("parent commit conflict")
        self.response = SimpleNamespace(status_code=409)


class HubMissingApi(FakeApi):
    def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
        if path_in_repo not in self.remote:
            raise EntryNotFoundError(path_in_repo)
        return self.remote[path_in_repo]


def prepare(tmp_path: Path):
    values = rows_for_all_splits()
    result = write_shards(
        values, tmp_path / "prepared", pilot_size=3,
        provenance=provenance_document(),
    )
    files = [result.output_root / shard.path for shard in result.shards]
    files.extend([
        result.readiness_path,
        result.readme_path,
        result.output_root / EMPTY_TEST_PATH,
        result.manifest_path,
    ])
    datasets = {split: [next(value for value in values if value["split"] == split)] for split in ("train", "validation", "test")}
    return result, files, datasets


def prepare_interview(tmp_path: Path):
    database = tmp_path / "reconcile.sqlite3"
    _create_database(database)
    frontier = freeze_frontier(database)
    selection = select_interview_ids(
        database,
        frontier,
        required_counts=SMALL_COUNTS,
        smoke_size=3,
    )
    result = write_interview_release(
        database,
        frontier,
        selection,
        tmp_path / "interview",
        source_sha256={"teacher": "a" * 64, "wikipedia": "b" * 64},
        code_commit="c" * 40,
    )
    datasets = {
        shard.split: pq.read_table(result.output_root / shard.path).to_pylist()
        for shard in result.shards
    }
    return result, datasets


def test_publish_interview_is_atomic_append_and_exact_revision_streamed(
    tmp_path: Path,
) -> None:
    result, datasets = prepare_interview(tmp_path)
    api = FakeApi(current_sha=PARENT)
    complete_manifest = b'{"active_release_root":null,"existing":"unchanged"}\n'
    api.remote["distillation_2016/manifest.json"] = complete_manifest
    api.remote["README.md"] = b"""---
configs:
- config_name: distillation_2016
  data_files:
  - split: train
    path: distillation_2016/train/*.parquet
  - split: validation
    path: distillation_2016/validation/*.parquet
  - split: test
    path: distillation_2016/test/*.parquet
- config_name: demo_catalog_2026_06
  data_files:
  - split: train
    path: demo_catalog_2026_06/train/*.parquet
---
# Babel 2016 distillation dataset
"""
    load_calls: list[dict[str, object]] = []

    def load(repo_id: str, **kwargs: object) -> object:
        load_calls.append({"repo_id": repo_id, **kwargs})
        return datasets[str(kwargs["split"])]

    revision = publish_interview_configuration(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        result.output_root,
        "token",
        load_dataset_fn=load,
        expected_counts=SMALL_COUNTS,
        retries=1,
        sleep=lambda _: None,
    )

    assert revision == COMMIT
    expected_config_paths = {
        *(shard.path for shard in result.shards),
        f"{INTERVIEW_CONFIG}/manifest.json",
        f"{INTERVIEW_CONFIG}/readiness.json",
    }
    assert set(api.operations) == expected_config_paths | {"README.md"}
    assert "distillation_2016/manifest.json" not in api.operations
    assert api.remote["distillation_2016/manifest.json"] == complete_manifest
    assert b"config_name: demo_catalog_2026_06" in api.remote["README.md"]
    assert api.commit_calls == [
        {
            "repo_id": "dhelmy990/babel-wikipedia-experiment",
            "repo_type": "dataset",
            "revision": "main",
            "parent_commit": PARENT,
            "commit_message": "Publish frozen 2016 interview configuration",
            "token": "token",
        }
    ]
    assert {call["split"] for call in load_calls} == {"train", "validation", "test"}
    assert all(call["name"] == INTERVIEW_CONFIG for call in load_calls)
    assert all(call["revision"] == COMMIT for call in load_calls)
    assert all(call["streaming"] is True and call["token"] == "token" for call in load_calls)


def test_publish_interview_rejects_nonidentical_existing_config_path(
    tmp_path: Path,
) -> None:
    result, datasets = prepare_interview(tmp_path)
    api = FakeApi(current_sha=PARENT)
    api.remote["distillation_2016/manifest.json"] = b'{"active_release_root":null}\n'
    api.remote[result.shards[0].path] = b"different"

    with pytest.raises(ValueError, match="refusing to overwrite"):
        publish_interview_configuration(
            api,
            "dhelmy990/babel-wikipedia-experiment",
            result.output_root,
            "token",
            load_dataset_fn=lambda *args, **kwargs: datasets[str(kwargs["split"])],
            expected_counts=SMALL_COUNTS,
            retries=1,
            sleep=lambda _: None,
        )
    assert api.operations == []


def test_interview_card_survives_complete_release_activation() -> None:
    active_root = "distillation_2016/releases/" + "d" * 64
    card = render_dataset_card(active_root)

    assert b"config_name: distillation_2016\n" in card
    assert b"config_name: distillation_2016_interview\n" in card
    assert f"path: {active_root}/train/*.parquet".encode() in card
    assert b"path: distillation_2016_interview/train/*.parquet" in card


def test_complete_publication_upgrades_legacy_local_card_without_dropping_interview(
    tmp_path: Path,
) -> None:
    values = rows_for_all_splits()
    pilot = write_shards(
        values,
        tmp_path / "pilot",
        pilot_size=len(values),
        provenance=provenance_document(),
    )
    api = FakeApi(current_sha=PARENT)
    api.remote["distillation_2016/manifest.json"] = pilot.manifest_path.read_bytes()
    api.remote["README.md"] = render_dataset_card().replace(
        b"---\n# Babel 2016 distillation dataset",
        b"- config_name: demo_catalog_2026_06\n"
        b"  data_files:\n"
        b"  - split: train\n"
        b"    path: demo_catalog_2026_06/train/*.parquet\n"
        b"---\n# Babel 2016 distillation dataset",
    )
    complete = write_complete_shards(
        values,
        tmp_path / "complete",
        spool_database=tmp_path / "complete.sqlite3",
        provenance=provenance_document(),
        release_id="f" * 64,
        supersedes_commit_sha=PARENT,
    )
    complete.readme_path.write_bytes(
        render_dataset_card(
            "distillation_2016/releases/" + "f" * 64,
            include_interview=False,
        )
    )
    readiness = json.loads(complete.readiness_path.read_text())
    readiness["state"] = "complete"
    complete.readiness_path.write_bytes(canonical_json(readiness))
    manifest = json.loads(complete.manifest_path.read_text())
    files = [complete.output_root / item["path"] for item in manifest["shards"]] + [
        complete.readiness_path,
        complete.readme_path,
        complete.output_root / EMPTY_TEST_PATH,
        complete.manifest_path,
    ]
    datasets = {
        split: [next(row for row in values if row["split"] == split)]
        for split in ("train", "validation", "test")
    }

    assert publish_verified_shards(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        files,
        "token",
        root=complete.output_root,
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1,
        sleep=lambda _: None,
    ) == COMMIT
    assert b"config_name: distillation_2016_interview" in api.remote["README.md"]
    assert b"config_name: demo_catalog_2026_06" in api.remote["README.md"]


def rolling_extension(result: object, destination: Path) -> tuple[Path, list[Path]]:
    shutil.copytree(result.output_root, destination)
    manifest_path = destination / "distillation_2016" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    original = manifest["shards"][0]
    added = copy.deepcopy(original)
    added["path"] = f"distillation_2016/{added['split']}/part-99999.parquet"
    added_path = destination / added["path"]
    original_table = pq.read_table(destination / original["path"])
    added_row = original_table.to_pylist()[0]
    minimum_rank = max(item["max_rank"] for item in manifest["shards"])
    page_id = 999999
    while True:
        article_key = f"enwiki:2016-10-01:{page_id}"
        rank = hashlib.sha256(article_key.encode()).hexdigest()
        if rank > minimum_rank and split_for(article_key) == added["split"]:
            break
        page_id += 1
    added_row["article_key"] = article_key
    added_row["page_id"] = page_id
    pq.write_table(pa.Table.from_pylist([added_row], schema=original_table.schema), added_path)
    added["bytes"] = added_path.stat().st_size
    added["sha256"] = hashlib.sha256(added_path.read_bytes()).hexdigest()
    added["rows_sha256"] = identity_rows_sha256([added_row])
    added["min_rank"] = added["max_rank"] = rank
    added["min_article_key"] = added["max_article_key"] = article_key
    manifest["shards"].append(added)
    manifest["counts"]["total"] += added["rows"]
    manifest["counts"][added["split"]] += added["rows"]
    manifest["provenance"]["document"]["reports"]["row_counts"]["matched"] += added["rows"]
    manifest["pilot_article_keys"].append(article_key)
    manifest["aggregate_sha256"] = hashlib.sha256(
        (json.dumps(manifest["shards"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    manifest["rows_sha256"] = identity_rows_sha256(
        [
            {"article_key": key, "page_id": int(key.rsplit(":", 1)[1])}
            for key in manifest["pilot_article_keys"]
        ]
    )
    reports = manifest["provenance"]["document"]["reports"]
    reports["dataset_aggregate_sha256"] = manifest["aggregate_sha256"]
    reports["dataset_rows_sha256"] = manifest["rows_sha256"]
    reports["dataset_counts"] = manifest["counts"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    readiness_path = destination / "readiness.json"
    readiness = json.loads(readiness_path.read_text())
    readiness["available_examples"] += added["rows"]
    readiness["verified_shards"].append({
        "path": added["path"], "sha256": added["sha256"], "examples": added["rows"]
    })
    readiness_path.write_text(
        json.dumps(readiness, sort_keys=True, separators=(",", ":")) + "\n"
    )
    files = [destination / item["path"] for item in manifest["shards"]] + [
        readiness_path,
        destination / "README.md",
        destination / EMPTY_TEST_PATH,
        manifest_path,
    ]
    return manifest_path, files


def test_publish_requires_exact_readiness_and_dataset_card(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    for omitted in (result.readiness_path, result.readme_path):
        with pytest.raises(ValueError, match="readiness|README|exactly"):
            publish_verified_shards(
                FakeApi(), "dhelmy990/babel-wikipedia-experiment",
                [path for path in files if path != omitted], "token",
                root=result.output_root,
                load_dataset_fn=lambda *args, **kwargs: datasets,
                retries=1, sleep=lambda _: None,
            )


@pytest.mark.parametrize("metadata_path", ["readiness.json", "README.md"])
def test_verify_remote_rejects_changed_release_metadata(
    tmp_path: Path, metadata_path: str
) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    api.remote[metadata_path] += b"changed"

    with pytest.raises(RemoteVerificationError, match="readiness|README"):
        verify_remote(
            api,
            "dhelmy990/babel-wikipedia-experiment",
            COMMIT,
            result.manifest_path,
            "token",
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1,
            sleep=lambda _: None,
        )


def test_manifest_extension_rejects_copied_or_identity_aliased_shard(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    extension_manifest, extension_files = rolling_extension(result, tmp_path / "rolling")
    extension = json.loads(extension_manifest.read_text())
    added = extension["shards"][-1]
    original = extension["shards"][0]

    for field in ("sha256", "rows_sha256"):
        candidate = copy.deepcopy(extension)
        candidate["shards"][-1][field] = original[field]
        candidate["aggregate_sha256"] = hashlib.sha256(
            (json.dumps(candidate["shards"], sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        extension_manifest.write_text(
            json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n"
        )
        with pytest.raises(ValueError, match="duplicate|overlap"):
            publish_verified_shards(
                api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
                root=extension_manifest.parent.parent,
                load_dataset_fn=lambda *args, **kwargs: datasets,
                retries=1, sleep=lambda _: None,
            )
    assert added["path"] != original["path"]


def test_manifest_extension_rejects_reencoded_copy_with_forged_identity_digest(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    extension_manifest, extension_files = rolling_extension(result, tmp_path / "rolling")
    manifest = json.loads(extension_manifest.read_text())
    original = manifest["shards"][0]
    added = manifest["shards"][-1]
    added_path = extension_manifest.parent.parent / added["path"]
    pq.write_table(
        pq.read_table(extension_manifest.parent.parent / original["path"]),
        added_path,
        compression="gzip",
    )
    added["bytes"] = added_path.stat().st_size
    added["sha256"] = hashlib.sha256(added_path.read_bytes()).hexdigest()
    added["rows_sha256"] = "9" * 64
    manifest["aggregate_sha256"] = hashlib.sha256(
        (json.dumps(manifest["shards"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    manifest["provenance"]["document"]["reports"][
        "dataset_aggregate_sha256"
    ] = manifest["aggregate_sha256"]
    extension_manifest.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    readiness_path = extension_manifest.parent.parent / "readiness.json"
    readiness = json.loads(readiness_path.read_text())
    readiness["verified_shards"][-1]["sha256"] = added["sha256"]
    readiness_path.write_text(
        json.dumps(readiness, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(ValueError, match="row identity digest|overlapping row identity"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
            root=extension_manifest.parent.parent,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


@pytest.mark.parametrize("forgery", ["bounds", "root_digest", "pilot_keys"])
def test_publish_recomputes_manifest_identity_evidence_from_parquet(
    tmp_path: Path, forgery: str
) -> None:
    result, files, datasets = prepare(tmp_path)
    manifest = json.loads(result.manifest_path.read_text())
    if forgery == "bounds":
        manifest["shards"][0]["min_rank"] = "0" * 64
        manifest["shards"][0]["max_rank"] = "0" * 64
        manifest["aggregate_sha256"] = hashlib.sha256(
            canonical_json(manifest["shards"])
        ).hexdigest()
        manifest["provenance"]["document"]["reports"][
            "dataset_aggregate_sha256"
        ] = manifest["aggregate_sha256"]
    elif forgery == "root_digest":
        manifest["rows_sha256"] = "0" * 64
        manifest["provenance"]["document"]["reports"][
            "dataset_rows_sha256"
        ] = manifest["rows_sha256"]
    else:
        manifest["pilot_article_keys"] = list(reversed(manifest["pilot_article_keys"]))
    result.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(ValueError, match="bounds|aggregate row|pilot article"):
        publish_verified_shards(
            FakeApi(), "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


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
    assert api.commit_calls == [{"repo_id": "dhelmy990/babel-wikipedia-experiment",
                                 "repo_type": "dataset", "revision": "main",
                                 "parent_commit": PARENT,
                                 "commit_message": "Publish verified distillation_2016 shards",
                                 "token": "super-secret"}]
    assert load_calls == [{
        "repo_id": "dhelmy990/babel-wikipedia-experiment",
        "name": "distillation_2016",
        "revision": COMMIT,
        "streaming": True,
        "token": "super-secret",
    }]


def test_publish_one_time_supersession_excludes_historical_pilot_paths(
    tmp_path: Path,
) -> None:
    old, old_files, datasets = prepare(tmp_path / "old")
    old_readiness = json.loads(old.readiness_path.read_text())
    old_readiness["state"] = "pilot_ready"
    old.readiness_path.write_text(
        json.dumps(old_readiness, sort_keys=True, separators=(",", ":")) + "\n"
    )
    api = FakeApi(current_sha=PARENT)
    for path in old_files:
        api.remote[path.relative_to(old.output_root).as_posix()] = path.read_bytes()

    values = rows_for_all_splits()
    complete = write_complete_shards(
        values,
        tmp_path / "complete",
        spool_database=tmp_path / "complete-spool.sqlite3",
        provenance=provenance_document(),
        release_id="b" * 64,
        supersedes_commit_sha=PARENT,
    )
    readiness = json.loads(complete.readiness_path.read_text())
    readiness["state"] = "complete"
    complete.readiness_path.write_text(
        json.dumps(readiness, sort_keys=True, separators=(",", ":")) + "\n"
    )
    manifest = json.loads(complete.manifest_path.read_text())
    files = [complete.output_root / item["path"] for item in manifest["shards"]]
    files += [
        complete.readiness_path,
        complete.readme_path,
        complete.output_root / EMPTY_TEST_PATH,
        complete.manifest_path,
    ]

    revision = publish_verified_shards(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        files,
        "token",
        root=complete.output_root,
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1,
        sleep=lambda _: None,
    )

    assert revision == COMMIT
    assert all("/releases/" in item["path"] for item in manifest["shards"])
    assert b"distillation_2016/train/*.parquet" not in api.remote["README.md"]
    assert old.shards[0].path in api.remote

    second = copy.deepcopy(manifest)
    second["supersedes_commit_sha"] = COMMIT
    with pytest.raises(ValueError, match="one-time|already active"):
        validate_manifest_extension(
            canonical_json(manifest),
            canonical_json(second),
            expected_predecessor_sha=COMMIT,
        )


def test_stage_versioned_release_uploads_and_streams_each_shard(
    tmp_path: Path,
) -> None:
    values = rows_for_all_splits()
    result = write_complete_shards(
        values,
        tmp_path / "complete",
        spool_database=tmp_path / "complete-spool.sqlite3",
        provenance=provenance_document(),
        release_id="b" * 64,
        supersedes_commit_sha=PARENT,
    )
    api = FakeApi(current_sha=PARENT)
    pilot = write_shards(
        values,
        tmp_path / "pilot",
        pilot_size=len(values),
        provenance=provenance_document(),
    )
    api.remote["distillation_2016/manifest.json"] = pilot.manifest_path.read_bytes()
    load_calls: list[dict[str, object]] = []

    def load(*args: object, **kwargs: object) -> object:
        load_calls.append(kwargs)
        split = str(kwargs["split"])
        return [next(value for value in values if value["split"] == split)]

    commits = stage_versioned_release_shards(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        result.manifest_path,
        "token",
        load_dataset_fn=load,
    )

    manifest = json.loads(result.manifest_path.read_text())
    paths = [item["path"] for item in manifest["shards"]]
    assert commits == (COMMIT,) * len(paths)
    assert api.operations == paths
    assert [call["data_files"] for call in load_calls] == [
        {item["split"]: item["path"]} for item in manifest["shards"]
    ]
    assert all(call["revision"] == COMMIT for call in load_calls)
    journal = [
        json.loads(line)
        for line in (result.output_root / "publication-commits.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [item["path"] for item in journal] == paths
    assert [item["commit_sha"] for item in journal] == list(commits)
    assert all(item["remote_stream_verified"] is True for item in journal)
    operations = list(api.operations)
    journal_bytes = (result.output_root / "publication-commits.jsonl").read_bytes()

    resumed = stage_versioned_release_shards(
        api,
        "dhelmy990/babel-wikipedia-experiment",
        result.manifest_path,
        "token",
        load_dataset_fn=load,
    )

    assert resumed == commits
    assert api.operations == operations
    assert (result.output_root / "publication-commits.jsonl").read_bytes() == journal_bytes


def test_publish_semantic_preflight_streams_parquet_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.hub as hub

    result, files, datasets = prepare(tmp_path)
    calls = 0
    real_parquet_file = hub.pq.ParquetFile

    class StreamingParquetFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.delegate = real_parquet_file(*args, **kwargs)

        @property
        def schema_arrow(self) -> pa.Schema:
            return self.delegate.schema_arrow

        @property
        def metadata(self) -> object:
            return self.delegate.metadata

        def iter_batches(self, *args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return self.delegate.iter_batches(*args, **kwargs)

    monkeypatch.setattr(
        hub.pq,
        "read_table",
        lambda *args, **kwargs: pytest.fail("publication must not materialize a shard"),
    )
    monkeypatch.setattr(hub.pq, "ParquetFile", StreamingParquetFile)
    assert publish_verified_shards(
        FakeApi(), "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root,
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1, sleep=lambda _: None,
    ) == COMMIT
    assert calls == len(result.shards) * 4


def test_publish_treats_hub_entry_not_found_as_an_append(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = HubMissingApi()

    assert publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
        sleep=lambda _: None,
    ) == COMMIT
    assert api.operations


def test_publish_never_treats_a_local_cache_miss_as_remote_absence(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)

    class LocalCacheMissApi(FakeApi):
        def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
            self.get_calls.append((path_in_repo, kwargs.get("revision")))
            raise LocalEntryNotFoundError("remote state could not be checked")

    api = LocalCacheMissApi()
    with pytest.raises(RemoteVerificationError, match="unable to inspect"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=2, sleep=lambda _: None,
        )
    assert len(api.get_calls) == 2
    assert api.operations == []


def test_hub_configuration_is_fixed(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    with pytest.raises(ValueError, match="fixed"):
        publish_verified_shards(
            FakeApi(), "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root, config_name="other",
            load_dataset_fn=lambda *args, **kwargs: datasets,
        )


def test_publish_is_idempotent_but_rejects_checksum_conflicts(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    api.current_sha = COMMIT
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


def test_publish_rejects_parquet_with_wrong_physical_schema(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    shard_path = result.output_root / result.shards[0].path
    table = pq.read_table(shard_path).replace_schema_metadata(None)
    pq.write_table(table, shard_path)
    manifest = json.loads(result.manifest_path.read_text())
    item = next(entry for entry in manifest["shards"] if entry["path"] == result.shards[0].path)
    item["bytes"] = shard_path.stat().st_size
    item["sha256"] = hashlib.sha256(shard_path.read_bytes()).hexdigest()
    manifest["aggregate_sha256"] = hashlib.sha256(canonical_json(manifest["shards"])).hexdigest()
    reports = manifest["provenance"]["document"]["reports"]
    reports["dataset_aggregate_sha256"] = manifest["aggregate_sha256"]
    result.manifest_path.write_bytes(canonical_json(manifest))
    readiness = json.loads(result.readiness_path.read_text())
    next(entry for entry in readiness["verified_shards"] if entry["path"] == item["path"])[
        "sha256"
    ] = item["sha256"]
    result.readiness_path.write_bytes(canonical_json(readiness))

    with pytest.raises(ValueError, match="physical Parquet schema"):
        publish_verified_shards(
            FakeApi(), "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_publish_rejects_float64_teacher_vector_physical_type(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    shard_path = result.output_root / result.shards[0].path
    table = pq.read_table(shard_path)
    vector_index = table.schema.get_field_index("teacher_vector")
    vector_field = pa.field(
        "teacher_vector",
        pa.list_(pa.field("element", pa.float64()), 100),
        nullable=False,
    )
    table = table.set_column(
        vector_index,
        vector_field,
        pa.array(table.column(vector_index).to_pylist(), type=vector_field.type),
    )
    pq.write_table(table, shard_path)
    manifest = json.loads(result.manifest_path.read_text())
    item = next(entry for entry in manifest["shards"] if entry["path"] == result.shards[0].path)
    item["bytes"] = shard_path.stat().st_size
    item["sha256"] = hashlib.sha256(shard_path.read_bytes()).hexdigest()
    manifest["aggregate_sha256"] = hashlib.sha256(canonical_json(manifest["shards"])).hexdigest()
    manifest["provenance"]["document"]["reports"][
        "dataset_aggregate_sha256"
    ] = manifest["aggregate_sha256"]
    result.manifest_path.write_bytes(canonical_json(manifest))
    readiness = json.loads(result.readiness_path.read_text())
    next(entry for entry in readiness["verified_shards"] if entry["path"] == item["path"])[
        "sha256"
    ] = item["sha256"]
    result.readiness_path.write_bytes(canonical_json(readiness))

    with pytest.raises(ValueError, match="physical Parquet schema"):
        publish_verified_shards(
            FakeApi(), "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


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


@pytest.mark.parametrize("private", [False, None])
def test_publish_proves_existing_repository_is_private(
    tmp_path: Path, private: bool | None
) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(private=private)

    with pytest.raises(RemoteVerificationError, match="private"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )
    assert api.operations == []


def test_publish_allows_only_a_monotonic_manifest_extension(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi()
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    api.current_sha = COMMIT
    api.returned_sha = "d" * 40
    extension_manifest, extension_files = rolling_extension(
        result, tmp_path / "rolling"
    )

    revision = publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
        root=extension_manifest.parent.parent,
        load_dataset_fn=lambda *args, **kwargs: datasets,
        sleep=lambda _: None,
    )

    assert revision == "d" * 40
    assert api.commit_calls[-1]["parent_commit"] == COMMIT
    assert ("distillation_2016/manifest.json", COMMIT) in api.get_calls
    assert api.operations == [
        next(
            path.relative_to(extension_manifest.parent.parent).as_posix()
            for path in extension_files if "part-99999" in path.name
        ),
        "readiness.json",
        "distillation_2016/manifest.json",
    ]

    regressed = json.loads(extension_manifest.read_text())
    regressed["shards"][0]["rows_sha256"] = "e" * 64
    regressed["aggregate_sha256"] = hashlib.sha256(
        (json.dumps(regressed["shards"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    regressed["provenance"]["document"]["reports"][
        "dataset_aggregate_sha256"
    ] = regressed["aggregate_sha256"]
    extension_manifest.write_text(
        json.dumps(regressed, sort_keys=True, separators=(",", ":")) + "\n"
    )
    api.remote["distillation_2016/manifest.json"] = files[-1].read_bytes()
    api.remote.pop(api.operations[0])
    api.current_sha = COMMIT
    with pytest.raises(ValueError, match="monotonic|overlapping|row identity"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
            root=extension_manifest.parent.parent,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_manifest_extension_cannot_reorder_prior_shards(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    extension_manifest, extension_files = rolling_extension(result, tmp_path / "rolling")
    extension = json.loads(extension_manifest.read_text())
    extension["shards"] = extension["shards"][1:] + extension["shards"][:1]
    extension["aggregate_sha256"] = hashlib.sha256(
        (json.dumps(extension["shards"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    extension_manifest.write_text(
        json.dumps(extension, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(ValueError, match="monotonic|overlapping"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
            root=extension_manifest.parent.parent,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_parent_conflict_reresolves_and_revalidates_before_retry(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)

    class ConflictOnceApi(FakeApi):
        def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
            if not self.commit_calls:
                self.commit_calls.append(kwargs)
                self.current_sha = "e" * 40
                raise ParentCommitConflictError()
            return super().create_commit(operations=operations, **kwargs)

    api = ConflictOnceApi(returned_sha="d" * 40)
    sleeps: list[float] = []
    assert publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=2, backoff_seconds=0.25, sleep=sleeps.append,
    ) == "d" * 40
    assert [call["parent_commit"] for call in api.commit_calls] == [PARENT, "e" * 40]
    assert sleeps == [0.25]


def test_parent_conflict_revalidates_local_shards_before_retry(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    shard_path = result.output_root / result.shards[0].path

    class MutatingConflictApi(FakeApi):
        def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
            self.commit_calls.append(kwargs)
            contents = shard_path.read_bytes()
            shard_path.write_bytes(bytes([contents[0] ^ 1]) + contents[1:])
            self.current_sha = "e" * 40
            raise ParentCommitConflictError()

    api = MutatingConflictApi()
    with pytest.raises(ValueError, match="local shard checksum"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", files, "token",
            root=result.output_root,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=2, sleep=lambda _: None,
        )
    assert len(api.commit_calls) == 1


def test_manifest_extension_preserves_prior_provenance_evidence(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    extension_manifest, extension_files = rolling_extension(result, tmp_path / "rolling")
    extension = json.loads(extension_manifest.read_text())
    extension["provenance"]["document"]["sources"][0]["role"] = "wikipedia"
    extension_manifest.write_text(
        json.dumps(extension, sort_keys=True, separators=(",", ":")) + "\n"
    )

    with pytest.raises(ValueError, match="monotonic|provenance source"):
        publish_verified_shards(
            api, "dhelmy990/babel-wikipedia-experiment", extension_files, "token",
            root=extension_manifest.parent.parent,
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_manifest_extension_rejects_prior_artifact_mutation(tmp_path: Path) -> None:
    result, _, _ = prepare(tmp_path)
    extension_manifest, _ = rolling_extension(result, tmp_path / "rolling-artifact")
    extension = json.loads(extension_manifest.read_text())
    extension["provenance"]["document"]["artifacts"]["accepted_jsonl"][
        "sha256"
    ] = "e" * 64
    with pytest.raises(ValueError, match="prior provenance artifact"):
        validate_manifest_extension(
            result.manifest_path.read_bytes(), canonical_json(extension)
        )


def test_manifest_extension_allows_current_reports_to_decrease_but_rejects_stale(
    tmp_path: Path,
) -> None:
    result, _, _ = prepare(tmp_path)
    extension_manifest, _ = rolling_extension(result, tmp_path / "rolling-reports")
    old_bytes = result.manifest_path.read_bytes()
    extension = json.loads(extension_manifest.read_text())
    reports = extension["provenance"]["document"]["reports"]
    reports["text_statistics"]["min_length"] = 0
    reports["text_statistics"]["mean_length"] = 1.0
    reports["text_statistics"]["histogram"] = [1, 0, 3]
    reports["vector_statistics"]["min_norm"] = 0.5
    reports["vector_statistics"]["mean_norm"] = 0.75
    current_bytes = canonical_json(extension)
    validate_manifest_extension(old_bytes, current_bytes)

    reports["dataset_counts"] = json.loads(old_bytes)["counts"]
    with pytest.raises(ValueError, match="report|stale|counts"):
        validate_manifest_extension(old_bytes, canonical_json(extension))


def test_publish_retries_transient_repo_info_preflight_and_commit_failures(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)

    class TransientApi(FakeApi):
        def __init__(self) -> None:
            super().__init__()
            self.failures = {"repo": 1, "info": 1, "get": 1, "commit": 1}

        def create_repo(self, **kwargs: object) -> None:
            if self.failures["repo"]:
                self.failures["repo"] -= 1
                raise TimeoutError("temporary")
            super().create_repo(**kwargs)

        def dataset_info(self, *args: object, **kwargs: object) -> object:
            if kwargs.get("revision") == "main" and self.failures["info"]:
                self.failures["info"] -= 1
                raise TimeoutError("temporary")
            return super().dataset_info(*args, **kwargs)

        def get_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> bytes:
            if self.failures["get"]:
                self.failures["get"] -= 1
                raise TimeoutError("temporary")
            return super().get_file_bytes(path_in_repo=path_in_repo, **kwargs)

        def create_commit(self, *, operations: list[object], **kwargs: object) -> object:
            if self.failures["commit"]:
                self.failures["commit"] -= 1
                raise TimeoutError("temporary")
            return super().create_commit(operations=operations, **kwargs)

    api = TransientApi()
    sleeps: list[float] = []
    assert publish_verified_shards(
        api, "dhelmy990/babel-wikipedia-experiment", files, "token",
        root=result.output_root, load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=3, backoff_seconds=0.1, sleep=sleeps.append,
    ) == COMMIT
    assert all(value == 0 for value in api.failures.values())
    assert sleeps


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


def test_verify_remote_requires_every_manifest_shard_object(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    api = FakeApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    api.remote.pop(result.shards[0].path)
    with pytest.raises(RemoteVerificationError, match="shard|remote path"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token",
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


@pytest.mark.parametrize("metadata_case", ["missing", "wrong", "duplicate"])
def test_verify_remote_rejects_untrusted_shard_metadata(
    tmp_path: Path, metadata_case: str
) -> None:
    result, files, datasets = prepare(tmp_path)

    class MetadataApi(FakeApi):
        def get_paths_info(self, *, paths: list[str], **kwargs: object) -> list[object]:
            infos = [
                SimpleNamespace(
                    path=path,
                    size=(result.output_root / path).stat().st_size,
                    lfs={
                        "sha256": hashlib.sha256(
                            (result.output_root / path).read_bytes()
                        ).hexdigest()
                    },
                )
                for path in paths
            ]
            if metadata_case == "missing":
                return infos[1:]
            if metadata_case == "wrong":
                infos[0].lfs["sha256"] = "f" * 64
            if metadata_case == "duplicate":
                infos.append(copy.deepcopy(infos[0]))
            return infos

    api = MetadataApi(current_sha=COMMIT)
    for path in files:
        relative = path.relative_to(result.output_root).as_posix()
        if relative not in {item.path for item in result.shards}:
            api.remote[relative] = path.read_bytes()
    with pytest.raises(RemoteVerificationError, match="shard metadata"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token",
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_verify_remote_accepts_exact_shard_metadata_without_downloading_shards(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)

    class MetadataApi(FakeApi):
        def get_paths_info(self, *, paths: list[str], **kwargs: object) -> list[object]:
            return [
                SimpleNamespace(
                    path=path,
                    size=(result.output_root / path).stat().st_size,
                    lfs={
                        "sha256": hashlib.sha256(
                            (result.output_root / path).read_bytes()
                        ).hexdigest()
                    },
                )
                for path in paths
            ]

    api = MetadataApi(current_sha=COMMIT)
    for path in files:
        relative = path.relative_to(result.output_root).as_posix()
        if relative not in {item.path for item in result.shards}:
            api.remote[relative] = path.read_bytes()
    verified = verify_remote(
        api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
        result.manifest_path, "token",
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1, sleep=lambda _: None,
    )
    assert verified.verified_paths == {
        "README.md", "readiness.json", "distillation_2016/manifest.json",
        EMPTY_TEST_PATH,
        *(item.path for item in result.shards),
    }
    assert not ({item.path for item in result.shards} & {path for path, _ in api.get_calls})


def test_verify_remote_streams_downloaded_shards_when_metadata_lacks_checksums(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)

    class DownloadApi(FakeApi):
        iter_file_bytes = None

        def __init__(self) -> None:
            super().__init__(current_sha=COMMIT)
            self.downloaded: list[str] = []

        def get_paths_info(self, *, paths: list[str], **kwargs: object) -> list[object]:
            return [SimpleNamespace(path=path, size=None, lfs={}) for path in paths]

        def hf_hub_download(self, *, filename: str, **kwargs: object) -> str:
            self.downloaded.append(filename)
            return str(result.output_root / filename)

    api = DownloadApi()
    for path in files:
        relative = path.relative_to(result.output_root).as_posix()
        if relative not in {item.path for item in result.shards}:
            api.remote[relative] = path.read_bytes()
    verified = verify_remote(
        api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
        result.manifest_path, "token",
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1, sleep=lambda _: None,
    )
    assert api.downloaded == [item.path for item in result.shards]
    assert verified.verified_paths.issuperset(api.downloaded)


def test_verify_remote_rejects_custom_adapter_without_streaming_shard_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub

    result, files, datasets = prepare(tmp_path)

    class UnstreamableApi(FakeApi):
        iter_file_bytes = None

    api = UnstreamableApi(current_sha=COMMIT)
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda **kwargs: pytest.fail("custom byte adapters must not use global download"),
    )
    with pytest.raises(RemoteVerificationError, match="streaming adapter"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token",
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )


def test_verify_remote_streams_all_shard_bytes_when_metadata_is_unavailable(
    tmp_path: Path,
) -> None:
    result, files, datasets = prepare(tmp_path)

    class StreamingApi(FakeApi):
        def __init__(self) -> None:
            super().__init__(current_sha=COMMIT)
            self.streamed: list[str] = []

        def iter_file_bytes(self, *, path_in_repo: str, **kwargs: object) -> object:
            self.streamed.append(path_in_repo)
            value = self.remote[path_in_repo]
            return (value[index:index + 17] for index in range(0, len(value), 17))

    api = StreamingApi()
    for path in files:
        api.remote[path.relative_to(result.output_root).as_posix()] = path.read_bytes()
    verified = verify_remote(
        api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
        result.manifest_path, "token",
        load_dataset_fn=lambda *args, **kwargs: datasets,
        retries=1, sleep=lambda _: None,
    )
    assert api.streamed == [item.path for item in result.shards]
    assert verified.verified_paths.issuperset(api.streamed)

    invalid = {name: list(values) for name, values in datasets.items()}
    invalid["train"] = [dict(invalid["train"][0], teacher_norm=2.0)]
    with pytest.raises(RemoteVerificationError, match="semantic-invalid row"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token", load_dataset_fn=lambda *args, **kwargs: invalid,
            retries=1, sleep=lambda _: None,
        )

    wrong_split = {name: list(values) for name, values in datasets.items()}
    wrong_split["train"] = [dict(wrong_split["train"][0], split="test")]
    with pytest.raises(RemoteVerificationError, match="semantic-invalid row"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token",
            load_dataset_fn=lambda *args, **kwargs: wrong_split,
            retries=1, sleep=lambda _: None,
        )


def test_verify_remote_requires_schema_valid_provenance(tmp_path: Path) -> None:
    result, files, datasets = prepare(tmp_path)
    manifest = json.loads(result.manifest_path.read_text())
    manifest["provenance"]["document"] = {"arbitrary": True}
    result.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    api = FakeApi(current_sha=COMMIT)
    api.remote["distillation_2016/manifest.json"] = result.manifest_path.read_bytes()

    with pytest.raises(ValidationError, match="Additional properties"):
        verify_remote(
            api, "dhelmy990/babel-wikipedia-experiment", COMMIT,
            result.manifest_path, "token",
            load_dataset_fn=lambda *args, **kwargs: datasets,
            retries=1, sleep=lambda _: None,
        )
    assert api.get_calls == []


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


def test_revision_file_publication_failure_leaves_destination_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.hub as hub

    target = tmp_path / "revision.txt"
    real_fsync = hub.os.fsync
    calls = 0

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(hub.os, "fsync", fail_parent_fsync)
    with pytest.raises(OSError, match="directory fsync"):
        write_revision_file(target, COMMIT)
    assert not target.exists()
    assert not list(tmp_path.glob(".revision.txt.*"))
    assert calls == 4


def test_revision_file_fsyncs_parent_after_removing_temporary_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.hub as hub

    real_fsync = hub.os.fsync
    synced: list[Path] = []

    def record_fsync(descriptor: int) -> None:
        synced.append(Path(hub.os.readlink(f"/proc/self/fd/{descriptor}")))
        real_fsync(descriptor)

    monkeypatch.setattr(hub.os, "fsync", record_fsync)
    target = tmp_path / "revision.txt"
    write_revision_file(target, COMMIT)
    assert synced.count(tmp_path) == 2
    assert not list(tmp_path.glob(".revision.txt.*"))


def test_prepare_cli_accepts_jsonl_and_emits_structured_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "accepted.jsonl"
    source.write_text("".join(json.dumps(value) + "\n" for value in rows_for_all_splits()))
    provenance = provenance_document()
    provenance["artifacts"]["accepted_jsonl"] = {  # type: ignore[index]
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "size": source.stat().st_size,
    }
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(json.dumps(provenance))
    output = tmp_path / "external" / "pilot"

    exit_code = main([
        "prepare-2016",
        "--input", str(source),
        "--provenance", str(provenance_path),
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


def test_prepare_cli_rejects_provenance_not_bound_to_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "accepted.jsonl"
    source.write_text(json.dumps(rows_for_all_splits()[0]) + "\n")
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(json.dumps(provenance_document()))

    assert main([
        "prepare-2016", "--input", str(source),
        "--provenance", str(provenance_path),
        "--output-root", str(tmp_path / "prepared"), "--pilot-size", "1",
    ]) == 1
    assert "provenance" in json.loads(capsys.readouterr().err)["message"]
    assert not (tmp_path / "prepared").exists()


def complete_release(
    tmp_path: Path, *, pilot_size: int = 3, versioned: bool = False
) -> tuple[object, Path]:
    provenance = provenance_document()
    provenance["artifacts"]["reconciliation_report"] = {  # type: ignore[index]
        "sha256": "d" * 64,
        "size": 17,
    }
    provenance["reports"]["row_counts"].update({  # type: ignore[index]
        "raw": 4,
        "teacher_input_rows": 4,
        "accepted": 3,
        "excluded": 1,
        "matched_wikipedia_pages": 3,
    })
    provenance["sources"].append({  # type: ignore[union-attr]
        "role": "wikipedia",
        "filename": "enwiki.xml.bz2",
        "url": "https://example.test/enwiki.xml.bz2",
        "size": 456,
        "md5": "d" * 32,
        "sha1": "e" * 40,
        "downloaded_at": "2016-10-01",
    })
    if versioned:
        result = write_complete_shards(
            rows_for_all_splits(),
            tmp_path / "full-release",
            spool_database=tmp_path / "complete-spool.sqlite3",
            provenance=provenance,
            release_id="b" * 64,
            supersedes_commit_sha=PARENT,
        )
    else:
        result = write_shards(
            rows_for_all_splits(),
            tmp_path / "full-release",
            pilot_size=pilot_size,
            provenance=provenance,
        )
    published_provenance = json.loads(result.manifest_path.read_text())["provenance"][
        "document"
    ]
    proof = {
        "schema_version": 1,
        "dataset_config": "distillation_2016",
        "provenance_sha256": hashlib.sha256(
            canonical_json(published_provenance)
        ).hexdigest(),
        "accepted_jsonl": {"sha256": "c" * 64, "size": 123, "rows": 3},
        "reconciliation_report": {
            "sha256": "d" * 64,
            "size": 17,
            "complete": True,
            "raw_rows": 4,
            "accepted_rows": 3,
            "excluded_rows": 1,
            "matched_wikipedia_pages": 3,
        },
        "source_inventories": [
            {
                **provenance["sources"][0],  # type: ignore[index]
                "records": 4, "emitted_records": 4,
                "upstream_excluded_records": 0,
            },
            {
                **provenance["sources"][1],  # type: ignore[index]
                "records": 10, "emitted_records": 8,
                "upstream_excluded_records": 2,
            },
        ],
    }
    proof_path = tmp_path / "full-release-proof.json"
    proof_path.write_text(json.dumps(proof))
    return result, proof_path


def test_publish_cli_complete_requires_full_release_proof_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    result, _, _ = prepare(tmp_path)
    called = False

    def publish(*args: object, **kwargs: object) -> str:
        nonlocal called
        called = True
        return COMMIT

    monkeypatch.setattr(cli, "publish_verified_shards", publish)
    revision = tmp_path / "revision.txt"
    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--token", "token",
        "--revision-out", str(revision),
    ]) == 1
    assert called is False
    assert revision.exists() is False
    assert "full-release-proof" in json.loads(capsys.readouterr().err)["message"]


def test_invalid_complete_proof_never_reads_environment_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    result, _, _ = prepare(tmp_path)
    invalid_proof = tmp_path / "invalid-full-release-proof.json"
    invalid_proof.write_text("{}")

    class GuardedEnvironment(dict[str, str]):
        def get(self, key: str, default: object = None) -> object:
            if key == "HF_TOKEN":
                pytest.fail("invalid proof must fail before HF_TOKEN lookup")
            return super().get(key, default)  # type: ignore[arg-type]

    monkeypatch.setattr(cli.os, "environ", GuardedEnvironment())
    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(invalid_proof),
    ]) == 1
    assert "full release proof" in json.loads(capsys.readouterr().err)["message"]


def test_invalid_proof_redacts_explicit_token_without_environment_lookup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result, _, _ = prepare(tmp_path)
    secret = "explicit-pre-auth-secret"
    missing_proof = tmp_path / secret
    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(missing_proof),
        "--token", secret,
    ]) == 1
    error = capsys.readouterr().err
    assert secret not in error
    assert "[REDACTED]" in error


def test_publish_cli_complete_rejects_incomplete_proof_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    result, proof_path = complete_release(tmp_path)
    proof = json.loads(proof_path.read_text())
    proof["reconciliation_report"]["complete"] = False
    proof_path.write_text(json.dumps(proof))
    monkeypatch.setattr(
        cli,
        "publish_verified_shards",
        lambda *args, **kwargs: pytest.fail("publication must not be attempted"),
    )
    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(proof_path),
        "--token", "token",
    ]) == 1
    assert "full release proof" in json.loads(capsys.readouterr().err)["message"]


@pytest.mark.parametrize(
    "invalid_case", [
        "pilot", "provenance", "artifact", "source", "inventory_count",
        "missing_wikipedia", "wikipedia_zero", "matched_wikipedia", "swapped_roles",
    ]
)
def test_publish_cli_complete_rejects_unbound_or_pilot_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_case: str,
) -> None:
    import babel_data.cli as cli

    result, proof_path = complete_release(
        tmp_path, pilot_size=2 if invalid_case == "pilot" else 3
    )
    proof = json.loads(proof_path.read_text())
    if invalid_case == "provenance":
        proof["provenance_sha256"] = "e" * 64
    elif invalid_case == "artifact":
        proof["accepted_jsonl"]["sha256"] = "e" * 64
    elif invalid_case == "source":
        proof["source_inventories"][0]["filename"] = "different.jsonl"
    elif invalid_case == "inventory_count":
        proof["source_inventories"][0]["records"] = 999999
    elif invalid_case == "missing_wikipedia":
        proof["source_inventories"] = [
            item for item in proof["source_inventories"] if item["role"] != "wikipedia"
        ]
    elif invalid_case == "wikipedia_zero":
        wiki = next(item for item in proof["source_inventories"] if item["role"] == "wikipedia")
        wiki["emitted_records"] = 0
        wiki["upstream_excluded_records"] = wiki["records"]
    elif invalid_case == "matched_wikipedia":
        proof["reconciliation_report"]["matched_wikipedia_pages"] = 2
    elif invalid_case == "swapped_roles":
        proof["source_inventories"][0]["role"] = "wikipedia"
        proof["source_inventories"][1]["role"] = "teacher"
    proof_path.write_text(json.dumps(proof))
    monkeypatch.setattr(
        cli,
        "publish_verified_shards",
        lambda *args, **kwargs: pytest.fail("publication must not be attempted"),
    )

    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(proof_path),
        "--token", "token",
    ]) == 1
    assert "full release proof" in json.loads(capsys.readouterr().err)["message"]


def test_publish_cli_complete_accepts_bound_full_release_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    result, proof_path = complete_release(tmp_path)
    readiness_path = result.output_root / "readiness.json"
    published_readiness: bytes | None = None

    def publish(*args: object, **kwargs: object) -> str:
        nonlocal published_readiness
        published_readiness = readiness_path.read_bytes()
        document = json.loads(published_readiness)
        assert document["state"] == "complete"
        assert document["remote_verified"] is False
        assert document["remote_commit_sha"] is None
        return COMMIT

    monkeypatch.setattr(cli, "_api", object)
    monkeypatch.setattr(cli, "publish_verified_shards", publish)
    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(proof_path),
        "--token", "token",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "complete"
    assert readiness_path.read_bytes() == published_readiness
    restored = load_readiness(readiness_path, result.manifest_path)
    assert restored.remote_verified is True
    assert restored.remote_commit_sha == COMMIT


def test_publish_cli_stages_versioned_shards_then_binds_final_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    result, proof_path = complete_release(tmp_path, versioned=True)
    interrupted_readiness = json.loads(result.readiness_path.read_text())
    interrupted_readiness["state"] = "complete"
    result.readiness_path.write_text(
        json.dumps(interrupted_readiness, sort_keys=True, separators=(",", ":")) + "\n"
    )
    staged_commit = "d" * 40
    calls: list[str] = []

    def stage(*args: object, **kwargs: object) -> tuple[str, ...]:
        calls.append("stage")
        return (staged_commit,)

    def publish(*args: object, **kwargs: object) -> str:
        calls.append("publish")
        manifest = json.loads(result.manifest_path.read_text())
        readiness = json.loads(result.readiness_path.read_text())
        assert manifest["supersedes_commit_sha"] == staged_commit
        assert readiness["supersedes_commit_sha"] == staged_commit
        assert readiness["state"] == "complete"
        return COMMIT

    monkeypatch.setattr(cli, "_api", object)
    monkeypatch.setattr(cli, "stage_versioned_release_shards", stage, raising=False)
    monkeypatch.setattr(cli, "publish_verified_shards", publish)

    assert main([
        "publish-2016", "--input-root", str(result.output_root),
        "--state", "complete", "--full-release-proof", str(proof_path),
        "--token", "token",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert calls == ["stage", "publish"]
    assert output["publication_commits"] == [staged_commit, COMMIT]


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


@pytest.mark.parametrize("token_source", ["argument", "environment"])
def test_cli_redacts_registered_token_echoed_by_downstream_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    token_source: str,
) -> None:
    import babel_data.cli as cli

    result, _, _ = prepare(tmp_path)
    secret = f"downstream-{token_source}-secret"
    if token_source == "environment":
        monkeypatch.setenv("HF_TOKEN", secret)
    monkeypatch.setattr(cli, "_api", object)
    monkeypatch.setattr(
        cli,
        "publish_verified_shards",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    argv = ["publish-2016", "--input-root", str(result.output_root)]
    if token_source == "argument":
        argv.extend(["--token", secret])
    assert main(argv) == 1
    error = capsys.readouterr().err
    assert secret not in error
    assert "[REDACTED]" in error


def test_cli_argument_errors_are_structured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["prepare-2016"]) == 2
    message = json.loads(capsys.readouterr().err)
    assert message["status"] == "error"
    assert message["command"] == "prepare-2016"
    assert message["error"] == "usage"


@pytest.mark.parametrize("token_form", ["separate", "equals"])
def test_cli_redacts_explicit_token_from_argument_parse_errors(
    capsys: pytest.CaptureFixture[str], token_form: str
) -> None:
    secret = f"parse-error-{token_form}-secret"
    token_arguments = (
        ["--token", secret] if token_form == "separate" else [f"--token={secret}"]
    )
    assert main([
        "publish-2016", "--state", secret, *token_arguments,
    ]) == 2
    error = capsys.readouterr().err
    assert secret not in error
    assert "[REDACTED]" in error


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
