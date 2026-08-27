"""Accelerate-compatible distillation loop with complete restart state."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Self

import torch
from torch import Tensor, nn
from torch.utils.data import IterableDataset

from .checkpointing import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointManifest,
    load_checkpoint,
    save_checkpoint,
)
from .losses import LossBreakdown, distillation_loss


_BATCH_METADATA = frozenset(
    {"teacher_vector", "teacher_norm", "article_key", "page_id", "split"}
)


class _StatefulStreamDataset(IterableDataset):
    def __init__(self, stream: Iterable[Mapping[str, Any]]) -> None:
        super().__init__()
        self.stream = stream

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.stream)

    def state_dict(self) -> Mapping[str, Any]:
        return self.stream.state_dict()  # type: ignore[attr-defined,no-any-return]

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.stream.load_state_dict(state)  # type: ignore[attr-defined]


def build_stateful_train_loader(
    stream: Iterable[Mapping[str, Any]],
    *,
    batch_size: int,
    collate_fn: Callable[[list[Mapping[str, Any]]], Mapping[str, Any]],
    loader_factory: Callable[..., Any] | None = None,
) -> Any:
    """Adapt the plain restartable stream to TorchData's iterable loader."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if not all(
        callable(getattr(stream, method, None))
        for method in ("state_dict", "load_state_dict")
    ):
        raise TypeError("stream must provide state_dict and load_state_dict")
    if loader_factory is None:
        from torchdata.stateful_dataloader import StatefulDataLoader

        loader_factory = StatefulDataLoader
    dataset = _StatefulStreamDataset(stream)
    return loader_factory(
        dataset,
        batch_size=batch_size,
        collate_fn=collate_fn,
        num_workers=0,
    )


class NonFiniteGradient(FloatingPointError):
    """A training step produced non-finite gradients."""


class DistillationTrainer:
    """Small notebook-facing owner of train, validate, save, and resume."""

    def __init__(
        self,
        model: nn.Module,
        train_loader: Iterable[Mapping[str, Any]],
        *,
        validation_batch: Mapping[str, Any],
        model_id: str,
        model_revision: str,
        dataset_revision: str,
        training_config: Mapping[str, Any] | None = None,
        optimizer: torch.optim.Optimizer | None = None,
        scheduler: Any | None = None,
        accelerator: Any | None = None,
        learning_rate: float = 2e-4,
        lambda_rel: float = 0.5,
        gradient_accumulation_steps: int = 1,
        mixed_precision: str = "no",
        max_grad_norm: float = 1.0,
        checkpoint_interval: int | None = None,
        checkpoint_root: str | Path | None = None,
        max_runtime_minutes: float | None = None,
    ) -> None:
        if accelerator is None:
            from accelerate import Accelerator

            accelerator = Accelerator(
                gradient_accumulation_steps=gradient_accumulation_steps,
                mixed_precision=mixed_precision,
            )
        self.accelerator = accelerator
        self.train_loader = train_loader
        self.validation_batch = dict(validation_batch)
        self.model_id = model_id
        self.model_revision = model_revision
        self.dataset_revision = dataset_revision
        self.training_config = dict(training_config or {})
        self.lambda_rel = lambda_rel
        self.max_grad_norm = max_grad_norm
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_root = Path(checkpoint_root) if checkpoint_root else None
        self.max_runtime_minutes = max_runtime_minutes
        self.global_step = 0
        self.epoch = 0
        trainable = [
            parameter for parameter in model.parameters() if parameter.requires_grad
        ]
        if not trainable:
            raise ValueError("model has no trainable parameters")
        optimizer = optimizer or torch.optim.AdamW(trainable, lr=learning_rate)
        scheduler = scheduler or torch.optim.lr_scheduler.LambdaLR(
            optimizer, lr_lambda=lambda _: 1.0
        )
        self.model, self.optimizer, self.scheduler = self.accelerator.prepare(
            model, optimizer, scheduler
        )
        register_save_hook = getattr(
            self.accelerator, "register_save_state_pre_hook", None
        )
        register_load_hook = getattr(
            self.accelerator, "register_load_state_pre_hook", None
        )
        if callable(register_save_hook) and callable(register_load_hook):
            register_save_hook(self._save_trainable_model_state)
            register_load_hook(self._load_trainable_model_state)
        if all(
            callable(getattr(train_loader, method, None))
            for method in ("state_dict", "load_state_dict")
        ):
            self.accelerator.register_for_checkpointing(train_loader)

    def _trainable_state(self) -> dict[str, Tensor]:
        model = self.accelerator.unwrap_model(self.model)
        return {
            name: parameter.detach().float().cpu().contiguous()
            for name, parameter in model.named_parameters()
            if parameter.requires_grad
        }

    def _save_trainable_model_state(
        self, models: list[nn.Module], weights: list[dict[str, Tensor]], output_dir: str
    ) -> None:
        del models
        torch.save(self._trainable_state(), Path(output_dir) / "trainable_model.pt")
        weights.clear()

    def _load_trainable_model_state(
        self, models: list[nn.Module], input_dir: str
    ) -> None:
        state = torch.load(
            Path(input_dir) / "trainable_model.pt",
            map_location="cpu",
            weights_only=True,
        )
        if not isinstance(state, dict) or set(state) != set(self._trainable_state()):
            raise ValueError("checkpoint trainable model state does not match the student")
        model = self.accelerator.unwrap_model(self.model)
        model.load_state_dict(state, strict=False)
        models.clear()

    def _device_batch(
        self, batch: Mapping[str, Any]
    ) -> tuple[dict[str, Any], Tensor]:
        if "teacher_vector" not in batch:
            raise ValueError("training batch is missing teacher_vector")
        device = self.accelerator.device
        teacher = batch["teacher_vector"]
        if not isinstance(teacher, Tensor):
            raise TypeError("teacher_vector must be a tensor")
        inputs = {
            name: value.to(device) if isinstance(value, Tensor) else value
            for name, value in batch.items()
            if name not in _BATCH_METADATA
        }
        return inputs, teacher.to(device)

    def _loss(self, batch: Mapping[str, Any]) -> LossBreakdown:
        inputs, teacher = self._device_batch(batch)
        student = self.model(**inputs)
        return distillation_loss(student, teacher, lambda_rel=self.lambda_rel)

    def _finite_gradient_norm(self) -> float:
        norm = self.accelerator.clip_grad_norm_(
            self.model.parameters(), self.max_grad_norm
        )
        value = float(norm.detach().float().cpu() if isinstance(norm, Tensor) else norm)
        if not math.isfinite(value):
            raise NonFiniteGradient("gradient norm is non-finite")
        return value

    def one_batch_gate(self) -> dict[str, float]:
        """Run finite forward/backward checks without updating weights or step state."""
        self.optimizer.zero_grad()
        autocast = getattr(self.accelerator, "autocast", None)
        with autocast() if callable(autocast) else nullcontext():
            breakdown = self._loss(self.validation_batch)
        self.accelerator.backward(breakdown.total)
        gradient_norm = self._finite_gradient_norm()
        self.optimizer.zero_grad()
        return {
            "total": float(breakdown.total.detach().cpu()),
            "vector": float(breakdown.vector.detach().cpu()),
            "relational": float(breakdown.relational.detach().cpu()),
            "gradient_norm": gradient_norm,
        }

    def train(
        self,
        *,
        max_steps: int,
        repeat_fixture: bool = False,
        max_runtime_minutes: float | None = None,
    ) -> list[float]:
        """Train until the absolute optimizer-step target or runtime budget."""
        if max_steps < self.global_step:
            raise ValueError("max_steps cannot be behind the restored global step")
        runtime_limit = (
            self.max_runtime_minutes
            if max_runtime_minutes is None
            else max_runtime_minutes
        )
        started = time.monotonic()
        losses: list[float] = []
        self.model.train()
        self.optimizer.zero_grad()
        while self.global_step < max_steps:
            saw_batch = False
            for batch in self.train_loader:
                saw_batch = True
                with self.accelerator.accumulate(self.model):
                    with self.accelerator.autocast():
                        breakdown = self._loss(batch)
                    self.accelerator.backward(breakdown.total)
                    if self.accelerator.sync_gradients:
                        self._finite_gradient_norm()
                    self.optimizer.step()
                    self.scheduler.step()
                    self.optimizer.zero_grad()
                if self.accelerator.sync_gradients:
                    self.global_step += 1
                    losses.append(float(breakdown.total.detach().float().cpu()))
                    if (
                        self.checkpoint_interval
                        and self.checkpoint_root
                        and self.global_step % self.checkpoint_interval == 0
                    ):
                        self.save(
                            self.checkpoint_root / f"step-{self.global_step:08d}"
                        )
                    if runtime_limit is not None and (
                        time.monotonic() - started >= runtime_limit * 60
                    ):
                        return losses
                    if self.global_step >= max_steps:
                        return losses
            if not saw_batch:
                raise ValueError("training loader produced no batches")
            self.epoch += 1
            if not repeat_fixture and self.global_step < max_steps:
                continue
        return losses

    def validation_fingerprint(self) -> tuple[float, ...]:
        """Return deterministic flattened validation embeddings for round trips."""
        was_training = self.model.training
        self.model.eval()
        with torch.no_grad():
            inputs, _ = self._device_batch(self.validation_batch)
            output = self.model(**inputs).detach().float().cpu().reshape(-1)
        if was_training:
            self.model.train()
        if not bool(torch.isfinite(output).all()):
            raise FloatingPointError("validation fingerprint is non-finite")
        return tuple(float(value) for value in output)

    def export_artifact(
        self,
        path: str | Path,
        *,
        validation_report: Mapping[str, object],
        dataset_manifest_sha256: str,
        dataset_readiness_sha256: str,
    ) -> object:
        """Export the unwrapped student through the package's artifact contract."""
        from .hub import export_distilled_artifact

        model = self.accelerator.unwrap_model(self.model)
        export_components = getattr(model, "export_components", None)
        if not callable(export_components):
            raise TypeError("model does not expose export_components")
        projection, adapter, adapter_config = export_components()
        return export_distilled_artifact(
            path,
            projection_tensors=projection,
            adapter_tensors=adapter,
            adapter_config=adapter_config,
            model_id=self.model_id,
            model_revision=self.model_revision,
            tokenizer_revision=self.model_revision,
            dataset_commit_sha=self.dataset_revision,
            dataset_manifest_sha256=dataset_manifest_sha256,
            dataset_readiness_sha256=dataset_readiness_sha256,
            training_config=self.training_config,
            validation_report=validation_report,
        )

    def save(
        self, path: str | Path, *, metrics: Mapping[str, Any] | None = None
    ) -> CheckpointManifest:
        loader_state = getattr(self.train_loader, "state_dict", lambda: {})()
        checkpoint_metrics = (
            dict(metrics)
            if metrics is not None
            else {"validation_fingerprint": list(self.validation_fingerprint())}
        )
        manifest = CheckpointManifest(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            model_id=self.model_id,
            model_revision=self.model_revision,
            dataset_revision=self.dataset_revision,
            global_step=self.global_step,
            epoch=self.epoch,
            loader_state=loader_state,
            training_config=self.training_config,
            metrics=checkpoint_metrics,
        )
        save_checkpoint(path, accelerator=self.accelerator, manifest=manifest)
        return manifest

    def reload(self, path: str | Path) -> Self:
        manifest = load_checkpoint(
            path,
            accelerator=self.accelerator,
            expected_model_revision=self.model_revision,
            expected_dataset_revision=self.dataset_revision,
        )
        self.global_step = manifest.global_step
        self.epoch = manifest.epoch
        load_loader = getattr(self.train_loader, "load_state_dict", None)
        if callable(load_loader):
            load_loader(dict(manifest.loader_state))
        return self


__all__ = [
    "DistillationTrainer",
    "NonFiniteGradient",
    "build_stateful_train_loader",
]
