from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pyarrow.parquet as pq
import pytest

from babel_online.feedback import InMemoryFeedbackBus
from babel_online.feedback.export import reconstruct_canonical_edges
from babel_online.runtime.performance_worker import PerformanceCondition
from tests.feedback.test_bus import feedback_event_v2


EXPERIMENT_ID = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")


def _completed_trial():
    conditions = []
    index = 0
    for topology in ("same_process", "same_host_split", "same_host_isolated"):
        for training, activation in ((False, False), (True, False), (True, True)):
            index += 1
            conditions.append(
                PerformanceCondition(
                    id=UUID(int=100 + index),
                    condition_index=index,
                    topology=topology,
                    training_enabled=training,
                    activation_enabled=activation,
                    run_id=UUID(int=200 + index),
                    status="completed",
                )
            )
    return SimpleNamespace(
        id=EXPERIMENT_ID,
        status="completed",
        conditions=tuple(conditions),
    )


def _write_evidence(root, trial, bus):
    events = {}
    for condition in trial.conditions:
        event = feedback_event_v2(event_number=condition.condition_index).model_copy(
            update={"runId": condition.run_id}
        )
        record = bus.publish(key=str(event.creatorId), event=event)
        events[condition.run_id] = event
        training = condition.training_enabled
        final = (
            {
                "available": True,
                "kafkaLag": 0,
                "trainerVersion": 1,
                "servingVersion": 1 if condition.activation_enabled else 0,
                "nextOffsets": [
                    {
                        "topic": record.topic,
                        "partition": record.partition,
                        "nextOffset": record.offset + 1,
                    }
                ],
                "offsetsCoverPublishedRanges": True,
                "checkpointManifestSha256": "a" * 64,
            }
            if training
            else {"available": False}
        )
        document = {
            "conditionId": str(condition.id),
            "runId": str(condition.run_id),
            "requestCount": 1,
            "p95Ms": 1.0,
            "rawEvidence": {
                "conditionIdentity": {
                    "topology": condition.topology,
                    "trainingEnabled": training,
                    "activationEnabled": condition.activation_enabled,
                    "retrievalBackend": "pgvector",
                },
                "measurements": [{"outcome": "success"}],
                "feedbackKafka": {
                    "recordCount": 1,
                    "records": [
                        {
                            "topic": record.topic,
                            "partition": record.partition,
                            "offset": record.offset,
                            "key": record.key,
                            "eventId": str(event.eventId),
                            "requestId": str(event.requestId),
                        }
                    ],
                    "offsetRanges": [
                        {
                            "topic": record.topic,
                            "partition": record.partition,
                            "startInclusive": record.offset,
                            "endExclusive": record.offset + 1,
                        }
                    ],
                    "finalTrainerState": final,
                },
            },
        }
        path = root / f"{condition.condition_index:02d}" / "live-evidence.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document))
    return events


def test_completed_trial_export_replays_exact_ranges_and_matches_database_edges(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    result = export_completed_performance_trial(
        database=database,
        experiment_id=trial.id,
        evidence_root=tmp_path / "conditions",
        output_root=tmp_path / "export",
        feedback_source=bus,
    )

    assert len(result.records) == 9
    assert pq.read_table(result.parquet_path).num_rows == 9
    assert pq.read_table(result.edge_parquet_path).num_rows == 9
    assert result.publication_files() == {
        "feedback.parquet": result.parquet_path,
        "edges.parquet": result.edge_parquet_path,
    }


def test_completed_trial_export_rejects_nonzero_training_lag(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    path = tmp_path / "conditions/02/live-evidence.json"
    document = json.loads(path.read_text())
    document["rawEvidence"]["feedbackKafka"]["finalTrainerState"]["kafkaLag"] = 1
    path.write_text(json.dumps(document))
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    with pytest.raises(ValueError, match="zero final Kafka lag"):
        export_completed_performance_trial(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=bus,
        )


def test_completed_trial_export_rejects_checkpoint_without_range_coverage(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    path = tmp_path / "conditions/02/live-evidence.json"
    document = json.loads(path.read_text())
    document["rawEvidence"]["feedbackKafka"]["finalTrainerState"][
        "offsetsCoverPublishedRanges"
    ] = False
    path.write_text(json.dumps(document))
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    with pytest.raises(ValueError, match="checkpoint"):
        export_completed_performance_trial(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=bus,
        )


def test_completed_trial_export_rejects_incomplete_replay_before_publication(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )
    missing_source = SimpleNamespace(records=lambda _offset_range: ())

    with pytest.raises(ValueError, match="every acknowledged feedback event"):
        export_completed_performance_trial(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=missing_source,
        )

    assert not (tmp_path / "export/feedback-export").exists()


def test_completed_trial_export_requires_exact_topology_mode_matrix(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    original = _completed_trial()
    conditions = list(original.conditions)
    conditions[-1] = replace(conditions[-1], topology="same_process")
    trial = SimpleNamespace(
        id=original.id,
        status=original.status,
        conditions=tuple(conditions),
    )
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    with pytest.raises(ValueError, match="exact 3x3 condition matrix"):
        export_completed_performance_trial(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=bus,
        )
