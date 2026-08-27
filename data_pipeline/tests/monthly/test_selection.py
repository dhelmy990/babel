from __future__ import annotations

import pytest
import json
from pathlib import Path

from babel_data.cli import main
from babel_data.monthly.selection import (
    CandidateIdentity,
    EngineeringSnapshotPolicyV1,
    freeze_joint_selection,
)


def candidates(
    period: str,
    shared: int,
    supplement: int,
    *,
    first_page_id: int = 1,
) -> list[CandidateIdentity]:
    rows = [
        CandidateIdentity(
            period=period,
            page_id=page_id,
            canonical_title=f"Shared {page_id}",
            traffic=100_000 - page_id,
            priority=2,
        )
        for page_id in range(first_page_id, first_page_id + shared)
    ]
    base = 1_000_000 if period == "2026-06" else 2_000_000
    rows.extend(
        CandidateIdentity(
            period=period,
            page_id=base + index,
            canonical_title=f"Supplement {period} {index}",
            traffic=50_000 - index,
            priority=3,
        )
        for index in range(supplement)
    )
    return rows


def test_policy_freezes_exact_10k_with_title_independent_membership() -> None:
    policy = EngineeringSnapshotPolicyV1()
    june = candidates("2026-06", 8_500, 2_500)
    july = candidates("2026-07", 8_500, 2_500)

    result = freeze_joint_selection(
        june,
        july,
        policy=policy,
    )

    assert result.rows_per_month == 10_000
    assert len(result.shared_page_ids) == 8_000
    assert len(result.june_supplement_page_ids) == 2_000
    assert len(result.july_supplement_page_ids) == 2_000
    assert len(result.union_page_ids) == 12_000
    assert set(result.june_supplement_page_ids).isdisjoint(
        result.july_supplement_page_ids
    )
    assert 0 <= result.june_elapsed_seconds < policy.deadline_seconds
    assert 0 <= result.july_elapsed_seconds < policy.deadline_seconds
    assert result.identity_basis == "page_id"
    assert len(result.ordered_identity_sha256) == 64


def test_policy_accepts_proportional_5k_floor() -> None:
    result = freeze_joint_selection(
        candidates("2026-06", 4_000, 1_000),
        candidates("2026-07", 4_000, 1_000),
    )

    assert result.rows_per_month == 5_000
    assert len(result.shared_page_ids) == 4_000
    assert len(result.june_supplement_page_ids) == 1_000
    assert len(result.july_supplement_page_ids) == 1_000


def test_policy_rejects_below_floor_without_fixture_fallback() -> None:
    with pytest.raises(ValueError, match="below the 5,000-row emergency floor"):
        freeze_joint_selection(
            candidates("2026-06", 3_999, 1_000),
            candidates("2026-07", 3_999, 1_000),
        )


def test_policy_rejects_title_only_shared_identity() -> None:
    june = candidates("2026-06", 4_000, 1_000)
    july = candidates("2026-07", 4_000, 1_000, first_page_id=10_000)
    july[0] = CandidateIdentity(
        period="2026-07",
        page_id=99_999_999,
        canonical_title=june[0].canonical_title,
        traffic=june[0].traffic,
        priority=2,
    )

    with pytest.raises(ValueError, match="below the 5,000-row emergency floor"):
        freeze_joint_selection(
            june,
            july,
        )


def test_policy_stops_consuming_each_month_when_its_deadline_expires() -> None:
    policy = EngineeringSnapshotPolicyV1(
        target_rows=5,
        minimum_rows=5,
        deadline_seconds=3.0,
    )

    def bounded(period: str):
        yield from candidates(period, 4, 1)
        raise AssertionError(f"{period} source was consumed beyond its deadline")

    timestamps = iter(
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            3.0,
            10.0,
            10.0,
            10.0,
            10.0,
            10.0,
            13.0,
        ]
    )

    result = freeze_joint_selection(
        bounded("2026-06"),
        bounded("2026-07"),
        policy=policy,
        clock=lambda: next(timestamps),
    )

    assert result.rows_per_month == 5
    assert result.june_elapsed_seconds == 3.0
    assert result.july_elapsed_seconds == 3.0


def test_cli_freezes_candidate_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = []
    for period in ("2026-06", "2026-07"):
        path = tmp_path / f"{period}.jsonl"
        with path.open("w") as output:
            for row in candidates(period, 4_000, 1_000):
                output.write(json.dumps({
                    "period": row.period,
                    "page_id": row.page_id,
                    "canonical_title": row.canonical_title,
                    "traffic": row.traffic,
                    "priority": row.priority,
                }) + "\n")
        paths.append(path)
    result = tmp_path / "selection.json"

    assert main([
        "select-monthly-snapshot",
        "--june-candidates", str(paths[0]),
        "--july-candidates", str(paths[1]),
        "--output", str(result),
        "--target-rows", "5000",
    ]) == 0
    assert json.loads(result.read_text())["rows_per_month"] == 5_000
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
