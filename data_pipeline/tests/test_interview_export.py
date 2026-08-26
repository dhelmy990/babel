from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "data_pipeline" / "src"))

from babel_data.interview_export import (  # noqa: E402
    INTERVIEW_CONFIG,
    InterviewSelectionV1,
    SelectedIdentity,
    freeze_frontier,
    select_interview_ids,
    write_interview_release,
)
from babel_data.reconcile import SNAPSHOT_DATE, split_for  # noqa: E402
from babel_data.shard import PARQUET_SCHEMA  # noqa: E402
from babel_data.cli import main  # noqa: E402


SMALL_COUNTS = {"train": 8, "validation": 2, "test": 2}


def _article_key(page_id: int) -> str:
    return f"enwiki:{SNAPSHOT_DATE}:{page_id}"


def _create_database(path: Path, *, rows_per_split: int = 16) -> list[int]:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE teacher (
          position INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          normalized TEXT NOT NULL,
          vector BLOB NOT NULL,
          norm REAL NOT NULL,
          status TEXT,
          detail TEXT,
          page_id INTEGER,
          rank TEXT
        );
        CREATE TABLE selected_text (
          page_id INTEGER PRIMARY KEY,
          canonical_title TEXT NOT NULL,
          revision_id INTEGER,
          lead_text TEXT NOT NULL,
          article_text TEXT NOT NULL
        );
        CREATE TABLE journal (
          range_id TEXT PRIMARY KEY,
          phase TEXT NOT NULL,
          start_row INTEGER NOT NULL,
          end_row INTEGER NOT NULL,
          row_count INTEGER NOT NULL
        );
        """
    )
    counts = {"train": 0, "validation": 0, "test": 0}
    page_ids: list[int] = []
    page_id = 1
    while min(counts.values()) < rows_per_split:
        key = _article_key(page_id)
        split = split_for(key)
        if counts[split] < rows_per_split:
            vector = np.full(100, page_id / 10_000, dtype=np.float32)
            connection.execute(
                "INSERT INTO teacher VALUES (?,?,?,?,?,'matched','',?,?)",
                (
                    len(page_ids) + 1,
                    f"Title {page_id}",
                    f"title {page_id}",
                    vector.tobytes(),
                    float(np.linalg.norm(vector)),
                    page_id,
                    hashlib.sha256(key.encode()).hexdigest(),
                ),
            )
            connection.execute(
                "INSERT INTO selected_text VALUES (?,?,?,?,?)",
                (
                    page_id,
                    f"Article {page_id}",
                    page_id + 100,
                    f"Lead {page_id}",
                    f"Article text {page_id}",
                ),
            )
            counts[split] += 1
            page_ids.append(page_id)
        page_id += 1
    connection.execute(
        "INSERT INTO journal VALUES (?,?,?,?,?)",
        ("selected-text:000000000001", "selected-text", 1, len(page_ids), len(page_ids)),
    )
    connection.commit()
    connection.close()
    return page_ids


@pytest.fixture
def reconciliation_db(tmp_path: Path) -> Path:
    path = tmp_path / "reconcile.sqlite3"
    _create_database(path)
    return path


def test_freeze_and_select_exact_split_preserving_hash_sample(
    reconciliation_db: Path,
) -> None:
    frontier = freeze_frontier(reconciliation_db)

    assert frontier.selected_count == 48
    assert frontier.max_selected_text_journal_row == 48
    assert frontier.max_page_id > 0
    assert frontier.database_path == str(reconciliation_db.resolve())
    assert len(frontier.database_identity_sha256) == 64

    selection = select_interview_ids(
        reconciliation_db,
        frontier,
        batch_size=7,
        required_counts=SMALL_COUNTS,
        smoke_size=3,
    )

    assert selection.counts == SMALL_COUNTS
    assert selection.smoke == selection.train[:3]
    assert len({item.article_key for item in selection.all_identities}) == 12
    for split in ("train", "validation", "test"):
        identities = getattr(selection, split)
        assert all(split_for(item.article_key) == split for item in identities)
        assert list(identities) == sorted(
            identities, key=lambda item: (item.rank_sha256, item.article_key)
        )
        expected = sorted(
            (
                (
                    hashlib.sha256(
                        b"babel-interview-2016-v1\0" + _article_key(page_id).encode()
                    ).hexdigest(),
                    _article_key(page_id),
                    page_id,
                )
                for page_id in _all_page_ids(reconciliation_db)
                if split_for(_article_key(page_id)) == split
            )
        )[: SMALL_COUNTS[split]]
        assert [
            (item.rank_sha256, item.article_key, item.page_id) for item in identities
        ] == expected


def _all_page_ids(path: Path) -> list[int]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [int(row[0]) for row in connection.execute("SELECT page_id FROM selected_text")]
    finally:
        connection.close()


def test_selection_closes_each_wal_reader_before_heap_work(
    reconciliation_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import babel_data.interview_export as interview_export

    frontier = freeze_frontier(reconciliation_db)
    writer = sqlite3.connect(reconciliation_db)
    observations: list[tuple[int, int, int]] = []
    original_rank = interview_export._rank_identity

    def rank_after_reader_closed(article_key: str) -> str:
        observations.append(
            tuple(int(value) for value in writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
        )
        return original_rank(article_key)

    monkeypatch.setattr(interview_export, "_rank_identity", rank_after_reader_closed)
    try:
        select_interview_ids(
            reconciliation_db,
            frontier,
            batch_size=5,
            required_counts=SMALL_COUNTS,
            smoke_size=3,
        )
    finally:
        writer.close()

    assert len(observations) >= frontier.selected_count
    assert all(busy == 0 for busy, _, _ in observations)


def test_selection_rejects_non_monotonic_insert_inside_frozen_frontier(
    reconciliation_db: Path,
) -> None:
    frontier = freeze_frontier(reconciliation_db)
    existing = set(_all_page_ids(reconciliation_db))
    missing_page = next(
        page_id for page_id in range(1, frontier.max_page_id) if page_id not in existing
    )
    key = _article_key(missing_page)
    connection = sqlite3.connect(reconciliation_db)
    vector = np.ones(100, dtype=np.float32)
    connection.execute(
        "INSERT INTO teacher VALUES (?,?,?,?,?,'matched','',?,?)",
        (9999, "Late", "late", vector.tobytes(), 10.0, missing_page, "late"),
    )
    connection.execute(
        "INSERT INTO selected_text VALUES (?,?,?,?,?)",
        (missing_page, "Late", None, "Late lead", "Late text"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="frozen frontier changed"):
        select_interview_ids(
            reconciliation_db,
            frontier,
            batch_size=5,
            required_counts=SMALL_COUNTS,
            smoke_size=3,
        )


def test_write_release_is_deterministic_and_discloses_partial_frontier(
    reconciliation_db: Path, tmp_path: Path
) -> None:
    frontier = freeze_frontier(reconciliation_db)
    selection = select_interview_ids(
        reconciliation_db,
        frontier,
        required_counts=SMALL_COUNTS,
        smoke_size=3,
    )

    result = write_interview_release(
        reconciliation_db,
        frontier,
        selection,
        tmp_path / "prepared",
        source_sha256={"teacher": "a" * 64, "wikipedia": "b" * 64},
        source_revisions={"teacher": "d" * 40, "wikipedia": "e" * 40},
        code_commit="c" * 40,
    )

    manifest = json.loads(result.manifest_path.read_text())
    readiness = json.loads(result.readiness_path.read_text())
    assert manifest["dataset_config"] == INTERVIEW_CONFIG
    assert manifest["counts"] == {"total": 12, **SMALL_COUNTS}
    assert manifest["selection"]["seed"] == "babel-interview-2016-v1"
    assert manifest["selection"]["smoke_article_keys"] == [
        item.article_key for item in selection.smoke
    ]
    assert manifest["frontier"]["complete_corpus"] is False
    assert manifest["frontier"]["selected_count"] == 48
    assert manifest["source_sha256"] == {"teacher": "a" * 64, "wikipedia": "b" * 64}
    assert manifest["source_revisions"] == {"teacher": "d" * 40, "wikipedia": "e" * 40}
    assert readiness["state"] == "interview_ready"
    assert readiness["available_examples"] == 12
    assert readiness["remote_verified"] is False
    for shard in result.shards:
        parquet = pq.ParquetFile(result.output_root / shard.path)
        assert parquet.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True)
        rows = parquet.read().to_pylist()
        assert len(rows) == SMALL_COUNTS[shard.split]
        assert all(len(row["teacher_vector"]) == 100 for row in rows)
        assert all(np.isfinite(row["teacher_vector"]).all() for row in rows)

    with pytest.raises(FileExistsError):
        write_interview_release(
            reconciliation_db,
            frontier,
            selection,
            tmp_path / "prepared",
            source_sha256={"teacher": "a" * 64, "wikipedia": "b" * 64},
            code_commit="c" * 40,
        )


@pytest.mark.parametrize("corruption", ["missing_text", "nonfinite", "wrong_dimension"])
def test_write_release_rejects_invalid_selected_rows(
    reconciliation_db: Path, tmp_path: Path, corruption: str
) -> None:
    frontier = freeze_frontier(reconciliation_db)
    selection = select_interview_ids(
        reconciliation_db,
        frontier,
        required_counts=SMALL_COUNTS,
        smoke_size=3,
    )
    page_id = selection.train[0].page_id
    connection = sqlite3.connect(reconciliation_db)
    if corruption == "missing_text":
        connection.execute("UPDATE selected_text SET lead_text='' WHERE page_id=?", (page_id,))
    else:
        vector = np.ones(100 if corruption == "nonfinite" else 99, dtype=np.float32)
        if corruption == "nonfinite":
            vector[4] = np.nan
        connection.execute("UPDATE teacher SET vector=? WHERE page_id=?", (vector.tobytes(), page_id))
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="text|finite|100"):
        write_interview_release(
            reconciliation_db,
            frontier,
            selection,
            tmp_path / corruption,
            source_sha256={"teacher": "a" * 64, "wikipedia": "b" * 64},
            code_commit="c" * 40,
        )


def test_write_release_rejects_duplicate_article_keys(
    reconciliation_db: Path, tmp_path: Path
) -> None:
    frontier = freeze_frontier(reconciliation_db)
    selection = select_interview_ids(
        reconciliation_db,
        frontier,
        required_counts=SMALL_COUNTS,
        smoke_size=3,
    )
    duplicate = InterviewSelectionV1(
        seed=selection.seed,
        train=selection.train[:-1] + (selection.train[0],),
        validation=selection.validation,
        test=selection.test,
        smoke=selection.smoke,
        ordered_sha256=selection.ordered_sha256,
    )

    with pytest.raises(ValueError, match="duplicate article_key"):
        write_interview_release(
            reconciliation_db,
            frontier,
            duplicate,
            tmp_path / "duplicate",
            source_sha256={"teacher": "a" * 64, "wikipedia": "b" * 64},
            code_commit="c" * 40,
        )


def test_selection_requires_sufficient_committed_rows(reconciliation_db: Path) -> None:
    frontier = freeze_frontier(reconciliation_db)
    with pytest.raises(ValueError, match="insufficient test"):
        select_interview_ids(
            reconciliation_db,
            frontier,
            required_counts={"train": 8, "validation": 2, "test": 999},
            smoke_size=3,
        )


def test_selected_identity_rejects_rank_or_article_key_drift() -> None:
    with pytest.raises(ValueError, match="rank"):
        SelectedIdentity("0" * 64, _article_key(1), 1)


def test_export_interview_cli_emits_only_safe_frontier_and_checksums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    frontier = SimpleNamespace(
        to_document=lambda: {
            "selected_count": 123,
            "max_page_id": 456,
            "max_selected_text_journal_row": 123,
        }
    )
    selection = SimpleNamespace(
        counts={"train": 50_000, "validation": 5_000, "test": 5_000},
        ordered_sha256={"train": "a" * 64, "validation": "b" * 64, "test": "c" * 64},
    )
    result = SimpleNamespace(
        manifest_path=tmp_path / "manifest.json",
        readiness_path=tmp_path / "readiness.json",
    )
    monkeypatch.setattr(cli, "freeze_frontier", lambda path: frontier, raising=False)
    monkeypatch.setattr(cli, "select_interview_ids", lambda path, value: selection, raising=False)
    monkeypatch.setattr(
        cli,
        "write_interview_release",
        lambda *args, **kwargs: result,
        raising=False,
    )

    assert main(
        [
            "export-interview-2016",
            "--database",
            str(tmp_path / "reconcile.sqlite3"),
            "--output-root",
            str(tmp_path / "prepared"),
            "--code-commit",
            "d" * 40,
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"] == "export-interview-2016"
    assert output["counts"] == selection.counts
    assert output["ordered_sha256"] == selection.ordered_sha256
    assert "token" not in json.dumps(output).lower()


def test_publish_interview_cli_reads_token_only_from_fixed_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import babel_data.cli as cli

    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=never-print-this-interview-token\n")
    monkeypatch.setattr(cli, "INTERVIEW_ENV_FILE", env_file, raising=False)
    monkeypatch.setattr(cli, "_api", object)

    def publish(*args: object, **kwargs: object) -> str:
        assert args[3] == "never-print-this-interview-token"
        return "e" * 40

    monkeypatch.setattr(cli, "publish_interview_configuration", publish, raising=False)
    assert main(
        [
            "publish-interview-2016",
            "--input-root",
            str(tmp_path / "prepared"),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["revision"] == "e" * 40
    assert "never-print-this-interview-token" not in output
