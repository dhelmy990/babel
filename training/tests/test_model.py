from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_training.config import DistillationConfig  # noqa: E402
from babel_training.losses import distillation_loss  # noqa: E402
from babel_training.model import DistilledQwenEncoder  # noqa: E402


class TinyBackbone(nn.Module):
    def __init__(self, hidden_size: int = 1024) -> None:
        super().__init__()
        self.embedding = nn.Embedding(12, hidden_size)
        self.q_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.q_proj.register_parameter(
            "lora_A", nn.Parameter(torch.eye(hidden_size)[:1].clone())
        )
        self.events: list[str] = []
        self.config = SimpleNamespace(use_cache=True)

    def enable_input_require_grads(self) -> None:
        self.events.append("input_grads")

    def gradient_checkpointing_enable(self) -> None:
        self.events.append("checkpointing")

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> object:
        del attention_mask
        hidden = self.q_proj(self.embedding(input_ids))
        return SimpleNamespace(last_hidden_state=hidden + self.q_proj.lora_A.mean())


def batch() -> dict[str, torch.Tensor]:
    return {
        "input_ids": torch.tensor([[0, 1, 2], [3, 4, 5]]),
        "attention_mask": torch.tensor([[0, 1, 1], [1, 1, 1]]),
    }


def test_projection_is_100d_normalized_and_fp32() -> None:
    model = DistilledQwenEncoder(TinyBackbone(), hidden_size=1024)

    output = model(**batch())

    assert output.shape == (2, 100)
    assert output.dtype == torch.float32
    torch.testing.assert_close(output.norm(dim=-1), torch.ones(2))


def test_only_lora_and_projection_receive_gradients() -> None:
    model = DistilledQwenEncoder(TinyBackbone(), hidden_size=1024)

    distillation_loss(model(**batch()), torch.randn(2, 100)).total.backward()

    trainable = {
        name
        for name, parameter in model.named_parameters()
        if parameter.grad is not None
    }
    assert trainable
    assert all("lora_" in name or "projection" in name for name in trainable)
    assert any("lora_" in name for name in trainable)
    assert all(
        parameter.requires_grad == ("lora_" in name or "projection" in name)
        for name, parameter in model.named_parameters()
    )


def test_from_pretrained_pins_qwen_and_configures_lora_before_checkpointing() -> None:
    loaded: dict[str, object] = {}
    backbone = TinyBackbone()

    def loader(model_id: str, **kwargs: object) -> TinyBackbone:
        loaded.update(model_id=model_id, **kwargs)
        return backbone

    def attach(model: TinyBackbone, **kwargs: object) -> TinyBackbone:
        loaded["lora"] = kwargs
        model.events.append("lora")
        return model

    model = DistilledQwenEncoder.from_pretrained(
        DistillationConfig(), model_loader=loader, adapter_factory=attach
    )

    assert loaded["model_id"] == "Qwen/Qwen3-Embedding-0.6B"
    assert loaded["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert loaded["use_cache"] is False
    assert loaded["lora"] == {
        "target_modules": ("q_proj", "v_proj"),
        "r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "bias": "none",
    }
    assert model.backbone.config.use_cache is False
    assert backbone.events == ["lora", "input_grads", "checkpointing"]


def test_from_pretrained_rejects_a_non_pinned_model() -> None:
    config = DistillationConfig(model_id="other/model", model_revision="a" * 40)

    with pytest.raises(ValueError, match="exact pinned Qwen"):
        DistilledQwenEncoder.from_pretrained(
            config, model_loader=lambda *_args, **_kwargs: TinyBackbone()
        )


def test_from_pretrained_rejects_nonstandard_lora_settings() -> None:
    with pytest.raises(ValueError, match="exact pinned LoRA"):
        DistilledQwenEncoder.from_pretrained(DistillationConfig(lora_rank=8))


def test_model_rejects_nonfinite_projection() -> None:
    model = DistilledQwenEncoder(TinyBackbone(), hidden_size=1024)
    with torch.no_grad():
        model.projection.weight.fill_(float("nan"))

    with pytest.raises(FloatingPointError, match="non-finite"):
        model(**batch())


def test_export_components_exposes_only_projection_and_lora_tensors() -> None:
    model = DistilledQwenEncoder(TinyBackbone(), hidden_size=1024)

    projection, adapter, adapter_config = model.export_components()

    assert projection.keys() == {"weight", "bias"}
    assert adapter.keys() == {"q_proj.lora_A"}
    assert all(
        tensor.device.type == "cpu" and tensor.dtype == torch.float32
        for tensor in projection.values()
    )
    assert adapter_config == {
        "r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "bias": "none",
        "target_modules": ["q_proj", "v_proj"],
    }
