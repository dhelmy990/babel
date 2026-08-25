from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from jsonschema import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.reconcile import split_for  # noqa: E402
from babel_data.contracts import validate_document  # noqa: E402
from babel_data.release import (  # noqa: E402
    canonical_json,
    render_dataset_card,
    validate_manifest_document,
    validate_readiness_alignment,
)
from babel_data.shard import (  # noqa: E402
    PARQUET_SCHEMA,
    build_readiness,
    load_readiness,
    write_shards,
)


def provenance_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "sources": [{
            "role": "teacher",
            "filename": "accepted.jsonl",
            "url": "https://example.test/accepted.jsonl",
            "size": 123,
            "md5": "a" * 32,
            "sha1": "b" * 40,
            "downloaded_at": "2016-10-01",
        }],
        "artifacts": {"accepted_jsonl": {"sha256": "c" * 64, "size": 123}},
        "reports": {
            "row_counts": {"input": 3, "matched": 3},
            "match_rate": 1.0,
            "exclusion_counts": {},
            "text_statistics": {
                "count": 3, "min_length": 6, "max_length": 14,
                "mean_length": 10.0, "stddev_length": 2.0,
                "p50_length": 10.0, "p95_length": 14.0,
                "p99_length": 14.0, "histogram": [3],
            },
            "vector_statistics": {
                "dimension": 100, "count": 3, "min_norm": 1.0,
                "max_norm": 7.0, "mean_norm": 4.0, "stddev_norm": 2.0,
                "p50_norm": 4.0, "p95_norm": 7.0,
                "non_finite_count": 0,
            },
        },
    }


def write_test_shards(
    rows: object, output_root: Path, **kwargs: object
):
    return write_shards(
        rows, output_root, provenance=provenance_document(), **kwargs  # type: ignore[arg-type]
    )


def row(article_key: str, page_id: int) -> dict[str, object]:
    return {
        "article_key": article_key,
        "page_id": page_id,
        "canonical_title": f"Article {page_id}",
        "wikidata_id": None,
        "lead_text": f"Lead {page_id}",
        "article_text": f"Article text {page_id}",
        "teacher_vector": [float(page_id % 7 + 1)] + [0.0] * 99,
        "teacher_norm": float(page_id % 7 + 1),
        "source_revision_id": page_id + 100,
        "snapshot_date": "2016-10-01",
        "split": split_for(article_key),
        "reconciliation_status": "matched",
    }


def test_pilot_sample_is_smallest_hash_rank_not_input_order(tmp_path: Path) -> None:
    rows = [row(f"enwiki:2016-10-01:{number}", number) for number in range(1, 13)]
    first = write_test_shards(rows, tmp_path / "first", pilot_size=5, target_shard_bytes=900)
    second = write_test_shards(
        list(reversed(rows)), tmp_path / "second", pilot_size=5, target_shard_bytes=900
    )

    expected = tuple(
        item[1]
        for item in sorted(
            (hashlib.sha256(str(value["article_key"]).encode()).hexdigest(), value["article_key"])
            for value in rows
        )[:5]
    )
    assert first.pilot_article_keys == second.pilot_article_keys == expected

    def output_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    assert output_bytes(first.output_root) == output_bytes(second.output_root)
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["pilot_article_keys"] == list(expected)


def keys_for_all_splits() -> list[str]:
    found: dict[str, str] = {}
    number = 1
    while len(found) < 3:
        key = f"enwiki:2016-10-01:{number}"
        found.setdefault(split_for(key), key)
        number += 1
    return [found[split] for split in ("train", "validation", "test")]


def test_writer_uses_explicit_schema_and_separate_split_paths(tmp_path: Path) -> None:
    keys = keys_for_all_splits()
    result = write_test_shards(
        [row(key, int(key.rsplit(":", 1)[1])) for key in keys],
        tmp_path / "prepared",
        pilot_size=3,
        target_shard_bytes=10_000,
    )

    assert {item.split for item in result.shards} == {"train", "validation", "test"}
    assert all(item.path.startswith(f"distillation_2016/{item.split}/") for item in result.shards)
    for item in result.shards:
        assert pq.read_schema(result.output_root / item.path) == PARQUET_SCHEMA

    manifest = json.loads(result.manifest_path.read_text())
    validate_manifest_document(manifest)
    assert manifest["schema_version"] == 1
    assert manifest["state"] == "prepared"
    assert manifest["provenance"]["schema"] == "provenance-v1"
    assert manifest["provenance"]["identifiers"] == {
        "dataset_config": "distillation_2016",
        "example_schema": "distillation-example-v1",
        "snapshot_date": "2016-10-01",
        "teacher_dimension": 100,
    }
    for item in manifest["shards"]:
        table = pq.read_table(result.output_root / item["path"])
        identities = [
            [row["article_key"], row["page_id"]]
            for row in table.select(["article_key", "page_id"]).to_pylist()
        ]
        assert item["rows_sha256"] == hashlib.sha256(
            canonical_json(identities)
        ).hexdigest()

    assert result.readme_path.read_bytes() == render_dataset_card()
    readiness = json.loads(result.readiness_path.read_text())
    validate_readiness_alignment(readiness, manifest)


def test_writer_requires_schema_valid_provenance(tmp_path: Path) -> None:
    value = row("enwiki:2016-10-01:1", 1)
    with pytest.raises((TypeError, ValueError, ValidationError), match="provenance"):
        write_shards([value], tmp_path / "missing", pilot_size=1)
    with pytest.raises((TypeError, ValueError, ValidationError)):
        write_shards(
            [value], tmp_path / "invalid", pilot_size=1,
            provenance={"arbitrary": True},
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda value: value.update(extra="unknown"), ValidationError),
        (lambda value: value.pop("lead_text"), ValidationError),
        (lambda value: value.update(page_id=True), (ValidationError, ValueError)),
        (lambda value: value.update(teacher_vector=[0.0] * 99), ValidationError),
        (lambda value: value.update(teacher_vector=[float("1e300")] + [0.0] * 99), ValueError),
        (lambda value: value.update(teacher_norm=float("nan")), ValidationError),
        (lambda value: value.update(teacher_norm=3.0), ValueError),
        (lambda value: value.update(split="test" if value["split"] != "test" else "train"), ValueError),
    ],
)
def test_writer_rejects_non_contract_rows(
    tmp_path: Path, mutation: object, error: type[BaseException] | tuple[type[BaseException], ...]
) -> None:
    value = row("enwiki:2016-10-01:1", 1)
    mutation(value)  # type: ignore[operator]

    with pytest.raises(error):
        write_test_shards([value], tmp_path / "invalid", pilot_size=1)
    assert not (tmp_path / "invalid").exists()


def test_writer_rejects_article_key_page_identity_mismatch(tmp_path: Path) -> None:
    value = row("enwiki:2016-10-01:1", 1)
    value["article_key"] = "enwiki:2016-10-01:999"
    value["split"] = split_for(str(value["article_key"]))
    with pytest.raises(ValueError, match="identity"):
        write_test_shards([value], tmp_path / "invalid", pilot_size=1)


def test_readiness_requires_source_checksum_provenance(tmp_path: Path) -> None:
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    with pytest.raises(ValueError, match="source checksum"):
        build_readiness(result, source_checksums={})


def test_writer_rejects_duplicate_identity(tmp_path: Path) -> None:
    first = row("enwiki:2016-10-01:1", 1)
    second = dict(first)
    second["canonical_title"] = "Conflicting duplicate"

    with pytest.raises(ValueError, match="duplicate"):
        write_test_shards([first, second], tmp_path / "duplicates", pilot_size=2)


def test_writer_failure_leaves_no_partial_final_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated full disk")

    monkeypatch.setattr(shard, "_write_table", fail)
    output = tmp_path / "prepared"
    with pytest.raises(OSError, match="full disk"):
        write_test_shards([row("enwiki:2016-10-01:1", 1)], output, pilot_size=1)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_directory_publication_never_replaces_racing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    real_write = shard._write_table
    output = tmp_path / "prepared"

    def race(rows: object, path: Path) -> None:
        real_write(rows, path)  # type: ignore[arg-type]
        output.mkdir(exist_ok=True)

    monkeypatch.setattr(shard, "_write_table", race)
    with pytest.raises(FileExistsError):
        write_test_shards([row("enwiki:2016-10-01:1", 1)], output, pilot_size=1)
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_atomic_directory_publication_rolls_back_after_parent_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    output = tmp_path / "prepared"
    real_fsync = shard.os.fsync
    parent_syncs = 0

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal parent_syncs
        path = Path(shard.os.readlink(f"/proc/self/fd/{descriptor}"))
        if path == tmp_path:
            parent_syncs += 1
            if output.exists():
                raise OSError("simulated directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(shard.os, "fsync", fail_parent_fsync)
    with pytest.raises(OSError, match="directory fsync"):
        write_test_shards([row("enwiki:2016-10-01:1", 1)], output, pilot_size=1)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
    assert parent_syncs == 2


def test_staged_file_fsync_failure_never_publishes_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    real_fsync = shard.os.fsync

    def fail_manifest_fsync(descriptor: int) -> None:
        path = Path(shard.os.readlink(f"/proc/self/fd/{descriptor}"))
        if path.name == "manifest.json":
            raise OSError("simulated staged file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(shard.os, "fsync", fail_manifest_fsync)
    output = tmp_path / "prepared"
    with pytest.raises(OSError, match="staged file fsync"):
        write_test_shards([row("enwiki:2016-10-01:1", 1)], output, pilot_size=1)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_shard_publication_fsyncs_files_then_directories_bottom_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    real_fsync = shard.os.fsync
    synced: list[tuple[str, bool]] = []

    def record_fsync(descriptor: int) -> None:
        path = Path(shard.os.readlink(f"/proc/self/fd/{descriptor}"))
        synced.append((path.name, path.is_dir()))
        real_fsync(descriptor)

    monkeypatch.setattr(shard.os, "fsync", record_fsync)
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "durable", pilot_size=1
    )
    names = [name for name, _ in synced]
    for required in ("part-00000.parquet", "manifest.json", "readiness.json", "README.md"):
        assert required in names
    first_directory = next(index for index, (_, is_dir) in enumerate(synced) if is_dir)
    assert all(not is_dir for _, is_dir in synced[:first_directory])
    assert synced[-1] == (tmp_path.name, True)
    assert result.output_root.exists()


def test_identity_uniqueness_uses_cleaned_disk_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    real_connect = shard.sqlite3.connect
    databases: list[Path] = []

    def connect(database: object, *args: object, **kwargs: object) -> object:
        databases.append(Path(str(database)))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(shard.sqlite3, "connect", connect)
    write_test_shards(
        [row(f"enwiki:2016-10-01:{number}", number) for number in range(1, 8)],
        tmp_path / "sqlite-unique",
        pilot_size=3,
    )
    assert len(databases) == 1
    assert databases[0].exists() is False

    duplicate = row("enwiki:2016-10-01:20", 20)
    with pytest.raises(ValueError, match="duplicate"):
        write_test_shards(
            [duplicate, duplicate],
            tmp_path / "sqlite-duplicate",
            pilot_size=2,
        )
    assert len(databases) == 2
    assert databases[1].exists() is False


def test_readiness_is_monotonic_and_delete_requires_exact_remote_evidence(tmp_path: Path) -> None:
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = build_readiness(
        result, source_checksums={"accepted_jsonl": "c" * 64}, path=readiness_path
    )

    validate_document("dataset-readiness-v1", readiness.to_document())
    assert readiness.state == "building"
    assert readiness.can_delete_local is False
    with pytest.raises(ValueError, match="remote verification"):
        readiness.transition("pilot_ready")

    readiness.mark_remote_verified("b" * 40)
    assert readiness.can_delete_local is False
    readiness.save(readiness_path)
    assert readiness.can_delete_local is True
    evidence_path = readiness_path.with_name(
        readiness_path.name + ".remote-verification.json"
    )
    evidence = json.loads(evidence_path.read_text())
    evidence["commit_sha"] = "c" * 40
    evidence_path.write_text(json.dumps(evidence))
    assert readiness.can_delete_local is False
    evidence["commit_sha"] = "not-a-commit"
    evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="commit SHA"):
        load_readiness(readiness_path, result.manifest_path)
    readiness.save(readiness_path)
    assert readiness.can_delete_local is True
    readiness.transition("pilot_ready")
    readiness.save(readiness_path)
    shard_path = result.output_root / result.shards[0].path
    original = shard_path.read_bytes()
    shard_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert readiness.can_delete_local is False
    shard_path.write_bytes(original)
    with pytest.raises(ValueError, match="regress"):
        readiness.transition("building")
    readiness.transition("complete")
    validate_document("dataset-readiness-v1", readiness.to_document())


def test_failed_verification_evidence_save_never_enables_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.shard as shard

    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = build_readiness(
        result, source_checksums={"accepted_jsonl": "c" * 64}, path=readiness_path
    )
    readiness.mark_remote_verified("b" * 40)
    real_write = shard._atomic_write_json

    def fail_evidence(path: Path, value: object) -> None:
        if path.name.endswith("remote-verification.json"):
            raise OSError("simulated evidence failure")
        real_write(path, value)

    monkeypatch.setattr(shard, "_atomic_write_json", fail_evidence)
    with pytest.raises(OSError, match="evidence failure"):
        readiness.save(readiness_path)
    assert readiness.can_delete_local is False
    restored = load_readiness(readiness_path, result.manifest_path)
    assert restored.remote_verified is False
    assert restored.can_delete_local is False


def test_delete_rehashes_every_shard_from_the_manifest(tmp_path: Path) -> None:
    values = [
        row(key, int(key.rsplit(":", 1)[1])) for key in keys_for_all_splits()
    ]
    result = write_test_shards(
        values, tmp_path / "prepared", pilot_size=3, target_shard_bytes=10_000
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = build_readiness(
        result, source_checksums={"accepted_jsonl": "c" * 64}, path=readiness_path
    )
    readiness.mark_remote_verified("b" * 40)
    readiness.save(readiness_path)

    tampered = result.output_root / result.shards[0].path
    contents = tampered.read_bytes()
    tampered.write_bytes(bytes([contents[0] ^ 1]) + contents[1:])
    readiness.verified_shards = readiness.verified_shards[1:]

    assert readiness.can_delete_local is False


def test_reloaded_readiness_does_not_trust_a_changed_local_manifest(tmp_path: Path) -> None:
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = build_readiness(
        result, source_checksums={"accepted_jsonl": "c" * 64}, path=readiness_path
    )
    readiness.mark_remote_verified("b" * 40)
    readiness.save(readiness_path)
    readiness.transition("pilot_ready")
    readiness.save(readiness_path)

    manifest = json.loads(result.manifest_path.read_text())
    manifest["provenance"]["document"]["reports"]["row_counts"]["tampered"] = 1
    result.manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    restored = load_readiness(readiness_path, result.manifest_path)
    assert restored.remote_verified is True
    assert restored.can_delete_local is False


def test_readiness_evidence_pointer_advances_only_for_new_exact_release(
    tmp_path: Path,
) -> None:
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = load_readiness(readiness_path, result.manifest_path)
    readiness.stage_publication("pilot_ready")
    readiness.save(readiness_path)
    readiness.mark_remote_verified("a" * 40)
    readiness.save_verification_evidence(readiness_path)
    assert load_readiness(readiness_path, result.manifest_path).remote_commit_sha == "a" * 40

    manifest = json.loads(result.manifest_path.read_text())
    manifest["provenance"]["document"]["reports"]["row_counts"]["release"] = 2
    result.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    next_release = load_readiness(readiness_path, result.manifest_path)
    assert next_release.remote_verified is False
    next_release.stage_publication("pilot_ready")
    next_release.save(readiness_path)
    next_release.mark_remote_verified("b" * 40)
    next_release.save_verification_evidence(readiness_path)
    restored = load_readiness(readiness_path, result.manifest_path)
    assert restored.remote_verified is True
    assert restored.remote_commit_sha == "b" * 40


def test_persisted_readiness_rejects_source_or_remote_verification_regression(
    tmp_path: Path,
) -> None:
    result = write_test_shards(
        [row("enwiki:2016-10-01:1", 1)], tmp_path / "prepared", pilot_size=1
    )
    readiness_path = result.output_root / "readiness.json"
    readiness = build_readiness(
        result, source_checksums={"accepted_jsonl": "c" * 64}, path=readiness_path
    )
    readiness.mark_remote_verified("b" * 40)
    readiness.save(readiness_path)

    readiness.source_checksums["accepted_jsonl"] = "d" * 64
    with pytest.raises(ValueError, match="source checksum"):
        readiness.save(readiness_path)
    readiness.source_checksums["accepted_jsonl"] = "c" * 64
    readiness.remote_verified = False
    readiness.remote_commit_sha = None
    with pytest.raises(ValueError, match="remote verification"):
        readiness.save(readiness_path)
