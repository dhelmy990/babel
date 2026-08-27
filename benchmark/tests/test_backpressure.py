from __future__ import annotations

import json

import pytest

from babel_benchmark.backpressure import (
    BackpressureOrchestrator,
    OnlineTrainerPacingAdapter,
    PersistedBackpressureController,
)


def test_backpressure_is_bounded_persisted_and_has_one_maximum_meaning(tmp_path) -> None:
    state_path = tmp_path / "backpressure-state.json"
    transitions_path = tmp_path / "backpressure-transitions.jsonl"
    controller = PersistedBackpressureController(
        state_path=state_path,
        transitions_path=transitions_path,
        configured_micro_batch=8,
        latency_threshold_ms=100,
    )

    for _ in range(46):
        controller.observe(p95_ms=130, kafka_lag=25)

    assert controller.state.micro_batch == 2
    assert controller.state.delay_ms == 500
    assert controller.maximum_backpressure_verified
    assert json.loads(state_path.read_text())["maximum_backpressure_verified"] is True
    assert len(transitions_path.read_text().splitlines()) == 22

    resumed = PersistedBackpressureController(
        state_path=state_path,
        transitions_path=transitions_path,
        configured_micro_batch=8,
        latency_threshold_ms=100,
    )
    assert resumed.state == controller.state
    assert resumed.maximum_backpressure_verified

    with pytest.raises(ValueError, match="dashboard"):
        PersistedBackpressureController(
            state_path=tmp_path / "bad.json",
            transitions_path=tmp_path / "bad.jsonl",
            configured_micro_batch=1025,
            latency_threshold_ms=100,
        )


def test_orchestrator_applies_persisted_batch_and_delay_to_live_trainer(tmp_path) -> None:
    applied = []

    class TrainerControl:
        def apply_backpressure(self, *, micro_batch, delay_ms):
            applied.append((micro_batch, delay_ms))

    controller = PersistedBackpressureController(
        state_path=tmp_path / "state.json",
        transitions_path=tmp_path / "transitions.jsonl",
        configured_micro_batch=8,
        latency_threshold_ms=100,
    )
    orchestrator = BackpressureOrchestrator(controller, TrainerControl())

    orchestrator.observe_window(p95_ms=130, kafka_lag=5)
    state = orchestrator.observe_window(p95_ms=130, kafka_lag=7)

    assert state.micro_batch == 4
    assert applied == [(8, 0), (4, 0)]


def test_online_trainer_adapter_applies_batch_and_delay_when_processing() -> None:
    calls = []
    sleeps = []

    class Trainer:
        def process_available(self, *, max_records, poll_timeout_seconds):
            calls.append((max_records, poll_timeout_seconds))
            return max_records

    adapter = OnlineTrainerPacingAdapter(Trainer(), sleep=lambda value: sleeps.append(value))
    adapter.apply_backpressure(micro_batch=4, delay_ms=25)

    assert adapter.process_once(poll_timeout_seconds=0.1) == 4
    assert calls == [(4, 0.1)]
    assert sleeps == [0.025]
