"""Pooling helpers shared by training and validation."""

from __future__ import annotations

import torch
from torch import Tensor


def last_token_pool(hidden: Tensor, attention_mask: Tensor) -> Tensor:
    """Return each sequence's final non-padding hidden state."""
    if hidden.ndim != 3 or attention_mask.ndim != 2:
        raise ValueError("hidden and attention_mask must be rank 3 and rank 2")
    if hidden.shape[:2] != attention_mask.shape:
        raise ValueError("hidden and attention_mask batch dimensions must match")
    positions = torch.arange(attention_mask.shape[1], device=hidden.device)
    positions = positions.unsqueeze(0).expand_as(attention_mask)
    sequence_lengths = positions.masked_fill(attention_mask == 0, -1).max(dim=1).values
    if bool(torch.any(sequence_lengths < 0)):
        raise ValueError("attention_mask contains an empty sequence")
    rows = torch.arange(hidden.shape[0], device=hidden.device)
    return hidden[rows, sequence_lengths]


__all__ = ["last_token_pool"]
