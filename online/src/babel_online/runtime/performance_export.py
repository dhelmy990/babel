"""Aggregate one completed live performance matrix into bounded feedback exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

from ..contracts import FeedbackEventV2
from ..feedback import FeedbackExport, FeedbackRecord, OffsetRange, TopicPartition
from ..feedback.export import export_offset_ranges


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPRESENTATIVE_TOPOLOGY_PROFILES = MappingProxyType(
    {
        "representative_same_process_vs_split": (
            "same_process",
            "same_host_split",
        ),
        "representative_split_smoke": ("same_host_split",),
        "representative_isolated_smoke": ("same_host_isolated",),
    }
)


@dataclass(frozen=True, slots=True)
class _ExpectedAcknowledgement:
    run_id: UUID
    event_id: UUID
    request_id: UUID
    key: str


class _VerifiedFeedbackSource:
    def __init__(self, source: Any, expected: dict[tuple[str, int, int], _ExpectedAcknowledgement]):
        self.source = source
        self.expected = expected
        self.seen: set[tuple[str, int, int]] = set()
        high_watermarks = getattr(source, "high_watermarks", None)
        self.high_watermarks = high_watermarks() if callable(high_watermarks) else None

    def records(self, offset_range: OffsetRange) -> tuple[FeedbackRecord, ...]:
        if self.high_watermarks is not None:
            high_watermark = self.high_watermarks.get(offset_range.topic_partition)
            if high_watermark is None or high_watermark < offset_range.end_exclusive:
                raise ValueError("feedback range exceeds the Kafka high watermark")
        records = self.source.records(offset_range)
        expected_offsets = {
            key
            for key in self.expected
            if key[0] == offset_range.topic_partition.topic
            and key[1] == offset_range.topic_partition.partition
            and offset_range.start <= key[2] < offset_range.end_exclusive
        }
        returned_offsets: set[tuple[str, int, int]] = set()
        for record in records:
            key = (record.topic, record.partition, record.offset)
            expected = self.expected.get(key)
            if expected is None or key in self.seen or key in returned_offsets:
                raise ValueError("Kafka replay returned an unexpected feedback offset")
            event = record.event
            if not isinstance(event, FeedbackEventV2):
                raise ValueError("formal feedback export requires V2 events")
            if (
                event.runId != expected.run_id
                or event.eventId != expected.event_id
                or event.requestId != expected.request_id
                or record.key != expected.key
                or str(event.creatorId) != expected.key
            ):
                raise ValueError("Kafka feedback identity differs from live acknowledgement")
            returned_offsets.add(key)
        if returned_offsets != expected_offsets:
            raise ValueError("Kafka replay did not cover every acknowledged feedback event")
        self.seen.update(returned_offsets)
        return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _canonical_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a canonical UUID") from error
    if str(parsed) != value:
        raise ValueError(f"{label} must be a canonical UUID")
    return parsed


def _write_canonical(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(_canonical_json(value), encoding="utf-8")
    with temporary.open("rb") as source:
        os.fsync(source.fileno())
    os.replace(temporary, path)


def _range_documents_for_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_partition: dict[tuple[str, int], list[int]] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("topic"), str)
            or not record["topic"]
            or type(record.get("partition")) is not int
            or record["partition"] < 0
            or type(record.get("offset")) is not int
            or record["offset"] < 0
        ):
            raise ValueError("live feedback offsets must use nonnegative integers")
        by_partition.setdefault(
            (record["topic"], record["partition"]), []
        ).append(record["offset"])
    ranges: list[dict[str, Any]] = []
    for (topic, partition), offsets in sorted(by_partition.items()):
        ordered = sorted(offsets)
        if len(set(ordered)) != len(ordered):
            raise ValueError("live feedback acknowledgements contain duplicate offsets")
        start = previous = ordered[0]
        for offset in ordered[1:]:
            if offset == previous + 1:
                previous = offset
                continue
            ranges.append(
                {
                    "topic": topic,
                    "partition": partition,
                    "startInclusive": start,
                    "endExclusive": previous + 1,
                }
            )
            start = previous = offset
        ranges.append(
            {
                "topic": topic,
                "partition": partition,
                "startInclusive": start,
                "endExclusive": previous + 1,
            }
        )
    return ranges


def _load_condition_evidence(condition: Any, evidence_root: Path):
    path = evidence_root / f"{condition.condition_index:02d}" / "live-evidence.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"condition {condition.condition_index} evidence is unavailable") from error
    try:
        condition_id = UUID(str(document.get("conditionId")))
        run_id = UUID(str(document.get("runId")))
    except (TypeError, ValueError) as error:
        raise ValueError("live condition evidence identity is not a UUID") from error
    request_count = document.get("requestCount")
    if type(request_count) is not int or request_count <= 0:
        raise ValueError("live condition request count must be a positive integer")
    if condition_id != condition.id or run_id != condition.run_id:
        raise ValueError("live condition evidence identity or request count differs")
    raw = document.get("rawEvidence")
    identity = raw.get("conditionIdentity") if isinstance(raw, dict) else None
    if identity != {
        "topology": condition.topology,
        "trainingEnabled": condition.training_enabled,
        "activationEnabled": condition.activation_enabled,
        "retrievalBackend": "pgvector",
    }:
        raise ValueError("live condition identity differs from durable matrix")
    kafka = raw.get("feedbackKafka")
    if not isinstance(kafka, dict):
        raise ValueError("live condition lacks feedback Kafka evidence")
    records = kafka.get("records")
    ranges = kafka.get("offsetRanges")
    record_count = kafka.get("recordCount")
    measurements = raw.get("measurements")
    if (
        not isinstance(records, list)
        or not records
        or type(record_count) is not int
        or record_count != len(records)
        or not isinstance(measurements, list)
        or len(measurements) != len(records)
        or not isinstance(ranges, list)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("topic"), str)
            or not row["topic"]
            or type(row.get("partition")) is not int
            or row["partition"] < 0
            or type(row.get("startInclusive")) is not int
            or row["startInclusive"] < 0
            or type(row.get("endExclusive")) is not int
            or row["endExclusive"] <= row["startInclusive"]
            for row in ranges
        )
        or ranges != _range_documents_for_records(records)
    ):
        raise ValueError(
            "live feedback acknowledgement count and offsets require exact integers"
        )
    if any(
        not isinstance(row, dict)
        or row.get("outcome") != "success"
        or type(row.get("isWarmup")) is not bool
        for row in measurements
    ):
        raise ValueError("live measurements must be successful with exact warmup flags")
    if sum(row["isWarmup"] is False for row in measurements) != request_count:
        raise ValueError("live measured request count differs from requestCount")
    try:
        measurement_request_ids = [
            _canonical_uuid(row.get("requestId"), "measurement requestId")
            for row in measurements
        ]
        record_request_ids = [
            _canonical_uuid(row.get("requestId"), "feedback record requestId")
            for row in records
        ]
    except ValueError as error:
        raise ValueError("live feedback request identities are invalid") from error
    if (
        len(set(measurement_request_ids)) != len(measurement_request_ids)
        or len(set(record_request_ids)) != len(record_request_ids)
        or set(measurement_request_ids) != set(record_request_ids)
    ):
        raise ValueError("live feedback request identities differ from measurements")
    final = kafka.get("finalTrainerState")
    if condition.training_enabled:
        if not isinstance(final, dict) or final.get("available") is not True:
            raise ValueError("training condition lacks final Kafka state")
        if type(final.get("kafkaLag")) is not int or final["kafkaLag"] != 0:
            raise ValueError("training condition requires exactly zero final Kafka lag")
        next_offset_rows = final.get("nextOffsets")
        if (
            final.get("offsetsCoverPublishedRanges") is not True
            or not isinstance(next_offset_rows, list)
            or not next_offset_rows
            or not isinstance(final.get("checkpointManifestSha256"), str)
            or not _SHA256.fullmatch(final["checkpointManifestSha256"])
        ):
            raise ValueError("training checkpoint does not cover published feedback ranges")
        if any(
            not isinstance(row, dict)
            or not isinstance(row.get("topic"), str)
            or not row["topic"]
            or type(row.get("partition")) is not int
            or row["partition"] < 0
            or type(row.get("nextOffset")) is not int
            or row["nextOffset"] < 0
            for row in next_offset_rows
        ):
            raise ValueError("training checkpoint offsets must use nonnegative integers")
        next_offsets = {
            (row["topic"], row["partition"]): row["nextOffset"]
            for row in next_offset_rows
        }
        if len(next_offsets) != len(next_offset_rows) or any(
            next_offsets.get((row["topic"], row["partition"]), -1)
            < row["endExclusive"]
            for row in ranges
        ):
            raise ValueError("training checkpoint offsets do not cover feedback ranges")
    return records, ranges


def _formal_condition_order(creator_cohort: int) -> tuple[tuple[str, bool, bool], ...]:
    if creator_cohort == 50:
        topologies = ("same_process", "same_host_split", "same_host_isolated")
    elif creator_cohort in {100, 500}:
        topologies = ("same_process", "same_host_split")
    else:
        raise ValueError("performance trial creator cohort is not formal")
    return tuple(
        (topology, training_enabled, activation_enabled)
        for topology in topologies
        for training_enabled, activation_enabled in (
            (False, False),
            (True, False),
            (True, True),
        )
    )


def _completed_formal_conditions(trial: Any) -> tuple[Any, ...]:
    if getattr(trial, "evidence_scope", "formal") != "formal":
        raise ValueError("non-formal representative trial cannot enter formal export")
    creator_cohort = int(trial.creator_count)
    expected_order = _formal_condition_order(creator_cohort)
    ordered = tuple(sorted(trial.conditions, key=lambda row: row.condition_index))
    if (
        len(ordered) != len(expected_order)
        or [row.condition_index for row in ordered]
        != list(range(1, len(expected_order) + 1))
        or any(type(row.condition_index) is not int for row in ordered)
        or any(row.status != "completed" or row.run_id is None for row in ordered)
    ):
        raise ValueError("performance trial lacks its completed bound conditions")
    actual_order = tuple(
        (row.topology, row.training_enabled, row.activation_enabled) for row in ordered
    )
    if actual_order != expected_order:
        matrix = "3x3" if creator_cohort == 50 else "2x3"
        raise ValueError(f"performance trial lacks the exact {matrix} condition matrix")
    return ordered


def _completed_representative_conditions(trial: Any) -> tuple[Any, ...]:
    scope = getattr(trial, "evidence_scope", "formal")
    if not isinstance(scope, str) or scope not in _REPRESENTATIVE_TOPOLOGY_PROFILES:
        raise ValueError("performance trial is not an explicitly representative rerun")
    topologies = _REPRESENTATIVE_TOPOLOGY_PROFILES[scope]
    expected_order = tuple(
        (topology, training_enabled, activation_enabled)
        for topology in topologies
        for training_enabled, activation_enabled in (
            (False, False),
            (True, False),
            (True, True),
        )
    )
    ordered = tuple(sorted(trial.conditions, key=lambda row: row.condition_index))
    actual_order = tuple(
        (row.topology, row.training_enabled, row.activation_enabled) for row in ordered
    )
    if (
        len(ordered) != len(expected_order)
        or [row.condition_index for row in ordered]
        != list(range(1, len(expected_order) + 1))
        or any(type(row.condition_index) is not int for row in ordered)
        or any(row.status != "completed" or row.run_id is None for row in ordered)
        or actual_order != expected_order
    ):
        raise ValueError("representative trial lacks its exact completed condition matrix")
    return ordered


def _condition_manifest_binding(condition: Any, evidence_scope: str) -> dict[str, object]:
    binding: dict[str, object] = {
        "conditionId": str(condition.id),
        "runId": str(condition.run_id),
    }
    if evidence_scope == "representative_isolated_smoke":
        binding.update(
            conditionIndex=condition.condition_index,
            formalConditionIndex=condition.condition_index + 6,
        )
    return binding


def _export_completed_conditions(
    *,
    database: Any,
    trial: Any,
    experiment_id: UUID,
    conditions: tuple[Any, ...],
    evidence_scope: str,
    evidence_root: str | Path,
    output_root: str | Path,
    feedback_source: Any,
) -> FeedbackExport:
    creator_cohort = int(trial.creator_count)
    condition_count = len(conditions)

    expected: dict[tuple[str, int, int], _ExpectedAcknowledgement] = {}
    ranges: list[OffsetRange] = []
    root = Path(evidence_root)
    for condition in conditions:
        records, range_documents = _load_condition_evidence(condition, root)
        for row in records:
            key = (row["topic"], row["partition"], row["offset"])
            if key in expected:
                raise ValueError("condition feedback acknowledgements overlap")
            expected[key] = _ExpectedAcknowledgement(
                run_id=condition.run_id,
                event_id=UUID(str(row["eventId"])),
                request_id=UUID(str(row["requestId"])),
                key=str(row["key"]),
            )
        ranges.extend(
            OffsetRange(
                TopicPartition(row["topic"], row["partition"]),
                row["startInclusive"],
                row["endExclusive"],
            )
            for row in range_documents
        )

    verified = _VerifiedFeedbackSource(feedback_source, expected)
    expected_edges = tuple(
        edge
        for condition in conditions
        for edge in database.canonical_edges(condition.run_id)
    )
    result = export_offset_ranges(
        verified,
        ranges,
        output_root,
        expected_edges=expected_edges,
    )
    if verified.seen != set(expected):
        raise ValueError("Kafka replay did not cover every acknowledged feedback event")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "experimentId": str(experiment_id),
            "creatorCohort": creator_cohort,
            "conditionCount": condition_count,
            "evidenceScope": evidence_scope,
            "formalPerformanceClaim": evidence_scope == "formal",
            "conditions": [
                _condition_manifest_binding(row, evidence_scope)
                for row in conditions
            ],
            "feedbackParquet": {
                "path": result.parquet_path.name,
                "rows": len(result.records),
                "sha256": _sha256(result.parquet_path),
            },
            "edgesParquet": {
                "path": result.edge_parquet_path.name,
                "rows": len(expected_edges),
                "sha256": _sha256(result.edge_parquet_path),
            },
        }
    )
    result.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with result.manifest_path.open("rb") as source:
        os.fsync(source.fileno())
    return result


def export_completed_performance_trial(
    *,
    database: Any,
    experiment_id: UUID,
    evidence_root: str | Path,
    output_root: str | Path,
    feedback_source: Any,
) -> FeedbackExport:
    """Validate one formal live cohort and export its exact Kafka/edge evidence."""
    trial = database.load_performance_experiment(experiment_id)
    if trial.id != experiment_id or trial.status != "completed":
        raise ValueError("performance trial must be durably completed")
    conditions = _completed_formal_conditions(trial)
    return _export_completed_conditions(
        database=database,
        trial=trial,
        experiment_id=experiment_id,
        conditions=conditions,
        evidence_scope="formal",
        evidence_root=evidence_root,
        output_root=output_root,
        feedback_source=feedback_source,
    )


def export_completed_representative_trial(
    *,
    database: Any,
    experiment_id: UUID,
    evidence_root: str | Path,
    output_root: str | Path,
    feedback_source: Any,
) -> FeedbackExport:
    """Export a completed rerun while preserving its non-formal evidence label."""
    trial = database.load_performance_experiment(experiment_id)
    if trial.id != experiment_id or trial.status != "completed":
        raise ValueError("performance trial must be durably completed")
    conditions = _completed_representative_conditions(trial)
    return _export_completed_conditions(
        database=database,
        trial=trial,
        experiment_id=experiment_id,
        conditions=conditions,
        evidence_scope=trial.evidence_scope,
        evidence_root=evidence_root,
        output_root=output_root,
        feedback_source=feedback_source,
    )


def write_trial_bundle_inputs(
    *,
    database: Any,
    experiment_id: UUID,
    evidence_root: str | Path,
    feedback_parquet: str | Path,
    edges_parquet: str | Path,
    feedback_export_manifest: str | Path,
    selected_condition_index: int,
    output_path: str | Path,
) -> Path:
    """Close one operator-selected activation child into build-ready local inputs."""
    trial = database.load_performance_experiment(experiment_id)
    if trial.id != experiment_id or trial.status != "completed":
        raise ValueError("performance trial must be durably completed")
    creator_cohort = int(trial.creator_count)
    ordered_conditions = _completed_formal_conditions(trial)
    condition_count = len(ordered_conditions)
    matches = [
        row
        for row in ordered_conditions
        if row.condition_index == selected_condition_index
    ]
    if (
        len(matches) != 1
        or matches[0].status != "completed"
        or not matches[0].training_enabled
        or not matches[0].activation_enabled
        or matches[0].run_id is None
    ):
        raise ValueError("selected child must come from one completed activation condition")
    condition = matches[0]
    root = Path(evidence_root)
    evidence_paths = tuple(
        (root / f"{row.condition_index:02d}" / "live-evidence.json").resolve()
        for row in ordered_conditions
    )
    if len(evidence_paths) != condition_count or any(
        not path.is_file() for path in evidence_paths
    ):
        raise ValueError("trial bundle inputs require every condition evidence file")
    try:
        evidence = json.loads(
            (root / f"{selected_condition_index:02d}" / "live-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        final = evidence["rawEvidence"]["finalServingIdentity"]
        model_id = UUID(str(final["modelId"]))
        model_version = int(final["modelVersion"])
        embedding_space_id = UUID(str(final["embeddingSpaceId"]))
        vector_snapshot = str(final["pgvectorSnapshotSha256"])
        backend_snapshot = str(final["backendSnapshotSha256"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("selected condition final serving identity is invalid") from error
    if (
        UUID(str(evidence.get("conditionId"))) != condition.id
        or UUID(str(evidence.get("runId"))) != condition.run_id
        or model_version <= 0
        or len(vector_snapshot) != 64
        or vector_snapshot != backend_snapshot
    ):
        raise ValueError("selected condition final serving identity differs")

    descriptor, descriptor_path = database.load_real_child_artifact(model_id)
    descriptor_path = Path(descriptor_path).resolve()
    child = descriptor.childManifest
    if (
        not descriptor_path.is_file()
        or descriptor_path.name != "state-descriptor.json"
        or child.modelId != model_id
        or child.parentModelId != trial.starting_model_id
        or child.producingRunId != condition.run_id
        or child.embeddingSpace.embeddingSpaceId != embedding_space_id
        or int(descriptor.modelVersion) != model_version
        or str(descriptor.vectorSnapshotSha256) != vector_snapshot
        or descriptor.immutable is not True
    ):
        raise ValueError("selected child descriptor differs from final serving identity")

    population = Path(str(trial.population_bundle_path or "")) / "manifest.json"
    source_paths = (
        population,
        Path(feedback_parquet),
        Path(edges_parquet),
        Path(feedback_export_manifest),
    )
    if any(not path.is_file() for path in source_paths):
        raise ValueError("trial bundle source evidence is unavailable")

    destination = Path(output_path).resolve()
    selected_path = destination.parent / "selected-child.json"
    model_manifest_path = destination.parent / "model-manifest.json"
    selected_document = {
        "conditionId": str(condition.id),
        "runId": str(condition.run_id),
        "modelId": str(model_id),
        "parentModelId": str(child.parentModelId),
        "modelVersion": model_version,
        "vectorSnapshotSha256": vector_snapshot,
    }
    _write_canonical(selected_path, selected_document)
    _write_canonical(model_manifest_path, child.model_dump(mode="json"))
    document = {
        "schemaVersion": 2,
        "trialId": str(experiment_id),
        "creatorCohort": creator_cohort,
        "conditionCount": condition_count,
        "conditionOrder": [
            {
                "conditionIndex": row.condition_index,
                "conditionId": str(row.id),
                "runId": str(row.run_id),
                "topology": row.topology,
                "trainingEnabled": row.training_enabled,
                "activationEnabled": row.activation_enabled,
            }
            for row in ordered_conditions
        ],
        "selectedConditionIndex": selected_condition_index,
        "evidencePaths": [str(path) for path in evidence_paths],
        "populationManifest": str(population.resolve()),
        "feedbackParquet": str(Path(feedback_parquet).resolve()),
        "edgesParquet": str(Path(edges_parquet).resolve()),
        "feedbackExportManifest": str(Path(feedback_export_manifest).resolve()),
        "modelManifest": str(model_manifest_path),
        "modelArtifactRoot": str(descriptor_path.parent),
        "selectedChild": str(selected_path),
        "pins": {
            "modelRepository": trial.model_repository,
            "modelRevision": trial.model_revision,
            "datasetRepository": trial.dataset_repository,
            "datasetRevision": trial.dataset_revision,
        },
    }
    _write_canonical(destination, document)
    return destination


__all__ = [
    "export_completed_performance_trial",
    "export_completed_representative_trial",
    "write_trial_bundle_inputs",
]
