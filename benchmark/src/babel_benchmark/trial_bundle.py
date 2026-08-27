"""Closed trial-level export for one accepted formal cohort matrix."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from .contracts import ConditionIdentityV2, RequestMeasurementV2
from .hub import RunBundle, build_run_bundle
from .resources import ResourceObservationV1


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _expected_condition_order(cohort_size: int) -> tuple[tuple[str, bool, bool], ...]:
    if cohort_size == 50:
        topologies = ("same_process", "same_host_split", "same_host_isolated")
    elif cohort_size in {100, 500}:
        topologies = ("same_process", "same_host_split")
    else:
        raise ValueError("formal creator cohort must be 50, 100, or 500")
    return tuple(
        (topology, training, activation)
        for topology in topologies
        for training, activation in ((False, False), (True, False), (True, True))
    )


@dataclass(frozen=True, slots=True)
class FormalPins:
    model_repository: str
    model_revision: str
    dataset_repository: str
    dataset_revision: str

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.model_repository) or not _REVISION.fullmatch(
            self.model_revision
        ):
            raise ValueError("immutable model pin is invalid")
        if not _REPOSITORY.fullmatch(self.dataset_repository) or not _REVISION.fullmatch(
            self.dataset_revision
        ):
            raise ValueError("immutable dataset pin is invalid")

    def as_document(self) -> dict[str, str]:
        return {
            "modelRepository": self.model_repository,
            "modelRevision": self.model_revision,
            "datasetRepository": self.dataset_repository,
            "datasetRevision": self.dataset_revision,
        }


@dataclass(frozen=True, slots=True)
class FormalTrialBundleInputs:
    trial_id: UUID
    creator_cohort: int
    selected_condition_index: int
    condition_order: tuple[dict[str, Any], ...]
    evidence_paths: tuple[Path, ...]
    population_manifest: Path
    feedback_parquet: Path
    edges_parquet: Path
    feedback_export_manifest: Path
    model_manifest: Path
    model_artifact_root: Path
    selected_child: Path
    pins: FormalPins


@dataclass(frozen=True, slots=True)
class _ConditionEvidence:
    condition_id: UUID
    run_id: UUID
    identity: ConditionIdentityV2
    workload_identity: tuple[str, ...]
    measurements: tuple[RequestMeasurementV2, ...]
    resources: tuple[ResourceObservationV1, ...]
    placement: dict[str, Any]
    final_serving_identity: dict[str, Any]
    request_count: int
    p95_ms: float

    def aggregate_row(self) -> dict[str, Any]:
        return {
            "conditionId": str(self.condition_id),
            "runId": str(self.run_id),
            "conditionIdentity": self.identity.model_dump(mode="json"),
            "requestCount": self.request_count,
            "measurementCount": len(self.measurements),
            "resourceCount": len(self.resources),
            "p95Ms": self.p95_ms,
            "placement": self.placement,
            "finalServingIdentity": self.final_serving_identity,
        }


def _object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be readable JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def load_formal_trial_bundle_inputs(path: str | Path) -> FormalTrialBundleInputs:
    """Load the closed local-path handoff emitted by performance-export."""
    document = _object(path, "trial bundle inputs")
    legacy_required = {
        "schemaVersion",
        "trialId",
        "selectedConditionIndex",
        "evidencePaths",
        "populationManifest",
        "feedbackParquet",
        "edgesParquet",
        "feedbackExportManifest",
        "modelManifest",
        "modelArtifactRoot",
        "selectedChild",
        "pins",
    }
    schema_version = int(document.get("schemaVersion", 0))
    required = (
        legacy_required
        if schema_version == 1
        else legacy_required | {"creatorCohort", "conditionCount", "conditionOrder"}
    )
    if schema_version not in {1, 2} or set(document) != required:
        raise ValueError("trial bundle input contract differs")
    creator_cohort = int(document.get("creatorCohort", 50))
    expected_order = _expected_condition_order(creator_cohort)
    condition_count = len(expected_order)
    raw_order = document.get("conditionOrder", [])
    if schema_version == 2:
        if document.get("conditionCount") != condition_count or not isinstance(
            raw_order, list
        ) or len(raw_order) != condition_count:
            raise ValueError("trial bundle input condition count differs")
        normalized_order: list[dict[str, Any]] = []
        bindings: set[tuple[UUID, UUID]] = set()
        for index, (row, expected_identity) in enumerate(
            zip(raw_order, expected_order, strict=True), start=1
        ):
            required_order = {
                "conditionIndex",
                "conditionId",
                "runId",
                "topology",
                "trainingEnabled",
                "activationEnabled",
            }
            if not isinstance(row, dict) or set(row) != required_order:
                raise ValueError("trial bundle input condition order differs")
            condition_id = UUID(str(row["conditionId"]))
            run_id = UUID(str(row["runId"]))
            identity = (
                row["topology"],
                row["trainingEnabled"],
                row["activationEnabled"],
            )
            if int(row["conditionIndex"]) != index or identity != expected_identity:
                raise ValueError("trial bundle input condition order differs")
            bindings.add((condition_id, run_id))
            normalized_order.append(dict(row))
        if len(bindings) != condition_count:
            raise ValueError("trial bundle input condition bindings are not unique")
        condition_order = tuple(normalized_order)
    else:
        condition_order = ()
    evidence = document.get("evidencePaths")
    pins = document.get("pins")
    if (
        not isinstance(evidence, list)
        or len(evidence) != condition_count
        or not all(isinstance(value, str) and value for value in evidence)
        or not isinstance(pins, dict)
        or set(pins)
        != {
            "modelRepository",
            "modelRevision",
            "datasetRepository",
            "datasetRevision",
        }
    ):
        raise ValueError("trial bundle input fields differ")
    path_fields = {
        name: document.get(name)
        for name in (
            "populationManifest",
            "feedbackParquet",
            "edgesParquet",
            "feedbackExportManifest",
            "modelManifest",
            "modelArtifactRoot",
            "selectedChild",
        )
    }
    if any(not isinstance(value, str) or not value for value in path_fields.values()):
        raise ValueError("trial bundle input paths differ")
    evidence_paths = tuple(Path(value) for value in evidence)
    paths = (*evidence_paths, *(Path(value) for value in path_fields.values()))
    if any(not value.is_absolute() for value in paths):
        raise ValueError("trial bundle input paths must be absolute")
    selected_index = int(document["selectedConditionIndex"])
    if (
        selected_index < 1
        or selected_index > condition_count
        or expected_order[selected_index - 1][2] is not True
    ):
        raise ValueError("selected condition index must identify an activation condition")
    return FormalTrialBundleInputs(
        trial_id=UUID(str(document["trialId"])),
        creator_cohort=creator_cohort,
        selected_condition_index=selected_index,
        condition_order=condition_order,
        evidence_paths=evidence_paths,
        population_manifest=Path(path_fields["populationManifest"]),
        feedback_parquet=Path(path_fields["feedbackParquet"]),
        edges_parquet=Path(path_fields["edgesParquet"]),
        feedback_export_manifest=Path(path_fields["feedbackExportManifest"]),
        model_manifest=Path(path_fields["modelManifest"]),
        model_artifact_root=Path(path_fields["modelArtifactRoot"]),
        selected_child=Path(path_fields["selectedChild"]),
        pins=FormalPins(
            str(pins["modelRepository"]),
            str(pins["modelRevision"]),
            str(pins["datasetRepository"]),
            str(pins["datasetRevision"]),
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nearest_p95(values: Sequence[int]) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("formal condition has no measured requests")
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _load_condition(path: Path) -> _ConditionEvidence:
    document = _object(path, "condition evidence")
    if set(document) != {"conditionId", "runId", "requestCount", "p95Ms", "rawEvidence"}:
        raise ValueError("condition evidence contract differs")
    raw = document["rawEvidence"]
    if not isinstance(raw, dict):
        raise ValueError("condition raw evidence must be an object")
    required = {
        "conditionIdentity",
        "workloadIdentity",
        "measurements",
        "resources",
        "placement",
        "finalServingIdentity",
    }
    if not required.issubset(raw):
        raise ValueError("condition raw evidence is incomplete")
    condition_id = UUID(str(document["conditionId"]))
    run_id = UUID(str(document["runId"]))
    identity = ConditionIdentityV2.model_validate(raw["conditionIdentity"])
    workload = tuple(str(value) for value in raw["workloadIdentity"])
    if len(workload) != 6 or any(not _SHA256.fullmatch(value) for value in workload):
        raise ValueError("formal condition requires six frozen workload checksums")
    measurements = tuple(
        RequestMeasurementV2.model_validate(value) for value in raw["measurements"]
    )
    resources = tuple(
        ResourceObservationV1.model_validate(value) for value in raw["resources"]
    )
    if not measurements or not resources:
        raise ValueError("formal condition requires request and resource evidence")
    if any(
        row.benchmarkRunId != run_id or row.conditionId != identity.stable_key
        for row in measurements
    ):
        raise ValueError("request evidence identity differs from its condition")
    if any(
        row.benchmarkRunId != run_id or row.conditionId != identity.stable_key
        for row in resources
    ):
        raise ValueError("resource evidence identity differs from its condition")
    failures = [row for row in measurements if row.outcome != "success"]
    if failures:
        raise ValueError("formal trial requires zero request errors")
    measured = [row for row in measurements if not row.isWarmup]
    request_count = int(document["requestCount"])
    if request_count != len(measured) or request_count <= 0:
        raise ValueError("condition request count differs from measured successes")
    p95_ms = float(document["p95Ms"])
    if not math.isclose(p95_ms, _nearest_p95([row.clientTotalNs for row in measured]) / 1e6):
        raise ValueError("condition p95 differs from raw measurements")
    placement = raw["placement"]
    final = raw["finalServingIdentity"]
    if not isinstance(placement, dict) or not placement:
        raise ValueError("condition placement evidence is required")
    if not isinstance(final, dict):
        raise ValueError("condition final serving identity is required")
    required_final = {
        "modelId",
        "modelVersion",
        "embeddingSpaceId",
        "pgvectorSnapshotSha256",
        "backendSnapshotSha256",
    }
    if set(final) != required_final or any(
        not _SHA256.fullmatch(str(final[name]))
        for name in ("pgvectorSnapshotSha256", "backendSnapshotSha256")
    ):
        raise ValueError("condition final serving identity differs")
    UUID(str(final["modelId"]))
    UUID(str(final["embeddingSpaceId"]))
    if int(final["modelVersion"]) < 0:
        raise ValueError("condition final serving model version is invalid")
    return _ConditionEvidence(
        condition_id,
        run_id,
        identity,
        workload,
        measurements,
        resources,
        placement,
        final,
        request_count,
        p95_ms,
    )


def _validate_matrix(
    rows: Sequence[_ConditionEvidence], *, cohort_size: int
) -> tuple[str, ...]:
    expected = _expected_condition_order(cohort_size)
    if len(rows) != len(expected):
        raise ValueError("formal trial condition count differs from its creator cohort")
    actual = tuple(
        (
            row.identity.topology,
            row.identity.trainingEnabled,
            row.identity.activationEnabled,
        )
        for row in rows
    )
    if actual != expected:
        matrix = "3x3" if cohort_size == 50 else "2x3"
        raise ValueError(f"formal trial does not contain the exact {matrix} matrix order")
    if len({row.condition_id for row in rows}) != len(rows) or len(
        {row.run_id for row in rows}
    ) != len(rows):
        raise ValueError("formal condition and execution identities must be unique")
    workloads = {row.workload_identity for row in rows}
    if len(workloads) != 1:
        raise ValueError("formal trial must reuse one frozen workload")
    return next(iter(workloads))


def _validate_declared_condition_order(
    rows: Sequence[_ConditionEvidence],
    *,
    cohort_size: int,
    expected_creator_cohort: int | None,
    expected_condition_order: Sequence[dict[str, Any]] | None,
) -> None:
    if expected_creator_cohort is None and expected_condition_order is None:
        return
    if (
        expected_creator_cohort != cohort_size
        or expected_condition_order is None
        or len(expected_condition_order) != len(rows)
    ):
        raise ValueError("declared creator cohort or condition count differs")
    required = {
        "conditionIndex",
        "conditionId",
        "runId",
        "topology",
        "trainingEnabled",
        "activationEnabled",
    }
    try:
        declared = tuple(
            (
                int(document["conditionIndex"]),
                UUID(str(document["conditionId"])),
                UUID(str(document["runId"])),
                document["topology"],
                document["trainingEnabled"],
                document["activationEnabled"],
            )
            for document in expected_condition_order
            if isinstance(document, dict) and set(document) == required
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("declared condition order binding differs") from error
    actual = tuple(
        (
            index,
            row.condition_id,
            row.run_id,
            row.identity.topology,
            row.identity.trainingEnabled,
            row.identity.activationEnabled,
        )
        for index, row in enumerate(rows, start=1)
    )
    if declared != actual:
        raise ValueError("declared condition order binding differs")


def _feedback_export_binding(
    manifest_path: str | Path,
    feedback_path: str | Path,
    edges_path: str | Path,
    *,
    trial_id: UUID,
    cohort_size: int,
    conditions: Sequence[_ConditionEvidence],
) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    document = _object(manifest_file, "feedback export manifest")
    if document.get("schemaVersion") != 1 or document.get("experimentId") != str(
        trial_id
    ):
        raise ValueError("feedback export belongs to another trial")
    declared_cohort = document.get("creatorCohort")
    declared_count = document.get("conditionCount")
    if (
        (declared_cohort is not None and int(declared_cohort) != cohort_size)
        or (declared_count is not None and int(declared_count) != len(conditions))
        or (cohort_size != 50 and (declared_cohort is None or declared_count is None))
    ):
        raise ValueError("feedback export cohort or condition count differs")
    expected_pairs = {
        (str(row.condition_id), str(row.run_id)) for row in conditions
    }
    condition_rows = document.get("conditions")
    if not isinstance(condition_rows, list) or len(condition_rows) != len(conditions):
        raise ValueError("feedback export condition binding differs")
    try:
        actual_pairs = {
            (str(UUID(str(row["conditionId"]))), str(UUID(str(row["runId"]))))
            for row in condition_rows
            if isinstance(row, dict) and set(row) == {"conditionId", "runId"}
        }
    except (KeyError, ValueError) as error:
        raise ValueError("feedback export condition binding differs") from error
    if len(actual_pairs) != len(conditions) or actual_pairs != expected_pairs:
        raise ValueError("feedback export condition binding differs")

    import pyarrow.parquet as pq

    paths = {
        "feedbackParquet": Path(feedback_path),
        "edgesParquet": Path(edges_path),
    }
    required_columns = {
        "feedbackParquet": {
            "topic",
            "partition",
            "offset",
            "key",
            "schemaVersion",
            "eventId",
            "runId",
            "requestId",
            "creatorId",
            "sourceBabelId",
            "sourceArticleKey",
            "traversalSessionId",
            "parentRequestId",
            "traversalDepth",
            "modelId",
            "modelVersion",
            "embeddingSpaceId",
            "retrievalBackend",
            "sourceVectorOrigin",
            "candidateActions",
            "occurredAtNs",
        },
        "edgesParquet": {
            "runId",
            "sourceBabelId",
            "targetBabelId",
            "actingCreatorId",
            "requestId",
            "feedbackEventId",
            "feedbackOccurredAtNs",
            "traversalSessionId",
            "traversalDepth",
        },
    }
    verified: dict[str, Any] = {}
    for key, path in paths.items():
        descriptor = document.get(key)
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "path",
            "rows",
            "sha256",
        }:
            raise ValueError(f"{key} binding differs")
        parquet = pq.ParquetFile(path)
        rows = parquet.metadata.num_rows
        if (
            descriptor["path"] != path.name
            or int(descriptor["rows"]) != rows
            or rows <= 0
            or descriptor["sha256"] != _sha256(path)
            or required_columns[key] != set(parquet.schema_arrow.names)
        ):
            raise ValueError(f"{key} checksum, row count, or schema differs")
        verified[key] = descriptor

    feedback_run_ids = {
        str(UUID(str(value)))
        for value in pq.read_table(paths["feedbackParquet"], columns=["runId"])
        .column("runId")
        .to_pylist()
    }
    expected_run_ids = {str(row.run_id) for row in conditions}
    if feedback_run_ids != expected_run_ids:
        raise ValueError("feedback run identities differ from formal conditions")
    edge_run_ids = {
        str(UUID(str(value)))
        for value in pq.read_table(paths["edgesParquet"], columns=["runId"])
        .column("runId")
        .to_pylist()
    }
    if not edge_run_ids.issubset(expected_run_ids):
        raise ValueError("edge run identities differ from formal conditions")
    return {
        "schemaVersion": 1,
        "experimentId": str(trial_id),
        "creatorCohort": cohort_size,
        "conditionCount": len(conditions),
        "conditions": condition_rows,
        **verified,
        "manifestSha256": _sha256(manifest_file),
    }


def _population_evidence(
    path: str | Path, *, trial_id: UUID, pins: FormalPins
) -> dict[str, Any]:
    document = _object(path, "population manifest")
    if str(document.get("experimentId")) != str(trial_id):
        raise ValueError("population manifest belongs to another trial")
    creator_cohort = int(document.get("creatorCount", 0))
    _expected_condition_order(creator_cohort)
    if (
        int(document.get("babelCount", 0)) != 10_000
        or int(document.get("scheduleCount", 0)) != 10_000
        or int(document.get("embeddingDimension", 0)) != 100
    ):
        raise ValueError("formal population must contain exact 10k 100d evidence")
    if (
        document.get("artifactRepo") != pins.model_repository
        or document.get("artifactRevision") != pins.model_revision
    ):
        raise ValueError("population differs from the immutable model pin")
    if (
        document.get("datasetRepo") != pins.dataset_repository
        or document.get("datasetRevision") != pins.dataset_revision
    ):
        raise ValueError("population differs from the immutable dataset pin")
    vectors = str(document.get("vectorsSha256", ""))
    pgvector = str(document.get("pgvectorSnapshotSha256", ""))
    if not _SHA256.fullmatch(vectors) or not _SHA256.fullmatch(pgvector):
        raise ValueError("formal population vector checksums are invalid")
    return {
        "rows": 10_000,
        "dimension": 100,
        "creatorCohort": creator_cohort,
        "modelId": str(UUID(str(document["modelId"]))),
        "vectorsSha256": vectors,
        "pgvectorSnapshotSha256": pgvector,
        **pins.as_document(),
    }


def _selected_child(
    path: str | Path,
    rows: Sequence[_ConditionEvidence],
    population: dict[str, Any],
    model_manifest_path: str | Path,
) -> dict[str, Any]:
    selected = _object(path, "selected child metadata")
    required = {
        "conditionId",
        "runId",
        "modelId",
        "parentModelId",
        "modelVersion",
        "vectorSnapshotSha256",
    }
    if set(selected) != required or not _SHA256.fullmatch(
        str(selected.get("vectorSnapshotSha256", ""))
    ):
        raise ValueError("selected child metadata contract differs")
    condition_id = UUID(str(selected["conditionId"]))
    run_id = UUID(str(selected["runId"]))
    model_id = UUID(str(selected["modelId"]))
    parent_id = UUID(str(selected["parentModelId"]))
    version = int(selected["modelVersion"])
    matching = [
        row
        for row in rows
        if row.condition_id == condition_id and row.run_id == run_id
    ]
    if (
        len(matching) != 1
        or not matching[0].identity.activationEnabled
        or UUID(str(matching[0].final_serving_identity["modelId"])) != model_id
        or int(matching[0].final_serving_identity["modelVersion"]) != version
        or str(matching[0].final_serving_identity["pgvectorSnapshotSha256"])
        != selected["vectorSnapshotSha256"]
        or parent_id != UUID(str(population["modelId"]))
        or version <= 0
    ):
        raise ValueError("selected child is not a completed activation condition result")
    manifest = _object(model_manifest_path, "selected child model manifest")
    if (
        str(manifest.get("modelId")) != str(model_id)
        or str(manifest.get("parentModelId")) != str(parent_id)
        or str(manifest.get("producingRunId")) != str(run_id)
        or manifest.get("immutable") is not True
    ):
        raise ValueError("selected child metadata differs from its model manifest")
    return {
        "conditionId": str(condition_id),
        "runId": str(run_id),
        "modelId": str(model_id),
        "parentModelId": str(parent_id),
        "modelVersion": version,
        "vectorSnapshotSha256": str(selected["vectorSnapshotSha256"]),
    }


def _condition_summary(row: _ConditionEvidence) -> dict[str, Any]:
    measured = [value for value in row.measurements if not value.isWarmup]
    values = sorted(value.clientTotalNs for value in measured)
    return {
        "conditionId": str(row.condition_id),
        "runId": str(row.run_id),
        "identity": row.identity.model_dump(mode="json"),
        "requestCount": len(measured),
        "p50Ns": values[max(0, math.ceil(0.50 * len(values)) - 1)],
        "p95Ns": _nearest_p95(values),
        "p99Ns": values[max(0, math.ceil(0.99 * len(values)) - 1)],
        "maxNs": values[-1],
        "resourceCount": len(row.resources),
    }


def _summarize(
    trial_id: UUID,
    rows: Sequence[_ConditionEvidence],
    workload: tuple[str, ...],
    population: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    conditions = [_condition_summary(row) for row in rows]
    by_topology: dict[str, dict[tuple[bool, bool], int]] = {}
    for row, summary in zip(rows, conditions, strict=True):
        by_topology.setdefault(row.identity.topology, {})[
            (row.identity.trainingEnabled, row.identity.activationEnabled)
        ] = int(summary["p95Ns"])
    interference: dict[str, dict[str, float]] = {}
    for topology, values in sorted(by_topology.items()):
        serving = values[(False, False)]
        training = values[(True, False)]
        full = values[(True, True)]
        interference[topology] = {
            "Itraining": training / serving,
            "Ifull": full / serving,
            "IActivationIncrement": full / training,
        }
    cohort_size = int(population["creatorCohort"])
    condition_order = [
        {
            "conditionIndex": index,
            "conditionId": str(row.condition_id),
            "runId": str(row.run_id),
            "identity": row.identity.model_dump(mode="json"),
        }
        for index, row in enumerate(rows, start=1)
    ]
    topology = "3x3_matrix" if cohort_size == 50 else "2x3_matrix"
    return {
        "schemaVersion": 1,
        "trialId": str(trial_id),
        "acceptanceLabel": "formal",
        "topology": topology,
        "creatorCohort": cohort_size,
        "conditionCount": len(rows),
        "conditionOrder": condition_order,
        "requestCount": sum(row.request_count for row in rows),
        "errorCount": 0,
        "workloadIdentity": list(workload),
        "population": population,
        "selectedChild": selected,
        "conditions": conditions,
        "interferenceByTopology": interference,
    }


def _report(summary: dict[str, Any]) -> str:
    lines = [
        f"# Formal {summary['topology'].replace('_matrix', '')} recommendation matrix",
        "",
        f"Trial: `{summary['trialId']}`",
        "",
        "All request outcomes are successful. Durations are monotonic nanoseconds.",
        "",
        "| Topology | Training | Activation | Requests | p95 (ns) |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summary["conditions"]:
        identity = row["identity"]
        lines.append(
            f"| {identity['topology']} | {str(identity['trainingEnabled']).lower()} | "
            f"{str(identity['activationEnabled']).lower()} | {row['requestCount']} | "
            f"{row['p95Ns']} |"
        )
    lines.extend(["", "## Interference", ""])
    for topology, ratios in summary["interferenceByTopology"].items():
        lines.append(
            f"- `{topology}`: Itraining={ratios['Itraining']:.6f}, "
            f"Ifull={ratios['Ifull']:.6f}, "
            f"IActivationIncrement={ratios['IActivationIncrement']:.6f}"
        )
    return "\n".join(lines) + "\n"


def build_formal_trial_bundle(
    output_root: str | Path,
    *,
    trial_id: UUID,
    evidence_paths: Sequence[str | Path],
    population_manifest_path: str | Path,
    feedback_parquet: str | Path,
    edges_parquet: str | Path,
    feedback_export_manifest_path: str | Path,
    model_manifest: str | Path,
    model_artifact_root: str | Path,
    selected_child_path: str | Path,
    pins: FormalPins,
    expected_creator_cohort: int | None = None,
    expected_condition_order: Sequence[dict[str, Any]] | None = None,
) -> RunBundle:
    """Validate one saved formal cohort, export aggregate evidence, and build locally."""
    rows = [_load_condition(Path(path)) for path in evidence_paths]
    population = _population_evidence(
        population_manifest_path, trial_id=trial_id, pins=pins
    )
    cohort_size = int(population["creatorCohort"])
    workload = _validate_matrix(rows, cohort_size=cohort_size)
    _validate_declared_condition_order(
        rows,
        cohort_size=cohort_size,
        expected_creator_cohort=expected_creator_cohort,
        expected_condition_order=expected_condition_order,
    )
    feedback_export = _feedback_export_binding(
        feedback_export_manifest_path,
        feedback_parquet,
        edges_parquet,
        trial_id=trial_id,
        cohort_size=cohort_size,
        conditions=rows,
    )
    selected = _selected_child(
        selected_child_path, rows, population, model_manifest
    )
    summary = _summarize(trial_id, rows, workload, population, selected)

    staging = Path(output_root) / "trial-evidence" / str(trial_id)
    staging.mkdir(parents=True, exist_ok=True)
    requests_path = staging / "requests.parquet"
    resources_path = staging / "resources.parquet"
    summary_path = staging / "summary.json"
    report_path = staging / "report.md"
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(
        pa.Table.from_pylist(
            [value.model_dump(mode="json") for row in rows for value in row.measurements]
        ),
        requests_path,
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [value.model_dump(mode="json") for row in rows for value in row.resources]
        ),
        resources_path,
        compression="zstd",
    )
    summary_path.write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_report(summary), encoding="utf-8")

    condition_evidence = [row.aggregate_row() for row in rows]
    condition_order = summary["conditionOrder"]
    condition_count = len(rows)
    topology = str(summary["topology"])
    trial_evidence = {
        "schemaVersion": 1,
        "trialId": str(trial_id),
        "creatorCohort": cohort_size,
        "conditionCount": condition_count,
        "conditionOrder": condition_order,
        "zeroErrors": True,
        "workloadIdentity": list(workload),
        "pins": pins.as_document(),
        "population": population,
        "selectedChild": selected,
        "feedbackExport": feedback_export,
        "conditions": condition_evidence,
    }
    placements = {
        "schemaVersion": 1,
        "conditions": [
            {
                "conditionId": str(row.condition_id),
                "identity": row.identity.model_dump(mode="json"),
                "placement": row.placement,
            }
            for row in rows
        ],
    }
    hardware = {
        "schemaVersion": 1,
        "gpuObserved": any(
            value.gpuAvailable for row in rows for value in row.resources
        ),
        "resourceObservationCount": sum(len(row.resources) for row in rows),
    }
    model_ledger = [
        {
            "modelId": population["modelId"],
            "parentModelId": None,
            "role": "original",
            "immutable": True,
        },
        {
            "modelId": selected["modelId"],
            "parentModelId": selected["parentModelId"],
            "producingRunId": selected["runId"],
            "role": "child",
            "immutable": True,
        },
    ]
    vector_snapshots = [
        {
            "sha256": population["pgvectorSnapshotSha256"],
            "rows": 10_000,
            "dimension": 100,
            "role": "population",
        },
        {
            "sha256": selected["vectorSnapshotSha256"],
            "rows": 10_000,
            "dimension": 100,
            "role": "selected_child",
        },
    ]
    return build_run_bundle(
        output_root,
        run_id=trial_id,
        feedback_parquet=feedback_parquet,
        edges_parquet=edges_parquet,
        requests_parquet=requests_path,
        resources_parquet=resources_path,
        summary_json=summary_path,
        report_markdown=report_path,
        model_manifest=model_manifest,
        model_artifact_root=model_artifact_root,
        progress={
            "phase": "completed",
            "conditionIndex": condition_count,
            "conditionCount": condition_count,
            "requested": summary["requestCount"],
            "completed": summary["requestCount"],
        },
        topology=topology,
        placement=placements,
        hardware=hardware,
        model_ledger=model_ledger,
        vector_snapshots=vector_snapshots,
        acceptance_label="formal",
        trial_evidence=trial_evidence,
    )


__all__ = [
    "FormalPins",
    "FormalTrialBundleInputs",
    "build_formal_trial_bundle",
    "load_formal_trial_bundle_inputs",
]
