from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_training.losses import NonFiniteLoss, distillation_loss  # noqa: E402


def test_distillation_loss_combines_vector_and_relational_terms_in_fp32() -> None:
    student = torch.tensor([[1.0, 0.0], [1.0, 1.0]], dtype=torch.float16)
    teacher = torch.tensor([[1.0, 0.0], [0.0, 1.0]], dtype=torch.float16)

    loss = distillation_loss(student, teacher, lambda_rel=0.5)

    assert loss.total.dtype == torch.float32
    torch.testing.assert_close(loss.total, loss.vector + 0.5 * loss.relational)
    assert loss.vector.item() == pytest.approx((1.0 - 2**-0.5) / 2)


def test_distillation_loss_rejects_nonfinite_values() -> None:
    with pytest.raises(NonFiniteLoss):
        distillation_loss(torch.tensor([[float("nan"), 0.0]]), torch.ones(1, 2))


def test_distillation_loss_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="same 2D shape"):
        distillation_loss(torch.ones(2, 3), torch.ones(2, 4))
