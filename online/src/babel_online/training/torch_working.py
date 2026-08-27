"""Small PyTorch online ranking head over immutable 100d Qwen vectors."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import numpy as np


def _torch():
    try:
        import torch
    except ImportError as error:  # pragma: no cover - exercised by base-only installs
        raise RuntimeError(
            "real online training requires the babel-online[qwen] dependencies"
        ) from error
    return torch


def _checked_vector(value: Any, *, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype="<f4").reshape(-1).copy()
    if vector.shape != (100,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be finite 100d float32")
    return vector


class _ContextModule:
    """Thin owner around an nn.Module so importing base Babel does not import Torch."""

    def __init__(self) -> None:
        torch = _torch()

        class Module(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.attention_query = torch.nn.Linear(100, 100, bias=False)
                self.attention_key = torch.nn.Linear(100, 100, bias=False)
                self.fusion = torch.nn.Linear(200, 100, bias=True)
                with torch.no_grad():
                    identity = torch.eye(100, dtype=torch.float32)
                    self.attention_query.weight.copy_(identity)
                    self.attention_key.weight.copy_(identity)
                    self.fusion.weight.zero_()
                    self.fusion.weight[:, :100].copy_(0.5 * identity)
                    self.fusion.weight[:, 100:].copy_(0.5 * identity)
                    self.fusion.bias.zero_()

            def forward(self, new, history):
                functional = torch.nn.functional
                new_unit = functional.normalize(new.reshape(1, 100), dim=1)[0]
                if history.numel() == 0:
                    return new_unit
                history_unit = functional.normalize(history.reshape(-1, 100), dim=1)
                query = self.attention_query(new_unit)
                keys = self.attention_key(history_unit)
                weights = torch.softmax((keys @ query) / math.sqrt(100.0), dim=0)
                attended = weights @ history_unit
                fused = self.fusion(torch.cat((new_unit, attended), dim=0))
                return functional.normalize(fused.reshape(1, 100), dim=1)[0]

        self.module = Module()


class TorchServingContext:
    """Inference view of the trainable creator-context attention/fusion tensors."""

    def __init__(self) -> None:
        self._owner = _ContextModule()

    @property
    def module(self):
        return self._owner.module

    def __call__(self, *, new: np.ndarray, history: np.ndarray) -> np.ndarray:
        torch = _torch()
        new_tensor = torch.as_tensor(
            _checked_vector(new, name="new context vector"), dtype=torch.float32
        )
        history_array = np.asarray(history, dtype="<f4")
        if history_array.size == 0:
            history_array = np.empty((0, 100), dtype="<f4")
        if (
            history_array.ndim != 2
            or history_array.shape[1] != 100
            or not np.isfinite(history_array).all()
        ):
            raise ValueError("history must contain finite 100d vectors")
        with torch.no_grad():
            value = self.module(
                new_tensor, torch.as_tensor(history_array, dtype=torch.float32)
            )
        return np.asarray(value.detach().cpu().numpy(), dtype="<f4")

    def semantic_probe(self, input_vector: Sequence[float]) -> list[float]:
        vector = _checked_vector(input_vector, name="semantic probe")
        history = np.roll(vector, 1).reshape(1, 100)
        return self(new=vector, history=history).tolist()

    def tensor_state(self) -> dict[str, list[Any]]:
        return {
            name: tensor.detach().cpu().tolist()
            for name, tensor in sorted(self.module.state_dict().items())
        }

    def load_tensor_state(self, state: Mapping[str, Any]) -> None:
        torch = _torch()
        expected = self.module.state_dict()
        if set(state) != set(expected):
            raise ValueError("online context tensor names differ")
        checked = {}
        for name, target in expected.items():
            value = torch.as_tensor(state[name], dtype=torch.float32)
            if value.shape != target.shape or not torch.isfinite(value).all():
                raise ValueError(f"online context tensor is invalid: {name}")
            checked[name] = value
        self.module.load_state_dict(checked, strict=True)

    @classmethod
    def from_working_state(cls, state: Mapping[str, Any]) -> "TorchServingContext":
        if state.get("modelKind") != "torch_online_recommender_v1":
            raise ValueError("online state is not a Torch recommender head")
        context = cls()
        context.load_tensor_state(state.get("contextState", {}))
        return context


class TorchOnlineRecommender:
    """Train context tensors and sparse touched-item residual parameters with Adam."""

    model_kind = "torch_online_recommender_v1"

    def __init__(
        self,
        frozen_vectors: Mapping[UUID, np.ndarray],
        *,
        learning_rate: float = 0.01,
        scheduler_gamma: float = 0.999,
    ) -> None:
        torch = _torch()
        if not frozen_vectors:
            raise ValueError("working model needs frozen item vectors")
        self._frozen: dict[UUID, np.ndarray] = {}
        for item_id, value in frozen_vectors.items():
            vector = _checked_vector(value, name="frozen vector")
            norm = float(np.linalg.norm(vector))
            if norm == 0.0:
                raise ValueError("frozen vectors must be nonzero")
            vector.flags.writeable = False
            self._frozen[item_id] = vector
        if learning_rate <= 0 or not math.isfinite(learning_rate):
            raise ValueError("learning rate must be positive and finite")
        if not 0 < scheduler_gamma <= 1 or not math.isfinite(scheduler_gamma):
            raise ValueError("scheduler gamma must be in (0, 1]")
        self.learning_rate = float(learning_rate)
        self.scheduler_gamma = float(scheduler_gamma)
        self.scheduler_step = 0
        self.context = TorchServingContext()
        self._residuals = torch.nn.ParameterDict()
        self._creator_histories: dict[UUID, list[UUID]] = {}
        self._optimizer = torch.optim.Adam(
            self.context.module.parameters(), lr=self.learning_rate
        )

    def __call__(self, *, new: np.ndarray, history: np.ndarray) -> np.ndarray:
        return self.context(new=new, history=history)

    def context_parameter_names(self) -> set[str]:
        return {name for name, _ in self.context.module.named_parameters()}

    def frozen_bytes(self) -> bytes:
        return b"".join(
            item_id.bytes + self._frozen[item_id].tobytes(order="C")
            for item_id in sorted(self._frozen, key=lambda value: value.hex)
        )

    def _frozen_identity(self) -> str:
        return hashlib.sha256(self.frozen_bytes()).hexdigest()

    def _residual_key(self, item_id: UUID) -> str:
        if item_id not in self._frozen:
            raise KeyError(f"unknown online item: {item_id}")
        return item_id.hex

    def _ensure_residuals(self, item_ids: Sequence[UUID]) -> None:
        torch = _torch()
        new_parameters = []
        for item_id in sorted(set(item_ids), key=lambda value: value.hex):
            key = self._residual_key(item_id)
            if key not in self._residuals:
                parameter = torch.nn.Parameter(torch.zeros(100, dtype=torch.float32))
                self._residuals[key] = parameter
                new_parameters.append(parameter)
        if new_parameters:
            self._optimizer.add_param_group(
                {"params": new_parameters, "lr": self.current_learning_rate}
            )

    @property
    def current_learning_rate(self) -> float:
        return self.learning_rate * self.scheduler_gamma**self.scheduler_step

    def touched_item_ids(self) -> set[UUID]:
        return {UUID(hex=key) for key in self._residuals}

    def residual(self, item_id: UUID) -> np.ndarray:
        key = self._residual_key(item_id)
        if key not in self._residuals:
            return np.zeros(100, dtype="<f4")
        return np.asarray(
            self._residuals[key].detach().cpu().numpy(), dtype="<f4"
        ).copy()

    def _materialized_tensor(self, item_id: UUID):
        torch = _torch()
        value = torch.tensor(self._frozen[item_id], dtype=torch.float32)
        key = self._residual_key(item_id)
        if key in self._residuals:
            value = value + self._residuals[key]
        return torch.nn.functional.normalize(value.reshape(1, 100), dim=1)[0]

    def materialized_vector(self, item_id: UUID) -> np.ndarray:
        value = self._materialized_tensor(item_id).detach().cpu().numpy()
        if not np.isfinite(value).all():
            raise FloatingPointError("working item vector is non-finite")
        return np.asarray(value, dtype="<f4")

    def materialized_vectors(self) -> dict[UUID, np.ndarray]:
        return {
            item_id: self.materialized_vector(item_id)
            for item_id in sorted(self._frozen, key=lambda value: value.hex)
        }

    def _context_tensor(self, source_id: UUID, history_ids: Sequence[UUID]):
        torch = _torch()
        new = self._materialized_tensor(source_id)
        history = (
            torch.stack([self._materialized_tensor(item_id) for item_id in history_ids])
            if history_ids
            else torch.empty((0, 100), dtype=torch.float32)
        )
        return self.context.module(new, history)

    def observe_event(self, event: Any) -> None:
        if int(getattr(event, "traversalDepth", 0)) != 0:
            return
        creator_id = UUID(str(event.creatorId))
        source_id = UUID(str(event.sourceBabelId))
        self._residual_key(source_id)
        history = self._creator_histories.setdefault(creator_id, [])
        if source_id not in history:
            history.append(source_id)

    def train_events(self, events: Sequence[Any]) -> float | None:
        torch = _torch()
        touch = []
        for event in events:
            touch.extend(
                UUID(str(action.babelId))
                for action in event.candidateActions
                if action.action in {"include", "exclude", "ignore"}
            )
        self._ensure_residuals(touch)
        self._optimizer.zero_grad(set_to_none=True)
        weighted_losses = []
        weights = []
        for event in events:
            creator_id = UUID(str(event.creatorId))
            source_id = UUID(str(event.sourceBabelId))
            history_ids = tuple(self._creator_histories.get(creator_id, ()))
            query = self._context_tensor(source_id, history_ids)
            positives = [
                UUID(str(action.babelId))
                for action in event.candidateActions
                if action.action == "include"
            ]
            negatives = [
                (
                    UUID(str(action.babelId)),
                    1.0 if action.action == "exclude" else 0.25,
                )
                for action in event.candidateActions
                if action.action in {"exclude", "ignore"}
            ]
            for positive in positives:
                positive_score = torch.dot(query, self._materialized_tensor(positive))
                for negative, weight in negatives:
                    negative_score = torch.dot(query, self._materialized_tensor(negative))
                    weighted_losses.append(
                        torch.nn.functional.softplus(-(positive_score - negative_score))
                        * weight
                    )
                    weights.append(weight)
            self.observe_event(event)
        if not weighted_losses:
            return None
        loss = torch.stack(weighted_losses).sum() / math.fsum(weights)
        if not torch.isfinite(loss):
            raise FloatingPointError("online ranking loss is non-finite")
        loss.backward()
        self._optimizer.step()
        self.scheduler_step += 1
        current_lr = self.current_learning_rate
        for group in self._optimizer.param_groups:
            group["lr"] = current_lr
        for parameter in list(self.context.module.parameters()) + list(
            self._residuals.parameters()
        ):
            if not torch.isfinite(parameter).all():
                raise FloatingPointError("online model update is non-finite")
        return float(loss.detach().cpu())

    def semantic_probe(self, input_vector: Sequence[float]) -> list[float]:
        return self.context.semantic_probe(input_vector)

    def _named_parameters(self) -> dict[str, Any]:
        result = {
            f"context.{name}": parameter
            for name, parameter in self.context.module.named_parameters()
        }
        result.update(
            {f"residual.{key}": parameter for key, parameter in self._residuals.items()}
        )
        return result

    def _optimizer_state(self) -> dict[str, Any]:
        torch = _torch()
        result = {}
        for name, parameter in sorted(self._named_parameters().items()):
            state = self._optimizer.state.get(parameter)
            if not state:
                continue
            result[name] = {
                "step": float(state["step"].detach().cpu()),
                "expAvg": state["exp_avg"].detach().cpu().tolist(),
                "expAvgSq": state["exp_avg_sq"].detach().cpu().tolist(),
            }
            if not all(
                torch.isfinite(value).all()
                for value in (state["exp_avg"], state["exp_avg_sq"])
            ):
                raise FloatingPointError("online optimizer state is non-finite")
        return {"kind": "adam", "parameterStates": result}

    def transfer_state_dict(self) -> dict[str, Any]:
        optimizer = self._optimizer_state()["parameterStates"]
        return {
            "contextState": self.context.tensor_state(),
            "contextOptimizerState": {
                name: state
                for name, state in optimizer.items()
                if name.startswith("context.")
            },
            "learningRate": self.learning_rate,
            "schedulerState": {
                "kind": "exponential",
                "gamma": self.scheduler_gamma,
                "step": self.scheduler_step,
            },
        }

    def state_dict(self) -> dict[str, Any]:
        return {
            "modelKind": self.model_kind,
            "frozenIdentitySha256": self._frozen_identity(),
            "learningRate": self.learning_rate,
            "contextState": self.context.tensor_state(),
            "residuals": {
                str(UUID(hex=key)): self._residuals[key].detach().cpu().tolist()
                for key in sorted(self._residuals)
            },
            "optimizerState": self._optimizer_state(),
            "schedulerState": {
                "kind": "exponential",
                "gamma": self.scheduler_gamma,
                "step": self.scheduler_step,
            },
            "creatorHistories": {
                str(creator): [str(item_id) for item_id in history]
                for creator, history in sorted(
                    self._creator_histories.items(), key=lambda row: row[0].hex
                )
            },
            "transferState": self.transfer_state_dict(),
        }

    def _reset_optimizer(self) -> None:
        torch = _torch()
        parameters = list(self.context.module.parameters()) + list(
            self._residuals.parameters()
        )
        self._optimizer = torch.optim.Adam(parameters, lr=self.current_learning_rate)

    def _load_optimizer_state(self, document: Mapping[str, Any]) -> None:
        torch = _torch()
        if document.get("kind") != "adam" or not isinstance(
            document.get("parameterStates"), Mapping
        ):
            raise ValueError("online optimizer state is invalid")
        named = self._named_parameters()
        states = document["parameterStates"]
        if not set(states).issubset(named):
            raise ValueError("online optimizer parameter names differ")
        for name, row in states.items():
            parameter = named[name]
            exp_avg = torch.as_tensor(row["expAvg"], dtype=torch.float32)
            exp_avg_sq = torch.as_tensor(row["expAvgSq"], dtype=torch.float32)
            if (
                exp_avg.shape != parameter.shape
                or exp_avg_sq.shape != parameter.shape
                or not torch.isfinite(exp_avg).all()
                or not torch.isfinite(exp_avg_sq).all()
            ):
                raise ValueError(f"online optimizer tensor is invalid: {name}")
            step = float(row["step"])
            if not math.isfinite(step) or step < 0:
                raise ValueError("online optimizer step is invalid")
            self._optimizer.state[parameter] = {
                "step": torch.tensor(step, dtype=torch.float32),
                "exp_avg": exp_avg.clone(),
                "exp_avg_sq": exp_avg_sq.clone(),
            }

    def load_transfer_state(self, state: Mapping[str, Any]) -> None:
        scheduler = state.get("schedulerState")
        if not isinstance(scheduler, Mapping) or scheduler.get("kind") != "exponential":
            raise ValueError("online transfer scheduler state is invalid")
        learning_rate = float(state.get("learningRate"))
        gamma = float(scheduler.get("gamma"))
        step = int(scheduler.get("step"))
        if learning_rate <= 0 or not 0 < gamma <= 1 or step < 0:
            raise ValueError("online transfer schedule is invalid")
        self.learning_rate = learning_rate
        self.scheduler_gamma = gamma
        self.scheduler_step = step
        self.context.load_tensor_state(state.get("contextState", {}))
        self._reset_optimizer()
        self._load_optimizer_state(
            {
                "kind": "adam",
                "parameterStates": state.get("contextOptimizerState", {}),
            }
        )

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        torch = _torch()
        if state.get("modelKind") != self.model_kind:
            raise ValueError("online state is not a Torch recommender head")
        if state.get("frozenIdentitySha256") != self._frozen_identity():
            raise ValueError("working frozen-vector identity mismatch")
        scheduler = state.get("schedulerState")
        if not isinstance(scheduler, Mapping) or scheduler.get("kind") != "exponential":
            raise ValueError("online scheduler state is invalid")
        self.learning_rate = float(state.get("learningRate"))
        self.scheduler_gamma = float(scheduler.get("gamma"))
        self.scheduler_step = int(scheduler.get("step"))
        if (
            self.learning_rate <= 0
            or not 0 < self.scheduler_gamma <= 1
            or self.scheduler_step < 0
        ):
            raise ValueError("online schedule is invalid")
        self.context.load_tensor_state(state.get("contextState", {}))
        residuals = state.get("residuals")
        if not isinstance(residuals, Mapping):
            raise ValueError("online residual state is invalid")
        self._residuals = torch.nn.ParameterDict()
        for raw_id, raw_vector in residuals.items():
            item_id = UUID(str(raw_id))
            vector = _checked_vector(raw_vector, name="working residual")
            self._residual_key(item_id)
            self._residuals[item_id.hex] = torch.nn.Parameter(
                torch.as_tensor(vector, dtype=torch.float32).clone()
            )
        histories = state.get("creatorHistories")
        if not isinstance(histories, Mapping):
            raise ValueError("creator history state is invalid")
        self._creator_histories = {}
        for creator, rows in histories.items():
            checked = [UUID(str(item_id)) for item_id in rows]
            for item_id in checked:
                self._residual_key(item_id)
            self._creator_histories[UUID(str(creator))] = checked
        self._reset_optimizer()
        self._load_optimizer_state(state.get("optimizerState", {}))


__all__ = ["TorchOnlineRecommender", "TorchServingContext"]
