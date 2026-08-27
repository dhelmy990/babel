from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROLLOUT = ROOT / "deploy" / "gcp" / "rollout.sh"


def _library_prelude() -> str:
    return f"BABEL_ROLLOUT_LIBRARY_ONLY=true; source {os.fspath(ROLLOUT)!r}; "


def test_term_after_promotion_runs_rollback_and_preserves_signal_status(tmp_path) -> None:
    receipt = tmp_path / "rollback-called"
    script = _library_prelude() + (
        f"PROMOTION_STARTED=true; "
        f"rollback_current_release() {{ printf called > {os.fspath(receipt)!r}; }}; "
        "install_rollout_traps; kill -TERM $$"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 143
    assert receipt.read_text() == "called"


def test_failed_rollback_surfaces_recovery_failure(tmp_path) -> None:
    receipt = tmp_path / "rollback-called"
    script = _library_prelude() + (
        f"PROMOTION_STARTED=true; "
        f"rollback_current_release() {{ printf called > {os.fspath(receipt)!r}; return 1; }}; "
        "install_rollout_traps; false"
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
            + f"acquire_rollout_lock {os.fspath(lock)!r} 2; printf 'ready\\n'; sleep 10",
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
                + f"if acquire_rollout_lock {os.fspath(lock)!r} 0; then exit 0; else exit 75; fi",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert contender.returncode == 75
    finally:
        holder.terminate()
        holder.wait(timeout=5)
