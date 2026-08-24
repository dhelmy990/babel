"""Frozen defaults shared by the distillation training entrypoints."""

from dataclasses import dataclass


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
