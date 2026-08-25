from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "training" / "src"))

from babel_training.config import DistillationConfig  # noqa: E402
from test_support.wheel_build import create_offline_build_environment  # noqa: E402


def test_training_defaults_are_frozen() -> None:
    cfg = DistillationConfig()

    assert cfg.model_id == "Qwen/Qwen3-Embedding-0.6B"
    assert cfg.model_revision == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert cfg.teacher_dimension == 100
    assert cfg.max_length == 512
    assert cfg.lambda_rel == 0.5
    assert cfg.lora_rank == 16
    assert cfg.lora_alpha == 32
    assert cfg.lora_dropout == 0.05
    assert cfg.lora_targets == ("q_proj", "v_proj")


def test_training_defaults_cannot_be_mutated() -> None:
    cfg = DistillationConfig()

    with pytest.raises(FrozenInstanceError):
        cfg.max_length = 256  # type: ignore[misc]


def test_training_config_accepts_valid_overrides() -> None:
    cfg = DistillationConfig(
        model_id="org/model",
        model_revision="a" * 40,
        max_length=128,
        lambda_rel=0.0,
        lora_rank=1,
        lora_alpha=1,
        lora_dropout=0.0,
        lora_targets=("q_proj",),
    )
    assert cfg == DistillationConfig(
        model_id="org/model",
        model_revision="a" * 40,
        max_length=128,
        lambda_rel=0.0,
        lora_rank=1,
        lora_alpha=1,
        lora_dropout=0.0,
        lora_targets=("q_proj",),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"teacher_dimension": 99},
        {"max_length": 0},
        {"max_length": -1},
        {"max_length": 1025},
        {"lora_rank": 0},
        {"lora_rank": -1},
        {"lora_alpha": 0},
        {"lora_alpha": -1},
        {"lambda_rel": -0.1},
        {"lambda_rel": float("nan")},
        {"lambda_rel": float("inf")},
        {"lora_dropout": -0.1},
        {"lora_dropout": 1.0},
        {"lora_dropout": float("nan")},
        {"lora_dropout": float("inf")},
        {"lora_targets": []},
        {"lora_targets": ()},
        {"lora_targets": ("q_proj", "q_proj")},
        {"lora_targets": ("q_proj", "Q_PROJ")},
        {"lora_targets": (" ",)},
        {"lora_targets": (" q_proj",)},
        {"lora_targets": ("q_proj ",)},
        {"lora_targets": ("q_proj", 1)},
        {"model_id": ""},
        {"model_id": "  "},
        {"model_id": 1},
        {"model_revision": "a" * 39},
        {"model_revision": "A" * 40},
        {"model_revision": "g" * 40},
    ],
)
def test_training_config_rejects_invalid_overrides(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DistillationConfig(**overrides)  # type: ignore[arg-type]


def test_installed_training_wheel_imports_config_outside_repository(tmp_path: Path) -> None:
    source = REPOSITORY_ROOT / "training"
    assert not (source / "build").exists()
    copied_source = tmp_path / "training"
    shutil.copytree(
        source,
        copied_source,
        ignore=shutil.ignore_patterns(
            "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache"
        ),
    )
    builder_python, environment = create_offline_build_environment(tmp_path)
    wheel_directory = tmp_path / "wheel"
    subprocess.run(
        [
            builder_python,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_directory),
            str(copied_source),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("babel_training-*.whl"))
    installed = tmp_path / "installed"
    subprocess.run(
        [
            builder_python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    outside_repository = tmp_path / "outside"
    outside_repository.mkdir()
    runtime_environment = os.environ.copy()
    runtime_environment["PYTHONPATH"] = str(installed)
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from babel_training import DistillationConfig; "
                "config = DistillationConfig(max_length=128); "
                "assert config.max_length == 128; "
                "assert config.teacher_dimension == 100"
            ),
        ],
        cwd=outside_repository,
        env=runtime_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not (source / "build").exists()
