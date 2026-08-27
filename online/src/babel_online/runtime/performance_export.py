"""Aggregate one completed live performance matrix into bounded feedback exports."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ..contracts import FeedbackEventV2
from ..feedback import FeedbackExport, FeedbackRecord, OffsetRange, TopicPartition
from ..feedback.export import export_offset_ranges


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
        by_partition.setdefault(
            (str(record["topic"]), int(record["partition"])), []
        ).append(int(record["offset"]))
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
    if (
        UUID(str(document.get("conditionId"))) != condition.id
        or UUID(str(document.get("runId"))) != condition.run_id
        or int(document.get("requestCount", 0)) <= 0
    ):
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
    if (
        not isinstance(records, list)
        or not records
        or int(kafka.get("recordCount", -1)) != len(records)
        or len(raw.get("measurements", ())) != len(records)
        or not isinstance(ranges, list)
        or ranges != _range_documents_for_records(records)
    ):
        raise ValueError("live feedback acknowledgement count or ranges differ")
    final = kafka.get("finalTrainerState")
    if condition.training_enabled:
        if not isinstance(final, dict) or final.get("available") is not True:
            raise ValueError("training condition lacks final Kafka state")
        if int(final.get("kafkaLag", -1)) != 0:
            raise ValueError("training condition requires zero final Kafka lag")
        if (
            final.get("offsetsCoverPublishedRanges") is not True
            or not isinstance(final.get("nextOffsets"), list)
            or len(str(final.get("checkpointManifestSha256", ""))) != 64
        ):
            raise ValueError("training checkpoint does not cover published feedback ranges")
        next_offsets = {
            (str(row["topic"]), int(row["partition"])): int(row["nextOffset"])
            for row in final["nextOffsets"]
        }
        if any(
            next_offsets.get((row["topic"], row["partition"]), -1)
            < row["endExclusive"]
            for row in ranges
        ):
            raise ValueError("training checkpoint offsets do not cover feedback ranges")
    return records, ranges


def export_completed_performance_trial(
    *,
    database: Any,
    experiment_id: UUID,
    evidence_root: str | Path,
    output_root: str | Path,
    feedback_source: Any,
) -> FeedbackExport:
    """Validate nine live conditions and export their exact Kafka/edge evidence."""
    trial = database.load_performance_experiment(experiment_id)
    if trial.id != experiment_id or trial.status != "completed":
        raise ValueError("performance trial must be durably completed")
    conditions = tuple(sorted(trial.conditions, key=lambda row: row.condition_index))
    expected_matrix = {
        (topology, training_enabled, activation_enabled)
        for topology in ("same_process", "same_host_split", "same_host_isolated")
        for training_enabled, activation_enabled in (
            (False, False),
            (True, False),
            (True, True),
        )
    }
    if (
        len(conditions) != 9
        or [row.condition_index for row in conditions] != list(range(1, 10))
        or any(row.status != "completed" or row.run_id is None for row in conditions)
    ):
        raise ValueError("performance trial lacks nine completed bound conditions")
    if {
        (row.topology, row.training_enabled, row.activation_enabled)
        for row in conditions
    } != expected_matrix:
        raise ValueError("performance trial lacks the exact 3x3 condition matrix")

    expected: dict[tuple[str, int, int], _ExpectedAcknowledgement] = {}
    ranges: list[OffsetRange] = []
    root = Path(evidence_root)
    for condition in conditions:
        records, range_documents = _load_condition_evidence(condition, root)
        for row in records:
            key = (str(row["topic"]), int(row["partition"]), int(row["offset"]))
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
                TopicPartition(str(row["topic"]), int(row["partition"])),
                int(row["startInclusive"]),
                int(row["endExclusive"]),
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
            "conditions": [
                {"conditionId": str(row.id), "runId": str(row.run_id)}
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
    ordered_conditions = tuple(
        sorted(trial.conditions, key=lambda value: value.condition_index)
    )
    if (
        len(ordered_conditions) != 9
        or [row.condition_index for row in ordered_conditions] != list(range(1, 10))
        or any(row.status != "completed" or row.run_id is None for row in ordered_conditions)
    ):
        raise ValueError("trial bundle inputs require nine completed bound conditions")
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
    if len(evidence_paths) != 9 or any(not path.is_file() for path in evidence_paths):
        raise ValueError("trial bundle inputs require all nine condition evidence files")
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
        "schemaVersion": 1,
        "trialId": str(experiment_id),
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


__all__ = ["export_completed_performance_trial", "write_trial_bundle_inputs"]
