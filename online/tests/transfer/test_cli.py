from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from babel_online.transfer.cli import main
from babel_online.transfer.contracts import ORIGIN_RUN_ID, ORIGIN_TRIAL_ID


def test_export_cli_requires_explicit_arguments_and_writes_protected_receipt_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = "postgresql://user:secret@localhost/babel"
    monkeypatch.setenv("TEST_DATABASE_URL", database_url)
    calls: list[tuple] = []

    def export(url, trial_id, output_root):
        calls.append((url, trial_id, Path(output_root)))
        return SimpleNamespace(
            schemaVersion=1,
            originTrialId=ORIGIN_TRIAL_ID,
            originRunId=ORIGIN_RUN_ID,
            bundlePath=str(Path(output_root).resolve()),
            bundleDigest="a" * 64,
            rowCount=10_000,
            exportedAt="2026-08-27T03:04:05Z",
            model_dump=lambda mode=None: {
                "schemaVersion": 1,
                "originTrialId": str(ORIGIN_TRIAL_ID),
                "originRunId": str(ORIGIN_RUN_ID),
                "bundlePath": str(Path(output_root).resolve()),
                "bundleDigest": "a" * 64,
                "rowCount": 10_000,
                "exportedAt": "2026-08-27T03:04:05Z",
            },
        )

    monkeypatch.setattr("babel_online.transfer.cli.export_population", export)
    receipt = tmp_path / "private" / "export-receipt.json"
    result = main(
        [
            "export",
            "--database-url-env",
            "TEST_DATABASE_URL",
            "--trial-id",
            str(ORIGIN_TRIAL_ID),
            "--output-root",
            str(tmp_path / "bundle"),
            "--receipt",
            str(receipt),
        ]
    )

    assert result == 0
    assert calls == [(database_url, ORIGIN_TRIAL_ID, tmp_path / "bundle")]
    assert json.loads(receipt.read_text())["bundleDigest"] == "a" * 64
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert database_url not in receipt.read_text()
    assert stat.S_IMODE(receipt.parent.stat().st_mode) == 0o700


def test_export_cli_never_logs_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "postgresql://user:never-print-me@localhost/babel"
    monkeypatch.setenv("TEST_DATABASE_URL", secret)
    monkeypatch.setattr(
        "babel_online.transfer.cli.export_population",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    result = main(
        [
            "export",
            "--database-url-env",
            "TEST_DATABASE_URL",
            "--trial-id",
            str(ORIGIN_TRIAL_ID),
            "--output-root",
            str(tmp_path / "bundle"),
            "--receipt",
            str(tmp_path / "receipt.json"),
        ]
    )

    captured = capsys.readouterr()
    assert result != 0
    assert secret not in captured.out
    assert secret not in captured.err
    assert not (tmp_path / "receipt.json").exists()


def test_verify_cli_requires_trusted_digest_and_calls_closed_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "babel_online.transfer.cli.verify_bundle",
        lambda bundle, digest: calls.append((Path(bundle), digest))
        or SimpleNamespace(manifest_contract=SimpleNamespace(rowCount=10_000)),
    )

    result = main(
        [
            "verify",
            "--bundle",
            str(tmp_path / "bundle"),
            "--trusted-digest",
            "b" * 64,
        ]
    )

    assert result == 0
    assert calls == [(tmp_path / "bundle", "b" * 64)]


@pytest.mark.parametrize(
    "argv",
    [
        ["export"],
        ["verify"],
        ["export", "--database-url-env", "MISSING"],
    ],
)
def test_cli_has_no_implicit_operational_defaults(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        main(argv)


def test_export_cli_rejects_missing_named_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ABSENT_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        main(
            [
                "export",
                "--database-url-env",
                "ABSENT_DATABASE_URL",
                "--trial-id",
                str(UUID(int=1)),
                "--output-root",
                str(tmp_path / "bundle"),
                "--receipt",
                str(tmp_path / "receipt.json"),
            ]
        )
