from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch
from accelerate import Accelerator
from torch import nn
from torch.nn import functional as F


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from babel_training.trainer import (  # noqa: E402
    DistillationTrainer,
    build_stateful_train_loader,
)


MODEL_REVISION = "9" * 40
DATASET_REVISION = "8" * 40


class TinyStudent(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.projection = nn.Linear(4, 3, bias=False)

    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        del attention_mask
        return F.normalize(self.projection(input_ids.float()), dim=-1)

    def export_components(self):
        return (
            {"weight": self.projection.weight, "bias": torch.zeros(3)},
            {"lora_A": torch.zeros(1)},
            {"r": 16},
        )


class StatefulFixtureLoader:
    def __init__(self, batch: dict[str, torch.Tensor]) -> None:
        self.batch = batch
        self.position = 0

    def __iter__(self):
        self.position += 1
        yield self.batch

    def state_dict(self) -> dict[str, int]:
        return {"position": self.position}

    def load_state_dict(self, state: dict[str, int]) -> None:
        self.position = state["position"]


def fixture_batch() -> dict[str, torch.Tensor]:
    inputs = torch.tensor([[1, 0, 1, 0], [0, 1, 0, 1]], dtype=torch.long)
    return {
        "input_ids": inputs,
        "attention_mask": torch.ones_like(inputs),
        "teacher_vector": torch.tensor([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        "teacher_norm": torch.ones(2),
        "article_key": ("a", "b"),
    }


def make_trainer(*, accumulation: int = 1) -> DistillationTrainer:
    torch.manual_seed(7)
    batch = fixture_batch()
    loader = StatefulFixtureLoader(batch)
    accelerator = Accelerator(cpu=True, gradient_accumulation_steps=accumulation)
    return DistillationTrainer(
        TinyStudent(),
        loader,
        validation_batch=batch,
        model_id="tiny/model",
        model_revision=MODEL_REVISION,
        dataset_revision=DATASET_REVISION,
        training_config={"learning_rate": 0.1, "lambda_rel": 0.5},
        learning_rate=0.1,
        lambda_rel=0.5,
        gradient_accumulation_steps=accumulation,
        accelerator=accelerator,
    )


def test_build_stateful_train_loader_adapts_plain_stream_and_forwards_state() -> None:
    class PlainStatefulStream:
        def __init__(self) -> None:
            self.position = 0

        def __iter__(self):
            self.position += 1
            yield {"value": self.position}

        def state_dict(self) -> dict[str, int]:
            return {"position": self.position}

        def load_state_dict(self, state: dict[str, int]) -> None:
            self.position = state["position"]

    captured: dict[str, object] = {}

    def loader_factory(dataset, **kwargs):
        captured.update(dataset=dataset, **kwargs)
        return dataset

    stream = PlainStatefulStream()
    loader = build_stateful_train_loader(
        stream, batch_size=2, collate_fn=list, loader_factory=loader_factory
    )

    assert isinstance(loader, torch.utils.data.IterableDataset)
    assert list(loader) == [{"value": 1}]
    assert loader.state_dict() == {"position": 1}
    loader.load_state_dict({"position": 7})
    assert stream.position == 7
    assert captured["batch_size"] == 2
    assert captured["collate_fn"] is list
    assert captured["num_workers"] == 0


def test_default_stateful_train_loader_batches_and_checkpoints_plain_stream() -> None:
    class Stream:
        def __init__(self) -> None:
            self.position = 0

        def __iter__(self):
            while self.position < 3:
                self.position += 1
                yield {"value": self.position}

        def state_dict(self) -> dict[str, int]:
            return {"position": self.position}

        def load_state_dict(self, state: dict[str, int]) -> None:
            self.position = state["position"]

    loader = build_stateful_train_loader(
        Stream(), batch_size=2, collate_fn=lambda rows: rows
    )

    assert next(iter(loader)) == [{"value": 1}, {"value": 2}]
    state = loader.state_dict()
    assert state


def test_tiny_batch_overfits_with_gradient_accumulation() -> None:
    trainer = make_trainer(accumulation=2)

    losses = trainer.train(max_steps=20, repeat_fixture=True)

    assert trainer.global_step == 20
    assert len(losses) == 20
    assert losses[-1] < losses[0]


def test_one_batch_gate_runs_finite_forward_and_backward_without_stepping() -> None:
    trainer = make_trainer()

    report = trainer.one_batch_gate()

    assert report.keys() == {"total", "vector", "relational", "gradient_norm"}
    assert all(torch.isfinite(torch.tensor(value)) for value in report.values())
    assert trainer.global_step == 0


def test_export_artifact_delegates_model_components_and_run_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trainer = make_trainer()
    captured: dict[str, object] = {}
    sentinel = object()

    def export(path: Path, **kwargs: object) -> object:
        captured.update(path=path, **kwargs)
        return sentinel

    monkeypatch.setattr("babel_training.hub.export_distilled_artifact", export)

    result = trainer.export_artifact(
        tmp_path / "artifact",
        validation_report={"report_version": 1},
        dataset_manifest_sha256="d" * 64,
        dataset_readiness_sha256="e" * 64,
    )

    assert result is sentinel
    assert captured["dataset_commit_sha"] == DATASET_REVISION
    assert captured["model_id"] == "tiny/model"
    assert captured["model_revision"] == MODEL_REVISION
    assert captured["training_config"] == {"learning_rate": 0.1, "lambda_rel": 0.5}
    assert captured["validation_report"] == {"report_version": 1}


def test_checkpoint_restores_fingerprint_and_resumes_one_step(tmp_path: Path) -> None:
    trainer = make_trainer()
    trainer.train(max_steps=3, repeat_fixture=True)
    expected = trainer.validation_fingerprint()
    manifest = trainer.save(tmp_path / "checkpoint")
    assert manifest.metrics == {"validation_fingerprint": list(expected)}
    state_dir = tmp_path / "checkpoint" / "accelerator_state"
    saved_model = torch.load(state_dir / "trainable_model.pt", weights_only=True)
    assert saved_model.keys() == {"projection.weight"}
    assert not (state_dir / "model.safetensors").exists()
    expected_random = torch.rand(4)
    trainer.train_loader.position = 99
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.add_(10)

    restored = trainer.reload(tmp_path / "checkpoint")

    assert restored is trainer
    assert restored.global_step == 3
    assert restored.train_loader.position == manifest.loader_state["position"]
    torch.testing.assert_close(torch.rand(4), expected_random)
    assert restored.validation_fingerprint() == pytest.approx(
        expected, rel=1e-6, abs=1e-6
    )
    restored.train(max_steps=4, repeat_fixture=True)
    assert restored.global_step == 4
