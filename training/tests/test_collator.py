from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training" / "src"))

from babel_training.collator import DistillationCollator  # noqa: E402
from test_data import row  # noqa: E402


class FakeTensor:
    def __init__(self, value: object, dtype: object) -> None:
        self.array = np.asarray(value)
        self.dtype = dtype

    @property
    def shape(self) -> tuple[int, ...]:
        return self.array.shape

    @property
    def ndim(self) -> int:
        return self.array.ndim

    def tolist(self) -> list[object]:
        return self.array.tolist()


class FakeTorch:
    long = "torch.int64"
    float32 = "torch.float32"

    @staticmethod
    def as_tensor(value: object, *, dtype: object) -> FakeTensor:
        return FakeTensor(value, dtype)

    @staticmethod
    def isfinite(value: FakeTensor) -> np.ndarray:
        return np.isfinite(value.array)


class FakeTokenizer:
    pad_token_id = 0

    def __init__(self) -> None:
        self.padding_side = "right"
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, texts: list[str], **kwargs: object) -> dict[str, object]:
        self.calls.append((texts, kwargs))
        # Explicitly left-padded and no longer than the requested maximum.
        return {
            "input_ids": [[0, 11, 12], [21, 22, 23]][: len(texts)],
            "attention_mask": [[0, 1, 1], [1, 1, 1]][: len(texts)],
        }


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", FakeTorch)


def test_formats_only_title_blank_line_and_lead_and_returns_tensor_batch(fake_torch: None) -> None:
    tokenizer = FakeTokenizer()
    first = row(1)
    first["canonical_title"] = "Virtual memory"
    first["lead_text"] = "Lead sentence."
    first["article_text"] = "SECRET FULL ARTICLE"

    batch = DistillationCollator(tokenizer, max_length=512)([first])

    assert tokenizer.padding_side == "left"
    assert tokenizer.calls[0][0] == ["Virtual memory\n\nLead sentence."]
    assert "SECRET FULL ARTICLE" not in repr(tokenizer.calls)
    assert tokenizer.calls[0][1] == {
        "padding": True,
        "truncation": True,
        "max_length": 512,
        "return_tensors": None,
    }
    assert batch["teacher_vector"].shape == (1, 100)
    assert batch["teacher_vector"].dtype == FakeTorch.float32
    assert batch["article_key"] == (first["article_key"],)
    assert batch["page_id"] == (first["page_id"],)
    assert batch["split"] == ("train",)
    assert "article_text" not in batch


@pytest.mark.parametrize("max_length", [0, -1, 1025, True, 1.5])
def test_rejects_invalid_max_length(max_length: object) -> None:
    with pytest.raises(ValueError, match="max_length"):
        DistillationCollator(FakeTokenizer(), max_length=max_length)  # type: ignore[arg-type]


def test_rejects_empty_batch_hidden_fields_nan_and_norm_mismatch(fake_torch: None) -> None:
    collator = DistillationCollator(FakeTokenizer(), max_length=8)
    with pytest.raises(ValueError, match="nonempty"):
        collator([])
    invalid_rows = []
    hidden = row(1); hidden["hidden_teacher"] = 1; invalid_rows.append(hidden)
    nan = row(1); nan["teacher_vector"][0] = math.nan; invalid_rows.append(nan)  # type: ignore[index]
    norm = row(1); norm["teacher_norm"] = 2.0; invalid_rows.append(norm)
    for invalid in invalid_rows:
        with pytest.raises(ValueError):
            collator([invalid])


def test_rejects_vectors_or_norms_that_become_invalid_in_float32(fake_torch: None) -> None:
    collator = DistillationCollator(FakeTokenizer(), max_length=8)
    overflow = row(1)
    overflow["teacher_vector"] = [3.0e38, 3.0e38] + [0.0] * 98
    overflow["teacher_norm"] = math.sqrt(18.0) * 1.0e38
    underflow = row(1)
    underflow["teacher_vector"] = [1.0e-46] + [0.0] * 99
    underflow["teacher_norm"] = 1.0e-46
    for invalid in (overflow, underflow):
        with pytest.raises(ValueError, match="float32"):
            collator([invalid])


class BadTokenizer(FakeTokenizer):
    def __init__(self, output: dict[str, object]) -> None:
        super().__init__()
        self.output = output

    def __call__(self, texts: list[str], **kwargs: object) -> dict[str, object]:
        return self.output


@pytest.mark.parametrize(
    "output",
    [
        {"input_ids": [[1, 2]], "attention_mask": [[1]]},
        {"input_ids": [1, 2], "attention_mask": [1, 1]},
        {"input_ids": [[1, 2, 3]], "attention_mask": [[1, 1, 1]]},
        {"input_ids": [[11, 0]], "attention_mask": [[1, 0]]},
        {"input_ids": [[9, 11]], "attention_mask": [[0, 1]]},
        {"input_ids": [[0, 0]], "attention_mask": [[0, 0]]},
    ],
)
def test_rejects_bad_tokenizer_shapes_lengths_or_left_padding(
    fake_torch: None, output: dict[str, object]
) -> None:
    collator = DistillationCollator(BadTokenizer(output), max_length=2)
    with pytest.raises(ValueError, match="tokenizer"):
        collator([row(1)])


def test_two_row_batch_preserves_validation_identity(fake_torch: None) -> None:
    training = row(1)
    validation = row(1, split="validation")
    batch = DistillationCollator(FakeTokenizer(), max_length=3)(
        [copy.deepcopy(training), copy.deepcopy(validation)]
    )
    assert batch["teacher_vector"].shape == (2, 100)
    assert batch["split"] == ("train", "validation")
