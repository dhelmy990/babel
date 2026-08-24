"""Frozen defaults shared by the distillation training entrypoints."""

import math
import re
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class DistillationConfig:
    model_id: str = "Qwen/Qwen3-Embedding-0.6B"
    model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    teacher_dimension: int = 100
    max_length: int = 512
    lambda_rel: float = 0.5
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_targets: tuple[str, ...] = ("q_proj", "v_proj")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.teacher_dimension, int)
            or isinstance(self.teacher_dimension, bool)
            or self.teacher_dimension != 100
        ):
            raise ValueError("teacher_dimension must be exactly 100")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in (self.max_length, self.lora_rank, self.lora_alpha)
        ):
            raise ValueError("max_length, lora_rank, and lora_alpha must be positive")
        if (
            not isinstance(self.lambda_rel, Real)
            or isinstance(self.lambda_rel, bool)
            or not math.isfinite(self.lambda_rel)
            or self.lambda_rel < 0
        ):
            raise ValueError("lambda_rel must be finite and nonnegative")
        if (
            not isinstance(self.lora_dropout, Real)
            or isinstance(self.lora_dropout, bool)
            or not math.isfinite(self.lora_dropout)
            or not 0 <= self.lora_dropout < 1
        ):
            raise ValueError("lora_dropout must be finite and in [0, 1)")
        if (
            not isinstance(self.lora_targets, tuple)
            or not self.lora_targets
            or any(
                not isinstance(target, str)
                or not target
                or target != target.strip()
                for target in self.lora_targets
            )
            or len({target.casefold() for target in self.lora_targets})
            != len(self.lora_targets)
        ):
            raise ValueError(
                "lora_targets must be unique, unpadded, nonblank tuple entries"
            )
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be nonblank")
        if not isinstance(self.model_revision, str) or not re.fullmatch(
            r"[a-f0-9]{40}", self.model_revision
        ):
            raise ValueError("model_revision must be a 40-character lowercase hex SHA")
