"""Closed trial-level export for one accepted formal 3x3 matrix."""

from __future__ import annotations

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
_MODES = {(False, False), (True, False), (True, True)}
_TOPOLOGIES = {"same_process", "same_host_split", "same_host_isolated"}


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


def _validate_matrix(rows: Sequence[_ConditionEvidence]) -> tuple[str, ...]:
    if len(rows) != 9:
        raise ValueError("formal trial requires exactly nine condition results")
    actual = {
        (
            row.identity.topology,
            row.identity.trainingEnabled,
            row.identity.activationEnabled,
        )
        for row in rows
    }
    expected = {
        (topology, training, activation)
        for topology in _TOPOLOGIES
        for training, activation in _MODES
    }
    if actual != expected:
        raise ValueError("formal trial does not contain the exact 3x3 matrix")
    if len({row.condition_id for row in rows}) != 9 or len({row.run_id for row in rows}) != 9:
        raise ValueError("formal condition and execution identities must be unique")
    workloads = {row.workload_identity for row in rows}
    if len(workloads) != 1:
        raise ValueError("formal trial must reuse one frozen workload")
    return next(iter(workloads))


def _population_evidence(
    path: str | Path, *, trial_id: UUID, pins: FormalPins
) -> dict[str, Any]:
    document = _object(path, "population manifest")
    if str(document.get("experimentId")) != str(trial_id):
        raise ValueError("population manifest belongs to another trial")
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
    return {
        "schemaVersion": 1,
        "trialId": str(trial_id),
        "acceptanceLabel": "formal",
        "topology": "3x3_matrix",
        "conditionCount": 9,
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
        "# Formal 3x3 recommendation matrix",
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
    model_manifest: str | Path,
    model_artifact_root: str | Path,
    selected_child_path: str | Path,
    pins: FormalPins,
) -> RunBundle:
    """Validate nine saved receipts, export aggregate evidence, and build locally."""
    rows = sorted(
        (_load_condition(Path(path)) for path in evidence_paths),
        key=lambda row: row.identity.stable_key,
    )
    workload = _validate_matrix(rows)
    population = _population_evidence(
        population_manifest_path, trial_id=trial_id, pins=pins
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
    trial_evidence = {
        "schemaVersion": 1,
        "trialId": str(trial_id),
        "conditionCount": 9,
        "zeroErrors": True,
        "workloadIdentity": list(workload),
        "pins": pins.as_document(),
        "population": population,
        "selectedChild": selected,
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
            "conditionIndex": 9,
            "conditionCount": 9,
            "requested": summary["requestCount"],
            "completed": summary["requestCount"],
        },
        topology="3x3_matrix",
        placement=placements,
        hardware=hardware,
        model_ledger=model_ledger,
        vector_snapshots=vector_snapshots,
        acceptance_label="formal",
        trial_evidence=trial_evidence,
    )


__all__ = ["FormalPins", "build_formal_trial_bundle"]
