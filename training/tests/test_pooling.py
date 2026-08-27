from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_training.pooling import last_token_pool  # noqa: E402


def test_pooling_finds_last_real_token_with_left_padding() -> None:
    hidden = torch.arange(2 * 4).reshape(2, 4, 1).float()
    mask = torch.tensor([[0, 0, 1, 1], [0, 1, 1, 1]])

    torch.testing.assert_close(
        last_token_pool(hidden, mask).squeeze(-1), torch.tensor([3.0, 7.0])
    )


def test_pooling_finds_last_real_token_with_right_padding() -> None:
    hidden = torch.arange(2 * 4).reshape(2, 4, 1).float()
    mask = torch.tensor([[1, 1, 0, 0], [1, 1, 1, 0]])

    torch.testing.assert_close(
        last_token_pool(hidden, mask).squeeze(-1), torch.tensor([1.0, 6.0])
    )


def test_pooling_rejects_an_all_padding_row() -> None:
    with pytest.raises(ValueError, match="empty sequence"):
        last_token_pool(torch.zeros(2, 3, 4), torch.tensor([[1, 1, 0], [0, 0, 0]]))
