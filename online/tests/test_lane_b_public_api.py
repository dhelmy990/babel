from __future__ import annotations


def test_lane_b_public_imports_are_stable() -> None:
    from babel_online.feedback import (
        InMemoryFeedbackBus,
        KafkaFeedbackConsumer,
        KafkaFeedbackProducer,
        export_offset_ranges,
    )
    from babel_online.simulation import SimulationEngine, SourceSampler
    from babel_online.training import (
        AtomicSynchronizer,
        NumpyWorkingModel,
        OnlineTrainer,
        export_immutable_child,
        pairs_from_event,
    )

    assert all(
        value is not None
        for value in (
            InMemoryFeedbackBus,
            KafkaFeedbackConsumer,
            KafkaFeedbackProducer,
            export_offset_ranges,
            SimulationEngine,
            SourceSampler,
            AtomicSynchronizer,
            NumpyWorkingModel,
            OnlineTrainer,
            export_immutable_child,
            pairs_from_event,
        )
    )
