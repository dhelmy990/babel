"""Pinned Qwen student encoder with an injected CPU-test seam."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Self

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .config import DistillationConfig
from .pooling import last_token_pool


QWEN_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
QWEN_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
QWEN_HIDDEN_SIZE = 1024


def _default_model_loader(model_id: str, **kwargs: object) -> nn.Module:
    from transformers import AutoModel

    return AutoModel.from_pretrained(model_id, **kwargs)


def _default_adapter_factory(backbone: nn.Module, **kwargs: object) -> nn.Module:
    from peft import LoraConfig, get_peft_model

    lora = LoraConfig(task_type="FEATURE_EXTRACTION", **kwargs)
    return get_peft_model(backbone, lora)


class DistilledQwenEncoder(nn.Module):
    """Pool Qwen's final real token and project it into teacher space."""

    def __init__(
        self,
        backbone: nn.Module,
        *,
        hidden_size: int = QWEN_HIDDEN_SIZE,
        teacher_dimension: int = 100,
    ) -> None:
        super().__init__()
        if hidden_size <= 0 or teacher_dimension <= 0:
            raise ValueError("model dimensions must be positive")
        self.backbone = backbone
        for name, parameter in self.backbone.named_parameters():
            parameter.requires_grad_("lora_" in name)
        self.projection = nn.Linear(hidden_size, teacher_dimension)
        self._assert_trainable_parameter_gate()

    @classmethod
    def from_pretrained(
        cls,
        config: DistillationConfig | None = None,
        *,
        model_loader: Callable[..., nn.Module] | None = None,
        adapter_factory: Callable[..., nn.Module] | None = None,
    ) -> Self:
        """Build the exact pinned Qwen/LoRA student without reading a token."""
        config = config or DistillationConfig()
        if config.model_id != QWEN_MODEL_ID or config.model_revision != QWEN_REVISION:
            raise ValueError("student must use the exact pinned Qwen model and revision")
        if (
            config.lora_rank != 16
            or config.lora_alpha != 32
            or config.lora_dropout != 0.05
            or config.lora_targets != ("q_proj", "v_proj")
        ):
            raise ValueError("student must use the exact pinned LoRA configuration")
        loader = model_loader or _default_model_loader
        attach_adapter = adapter_factory or _default_adapter_factory
        backbone = loader(
            config.model_id,
            revision=config.model_revision,
            use_cache=False,
            attn_implementation="sdpa",
        )
        backbone = attach_adapter(
            backbone,
            target_modules=config.lora_targets,
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
        )
        backbone_config = getattr(backbone, "config", None)
        if backbone_config is not None:
            backbone_config.use_cache = False
        enable_inputs = getattr(backbone, "enable_input_require_grads", None)
        enable_checkpointing = getattr(backbone, "gradient_checkpointing_enable", None)
        if not callable(enable_inputs) or not callable(enable_checkpointing):
            raise TypeError("backbone must support input gradients and gradient checkpointing")
        enable_inputs()
        enable_checkpointing()
        return cls(
            backbone,
            hidden_size=QWEN_HIDDEN_SIZE,
            teacher_dimension=config.teacher_dimension,
        )

    def _assert_trainable_parameter_gate(self) -> None:
        violations = [
            name
            for name, parameter in self.named_parameters()
            if parameter.requires_grad
            and "lora_" not in name
            and not name.startswith("projection.")
        ]
        if violations:
            raise RuntimeError(f"unexpected trainable parameters: {violations}")

    def export_components(
        self,
    ) -> tuple[dict[str, Tensor], dict[str, Tensor], dict[str, object]]:
        """Return exporter-ready projection, adapter weights, and LoRA config."""
        projection = {
            "weight": self.projection.weight.detach().float().cpu().contiguous(),
            "bias": self.projection.bias.detach().float().cpu().contiguous(),
        }
        adapter: dict[str, Tensor] = {}
        for name, parameter in self.backbone.named_parameters():
            if "lora_" not in name:
                continue
            export_name = name.replace(
                "base_model.model.model.layers.", "base_model.model.layers."
            )
            adapter[export_name] = parameter.detach().float().cpu().contiguous()
        if not adapter:
            raise RuntimeError("student has no LoRA adapter tensors to export")
        adapter_config: dict[str, object] = {
            "r": 16,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "bias": "none",
            "target_modules": ["q_proj", "v_proj"],
        }
        return projection, adapter, adapter_config

    def forward(
        self, input_ids: Tensor, attention_mask: Tensor, **backbone_kwargs: Any
    ) -> Tensor:
        outputs = self.backbone(
            input_ids=input_ids,
            attention_mask=attention_mask,
            **backbone_kwargs,
        )
        hidden = getattr(outputs, "last_hidden_state", None)
        if hidden is None:
            try:
                hidden = outputs[0]
            except (KeyError, IndexError, TypeError) as error:
                raise TypeError("backbone output must contain last_hidden_state") from error
        pooled = last_token_pool(hidden, attention_mask)
        projected = self.projection(pooled.float())
        norms = projected.norm(dim=-1)
        if not bool(torch.isfinite(projected).all()) or not bool(torch.all(norms > 0)):
            raise FloatingPointError("model projection contains non-finite or zero-norm rows")
        return F.normalize(projected, dim=-1)


__all__ = [
    "DistilledQwenEncoder",
    "QWEN_HIDDEN_SIZE",
    "QWEN_MODEL_ID",
    "QWEN_REVISION",
]
