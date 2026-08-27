from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from babel_benchmark.trial_bundle import (
    FormalPins,
    build_formal_trial_bundle,
    load_formal_trial_bundle_inputs,
)


TRIAL_ID = UUID("00000000-0000-5000-8000-000000000130")
ORIGINAL_ID = UUID("00000000-0000-5000-8000-000000000131")
CHILD_ID = UUID("00000000-0000-5000-8000-000000000132")
EMBEDDING_ID = UUID("00000000-0000-5000-8000-000000000133")
MODEL_REPO = "dhelmy990/babel-qwen-navigation-2016-interview"
MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
DATASET_REPO = "dhelmy990/babel-wikipedia-experiment"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
WORKLOAD = tuple(character * 64 for character in "abcdef")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measurement(run_id: UUID, condition_id: str, index: int) -> dict[str, object]:
    start = 1_000_000 + index * 10_000
    total = 2_000 + index
    timings = {
        "queue": 10,
        "encode": 100,
        "context": 100,
        "ann": 100,
        "filtering": 100,
        "serialization": 100,
        "serverTotal": 600,
    }
    return {
        "schemaVersion": 2,
        "benchmarkRunId": str(run_id),
        "conditionId": condition_id,
        "requestId": str(uuid5(run_id, f"request:{index}")),
        "scheduleIndex": index,
        "scheduleMode": "open_loop",
        "intendedStartMonotonicNs": start,
        "actualStartMonotonicNs": start,
        "completedAtMonotonicNs": start + total,
        "queueDelayNs": 0,
        "inFlightAtStart": 1,
        "clientTotalNs": total,
        "clientOverheadNs": total - 600,
        "isWarmup": False,
        "outcome": "success",
        "httpStatus": 200,
        "errorType": None,
        "serverTimingsNs": timings,
        "cacheStatus": "hit",
        "sourceVectorOrigin": "cache_hit",
        "modelId": str(ORIGINAL_ID),
        "servingModelVersion": 0,
        "trainerModelVersion": 0,
        "versionStaleness": 0,
        "retrievalBackend": "pgvector",
        "datasetSnapshotSha256": "1" * 64,
        "pgvectorSnapshotSha256": "2" * 64,
        "backendSnapshotSha256": "2" * 64,
        "queryVectorSha256": "3" * 64,
        "candidateCount": 10,
    }


def _resource(run_id: UUID, condition_id: str, index: int) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "benchmarkRunId": str(run_id),
        "conditionId": condition_id,
        "observedAtMonotonicNs": 2_000_000 + index,
        "service": "serving",
        "pid": 100 + index,
        "cpuPercent": 1.0,
        "rssBytes": 1024,
        "threadCount": 1,
        "processReadBytes": 0,
        "processWriteBytes": 0,
        "hostMemoryUsedBytes": 2048,
        "hostDiskReadBytes": 0,
        "hostDiskWriteBytes": 0,
        "hostNetworkRxBytes": 0,
        "hostNetworkTxBytes": 0,
        "gpuAvailable": False,
        "gpuUtilizationPercent": None,
        "gpuMemoryUsedBytes": None,
        "kafkaLag": 0,
        "trainingStepRate": 0.0,
        "checkpointVersion": None,
        "activationVersion": None,
        "trainerVersion": 0,
        "servingVersion": 0,
        "versionStaleness": 0,
    }


def _evidence_files(
    tmp_path: Path, *, cohort_size: int = 50, selected_condition_index: int = 3
) -> tuple[list[Path], dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    selected: dict[str, object] | None = None
    modes = ((False, False), (True, False), (True, True))
    condition_index = 0
    topologies = (
        ("same_process", "same_host_split", "same_host_isolated")
        if cohort_size == 50
        else ("same_process", "same_host_split")
    )
    for topology in topologies:
        for training, activation in modes:
            condition_index += 1
            run_id = uuid5(TRIAL_ID, f"condition:{condition_index}")
            condition_uuid = uuid5(TRIAL_ID, f"condition-row:{condition_index}")
            training_name = "training" if training else "serving"
            activation_name = "activation" if activation else "no_activation"
            stable = f"{topology}.{training_name}.{activation_name}.pgvector"
            final_model = (
                CHILD_ID if condition_index == selected_condition_index else ORIGINAL_ID
            )
            document = {
                "conditionId": str(condition_uuid),
                "runId": str(run_id),
                "requestCount": 1,
                "p95Ms": (2_000 + condition_index) / 1_000_000,
                "rawEvidence": {
                    "conditionIdentity": {
                        "topology": topology,
                        "trainingEnabled": training,
                        "activationEnabled": activation,
                        "retrievalBackend": "pgvector",
                    },
                    "workloadIdentity": list(WORKLOAD),
                    "warmupCount": 0,
                    "measurements": [_measurement(run_id, stable, condition_index)],
                    "resources": [_resource(run_id, stable, condition_index)],
                    "placement": {
                        "schemaVersion": 1,
                        "requestedTopology": topology,
                        "actualTopology": topology,
                        "processes": [{"role": "serving", "pid": 100 + condition_index}],
                    },
                    "observedActivationTargets": [],
                    "finalServingIdentity": {
                        "modelId": str(final_model),
                        "modelVersion": 1 if final_model == CHILD_ID else 0,
                        "embeddingSpaceId": str(EMBEDDING_ID),
                        "pgvectorSnapshotSha256": "4" * 64,
                        "backendSnapshotSha256": "4" * 64,
                    },
                },
            }
            path = tmp_path / f"condition-{condition_index}.json"
            path.write_text(json.dumps(document))
            paths.append(path)
            if final_model == CHILD_ID:
                selected = {
                    "conditionId": str(condition_uuid),
                    "runId": str(run_id),
                    "modelId": str(CHILD_ID),
                    "parentModelId": str(ORIGINAL_ID),
                    "modelVersion": 1,
                    "vectorSnapshotSha256": "4" * 64,
                }
    assert selected is not None
    return paths, selected


def _population(tmp_path: Path, *, cohort_size: int = 50) -> Path:
    path = tmp_path / "population-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "experimentId": str(TRIAL_ID),
                "babelCount": 10_000,
                "scheduleCount": 10_000,
                "creatorCount": cohort_size,
                "embeddingDimension": 100,
                "modelId": str(ORIGINAL_ID),
                "artifactRepo": MODEL_REPO,
                "artifactRevision": MODEL_REVISION,
                "datasetRepo": DATASET_REPO,
                "datasetRevision": DATASET_REVISION,
                "vectorsSha256": "5" * 64,
                "pgvectorSnapshotSha256": "6" * 64,
            }
        )
    )
    return path


def _feedback_export(
    tmp_path: Path,
    evidence_paths: list[Path],
    *,
    cohort_size: int = 50,
    wrong_run: bool = False,
    extra_feedback_column: bool = False,
) -> tuple[Path, Path, Path]:
    conditions = []
    feedback_rows = []
    for index, evidence_path in enumerate(evidence_paths):
        document = json.loads(evidence_path.read_text())
        run_id = document["runId"]
        conditions.append(
            {"conditionId": document["conditionId"], "runId": run_id}
        )
        if wrong_run and index == 0:
            run_id = str(UUID("ffffffff-ffff-5fff-8fff-ffffffffffff"))
        row = {
                "topic": "babel.feedback.v1",
                "partition": 0,
                "offset": index,
                "key": str(uuid5(TRIAL_ID, f"creator:{index}")),
                "schemaVersion": 2,
                "eventId": str(uuid5(TRIAL_ID, f"event:{index}")),
                "runId": run_id,
                "requestId": str(uuid5(TRIAL_ID, f"request:{index}")),
                "creatorId": str(uuid5(TRIAL_ID, f"creator:{index}")),
                "sourceBabelId": str(uuid5(TRIAL_ID, f"source:{index}")),
                "sourceArticleKey": f"enwiki:{index + 1}",
                "traversalSessionId": str(uuid5(TRIAL_ID, f"session:{index}")),
                "parentRequestId": None,
                "traversalDepth": 0,
                "modelId": str(ORIGINAL_ID),
                "modelVersion": 0,
                "embeddingSpaceId": str(EMBEDDING_ID),
                "retrievalBackend": "pgvector",
                "sourceVectorOrigin": "cache_hit",
                "candidateActions": [],
                "occurredAtNs": index + 1,
            }
        if extra_feedback_column:
            row["unbound"] = "not-formal"
        feedback_rows.append(row)
    feedback = tmp_path / "feedback.parquet"
    pq.write_table(pa.Table.from_pylist(feedback_rows), feedback)
    first = feedback_rows[0]
    edge = tmp_path / "edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "runId": first["runId"],
                    "sourceBabelId": first["sourceBabelId"],
                    "targetBabelId": str(uuid5(TRIAL_ID, "target:0")),
                    "actingCreatorId": first["creatorId"],
                    "requestId": first["requestId"],
                    "feedbackEventId": first["eventId"],
                    "feedbackOccurredAtNs": 1,
                    "traversalSessionId": str(uuid5(TRIAL_ID, "session:0")),
                    "traversalDepth": 1,
                }
            ]
        ),
        edge,
    )
    manifest = tmp_path / "feedback-export-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "experimentId": str(TRIAL_ID),
                "creatorCohort": cohort_size,
                "conditionCount": len(conditions),
                "conditions": conditions,
                "feedbackParquet": {
                    "path": "feedback.parquet",
                    "rows": len(feedback_rows),
                    "sha256": _sha(feedback),
                },
                "edgesParquet": {
                    "path": "edges.parquet",
                    "rows": 1,
                    "sha256": _sha(edge),
                },
            }
        )
    )
    return feedback, edge, manifest


def _model_artifact(tmp_path: Path, selected: dict[str, object]) -> tuple[Path, Path]:
    child = {
        "schemaVersion": 2,
        "modelId": selected["modelId"],
        "parentModelId": selected["parentModelId"],
        "producingRunId": selected["runId"],
        "immutable": True,
    }
    manifest = tmp_path / "model-manifest.json"
    manifest.write_text(json.dumps(child))
    root = tmp_path / "model-artifact"
    root.mkdir()
    state = root / "online-state.json"
    state.write_text(json.dumps({"version": 1}))
    digest = hashlib.sha256(state.read_bytes()).hexdigest()
    (root / "state-descriptor.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "childManifest": child,
                "onlineStatePath": "online-state.json",
                "files": {"online-state.json": digest},
                "immutable": True,
            }
        )
    )
    return manifest, root


def _build(
    tmp_path: Path,
    *,
    cohort_size: int = 50,
    selected_condition_index: int = 3,
    mutate=None,
    feedback_mutate=None,
    declared_order_mutate=None,
):
    evidence, selected = _evidence_files(
        tmp_path,
        cohort_size=cohort_size,
        selected_condition_index=selected_condition_index,
    )
    if mutate is not None:
        mutate(evidence)
    declared_order = []
    for index, path in enumerate(evidence, start=1):
        document = json.loads(path.read_text())
        identity = document["rawEvidence"]["conditionIdentity"]
        declared_order.append(
            {
                "conditionIndex": index,
                "conditionId": document["conditionId"],
                "runId": document["runId"],
                "topology": identity["topology"],
                "trainingEnabled": identity["trainingEnabled"],
                "activationEnabled": identity["activationEnabled"],
            }
        )
    if declared_order_mutate is not None:
        declared_order_mutate(declared_order)
    selected_path = tmp_path / "selected-child.json"
    selected_path.write_text(json.dumps(selected))
    model_manifest, model_root = _model_artifact(tmp_path, selected)
    feedback, edges, feedback_manifest = _feedback_export(
        tmp_path, evidence, cohort_size=cohort_size
    )
    if feedback_mutate is not None:
        feedback_mutate(feedback_manifest)
    return build_formal_trial_bundle(
        tmp_path / "accepted",
        trial_id=TRIAL_ID,
        evidence_paths=evidence,
        population_manifest_path=_population(tmp_path, cohort_size=cohort_size),
        feedback_parquet=feedback,
        edges_parquet=edges,
        feedback_export_manifest_path=feedback_manifest,
        model_manifest=model_manifest,
        model_artifact_root=model_root,
        selected_child_path=selected_path,
        pins=FormalPins(MODEL_REPO, MODEL_REVISION, DATASET_REPO, DATASET_REVISION),
        expected_creator_cohort=cohort_size,
        expected_condition_order=declared_order,
    )


def test_formal_trial_bundle_exports_exact_aggregate_matrix(tmp_path: Path) -> None:
    bundle = _build(tmp_path)

    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["runId"] == str(TRIAL_ID)
    assert manifest["topology"] == "3x3_matrix"
    assert manifest["acceptanceLabel"] == "formal"
    evidence = manifest["trialEvidence"]
    assert evidence["conditionCount"] == 9
    assert evidence["population"]["rows"] == 10_000
    assert evidence["workloadIdentity"] == list(WORKLOAD)
    assert evidence["zeroErrors"] is True
    assert evidence["selectedChild"]["modelId"] == str(CHILD_ID)
    assert evidence["feedbackExport"]["experimentId"] == str(TRIAL_ID)
    assert len(evidence["conditions"]) == 9
    assert pq.read_table(bundle.root / "requests.parquet").num_rows == 9
    assert pq.read_table(bundle.root / "resources.parquet").num_rows == 9
    summary = json.loads((bundle.root / "summary.json").read_text())
    assert summary["conditionCount"] == 9
    assert summary["requestCount"] == 9
    assert summary["errorCount"] == 0
    assert set(summary["interferenceByTopology"]) == {
        "same_process",
        "same_host_split",
        "same_host_isolated",
    }
    assert "Formal 3x3 recommendation matrix" in (bundle.root / "report.md").read_text()


@pytest.mark.parametrize("cohort_size", (100, 500))
def test_higher_cohort_bundle_records_exact_six_condition_order(
    tmp_path: Path, cohort_size: int
) -> None:
    bundle = _build(
        tmp_path,
        cohort_size=cohort_size,
        selected_condition_index=6,
    )

    manifest = json.loads(bundle.manifest_path.read_text())
    assert manifest["topology"] == "2x3_matrix"
    evidence = manifest["trialEvidence"]
    assert evidence["creatorCohort"] == cohort_size
    assert evidence["conditionCount"] == 6
    assert [row["conditionIndex"] for row in evidence["conditionOrder"]] == list(
        range(1, 7)
    )
    assert [row["conditionIdentity"]["topology"] for row in evidence["conditions"]] == [
        "same_process",
        "same_process",
        "same_process",
        "same_host_split",
        "same_host_split",
        "same_host_split",
    ]
    assert evidence["selectedChild"]["conditionId"] == evidence["conditionOrder"][5][
        "conditionId"
    ]
    summary = json.loads((bundle.root / "summary.json").read_text())
    assert summary["creatorCohort"] == cohort_size
    assert summary["conditionCount"] == 6
    assert summary["topology"] == "2x3_matrix"
    assert pq.read_table(bundle.root / "requests.parquet").num_rows == 6
    assert pq.read_table(bundle.root / "resources.parquet").num_rows == 6
    assert set(summary["interferenceByTopology"]) == {
        "same_process",
        "same_host_split",
    }
    assert "Formal 2x3 recommendation matrix" in (bundle.root / "report.md").read_text()


def test_higher_cohort_input_manifest_loads_exact_trial_bound_order(
    tmp_path: Path,
) -> None:
    evidence, _selected = _evidence_files(
        tmp_path / "evidence", cohort_size=100, selected_condition_index=6
    )
    order = []
    for index, path in enumerate(evidence, start=1):
        document = json.loads(path.read_text())
        identity = document["rawEvidence"]["conditionIdentity"]
        order.append(
            {
                "conditionIndex": index,
                "conditionId": document["conditionId"],
                "runId": document["runId"],
                "topology": identity["topology"],
                "trainingEnabled": identity["trainingEnabled"],
                "activationEnabled": identity["activationEnabled"],
            }
        )
    document = {
        "schemaVersion": 2,
        "trialId": str(TRIAL_ID),
        "creatorCohort": 100,
        "conditionCount": 6,
        "conditionOrder": order,
        "selectedConditionIndex": 6,
        "evidencePaths": [str(path.resolve()) for path in evidence],
        "populationManifest": str((tmp_path / "population.json").resolve()),
        "feedbackParquet": str((tmp_path / "feedback.parquet").resolve()),
        "edgesParquet": str((tmp_path / "edges.parquet").resolve()),
        "feedbackExportManifest": str((tmp_path / "feedback-manifest.json").resolve()),
        "modelManifest": str((tmp_path / "model-manifest.json").resolve()),
        "modelArtifactRoot": str((tmp_path / "model-artifact").resolve()),
        "selectedChild": str((tmp_path / "selected-child.json").resolve()),
        "pins": {
            "modelRepository": MODEL_REPO,
            "modelRevision": MODEL_REVISION,
            "datasetRepository": DATASET_REPO,
            "datasetRevision": DATASET_REVISION,
        },
    }
    path = tmp_path / "trial-bundle-inputs.json"
    path.write_text(json.dumps(document))

    loaded = load_formal_trial_bundle_inputs(path)

    assert loaded.creator_cohort == 100
    assert loaded.selected_condition_index == 6
    assert len(loaded.evidence_paths) == 6
    assert [row["conditionIndex"] for row in loaded.condition_order] == list(range(1, 7))


def test_higher_cohort_bundle_rejects_feedback_export_for_another_cohort(
    tmp_path: Path,
) -> None:
    def drift(path: Path) -> None:
        document = json.loads(path.read_text())
        document["creatorCohort"] = 500
        path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="feedback export cohort"):
        _build(
            tmp_path,
            cohort_size=100,
            selected_condition_index=6,
            feedback_mutate=drift,
        )


def test_higher_cohort_bundle_rejects_reordered_evidence_paths(tmp_path: Path) -> None:
    def reorder(paths: list[Path]) -> None:
        paths[2], paths[3] = paths[3], paths[2]

    with pytest.raises(ValueError, match="exact 2x3 matrix order"):
        _build(
            tmp_path,
            cohort_size=100,
            selected_condition_index=6,
            mutate=reorder,
        )


def test_higher_cohort_bundle_rejects_cross_bound_handoff_condition(
    tmp_path: Path,
) -> None:
    def bind_another_run(order: list[dict[str, object]]) -> None:
        order[5]["runId"] = str(uuid5(TRIAL_ID, "another-trial-run"))

    with pytest.raises(ValueError, match="declared condition order binding differs"):
        _build(
            tmp_path,
            cohort_size=100,
            selected_condition_index=6,
            declared_order_mutate=bind_another_run,
        )


def test_formal_trial_bundle_rejects_any_failed_measurement(tmp_path: Path) -> None:
    def fail_one(paths: list[Path]) -> None:
        document = json.loads(paths[0].read_text())
        row = document["rawEvidence"]["measurements"][0]
        row.update(
            outcome="error",
            errorType="HTTPError",
            httpStatus=500,
            clientOverheadNs=None,
            serverTimingsNs=None,
            modelId=None,
            servingModelVersion=None,
            trainerModelVersion=None,
            versionStaleness=None,
            retrievalBackend=None,
            datasetSnapshotSha256=None,
            pgvectorSnapshotSha256=None,
            backendSnapshotSha256=None,
            queryVectorSha256=None,
            candidateCount=None,
        )
        paths[0].write_text(json.dumps(document))

    with pytest.raises(ValueError, match="zero request errors"):
        _build(tmp_path, mutate=fail_one)


def test_formal_trial_bundle_rejects_workload_or_pin_drift(tmp_path: Path) -> None:
    def drift(paths: list[Path]) -> None:
        document = json.loads(paths[-1].read_text())
        document["rawEvidence"]["workloadIdentity"][0] = "0" * 64
        paths[-1].write_text(json.dumps(document))

    with pytest.raises(ValueError, match="one frozen workload"):
        _build(tmp_path, mutate=drift)

    evidence, selected = _evidence_files(tmp_path / "second")
    selected_path = tmp_path / "selected.json"
    selected_path.write_text(json.dumps(selected))
    second_model = tmp_path / "second-model"
    second_model.mkdir()
    model_manifest, model_root = _model_artifact(second_model, selected)
    feedback, edges, feedback_manifest = _feedback_export(tmp_path / "second", evidence)
    with pytest.raises(ValueError, match="immutable model pin"):
        build_formal_trial_bundle(
            tmp_path / "accepted-two",
            trial_id=TRIAL_ID,
            evidence_paths=evidence,
            population_manifest_path=_population(tmp_path),
            feedback_parquet=feedback,
            edges_parquet=edges,
            feedback_export_manifest_path=feedback_manifest,
            model_manifest=model_manifest,
            model_artifact_root=model_root,
            selected_child_path=selected_path,
            pins=FormalPins("wrong/repository", MODEL_REVISION, DATASET_REPO, DATASET_REVISION),
        )


def test_formal_trial_bundle_rejects_feedback_from_another_run(tmp_path: Path) -> None:
    evidence, selected = _evidence_files(tmp_path)
    selected_path = tmp_path / "selected-child.json"
    selected_path.write_text(json.dumps(selected))
    model_manifest, model_root = _model_artifact(tmp_path, selected)
    feedback, edges, feedback_manifest = _feedback_export(
        tmp_path, evidence, wrong_run=True
    )

    with pytest.raises(ValueError, match="feedback run identities differ"):
        build_formal_trial_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            evidence_paths=evidence,
            population_manifest_path=_population(tmp_path),
            feedback_parquet=feedback,
            edges_parquet=edges,
            feedback_export_manifest_path=feedback_manifest,
            model_manifest=model_manifest,
            model_artifact_root=model_root,
            selected_child_path=selected_path,
            pins=FormalPins(MODEL_REPO, MODEL_REVISION, DATASET_REPO, DATASET_REVISION),
        )


def test_formal_trial_bundle_rejects_feedback_schema_drift(tmp_path: Path) -> None:
    evidence, selected = _evidence_files(tmp_path)
    selected_path = tmp_path / "selected-child.json"
    selected_path.write_text(json.dumps(selected))
    model_manifest, model_root = _model_artifact(tmp_path, selected)
    feedback, edges, feedback_manifest = _feedback_export(
        tmp_path, evidence, extra_feedback_column=True
    )

    with pytest.raises(ValueError, match="schema differs"):
        build_formal_trial_bundle(
            tmp_path / "accepted",
            trial_id=TRIAL_ID,
            evidence_paths=evidence,
            population_manifest_path=_population(tmp_path),
            feedback_parquet=feedback,
            edges_parquet=edges,
            feedback_export_manifest_path=feedback_manifest,
            model_manifest=model_manifest,
            model_artifact_root=model_root,
            selected_child_path=selected_path,
            pins=FormalPins(MODEL_REPO, MODEL_REVISION, DATASET_REPO, DATASET_REVISION),
        )
