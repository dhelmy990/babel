from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training" / "src"))

from babel_training.data import (  # noqa: E402
    DatasetContractError,
    ForbiddenDatasetConfiguration,
    InvalidDatasetRevision,
    load_distillation_stream,
    load_validation_stream,
    resolve_dataset_revision,
)


COMMIT = "a" * 40


@pytest.mark.parametrize(
    "name",
    [
        "dataset-manifest-v1.json",
        "dataset-readiness-v1.json",
        "provenance-v1.json",
        "distillation-example-v1.json",
    ],
)
def test_training_wheel_packages_exact_canonical_schemas(name: str) -> None:
    packaged = files("babel_training").joinpath("schemas", name).read_bytes()
    assert packaged == (ROOT / "schemas" / name).read_bytes()


def test_training_package_exposes_public_data_artifact_interfaces() -> None:
    import babel_training

    assert babel_training.resolve_dataset_revision is resolve_dataset_revision
    assert babel_training.load_distillation_stream is load_distillation_stream
    assert babel_training.load_validation_stream is load_validation_stream
    assert babel_training.DistillationCollator.__name__ == "DistillationCollator"
    assert callable(babel_training.export_distilled_artifact)
    assert callable(babel_training.publish_model_artifact)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def split_for(key: str) -> str:
    bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % 100
    return "train" if bucket < 98 else "validation" if bucket == 98 else "test"


def row(number: int, *, split: str = "train") -> dict[str, object]:
    while split_for(f"enwiki:2016-10-01:{number}") != split:
        number += 1
    key = f"enwiki:2016-10-01:{number}"
    return {
        "article_key": key,
        "page_id": number,
        "canonical_title": f"Article {number}",
        "wikidata_id": None,
        "lead_text": f"Lead {number}",
        "article_text": f"Article body {number}",
        "teacher_vector": [1.0] + [0.0] * 99,
        "teacher_norm": 1.0,
        "source_revision_id": number,
        "snapshot_date": "2016-10-01",
        "split": split,
        "reconciliation_status": "matched",
    }


def metadata(
    *, state: str = "pilot_ready", revision: str = COMMIT, count: int = 8,
    split: str = "train",
) -> dict[str, bytes]:
    shard = {
        "path": f"distillation_2016/{split}/part-00000.parquet",
        "split": split,
        "rows": count,
        "bytes": 10,
        "sha256": "b" * 64,
        "rows_sha256": "c" * 64,
        "schema": "distillation-example-v1",
        "version": 1,
        "min_article_key": "enwiki:2016-10-01:1",
        "max_article_key": f"enwiki:2016-10-01:{count}",
        "min_rank": "1" * 64,
        "max_rank": "2" * 64,
    }
    counts = {"total": count, "train": 0, "validation": 0, "test": 0}
    counts[split] = count
    aggregate = hashlib.sha256(canonical_json([shard])).hexdigest()
    provenance = {
        "schema_version": 1,
        "sources": [{
            "role": "teacher", "filename": "2016-09-01_2016-09-30_en_100.zip",
            "url": "https://example.test/teacher.zip", "size": 1,
            "md5": "ac70acfc41aff7a23cc9439e3bb1771f", "downloaded_at": "2016-10-01",
        }, {
            "role": "wikipedia", "filename": "enwiki-20161001-pages-articles-multistream.xml.bz2",
            "url": "https://example.test/enwiki.xml.bz2", "size": 1,
            "md5": "5df8e610829c336138dcb9191071b283", "downloaded_at": "2016-10-01",
        }],
        "artifacts": {"accepted_jsonl": {"sha256": "e" * 64, "size": 1}},
        "reports": {
            "row_counts": {"accepted": count}, "match_rate": 1.0,
            "exclusion_counts": {},
            "text_statistics": {"count": count, "min_length": 1, "max_length": 2,
                "mean_length": 1.5, "stddev_length": 0.5, "p50_length": 1.5,
                "p95_length": 2.0, "p99_length": 2.0, "histogram": [count]},
            "vector_statistics": {"dimension": 100, "count": count, "min_norm": 1.0,
                "max_norm": 1.0, "mean_norm": 1.0, "stddev_norm": 0.0,
                "p50_norm": 1.0, "p95_norm": 1.0, "non_finite_count": 0},
            "dataset_aggregate_sha256": aggregate,
            "dataset_rows_sha256": "f" * 64,
            "dataset_counts": counts,
        },
    }
    manifest = {
        "manifest_version": 1, "schema_version": 1, "state": "prepared",
        "schema": "distillation-example-v1", "dataset_config": "distillation_2016",
        "pilot_article_keys": ["enwiki:2016-10-01:1"], "counts": counts,
        "shards": [shard], "aggregate_sha256": aggregate, "rows_sha256": "f" * 64,
        "provenance": {"schema": "provenance-v1", "identifiers": {
            "dataset_config": "distillation_2016",
            "example_schema": "distillation-example-v1",
            "snapshot_date": "2016-10-01", "teacher_dimension": 100,
        }, "document": provenance},
    }
    readiness = {
        "state": state, "schema_version": 1, "teacher_dimension": 100,
        "available_examples": count,
        "verified_shards": [{"path": shard["path"], "sha256": shard["sha256"], "examples": count}],
        "source_checksums": {"accepted_jsonl": "e" * 64},
        "remote_verified": state != "building",
        "remote_commit_sha": revision if state != "building" else None,
    }
    readme = b"""---
configs:
- config_name: distillation_2016
  data_files:
  - split: train
    path: distillation_2016/train/*.parquet
  - split: validation
    path: distillation_2016/validation/*.parquet
  - split: test
    path: distillation_2016/test/*.parquet
---
# Babel 2016 distillation dataset
"""
    return {
        "distillation_2016/manifest.json": canonical_json(manifest),
        "readiness.json": canonical_json(readiness),
        "README.md": readme,
    }


class FakeApi:
    def __init__(
        self, files: dict[str, bytes] | None = None, *, sha: str = COMMIT,
        private: bool | None = True,
    ) -> None:
        self.files = files or metadata()
        self.sha = sha
        self.private = private
        self.info_calls: list[dict[str, object]] = []
        self.file_calls: list[tuple[str, str]] = []

    def dataset_info(self, repo_id: str, **kwargs: object) -> object:
        assert set(kwargs) == {"revision", "token"}
        self.info_calls.append({"repo_id": repo_id, **kwargs})
        return SimpleNamespace(sha=self.sha, private=self.private)

    def get_file_bytes(self, *, path_in_repo: str, revision: str, **kwargs: object) -> bytes:
        self.file_calls.append((path_in_repo, revision))
        return self.files[path_in_repo]


def test_revision_resolves_floating_ref_once_and_verifies_exact_sha() -> None:
    api = FakeApi()
    assert resolve_dataset_revision(api, "org/data", "main", "secret") == COMMIT
    assert len(api.info_calls) == 1
    assert api.info_calls[0]["revision"] == "main"

    exact = FakeApi()
    assert resolve_dataset_revision(exact, "org/data", COMMIT, "secret") == COMMIT
    assert len(exact.info_calls) == 1


@pytest.mark.parametrize("bad", ["a" * 39, "A" * 40, "g" * 40, None])
def test_revision_rejects_malformed_or_ambiguous_response(bad: object) -> None:
    with pytest.raises(InvalidDatasetRevision, match="revision") as captured:
        resolve_dataset_revision(FakeApi(sha=bad), "org/data", "main", "do-not-print")  # type: ignore[arg-type]
    assert "do-not-print" not in str(captured.value)


def test_exact_revision_rejects_moving_identity() -> None:
    with pytest.raises(InvalidDatasetRevision, match="identity"):
        resolve_dataset_revision(FakeApi(sha="b" * 40), "org/data", COMMIT, "secret")


@pytest.mark.parametrize("config", ["simulator_2026_hidden", "observed", "other"])
def test_training_loader_rejects_every_non_distillation_config(config: str) -> None:
    with pytest.raises(ForbiddenDatasetConfiguration):
        load_distillation_stream(revision=COMMIT, token="secret", config_name=config)


def test_training_loader_blocks_validation_test_and_arbitrary_splits() -> None:
    for split in ("validation", "test", "dev"):
        with pytest.raises(ValueError, match="train"):
            load_distillation_stream(revision=COMMIT, token="secret", split=split)


def test_loader_fetches_pinned_metadata_before_one_streaming_dataset_call() -> None:
    events: list[str] = []
    api = FakeApi()
    original = api.get_file_bytes

    def get_file_bytes(**kwargs: object) -> bytes:
        events.append(str(kwargs["path_in_repo"]))
        return original(**kwargs)  # type: ignore[arg-type]

    api.get_file_bytes = get_file_bytes  # type: ignore[method-assign]
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def loader(*args: object, **kwargs: object) -> list[dict[str, object]]:
        events.append("load_dataset")
        calls.append((args, kwargs))
        return [row(index) for index in range(1, 9)]

    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=api, load_dataset_fn=loader,
        seed=7, shuffle_buffer_size=3,
    )
    assert events[:3] == ["distillation_2016/manifest.json", "readiness.json", "README.md"]
    assert events[3] == "load_dataset"
    assert calls == [(('dhelmy990/babel-wikipedia-experiment', 'distillation_2016'), {
        "split": "train", "revision": COMMIT, "token": "secret", "streaming": True,
    })]
    assert len(list(stream)) == 8


def test_loader_accepts_task5_non_self_referential_readiness_and_proves_privacy() -> None:
    files = metadata(count=1)
    readiness = json.loads(files["readiness.json"])
    readiness["remote_verified"] = False
    readiness["remote_commit_sha"] = None
    files["readiness.json"] = canonical_json(readiness)
    api = FakeApi(files)
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=api,
        load_dataset_fn=lambda *a, **k: [row(1)], shuffle_buffer_size=1,
    )
    assert next(iter(stream))["split"] == "train"
    assert api.info_calls[-1]["revision"] == COMMIT

    with pytest.raises(DatasetContractError, match="private"):
        load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(files, private=None),
            load_dataset_fn=lambda *a, **k: [row(1)],
        )


def test_provenance_uses_json_schema_uri_format_not_http_only_policy() -> None:
    files = metadata(count=1)
    manifest = json.loads(files["distillation_2016/manifest.json"])
    sources = manifest["provenance"]["document"]["sources"]
    sources[0]["url"] = "urn:babel:teacher:2016"
    sources[1]["url"] = "urn:babel:wikipedia:2016-10-01"
    files["distillation_2016/manifest.json"] = canonical_json(manifest)
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(files),
        load_dataset_fn=lambda *a, **k: [row(1)], shuffle_buffer_size=1,
    )
    assert next(iter(stream))["page_id"] == 1


@pytest.mark.parametrize(
    ("field", "bad"),
    [("url", "not a uri with spaces"), ("downloaded_at", "2016-99-99")],
)
def test_provenance_rejects_invalid_packaged_schema_formats(
    field: str, bad: str,
) -> None:
    values = metadata()
    manifest = json.loads(values["distillation_2016/manifest.json"])
    manifest["provenance"]["document"]["sources"][0][field] = bad
    values["distillation_2016/manifest.json"] = canonical_json(manifest)

    with pytest.raises(DatasetContractError, match="schema field"):
        load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(values),
            load_dataset_fn=lambda *a, **k: [row(1)], shuffle_buffer_size=1,
        )


@pytest.mark.parametrize("state", ["building", "unknown"])
def test_loader_rejects_unready_state(state: str) -> None:
    files = metadata(state="building")
    if state == "unknown":
        readiness = json.loads(files["readiness.json"])
        readiness["state"] = state
        files["readiness.json"] = canonical_json(readiness)
    with pytest.raises(DatasetContractError):
        load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(files),
            load_dataset_fn=lambda *a, **k: [],
        )


def test_loader_rejects_manifest_readiness_or_readme_mismatch() -> None:
    mutations = []
    files = metadata()
    manifest = json.loads(files["distillation_2016/manifest.json"])
    manifest["dataset_config"] = "simulator_hidden"
    mutated = dict(files); mutated["distillation_2016/manifest.json"] = canonical_json(manifest)
    mutations.append(mutated)
    readiness = json.loads(files["readiness.json"]); readiness["remote_commit_sha"] = "b" * 40
    mutated = dict(files); mutated["readiness.json"] = canonical_json(readiness); mutations.append(mutated)
    mutated = dict(files); mutated["README.md"] += b"simulator_hidden\n"; mutations.append(mutated)
    manifest = json.loads(files["distillation_2016/manifest.json"])
    manifest["provenance"]["document"]["sources"][0]["hidden"] = "secret"
    mutated = dict(files); mutated["distillation_2016/manifest.json"] = canonical_json(manifest)
    mutations.append(mutated)
    manifest = json.loads(files["distillation_2016/manifest.json"])
    manifest["provenance"]["document"]["reports"]["hidden"] = 1
    mutated = dict(files); mutated["distillation_2016/manifest.json"] = canonical_json(manifest)
    mutations.append(mutated)
    manifest = json.loads(files["distillation_2016/manifest.json"])
    manifest["provenance"]["document"]["sources"] = manifest["provenance"]["document"]["sources"][:1]
    mutated = dict(files); mutated["distillation_2016/manifest.json"] = canonical_json(manifest)
    mutations.append(mutated)
    for invalid in mutations:
        with pytest.raises(DatasetContractError):
            load_distillation_stream(
                revision=COMMIT, token="secret", api=FakeApi(invalid),
                load_dataset_fn=lambda *a, **k: [],
            )


def test_every_streamed_row_is_validated_and_unknown_fields_fail_closed() -> None:
    values = [row(1), row(2), row(3)]
    values[-1]["teacher_norm"] = 2.0
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(),
        load_dataset_fn=lambda *a, **k: values, shuffle_buffer_size=1,
    )
    iterator = iter(stream)
    next(iterator)
    with pytest.raises(DatasetContractError, match="row"):
        next(iterator)

    hidden = row(1); hidden["hidden_label"] = "secret"
    closed = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(),
        load_dataset_fn=lambda *a, **k: [hidden], shuffle_buffer_size=1,
    )
    with pytest.raises(DatasetContractError, match="field"):
        next(iter(closed))


@pytest.mark.parametrize(
    "missing",
    [
        "article_text",
        "wikidata_id",
        "source_revision_id",
        "snapshot_date",
        "reconciliation_status",
    ],
)
def test_physical_stream_requires_every_canonical_v1_field(missing: str) -> None:
    physical = row(1)
    del physical[missing]
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [physical], shuffle_buffer_size=1,
    )

    with pytest.raises(DatasetContractError, match="field|schema|row"):
        next(iter(stream))


def test_physical_stream_rejects_internal_seven_field_projection() -> None:
    physical = row(1)
    projected = {
        name: physical[name]
        for name in (
            "article_key", "page_id", "canonical_title", "lead_text",
            "teacher_vector", "teacher_norm", "split",
        )
    }
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [projected], shuffle_buffer_size=1,
    )

    with pytest.raises(DatasetContractError, match="field|schema|row"):
        next(iter(stream))


def test_shuffle_is_deterministic_by_seed_and_epoch() -> None:
    values = list({
        item["article_key"]: item for item in (row(index) for index in range(1, 20))
    }.values())

    def make(seed: int, epoch: int = 0):
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=len(values))),
            load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
            seed=seed, epoch=epoch, shuffle_buffer_size=4,
        )

    keys = lambda stream: [item["article_key"] for item in stream]
    assert keys(make(12)) == keys(make(12))
    assert keys(make(12)) != keys(make(13))
    assert keys(make(12, 0)) != keys(make(12, 1))


def test_state_resume_preserves_shuffle_buffer_without_duplicates_or_skips() -> None:
    values_by_key = {item["article_key"]: item for item in (row(index) for index in range(1, 30))}
    values = list(values_by_key.values())

    def make():
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=len(values))),
            load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
            seed=99, shuffle_buffer_size=5,
        )

    expected = [item["article_key"] for item in make()]
    interrupted = make(); iterator = iter(interrupted)
    prefix = [next(iterator)["article_key"] for _ in range(7)]
    state = interrupted.state_dict()
    json.dumps(state)
    resumed = make(); resumed.load_state_dict(state)
    actual = prefix + [item["article_key"] for item in resumed]
    assert actual == expected
    assert len(actual) == len(set(actual))


def test_state_rejects_rehashed_buffer_replacement_from_already_yielded_row() -> None:
    values = list({row(i)["article_key"]: row(i) for i in range(1, 30)}.values())[:8]

    def make():
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=8)),
            load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
            seed=31, shuffle_buffer_size=4,
        )

    source = make()
    yielded = next(iter(source))
    forged = source.state_dict()
    forged["cursor"]["shuffle_buffer"][0] = copy.deepcopy(yielded)
    unsigned = {name: forged[name] for name in forged if name != "state_sha256"}
    forged["state_sha256"] = hashlib.sha256(canonical_json(unsigned)).hexdigest()

    with pytest.raises(ValueError, match="history"):
        make().load_state_dict(forged)


def test_state_rejects_buffer_that_would_duplicate_unconsumed_source_row() -> None:
    values = [row(index) for index in range(1, 12)]
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(),
        load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
        seed=9, shuffle_buffer_size=3,
    )
    forged = stream.state_dict()
    forged["cursor"]["shuffle_buffer"] = [copy.deepcopy(values[0])]

    with pytest.raises(ValueError, match="state"):
        stream.load_state_dict(forged)


def test_state_rejects_forged_cursor_rng_buffer_flags_and_redundant_shard() -> None:
    values = [row(index) for index in range(1, 20)]

    def make():
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=len(values))),
            load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
            seed=21, shuffle_buffer_size=4,
        )

    source = make(); iterator = iter(source)
    next(iterator); next(iterator)
    valid = source.state_dict()
    mutations = []

    shard = copy.deepcopy(valid)
    shard["shard"]["example_cursor"] += 1
    mutations.append(shard)

    arithmetic = copy.deepcopy(valid)
    arithmetic["cursor"]["processed_examples"] += 1
    mutations.append(arithmetic)

    wrong_buffer = copy.deepcopy(valid)
    wrong_buffer["cursor"]["shuffle_buffer"][0] = copy.deepcopy(values[-1])
    mutations.append(wrong_buffer)

    wrong_rng = copy.deepcopy(valid)
    wrong_rng["cursor"]["shuffle_rng_state"] = make().state_dict()["cursor"]["shuffle_rng_state"]
    mutations.append(wrong_rng)

    wrong_exhaustion = copy.deepcopy(valid)
    wrong_exhaustion["cursor"]["source_exhausted"] = True
    mutations.append(wrong_exhaustion)

    wrong_completion = copy.deepcopy(valid)
    wrong_completion["cursor"]["complete"] = True
    mutations.append(wrong_completion)

    for forged in mutations:
        with pytest.raises(ValueError, match="state"):
            make().load_state_dict(forged)


def test_unstarted_state_has_rng_and_restores_exact_start() -> None:
    values = list({item["article_key"]: item for item in (row(i) for i in range(1, 12))}.values())

    def make():
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=len(values))),
            load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
            seed=42, shuffle_buffer_size=3,
        )

    fresh = make()
    state = fresh.state_dict()
    assert state["cursor"]["shuffle_rng_state"] is not None
    assert state["cursor"]["source_cursor"] == 0
    assert state["cursor"]["shuffle_buffer"] == []
    restored = make(); restored.load_state_dict(state)
    assert list(restored) == list(make())


def test_empty_stream_state_round_trip_and_epoch_boundary() -> None:
    def make(epoch: int = 0):
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=1, split="validation")),
            load_dataset_fn=lambda *a, **k: [], seed=4, epoch=epoch,
            shuffle_buffer_size=2,
        )

    stream = make()
    initial = stream.state_dict()
    assert list(stream) == []
    complete = stream.state_dict()
    assert complete["cursor"]["complete"] is True
    restored = make(); restored.load_state_dict(complete)
    assert list(restored) == []
    stream.set_epoch(1)
    epoch_state = stream.state_dict()
    assert epoch_state["identity"]["epoch"] == 1
    assert epoch_state["cursor"]["shuffle_rng_state"] != initial["cursor"]["shuffle_rng_state"]


def test_state_rejects_immutable_identity_mismatch() -> None:
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [row(1)], seed=1, shuffle_buffer_size=2,
    )
    next(iter(stream)); state = stream.state_dict()
    other = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [row(1)], seed=2, shuffle_buffer_size=2,
    )
    with pytest.raises(ValueError, match="identity"):
        other.load_state_dict(state)


def test_stream_projects_away_large_article_and_bounds_checkpoint_bytes() -> None:
    value = row(1)
    value["article_text"] = "x" * (4 * 1024 * 1024)
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [value], shuffle_buffer_size=10_000,
    )
    example = next(iter(stream))
    state = stream.state_dict()

    assert set(example) == {
        "article_key", "page_id", "canonical_title", "lead_text",
        "teacher_vector", "teacher_norm", "split",
    }
    assert "article_text" not in json.dumps(state)
    assert len(canonical_json(state)) <= stream.checkpoint_byte_limit
    assert stream.checkpoint_byte_limit <= 256 * 1024 * 1024


def test_stream_rejects_oversized_training_text_before_buffering() -> None:
    value = row(1)
    value["lead_text"] = "x" * (16 * 1024 + 1)
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [value], shuffle_buffer_size=1,
    )
    with pytest.raises(DatasetContractError, match="size|large"):
        next(iter(stream))
    assert stream.state_dict()["cursor"]["shuffle_buffer"] == []


def test_stream_rejects_wide_mapping_before_copying_its_entries() -> None:
    class HostileWideRow(Mapping[str, object]):
        def __len__(self) -> int:
            return 1_000_000

        def __iter__(self):
            raise AssertionError("oversized mapping must not be traversed")

        def __getitem__(self, key: str) -> object:
            raise AssertionError("oversized mapping must not be read")

    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [HostileWideRow()], shuffle_buffer_size=1,
    )
    with pytest.raises(DatasetContractError, match="fields|row"):
        next(iter(stream))


def test_state_rejects_wide_root_mapping_before_traversal() -> None:
    class HostileWideState(Mapping[str, object]):
        def __len__(self) -> int:
            return 1_000_000

        def __iter__(self):
            raise AssertionError("oversized state must not be traversed")

        def __getitem__(self, key: str) -> object:
            raise AssertionError("oversized state must not be read")

    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1)),
        load_dataset_fn=lambda *a, **k: [row(1)], shuffle_buffer_size=1,
    )
    with pytest.raises(ValueError, match="shape|values"):
        stream.load_state_dict(HostileWideState())


@pytest.mark.parametrize(
    ("declared", "physical", "message"),
    [(8, 5, "early|count"), (5, 6, "additional|count")],
)
def test_stream_rejects_physical_split_count_mismatch(
    declared: int, physical: int, message: str,
) -> None:
    unique = {item["article_key"]: item for item in (row(i) for i in range(1, 30))}
    values = list(unique.values())[:physical]
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=declared)),
        load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
        shuffle_buffer_size=2,
    )
    with pytest.raises(DatasetContractError, match=message):
        list(stream)


def test_stream_rejects_duplicate_physical_article_identity() -> None:
    unique = {item["article_key"]: item for item in (row(i) for i in range(1, 30))}
    values = list(unique.values())[:4]
    values.append(copy.deepcopy(values[0]))
    stream = load_distillation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=5)),
        load_dataset_fn=lambda *a, **k: copy.deepcopy(values),
        shuffle_buffer_size=2,
    )
    with pytest.raises(DatasetContractError, match="duplicate"):
        list(stream)


def test_resumption_reads_each_physical_row_once_at_every_cut_point() -> None:
    unique = {item["article_key"]: item for item in (row(i) for i in range(1, 30))}
    values = list(unique.values())[:8]

    class CountingRows:
        def __init__(self) -> None:
            self.reads = 0

        def __iter__(self):
            for item in values:
                self.reads += 1
                yield copy.deepcopy(item)

    def make(source: CountingRows):
        return load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(metadata(count=8)),
            load_dataset_fn=lambda *a, **k: source,
            seed=17, shuffle_buffer_size=3,
        )

    expected = [item["article_key"] for item in make(CountingRows())]
    for cut in range(9):
        original = make(CountingRows()); iterator = iter(original)
        prefix = [next(iterator)["article_key"] for _ in range(cut)]
        state = original.state_dict()
        resumed_source = CountingRows(); resumed = make(resumed_source)
        resumed.load_state_dict(state)
        assert resumed_source.reads == state["cursor"]["source_cursor"]
        actual = prefix + [item["article_key"] for item in resumed]
        assert actual == expected
        assert resumed_source.reads == 8


def test_resumable_stream_rejects_one_shot_iterators() -> None:
    source = (row(index) for index in range(1, 9))
    with pytest.raises(DatasetContractError, match="restartable"):
        load_distillation_stream(
            revision=COMMIT, token="secret", api=FakeApi(),
            load_dataset_fn=lambda *a, **k: source,
        )


def test_validation_has_explicit_separate_api() -> None:
    value = row(1, split="validation")
    stream = load_validation_stream(
        revision=COMMIT, token="secret", api=FakeApi(metadata(count=1, split="validation")),
        load_dataset_fn=lambda *a, **k: [value], shuffle_buffer_size=1,
    )
    assert next(iter(stream))["split"] == "validation"
