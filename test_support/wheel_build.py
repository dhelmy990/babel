from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SUPPORT_DIRECTORY = Path(__file__).resolve().parent
BUILD_REQUIREMENTS = SUPPORT_DIRECTORY / "build-requirements.lock"
BUILD_WHEELHOUSE = SUPPORT_DIRECTORY / "wheelhouse"


def create_offline_build_environment(parent: Path) -> tuple[Path, dict[str, str]]:
    builder = parent / "builder"
    subprocess.run([sys.executable, "-m", "venv", str(builder)], check=True)
    builder_python = builder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = os.environ.copy()
    environment.update({
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_FIND_LINKS": str(BUILD_WHEELHOUSE),
        "PIP_NO_INDEX": "1",
        "PYTHONNOUSERSITE": "1",
    })
    subprocess.run(
        [
            builder_python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(BUILD_WHEELHOUSE),
            "--no-deps",
            "--require-hashes",
            "-r",
            str(BUILD_REQUIREMENTS),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            builder_python,
            "-c",
            (
                "from importlib.metadata import version; "
                "assert version('setuptools') == '75.8.0'; "
                "assert version('wheel') == '0.45.1'"
            ),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return builder_python, environment
