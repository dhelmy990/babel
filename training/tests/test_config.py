from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "training" / "src"))

from babel_training.config import DistillationConfig  # noqa: E402


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
