from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ROLLOUT = ROOT / "deploy" / "gcp" / "rollout.sh"
SUPERVISOR = ROOT / "deploy" / "gcp" / "rollout_supervisor.sh"


def _library_prelude() -> str:
    return f"source {os.fspath(SUPERVISOR)!r}; "


def test_term_after_promotion_runs_rollback_and_preserves_signal_status(tmp_path) -> None:
    receipt = tmp_path / "rollback-called"
    script = _library_prelude() + (
        f"PROMOTION_STARTED=true; "
        f"rollback_current_release() {{ printf called > {os.fspath(receipt)!r}; }}; "
        "babel_install_rollout_traps; kill -TERM $$"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 143
    assert receipt.read_text() == "called"


def test_failed_rollback_surfaces_recovery_failure(tmp_path) -> None:
    receipt = tmp_path / "rollback-called"
    script = _library_prelude() + (
        f"PROMOTION_STARTED=true; "
        f"rollback_current_release() {{ printf called > {os.fspath(receipt)!r}; return 1; }}; "
        "babel_install_rollout_traps; false"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 70
    assert receipt.read_text() == "called"


def test_rollout_lock_rejects_a_concurrent_owner(tmp_path) -> None:
    lock = tmp_path / "rollout.lock"
    holder = subprocess.Popen(
        [
            "bash",
            "-c",
            _library_prelude()
            + f"babel_acquire_rollout_lock {os.fspath(lock)!r} 2; printf 'ready\\n'; sleep 10",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        contender = subprocess.run(
            [
                "bash",
                "-c",
                _library_prelude()
                + f"if babel_acquire_rollout_lock {os.fspath(lock)!r} 0; then exit 0; else exit 75; fi",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert contender.returncode == 75
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_rollout_lock_open_failure_cannot_reuse_inherited_fd_nine(tmp_path) -> None:
    inherited = tmp_path / "unrelated.lock"
    missing = tmp_path / "missing" / "rollout.lock"
    script = (
        f"exec 9>{os.fspath(inherited)!r}; "
        + _library_prelude()
        + f"if babel_acquire_rollout_lock {os.fspath(missing)!r} 0; "
        "then exit 0; else exit 75; fi"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 75


def _restore_script(tmp_path: Path, *, failure: str) -> tuple[str, Path]:
    previous = tmp_path / "previous"
    candidate = tmp_path / "candidate"
    ready = tmp_path / "trainer-ready.json"
    current = tmp_path / "current"
    previous.mkdir()
    candidate.mkdir()
    current.symlink_to(previous)
    common = _library_prelude() + (
        f"compose_for() {{ return 0; }}; "
        f"date() {{ printf 123; }}; "
        f"verify_release() {{ return 0; }}; "
    )
    if failure == "readiness-remove":
        common += "rm() { return 1; }; "
    elif failure == "attestation":
        common += "verify_release() { return 1; }; "
    elif failure == "link":
        common += "ln() { return 1; }; "
    elif failure == "move":
        common += "mv() { return 1; }; "
    command = common + "if " + (
        f"babel_restore_previous_release {os.fspath(candidate)!r} "
        f"{os.fspath(previous)!r} {os.fspath(ready)!r} {os.fspath(current)!r}; "
        "then exit 0; else exit 71; fi"
    )
    return command, current


@pytest.mark.parametrize(
    "failure", ["readiness-remove", "attestation", "link", "move"]
)
def test_restore_failure_never_repoints_current_or_reports_success(
    tmp_path, failure: str
) -> None:
    command, current = _restore_script(tmp_path, failure=failure)
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    # The function is deliberately called in an `if`, reproducing the Bash
    # context that suppresses implicit errexit inside functions.
    assert result.returncode == 71
    assert current.resolve() == tmp_path / "previous"
