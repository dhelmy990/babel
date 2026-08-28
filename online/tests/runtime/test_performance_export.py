from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import pyarrow.parquet as pq
import pytest

from babel_online.feedback import InMemoryFeedbackBus, TopicPartition
from babel_online.feedback.export import reconstruct_canonical_edges
from babel_online.runtime.performance_worker import PerformanceCondition
from online.tests.feedback.test_bus import feedback_event_v2


EXPERIMENT_ID = UUID("aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa")


def _completed_trial(cohort_size: int = 50):
    conditions = []
    index = 0
    topologies = (
        ("same_process", "same_host_split", "same_host_isolated")
        if cohort_size == 50
        else ("same_process", "same_host_split")
    )
    for topology in topologies:
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
        starting_model_id=UUID("11111111-1111-5111-8111-111111111111"),
        model_repository="owner/model",
        model_revision="a" * 40,
        dataset_repository="owner/dataset",
        dataset_revision="b" * 40,
        creator_count=cohort_size,
        population_bundle_path=None,
        conditions=tuple(conditions),
    )


def _completed_isolated_trial():
    trial = _completed_trial()
    conditions = tuple(
        replace(condition, condition_index=index)
        for index, condition in enumerate(trial.conditions[6:], start=1)
    )
    return SimpleNamespace(
        **{
            **trial.__dict__,
            "evidence_scope": "representative_isolated_smoke",
            "creator_count": 50,
            "conditions": conditions,
        }
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
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["experimentId"] == str(trial.id)
    assert manifest["creatorCohort"] == 50
    assert manifest["conditionCount"] == 9
    assert manifest["conditions"] == [
        {"conditionId": str(row.id), "runId": str(row.run_id)}
        for row in trial.conditions
    ]
    assert manifest["feedbackParquet"] == {
        "path": "feedback.parquet",
        "rows": 9,
        "sha256": hashlib.sha256(result.parquet_path.read_bytes()).hexdigest(),
    }
    assert manifest["edgesParquet"] == {
        "path": "edges.parquet",
        "rows": 9,
        "sha256": hashlib.sha256(result.edge_parquet_path.read_bytes()).hexdigest(),
    }


def test_formal_export_rejects_explicitly_representative_trial_before_reading_evidence(
    tmp_path,
):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = SimpleNamespace(
        **{
            **_completed_trial().__dict__,
            "evidence_scope": "representative_same_process_vs_split",
        }
    )
    with pytest.raises(ValueError, match="non-formal"):
        export_completed_performance_trial(
            database=SimpleNamespace(load_performance_experiment=lambda _id: trial),
            experiment_id=trial.id,
            evidence_root=tmp_path / "missing",
            output_root=tmp_path / "export",
            feedback_source=object(),
        )


def test_representative_export_is_saved_with_non_formal_scope(tmp_path):
    from babel_online.runtime.performance_export import (
        export_completed_representative_trial,
    )

    trial = SimpleNamespace(
        **{
            **_completed_trial(100).__dict__,
            "evidence_scope": "representative_same_process_vs_split",
            "creator_count": 50,
        }
    )
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    result = export_completed_representative_trial(
        database=database,
        experiment_id=trial.id,
        evidence_root=tmp_path / "conditions",
        output_root=tmp_path / "representative-export",
        feedback_source=bus,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["evidenceScope"] == "representative_same_process_vs_split"
    assert manifest["formalPerformanceClaim"] is False
    assert manifest["conditionCount"] == 6
    assert manifest["feedbackParquet"]["rows"] == 6


def test_isolated_representative_export_preserves_exact_formal_positions(tmp_path):
    from babel_online.runtime.performance_export import (
        export_completed_representative_trial,
    )

    trial = _completed_isolated_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    result = export_completed_representative_trial(
        database=database,
        experiment_id=trial.id,
        evidence_root=tmp_path / "conditions",
        output_root=tmp_path / "representative-export",
        feedback_source=bus,
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["evidenceScope"] == "representative_isolated_smoke"
    assert manifest["formalPerformanceClaim"] is False
    assert manifest["conditionCount"] == 3
    assert manifest["conditions"] == [
        {
            "conditionId": str(condition.id),
            "runId": str(condition.run_id),
            "conditionIndex": condition.condition_index,
            "formalConditionIndex": condition.condition_index + 6,
        }
        for condition in trial.conditions
    ]


def test_isolated_representative_export_rejects_topology_drift(tmp_path):
    from babel_online.runtime.performance_export import (
        export_completed_representative_trial,
    )

    original = _completed_isolated_trial()
    conditions = list(original.conditions)
    conditions[1] = replace(conditions[1], topology="same_host_split")
    trial = SimpleNamespace(**{**original.__dict__, "conditions": tuple(conditions)})

    with pytest.raises(ValueError, match="exact completed condition matrix"):
        export_completed_representative_trial(
            database=SimpleNamespace(load_performance_experiment=lambda _id: trial),
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "representative-export",
            feedback_source=object(),
        )


@pytest.mark.parametrize("cohort_size", (100, 500))
def test_higher_cohort_export_replays_exact_six_condition_trial(
    tmp_path, cohort_size
):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial(cohort_size)
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

    manifest = json.loads(result.manifest_path.read_text())
    assert len(result.records) == 6
    assert manifest["creatorCohort"] == cohort_size
    assert manifest["conditionCount"] == 6
    assert manifest["conditions"] == [
        {"conditionId": str(row.id), "runId": str(row.run_id)}
        for row in trial.conditions
    ]


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
        creator_count=original.creator_count,
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


def test_higher_cohort_export_rejects_out_of_order_condition_bindings(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    original = _completed_trial(100)
    conditions = list(original.conditions)
    conditions[2] = replace(conditions[2], condition_index=4)
    conditions[3] = replace(conditions[3], condition_index=3)
    trial = SimpleNamespace(
        id=original.id,
        status=original.status,
        creator_count=100,
        conditions=tuple(conditions),
    )
    bus = InMemoryFeedbackBus()

    with pytest.raises(ValueError, match="exact 2x3 condition matrix"):
        export_completed_performance_trial(
            database=SimpleNamespace(load_performance_experiment=lambda _id: trial),
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=bus,
        )


def test_completed_trial_export_rejects_range_beyond_kafka_high_watermark(tmp_path):
    from babel_online.runtime.performance_export import export_completed_performance_trial

    trial = _completed_trial()
    bus = InMemoryFeedbackBus()
    events = _write_evidence(tmp_path / "conditions", trial, bus)
    source = SimpleNamespace(
        high_watermarks=lambda: {TopicPartition("babel.feedback.v1", 0): 0},
        records=bus.records,
    )
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        canonical_edges=lambda run_id: reconstruct_canonical_edges([events[run_id]]),
    )

    with pytest.raises(ValueError, match="high watermark"):
        export_completed_performance_trial(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            output_root=tmp_path / "export",
            feedback_source=source,
        )


@pytest.mark.parametrize(("cohort_size", "condition_count"), ((50, 9), (100, 6), (500, 6)))
def test_bundle_inputs_select_completed_activation_child_from_registry(
    tmp_path, cohort_size, condition_count
):
    from babel_online.runtime.performance_export import write_trial_bundle_inputs

    trial = _completed_trial(cohort_size)
    population = tmp_path / "performance" / str(trial.id) / "population"
    population.mkdir(parents=True)
    (population / "manifest.json").write_text("{}")
    trial.population_bundle_path = str(population)
    evidence_root = tmp_path / "conditions"
    bus = InMemoryFeedbackBus()
    _write_evidence(evidence_root, trial, bus)
    selected = trial.conditions[5]
    child_id = uuid5(selected.run_id, "selected-child")
    embedding_id = UUID("22222222-2222-5222-8222-222222222222")
    snapshot = "c" * 64
    selected_path = evidence_root / "06/live-evidence.json"
    selected_evidence = json.loads(selected_path.read_text())
    selected_evidence["rawEvidence"]["finalServingIdentity"] = {
        "modelId": str(child_id),
        "modelVersion": 7,
        "embeddingSpaceId": str(embedding_id),
        "pgvectorSnapshotSha256": snapshot,
        "backendSnapshotSha256": snapshot,
    }
    selected_path.write_text(json.dumps(selected_evidence))
    artifact_root = tmp_path / "registry" / f"model-{child_id}"
    artifact_root.mkdir(parents=True)
    descriptor_path = artifact_root / "state-descriptor.json"
    descriptor_path.write_text("{}")
    child_document = {
        "schemaVersion": 2,
        "modelId": str(child_id),
        "parentModelId": str(trial.starting_model_id),
        "producingRunId": str(selected.run_id),
        "immutable": True,
    }
    child = SimpleNamespace(
        modelId=child_id,
        parentModelId=trial.starting_model_id,
        producingRunId=selected.run_id,
        embeddingSpace=SimpleNamespace(embeddingSpaceId=embedding_id),
        model_dump=lambda **_kwargs: child_document,
    )
    descriptor = SimpleNamespace(
        childManifest=child,
        modelVersion=7,
        vectorSnapshotSha256=snapshot,
        immutable=True,
    )
    database = SimpleNamespace(
        load_performance_experiment=lambda _trial_id: trial,
        load_real_child_artifact=lambda model_id: (
            (descriptor, descriptor_path)
            if model_id == child_id
            else (_ for _ in ()).throw(KeyError(model_id))
        ),
    )
    feedback_root = tmp_path / "feedback-export"
    feedback_root.mkdir()
    feedback = feedback_root / "feedback.parquet"
    edges = feedback_root / "edges.parquet"
    manifest = feedback_root / "manifest.json"
    for path in (feedback, edges, manifest):
        path.write_bytes(b"evidence")

    result = write_trial_bundle_inputs(
        database=database,
        experiment_id=trial.id,
        evidence_root=evidence_root,
        feedback_parquet=feedback,
        edges_parquet=edges,
        feedback_export_manifest=manifest,
        selected_condition_index=6,
        output_path=tmp_path / "handoff/trial-bundle-inputs.json",
    )

    inputs = json.loads(result.read_text())
    assert inputs["selectedConditionIndex"] == 6
    assert inputs["trialId"] == str(trial.id)
    assert inputs["schemaVersion"] == 2
    assert inputs["creatorCohort"] == cohort_size
    assert inputs["conditionCount"] == condition_count
    assert inputs["evidencePaths"] == [
        str((evidence_root / f"{index:02d}/live-evidence.json").resolve())
        for index in range(1, condition_count + 1)
    ]
    assert inputs["conditionOrder"] == [
        {
            "conditionIndex": row.condition_index,
            "conditionId": str(row.id),
            "runId": str(row.run_id),
            "topology": row.topology,
            "trainingEnabled": row.training_enabled,
            "activationEnabled": row.activation_enabled,
        }
        for row in trial.conditions
    ]
    assert inputs["populationManifest"] == str((population / "manifest.json").resolve())
    assert inputs["modelArtifactRoot"] == str(artifact_root.resolve())
    assert inputs["pins"] == {
        "modelRepository": "owner/model",
        "modelRevision": "a" * 40,
        "datasetRepository": "owner/dataset",
        "datasetRevision": "b" * 40,
    }
    selected_document = json.loads(Path(inputs["selectedChild"]).read_text())
    assert selected_document == {
        "conditionId": str(selected.id),
        "runId": str(selected.run_id),
        "modelId": str(child_id),
        "parentModelId": str(trial.starting_model_id),
        "modelVersion": 7,
        "vectorSnapshotSha256": snapshot,
    }
    assert json.loads(Path(inputs["modelManifest"]).read_text()) == child_document


def test_bundle_inputs_reject_nonactivation_selection(tmp_path):
    from babel_online.runtime.performance_export import write_trial_bundle_inputs

    trial = _completed_trial()
    database = SimpleNamespace(load_performance_experiment=lambda _trial_id: trial)

    with pytest.raises(ValueError, match="completed activation condition"):
        write_trial_bundle_inputs(
            database=database,
            experiment_id=trial.id,
            evidence_root=tmp_path / "conditions",
            feedback_parquet=tmp_path / "feedback.parquet",
            edges_parquet=tmp_path / "edges.parquet",
            feedback_export_manifest=tmp_path / "manifest.json",
            selected_condition_index=5,
            output_path=tmp_path / "trial-bundle-inputs.json",
        )
