"""Serving-time adapter for the pinned distilled Qwen 100d encoder."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from ..contracts import DistilledServingArtifactV1


_LORA_CONFIG: dict[str, object] = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "bias": "none",
    "target_modules": ["q_proj", "v_proj"],
}


class _Artifact(Protocol):
    serving_contract: DistilledServingArtifactV1

    def path_for(self, name: str) -> Path: ...


def format_article_input(title: str, lead_text: str) -> str:
    """Apply the exact title/blank-line/lead format used during training."""
    if not isinstance(title, str) or not title.strip() or not isinstance(lead_text, str) or not lead_text.strip():
        raise ValueError("article title and lead text must be nonblank")
    return f"{title}\n\n{lead_text}"


def last_token_pool(hidden: Any, attention_mask: Any) -> Any:
    """Select the final non-padding position for either padding direction."""
    import torch

    if hidden.ndim != 3 or attention_mask.ndim != 2:
        raise ValueError("hidden and attention_mask must be rank 3 and rank 2")
    if tuple(hidden.shape[:2]) != tuple(attention_mask.shape):
        raise ValueError("hidden and attention_mask batch dimensions must match")
    positions = torch.arange(attention_mask.shape[1], device=hidden.device)
    positions = positions.unsqueeze(0).expand_as(attention_mask)
    sequence_lengths = positions.masked_fill(attention_mask == 0, -1).max(dim=1).values
    if bool(torch.any(sequence_lengths < 0)):
        raise ValueError("attention_mask contains an empty sequence")
    rows = torch.arange(hidden.shape[0], device=hidden.device)
    return hidden[rows, sequence_lengths]


def _default_tokenizer_loader(model_id: str, **kwargs: object) -> object:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_id, **kwargs)


def _default_model_loader(model_id: str, **kwargs: object) -> object:
    from transformers import AutoModel

    return AutoModel.from_pretrained(model_id, **kwargs)


def _default_adapter_loader(backbone: object, path: Path, lora: Mapping[str, object]) -> object:
    import torch
    from peft import LoraConfig, get_peft_model
    from safetensors.torch import load_file

    configuration = LoraConfig(
        task_type="FEATURE_EXTRACTION",
        inference_mode=True,
        r=int(lora["r"]),
        lora_alpha=int(lora["lora_alpha"]),
        lora_dropout=float(lora["lora_dropout"]),
        bias=str(lora["bias"]),
        target_modules=list(cast(Sequence[str], lora["target_modules"])),
    )
    adapted = get_peft_model(backbone, configuration)
    exported = load_file(str(path), device="cpu")
    destination_names = set(adapted.state_dict())
    mapped: dict[str, Any] = {}
    for name, tensor in exported.items():
        candidates = (
            name,
            name.replace("base_model.model.layers.", "base_model.model.model.layers.", 1),
        )
        destination = next((candidate for candidate in candidates if candidate in destination_names), None)
        if destination is None:
            raise ValueError(f"LoRA tensor has no serving-model destination: {name}")
        mapped[destination] = tensor
    if len(mapped) != len(exported):
        raise ValueError("LoRA tensor mapping is incomplete")
    result = adapted.load_state_dict(mapped, strict=False)
    if result.unexpected_keys:
        raise ValueError(f"LoRA load produced unexpected tensors: {result.unexpected_keys}")
    loaded = set(mapped)
    expected_adapter = {name for name in destination_names if "lora_" in name}
    if loaded != expected_adapter:
        raise ValueError("LoRA artifact does not fill the complete serving adapter")
    for parameter in adapted.parameters():
        parameter.requires_grad_(False)
    if not all(torch.isfinite(tensor).all() for tensor in mapped.values()):
        raise FloatingPointError("LoRA artifact contains non-finite weights")
    return adapted


def _default_projection_loader(path: Path, input_dimension: int, output_dimension: int) -> object:
    import torch
    from safetensors.torch import load_file

    tensors = load_file(str(path), device="cpu")
    if set(tensors) != {"weight", "bias"}:
        raise ValueError("projection must contain exactly weight and bias")
    weight, bias = tensors["weight"], tensors["bias"]
    if tuple(weight.shape) != (output_dimension, input_dimension) or tuple(bias.shape) != (output_dimension,):
        raise ValueError("projection tensor dimensions do not match serving contract")
    if weight.dtype != torch.float32 or bias.dtype != torch.float32:
        raise ValueError("projection tensors must be float32")
    if not bool(torch.isfinite(weight).all()) or not bool(torch.isfinite(bias).all()):
        raise FloatingPointError("projection contains non-finite weights")
    projection = torch.nn.Linear(input_dimension, output_dimension, bias=True)
    projection.load_state_dict({"weight": weight, "bias": bias}, strict=True)
    for parameter in projection.parameters():
        parameter.requires_grad_(False)
    return projection


class Qwen100Encoder:
    """Pinned Qwen+LoRA+projection inference returning normalized float32."""

    def __init__(
        self,
        *,
        contract: DistilledServingArtifactV1,
        tokenizer: object,
        backbone: object,
        projection: object,
        device: str = "cpu",
    ) -> None:
        import torch

        if device != "cpu" and not device.startswith("cuda"):
            raise ValueError("device must be cpu or cuda[:index]")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        if not callable(tokenizer):
            raise TypeError("tokenizer must be callable")
        setattr(tokenizer, "padding_side", contract.paddingSide)
        if getattr(tokenizer, "padding_side", None) != "left":
            raise ValueError("tokenizer must accept left padding")
        for label, module in (("backbone", backbone), ("projection", projection)):
            if not callable(getattr(module, "to", None)) or not callable(getattr(module, "eval", None)):
                raise TypeError(f"{label} must be a PyTorch module")
        self.contract = contract
        self.tokenizer = tokenizer
        self.backbone = backbone.to(device).eval()
        self.projection = projection.to(device).eval()
        self.device = device

    @classmethod
    def from_artifact(
        cls,
        artifact: _Artifact,
        *,
        token: str | None = None,
        device: str = "cpu",
        require_real_acceptance: bool = True,
        tokenizer_loader: Callable[..., object] | None = None,
        model_loader: Callable[..., object] | None = None,
        adapter_loader: Callable[[object, Path, Mapping[str, object]], object] | None = None,
        projection_loader: Callable[[Path, int, int], object] | None = None,
    ) -> "Qwen100Encoder":
        if require_real_acceptance:
            acceptance = getattr(artifact, "assert_real_acceptance", None)
            if not callable(acceptance):
                raise ValueError("artifact cannot prove real acceptance")
            acceptance()
        contract = artifact.serving_contract
        tokenizer = (tokenizer_loader or _default_tokenizer_loader)(
            contract.baseModelId,
            revision=contract.tokenizerRevision,
            token=token,
        )
        model_kwargs: dict[str, object] = {
            "revision": contract.baseModelRevision,
            "token": token,
            "attn_implementation": "sdpa",
        }
        backbone = (model_loader or _default_model_loader)(contract.baseModelId, **model_kwargs)
        configuration = getattr(backbone, "config", None)
        if configuration is not None:
            configuration.use_cache = False
        backbone = (adapter_loader or _default_adapter_loader)(
            backbone,
            artifact.path_for("adapter_model.safetensors"),
            _LORA_CONFIG,
        )
        projection = (projection_loader or _default_projection_loader)(
            artifact.path_for("projection.safetensors"),
            contract.projectionInputDimension,
            contract.embeddingDimension,
        )
        return cls(
            contract=contract,
            tokenizer=tokenizer,
            backbone=backbone,
            projection=projection,
            device=device,
        )

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
        import torch
        from torch.nn import functional as functional

        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence) or not texts:
            raise ValueError("texts must be a nonempty sequence")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("every encoded text must be nonblank")
        encoded = self.tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.contract.maxLength,
            return_tensors="pt",
        )
        if not isinstance(encoded, Mapping) or "input_ids" not in encoded or "attention_mask" not in encoded:
            raise ValueError("tokenizer must return input_ids and attention_mask")
        input_ids = encoded["input_ids"].to(self.device)
        attention_mask = encoded["attention_mask"].to(self.device)
        with torch.inference_mode():
            outputs = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
            hidden = getattr(outputs, "last_hidden_state", None)
            if hidden is None:
                try:
                    hidden = outputs[0]
                except (KeyError, IndexError, TypeError) as error:
                    raise TypeError("backbone output must contain last_hidden_state") from error
            pooled = last_token_pool(hidden, attention_mask)
            projected = self.projection(pooled.float())
            norms = projected.norm(dim=-1)
            if tuple(projected.shape) != (len(texts), 100):
                raise ValueError("projection output must have shape [batch, 100]")
            if not bool(torch.isfinite(projected).all()) or not bool(torch.all(norms > 0)):
                raise FloatingPointError("projection produced a non-finite or zero-norm vector")
            normalized = functional.normalize(projected, dim=-1).float()
        result = normalized.detach().cpu().numpy().astype(np.float32, copy=False)
        if result.shape != (len(texts), 100) or not np.isfinite(result).all():
            raise FloatingPointError("encoder output violated the finite 100d contract")
        return result


__all__ = ["Qwen100Encoder", "format_article_input", "last_token_pool"]
