from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.full_2016 import (  # noqa: E402
    Full2016SourcePin,
    build_complete_2016,
)
from babel_data.reconcile import split_for  # noqa: E402
from babel_data.release import validate_full_release_proof  # noqa: E402
from babel_data.teacher import TeacherRecord  # noqa: E402
from babel_data.wikipedia import WikipediaPage  # noqa: E402


def _teacher(title: str, value: float = 1.0) -> TeacherRecord:
    vector = np.full(100, value, dtype=np.float32)
    vector.setflags(write=False)
    return TeacherRecord(title, vector)


def _page(title: str, page_id: int) -> WikipediaPage:
    return WikipediaPage(
        page_id=page_id,
        canonical_title=title,
        revision_id=page_id + 100,
        article_text=f"{title} lead.\n\nMore text.",
        lead_text=f"{title} lead.",
        redirect_target=None,
    )


def _pin() -> Full2016SourcePin:
    fixture_sha256 = hashlib.sha256(b"fixture").hexdigest()
    return Full2016SourcePin(
        repository="dhelmy990/babel-wikipedia-experiment",
        teacher_revision="a" * 40,
        teacher_path="sources/teacher-zip/teacher.zip",
        teacher_sha256=fixture_sha256,
        wikipedia_revision="b" * 40,
        wikipedia_path="sources/wikipedia-xml/enwiki.xml.bz2",
        wikipedia_sha256=fixture_sha256,
        token="test-token",
    )


def test_complete_builder_uses_only_pinned_hf_processing_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    teacher_path = tmp_path / "teacher.zip"
    wikipedia_path = tmp_path / "enwiki.xml.bz2"
    teacher_path.write_bytes(b"fixture")
    wikipedia_path.write_bytes(b"fixture")
    opened: list[tuple[str, str, str]] = []

    def open_pinned(
        repository: str,
        revision: str,
        path: str,
        token: str,
        cache_root: Path,
    ) -> Path:
        assert cache_root == tmp_path / "data" / "hf-cache"
        opened.append((repository, revision, path))
        return teacher_path if "teacher" in path else wikipedia_path

    monkeypatch.setattr(full, "open_processing_source", open_pinned)
    monkeypatch.setattr(full, "iter_teacher", lambda _path, audit=None: iter([_teacher("One")]))
    monkeypatch.setattr(full, "iter_wikipedia_pages", lambda _path: iter([_page("One", 1)]))

    result = build_complete_2016(
        _pin(), tmp_path / "data", tmp_path / "release", resume=True
    )

    assert opened == [
        (_pin().repository, _pin().teacher_revision, _pin().teacher_path),
        (_pin().repository, _pin().wikipedia_revision, _pin().wikipedia_path),
    ]
    assert result.teacher_total == result.matched + result.excluded == 1
    assert result.rows_written == result.matched == 1
    assert result.duplicate_article_keys == result.invalid_vector_count == 0


def test_complete_builder_accounts_for_matches_and_explicit_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(
        full,
        "iter_teacher",
        lambda _path, audit=None: iter([_teacher("One"), _teacher("Missing")]),
    )
    monkeypatch.setattr(full, "iter_wikipedia_pages", lambda _path: iter([_page("One", 1)]))

    result = build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")

    assert result.teacher_total == 2
    assert result.matched == result.rows_written == 1
    assert result.excluded == 1
    exclusions = [json.loads(line) for line in result.exclusion_ledger.read_text().splitlines()]
    assert exclusions == [
        {
            "detail": "no exact normalized-title page in the pinned snapshot",
            "normalized_title": "Missing",
            "reason": "title_not_found",
            "teacher_title": "Missing",
        }
    ]
    assert result.split_counts[split_for("enwiki:2016-10-01:1")] == 1


def test_complete_builder_explicitly_excludes_empty_selected_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    empty = _page("Empty", 2)
    empty = WikipediaPage(
        page_id=empty.page_id,
        canonical_title=empty.canonical_title,
        revision_id=empty.revision_id,
        article_text="",
        lead_text="",
        redirect_target=None,
    )
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(
        full,
        "iter_teacher",
        lambda _path, audit=None: iter([_teacher("One"), _teacher("Empty")]),
    )
    monkeypatch.setattr(
        full,
        "iter_wikipedia_pages",
        lambda _path: iter([_page("One", 1), empty]),
    )

    result = build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")

    assert result.teacher_total == result.matched + result.excluded == 2
    assert result.exclusion_counts == {"empty_text": 1}


def test_resume_after_interruption_is_idempotent_and_does_not_duplicate_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(
        full,
        "iter_teacher",
        lambda _path, audit=None: iter([_teacher("One"), _teacher("Two")]),
    )
    real_pages = lambda _path: iter([_page("One", 1), _page("Two", 2)])
    calls = 0

    def interrupted_pages(path: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _page("One", 1)
            raise RuntimeError("simulated interruption")
        yield from real_pages(path)

    monkeypatch.setattr(full, "iter_wikipedia_pages", interrupted_pages)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")

    resumed = build_complete_2016(
        _pin(), tmp_path / "data", tmp_path / "release", resume=True
    )
    repeated = build_complete_2016(
        _pin(), tmp_path / "data", tmp_path / "release", resume=True
    )

    assert resumed == repeated
    assert resumed.rows_written == resumed.matched == 2
    assert resumed.duplicate_article_keys == 0
    journals = [json.loads(line) for line in resumed.range_journal.read_text().splitlines()]
    assert len({item["range_id"] for item in journals}) == len(journals)


def test_resume_adopts_proof_valid_shards_after_receipt_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(full, "iter_teacher", lambda _path, audit=None: iter([_teacher("One")]))
    monkeypatch.setattr(full, "iter_wikipedia_pages", lambda _path: iter([_page("One", 1)]))
    real_writer = full.write_complete_shards
    calls = 0

    def interrupted_writer(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        value = real_writer(*args, **kwargs)
        raise RuntimeError("interrupted after durable shard publish")

    monkeypatch.setattr(full, "write_complete_shards", interrupted_writer)
    with pytest.raises(RuntimeError, match="durable shard publish"):
        build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")

    monkeypatch.setattr(full, "write_complete_shards", real_writer)
    resumed = build_complete_2016(
        _pin(), tmp_path / "data", tmp_path / "release", resume=True
    )

    assert calls == 1
    assert resumed.rows_written == resumed.matched == 1
    validate_full_release_proof(
        json.loads(resumed.full_release_proof.read_text()),
        json.loads(resumed.manifest_path.read_text()),
    )


def test_complete_readiness_requires_inventory_count_digest_and_remote_proofs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(full, "iter_teacher", lambda _path, audit=None: iter([_teacher("One")]))
    monkeypatch.setattr(full, "iter_wikipedia_pages", lambda _path: iter([_page("One", 1)]))

    local = build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")
    assert local.readiness_state == "building"
    assert local.remote_commit_sha is None
    assert local.publication_commits == ()
    manifest = json.loads(local.manifest_path.read_text())
    proof = json.loads(local.full_release_proof.read_text())
    validate_full_release_proof(proof, manifest)


def test_complete_builder_rejects_pinned_cache_checksum_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"different")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)

    with pytest.raises(ValueError, match="pinned teacher SHA-256"):
        build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")


def test_complete_builder_uses_all_row_spool_not_pilot_sampler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.full_2016 as full

    pinned = tmp_path / "pinned"
    pinned.write_bytes(b"fixture")
    monkeypatch.setattr(full, "open_processing_source", lambda *args, **kwargs: pinned)
    monkeypatch.setattr(
        full,
        "iter_teacher",
        lambda _path, audit=None: iter([_teacher(f"Page {index}") for index in range(12)]),
    )
    monkeypatch.setattr(
        full,
        "iter_wikipedia_pages",
        lambda _path: iter([_page(f"Page {index}", index + 1) for index in range(12)]),
    )
    monkeypatch.setattr(
        full,
        "write_shards",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pilot sampler must not serve complete builds")
        ),
        raising=False,
    )

    result = build_complete_2016(_pin(), tmp_path / "data", tmp_path / "release")

    assert result.rows_written == 12
