"""Strict title-and-lead collation for the pinned Qwen tokenizer."""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .data import validate_distillation_row


class DistillationCollator:
    """Build a left-padded PyTorch batch without exposing hidden/full text."""

    def __init__(self, tokenizer: object, *, max_length: int) -> None:
        if (
            not isinstance(max_length, int)
            or isinstance(max_length, bool)
            or not 1 <= max_length <= 1024
        ):
            raise ValueError("max_length must be an integer in [1, 1024]")
        if not callable(tokenizer):
            raise TypeError("tokenizer must be callable")
        try:
            setattr(tokenizer, "padding_side", "left")
        except BaseException as error:
            raise ValueError("tokenizer must support explicit left padding") from error
        if getattr(tokenizer, "padding_side", None) != "left":
            raise ValueError("tokenizer did not accept explicit left padding")
        self.tokenizer = tokenizer
        self.max_length = max_length

    @staticmethod
    def _rows(value: object, *, label: str) -> list[list[int]]:
        converter = getattr(value, "tolist", None)
        if callable(converter):
            value = converter()
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError(f"tokenizer {label} must be a nonempty 2D value")
        checked: list[list[int]] = []
        for row in value:
            converter = getattr(row, "tolist", None)
            if callable(converter):
                row = converter()
            if not isinstance(row, (list, tuple)) or not row:
                raise ValueError(f"tokenizer {label} must be a nonempty 2D value")
            if any(not isinstance(item, int) or isinstance(item, bool) for item in row):
                raise ValueError(f"tokenizer {label} must contain integers")
            checked.append([int(item) for item in row])
        if len({len(row) for row in checked}) != 1:
            raise ValueError(f"tokenizer {label} must be rectangular")
        return checked

    def __call__(self, examples: Sequence[Mapping[str, object]]) -> dict[str, Any]:
        if not isinstance(examples, Sequence) or isinstance(examples, (str, bytes)) or not examples:
            raise ValueError("collator batch must be nonempty")
        checked = [validate_distillation_row(example) for example in examples]
        texts = [
            str(example["canonical_title"]) + "\n\n" + str(example["lead_text"])
            for example in checked
        ]
        if getattr(self.tokenizer, "padding_side", None) != "left":
            raise ValueError("shared tokenizer padding_side changed after collator setup")
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors=None,
        )
        if not isinstance(encoded, Mapping) or set(encoded) != {"input_ids", "attention_mask"}:
            raise ValueError("tokenizer must return exactly input_ids and attention_mask")
        input_ids = self._rows(encoded["input_ids"], label="input_ids")
        attention_mask = self._rows(encoded["attention_mask"], label="attention_mask")
        batch_size = len(checked)
        if (
            len(input_ids) != batch_size
            or len(attention_mask) != batch_size
            or len(input_ids[0]) != len(attention_mask[0])
            or len(input_ids[0]) > self.max_length
        ):
            raise ValueError("tokenizer tensors have invalid batch shape or length")
        pad_token_id = getattr(self.tokenizer, "pad_token_id", None)
        for ids, mask in zip(input_ids, attention_mask, strict=True):
            if len(ids) != len(input_ids[0]) or len(mask) != len(ids):
                raise ValueError("tokenizer tensors have inconsistent shape")
            if any(bit not in (0, 1) for bit in mask) or 1 not in mask:
                raise ValueError("tokenizer attention_mask is invalid")
            first_token = mask.index(1)
            if mask != [0] * first_token + [1] * (len(mask) - first_token):
                raise ValueError("tokenizer output does not use left padding")
            if first_token:
                if not isinstance(pad_token_id, int) or isinstance(pad_token_id, bool):
                    raise ValueError("tokenizer pad_token_id is required for left padding")
                if ids[:first_token] != [pad_token_id] * first_token:
                    raise ValueError("tokenizer input_ids do not match left padding mask")
        torch = importlib.import_module("torch")
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            teacher_vectors = np.asarray(
                [example["teacher_vector"] for example in checked], dtype=np.float32
            )
            teacher_norms = np.asarray(
                [example["teacher_norm"] for example in checked], dtype=np.float32
            )
        float32_norms = np.sqrt(
            np.sum(teacher_vectors.astype(np.float64) ** 2, axis=1)
        )
        if (
            teacher_vectors.shape != (batch_size, 100)
            or teacher_norms.shape != (batch_size,)
            or not np.isfinite(teacher_vectors).all()
            or not np.isfinite(teacher_norms).all()
            or np.any(teacher_norms <= 0)
            or not np.isfinite(float32_norms).all()
            or np.any(float32_norms <= 0)
            or not np.allclose(
                teacher_norms.astype(np.float64),
                float32_norms,
                rtol=1e-6,
                atol=1e-7,
            )
        ):
            raise ValueError("teacher vectors and norms must remain valid in float32")
        teacher_vector_tensor = torch.as_tensor(teacher_vectors, dtype=torch.float32)
        teacher_norm_tensor = torch.as_tensor(teacher_norms, dtype=torch.float32)
        if not bool(torch.isfinite(teacher_vector_tensor).all()) or not bool(
            torch.isfinite(teacher_norm_tensor).all()
        ):
            raise ValueError("teacher tensors must be finite after float32 conversion")
        return {
            "input_ids": torch.as_tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.as_tensor(attention_mask, dtype=torch.long),
            "teacher_vector": teacher_vector_tensor,
            "teacher_norm": teacher_norm_tensor,
            "article_key": tuple(str(example["article_key"]) for example in checked),
            "page_id": tuple(int(example["page_id"]) for example in checked),
            "split": tuple(str(example["split"]) for example in checked),
        }


__all__ = ["DistillationCollator"]
