from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from babel_online.contracts import DistilledServingArtifactV1
from babel_online.model.qwen_encoder import Qwen100Encoder, format_article_input, last_token_pool


def _contract() -> DistilledServingArtifactV1:
    return DistilledServingArtifactV1(
        schemaVersion=1,
        artifactRepo="dhelmy990/babel-qwen-navigation-2016-interview",
        artifactRevision="5" * 40,
        artifactPath="artifacts/" + "6" * 64,
        artifactId="6" * 64,
        artifactSchema="babel-distillation-2016-interview-v1",
        baseModelId="Qwen/Qwen3-Embedding-0.6B",
        baseModelRevision="9" * 40,
        tokenizerRevision="9" * 40,
        datasetRepo="dhelmy990/babel-wikipedia-experiment",
        datasetConfig="distillation_2016_interview",
        datasetRevision="b" * 40,
        trainingSourceRevision="d" * 40,
        semanticsAuthority="pinned_training_source",
        inputFormat="canonical_title\\n\\nlead_text",
        maxLength=384,
        paddingSide="left",
        pooling="last_non_padding_token",
        projectionInputDimension=1024,
        embeddingDimension=100,
        normalization="l2",
        adapterSha256="a" * 64,
        projectionSha256="b" * 64,
        validationSha256="c" * 64,
        immutable=True,
    )


def test_last_token_pool_supports_left_and_right_padding() -> None:
    hidden = torch.arange(2 * 4).reshape(2, 4, 1).float()

    left = last_token_pool(hidden, torch.tensor([[0, 0, 1, 1], [0, 1, 1, 1]]))
    right = last_token_pool(hidden, torch.tensor([[1, 1, 0, 0], [1, 1, 1, 0]]))

    torch.testing.assert_close(left.squeeze(-1), torch.tensor([3.0, 7.0]))
    torch.testing.assert_close(right.squeeze(-1), torch.tensor([1.0, 6.0]))


def test_article_format_matches_training_collator() -> None:
    assert format_article_input("Virtual memory", "A memory-management technique.") == (
        "Virtual memory\n\nA memory-management technique."
    )
    with pytest.raises(ValueError, match="title.*lead"):
        format_article_input("", "text")


class _Tokenizer:
    padding_side = "right"
    pad_token_id = 0

    def __init__(self) -> None:
        self.call = None

    def __call__(self, texts, **kwargs):
        self.call = (texts, kwargs)
        return {
            "input_ids": torch.tensor([[0, 11, 12], [21, 22, 23]]),
            "attention_mask": torch.tensor([[0, 1, 1], [1, 1, 1]]),
        }


class _Backbone(torch.nn.Module):
    def forward(self, input_ids, attention_mask):
        batch, length = input_ids.shape
        hidden = torch.zeros(batch, length, 1024, dtype=torch.float32)
        hidden[0, 1, 0] = 1000.0  # Must not be selected for the left-padded row.
        hidden[0, 2, 1] = 2.0
        hidden[1, 2, 2] = 3.0
        return SimpleNamespace(last_hidden_state=hidden)


def _projection() -> torch.nn.Linear:
    layer = torch.nn.Linear(1024, 100, bias=True)
    with torch.no_grad():
        layer.weight.zero_()
        layer.bias.zero_()
        layer.weight[0, 1] = 1.0
        layer.weight[1, 2] = 1.0
    return layer


def test_encode_returns_finite_normalized_float32_100d_vectors() -> None:
    tokenizer = _Tokenizer()
    encoder = Qwen100Encoder(
        contract=_contract(),
        tokenizer=tokenizer,
        backbone=_Backbone(),
        projection=_projection(),
        device="cpu",
    )

    vectors = encoder.encode(["First", "Second"])

    assert tokenizer.padding_side == "left"
    assert tokenizer.call[1] == {
        "padding": True,
        "truncation": True,
        "max_length": 384,
        "return_tensors": "pt",
    }
    assert vectors.shape == (2, 100)
    assert vectors.dtype == np.float32
    assert np.isfinite(vectors).all()
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), np.ones(2), atol=1e-6)
    assert vectors[0, 0] == pytest.approx(1.0)
    assert vectors[1, 1] == pytest.approx(1.0)


def test_from_artifact_attaches_lora_and_projection_with_pinned_identity(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter_model.safetensors"; adapter.write_bytes(b"adapter")
    projection = tmp_path / "projection.safetensors"; projection.write_bytes(b"projection")
    artifact = SimpleNamespace(
        serving_contract=_contract(),
        path_for=lambda name: {"adapter_model.safetensors": adapter, "projection.safetensors": projection}[name],
    )
    calls = []

    def tokenizer_loader(model_id, **kwargs):
        calls.append(("tokenizer", model_id, kwargs))
        return _Tokenizer()

    def model_loader(model_id, **kwargs):
        calls.append(("model", model_id, kwargs))
        return _Backbone()

    def adapter_loader(backbone, path, lora):
        calls.append(("adapter", path, lora))
        return backbone

    def projection_loader(path, input_dimension, output_dimension):
        calls.append(("projection", path, input_dimension, output_dimension))
        return _projection()

    encoder = Qwen100Encoder.from_artifact(
        artifact,
        token="private-token",
        device="cpu",
        require_real_acceptance=False,
        tokenizer_loader=tokenizer_loader,
        model_loader=model_loader,
        adapter_loader=adapter_loader,
        projection_loader=projection_loader,
    )

    assert isinstance(encoder, Qwen100Encoder)
    assert calls[0] == ("tokenizer", _contract().baseModelId, {"revision": _contract().tokenizerRevision, "token": "private-token"})
    assert calls[1][0:2] == ("model", _contract().baseModelId)
    assert calls[1][2]["revision"] == _contract().baseModelRevision
    assert calls[2][0:2] == ("adapter", adapter)
    assert calls[2][2] == {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05, "bias": "none", "target_modules": ["q_proj", "v_proj"]}
    assert calls[3] == ("projection", projection, 1024, 100)


def test_from_artifact_rejects_fixture_by_default() -> None:
    artifact = SimpleNamespace(serving_contract=_contract(), path_for=lambda _name: Path("unused"))

    with pytest.raises(ValueError, match="real acceptance"):
        Qwen100Encoder.from_artifact(artifact)
