from __future__ import annotations

import hashlib
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_LOCK = REPOSITORY_ROOT / "test_support" / "build-requirements.lock"
WHEELHOUSE = REPOSITORY_ROOT / "test_support" / "wheelhouse"


def build_system_requirements(pyproject: Path) -> set[str]:
    build_system = re.search(
        r"(?ms)^\[build-system\]\n(.*?)(?=^\[|\Z)", pyproject.read_text()
    )
    assert build_system is not None
    requires = re.search(r"(?ms)^requires\s*=\s*\[(.*?)\]", build_system.group(1))
    assert requires is not None
    return set(re.findall(r'"([^"]+)"', requires.group(1)))


def locked_build_requirements() -> dict[str, set[str]]:
    entries: dict[str, set[str]] = {}
    current_requirement: str | None = None
    for line in BUILD_LOCK.read_text().splitlines():
        if line and not line.startswith(" ") and line.endswith(" \\"):
            current_requirement = line.removesuffix(" \\")
            entries[current_requirement] = set()
        elif line.startswith("    --hash=sha256:"):
            assert current_requirement is not None
            entries[current_requirement].add(
                line.removeprefix("    --hash=sha256:").removesuffix(" \\")
            )
    return entries


def test_package_build_systems_match_hashed_local_wheelhouse() -> None:
    locked = locked_build_requirements()

    assert locked
    for package in ("data_pipeline", "training"):
        assert build_system_requirements(
            REPOSITORY_ROOT / package / "pyproject.toml"
        ) == set(locked)

    wheels = list(WHEELHOUSE.glob("*.whl"))
    assert len(wheels) == len(locked)
    for requirement, hashes in locked.items():
        distribution, version = requirement.split("==")
        matching_wheels = list(WHEELHOUSE.glob(f"{distribution}-{version}-*.whl"))
        assert len(matching_wheels) == 1
        assert hashlib.sha256(matching_wheels[0].read_bytes()).hexdigest() in hashes
