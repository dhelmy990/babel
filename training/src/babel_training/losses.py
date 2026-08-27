"""FP32 objectives for vector distillation."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor
from torch.nn import functional as F


class NonFiniteLoss(FloatingPointError):
    """The distillation objective produced a non-finite value."""


@dataclass(frozen=True)
class LossBreakdown:
    total: Tensor
    vector: Tensor
    relational: Tensor


def distillation_loss(
    student: Tensor, teacher: Tensor, lambda_rel: float = 0.5
) -> LossBreakdown:
    """Compute vector cosine loss plus relational similarity MSE in FP32."""
    if student.ndim != 2 or student.shape != teacher.shape:
        raise ValueError("student and teacher must have the same 2D shape")
    if not isinstance(lambda_rel, (int, float)) or lambda_rel < 0:
        raise ValueError("lambda_rel must be nonnegative")
    s = F.normalize(student.float(), dim=-1)
    t = F.normalize(teacher.float(), dim=-1)
    vector = (1.0 - (s * t).sum(dim=-1)).mean()
    relational = F.mse_loss(s @ s.T, t @ t.T)
    total = vector + float(lambda_rel) * relational
    if not bool(torch.isfinite(total)):
        raise NonFiniteLoss("distillation loss is non-finite")
    return LossBreakdown(total=total, vector=vector, relational=relational)


__all__ = ["LossBreakdown", "NonFiniteLoss", "distillation_loss"]
