"""Prepare one labelled same-host rerun from already frozen trial inputs."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid5

from ..model.frozen_population import FrozenPopulationManifestV1
from .performance_worker import FrozenWorkload, PerformanceExperiment


REPRESENTATIVE_SCOPE = "representative_same_process_vs_split"
SPLIT_SMOKE_SCOPE = "representative_split_smoke"
ISOLATED_SMOKE_SCOPE = "representative_isolated_smoke"
_SUPPORTED_REPRESENTATIVE_SCOPES = frozenset(
    {REPRESENTATIVE_SCOPE, SPLIT_SMOKE_SCOPE, ISOLATED_SMOKE_SCOPE}
)


@dataclass(frozen=True, slots=True)
class RepresentativeRerunBinding:
    rerun_id: UUID
    source_trial_id: UUID
    evidence_scope: str
    population_run_id: UUID
    population_path: Path
    population_manifest_sha256: str
    workload_path: Path
    workload_identity: tuple[str, ...]
    warmup_seconds: int
    duration_seconds: int
    target_rps: float
    request_limit: int


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"frozen {label} differs from source trial evidence")


def validate_representative_reuse(
    *,
    source: PerformanceExperiment,
    manifest: FrozenPopulationManifestV1,
    workload: FrozenWorkload,
    rerun_id: UUID,
    evidence_scope: str = REPRESENTATIVE_SCOPE,
    warmup_seconds: int = 5,
    duration_seconds: int = 25,
    target_rps: float = 5.0,
) -> RepresentativeRerunBinding:
    """Fail closed unless every reusable population/workload identity is exact."""
    if rerun_id == source.id:
        raise ValueError("rerun identity must differ from source trial")
    if evidence_scope not in _SUPPORTED_REPRESENTATIVE_SCOPES:
        raise ValueError("representative rerun evidence scope is unsupported")
    if warmup_seconds < 0 or duration_seconds <= 0 or target_rps <= 0:
        raise ValueError("representative rerun load window is invalid")
    request_limit = max(1, math.ceil(
        (warmup_seconds + duration_seconds) * target_rps
    ))
    if not source.population_ready or source.population_run_id is None:
        raise ValueError("source trial does not have a completed frozen population")
    if source.population_bundle_path is None or source.population_manifest_sha256 is None:
        raise ValueError("source trial population binding is incomplete")
    if manifest.experimentId != str(source.id):
        raise ValueError("frozen population belongs to another source trial")
    _require_equal(manifest.sourcePopulationRunId, source.population_run_id, "population run")
    _require_equal(manifest.babelCount, source.target_created_babels, "vector count")
    _require_equal(source.population_vector_count, manifest.babelCount, "vector count")
    _require_equal(source.population_vector_sha256, manifest.vectorsSha256, "vector checksum")
    _require_equal(manifest.modelId, source.starting_model_id, "model identity")
    _require_equal(manifest.artifactRepo, source.model_repository, "model identity")
    _require_equal(manifest.artifactRevision, source.model_revision, "model identity")
    _require_equal(
        source.population_model_repository, manifest.artifactRepo, "model identity"
    )
    _require_equal(
        source.population_model_revision, manifest.artifactRevision, "model identity"
    )
    _require_equal(
        source.population_model_sha256, manifest.modelManifestSha256, "model checksum"
    )
    _require_equal(manifest.datasetRepo, source.dataset_repository, "dataset identity")
    _require_equal(manifest.datasetConfig, source.dataset_config, "dataset identity")
    _require_equal(manifest.datasetRevision, source.dataset_revision, "dataset identity")
    _require_equal(
        source.population_dataset_repository, manifest.datasetRepo, "dataset identity"
    )
    _require_equal(
        source.population_dataset_revision, manifest.datasetRevision, "dataset identity"
    )
    _require_equal(
        source.population_dataset_sha256,
        manifest.datasetManifestSha256,
        "dataset checksum",
    )
    if (
        len(workload.identity) != 6
        or any(len(value) != 64 for value in workload.identity)
        or not workload.path.is_dir()
    ):
        raise ValueError("frozen workload identity is incomplete")
    return RepresentativeRerunBinding(
        rerun_id=rerun_id,
        source_trial_id=source.id,
        evidence_scope=evidence_scope,
        population_run_id=source.population_run_id,
        population_path=Path(source.population_bundle_path).resolve(),
        population_manifest_sha256=source.population_manifest_sha256,
        workload_path=workload.path.resolve(),
        workload_identity=tuple(workload.identity),
        warmup_seconds=warmup_seconds,
        duration_seconds=duration_seconds,
        target_rps=target_rps,
        request_limit=request_limit,
    )


def create_representative_rerun(
    *,
    database: Any,
    source_trial_id: UUID,
    state_root: str | Path,
    rerun_id: UUID | None = None,
    nonce: str | None = None,
    population_loader: Callable[[Path], FrozenPopulationManifestV1] | None = None,
    workload_loader: Callable[[Path], Any] | None = None,
    evidence_scope: str = REPRESENTATIVE_SCOPE,
    warmup_seconds: int = 5,
    duration_seconds: int = 25,
    target_rps: float = 5.0,
) -> RepresentativeRerunBinding:
    """Verify reusable bytes, then atomically save a fresh unapproved trial."""
    if rerun_id is None:
        if not nonce:
            raise ValueError("rerun nonce is required when rerun ID is not supplied")
        rerun_id = uuid5(source_trial_id, f"representative-rerun:{nonce}")
    if population_loader is None:
        from ..model.frozen_population import load_frozen_population

        population_loader = load_frozen_population
    if workload_loader is None:
        from babel_benchmark.workload import load_frozen_workload

        workload_loader = load_frozen_workload
    source = database.load_performance_experiment(source_trial_id)
    population_path = Path(source.population_bundle_path or "")
    manifest_path = population_path / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ValueError("source frozen population manifest is unavailable") from error
    if hashlib.sha256(manifest_bytes).hexdigest() != source.population_manifest_sha256:
        raise ValueError("frozen population manifest checksum differs")
    manifest = population_loader(population_path)
    workload_path = Path(state_root) / str(source_trial_id) / "workload"
    loaded_workload = workload_loader(workload_path)
    workload = FrozenWorkload(
        path=Path(loaded_workload.path), identity=tuple(loaded_workload.identity)
    )
    request_path = workload.path / "requests.template.jsonl"
    try:
        with request_path.open("r", encoding="utf-8") as source_requests:
            available_requests = sum(bool(line.strip()) for line in source_requests)
    except OSError as error:
        raise ValueError("source frozen workload requests are unavailable") from error
    binding = validate_representative_reuse(
        source=source,
        manifest=manifest,
        workload=workload,
        rerun_id=rerun_id,
        evidence_scope=evidence_scope,
        warmup_seconds=warmup_seconds,
        duration_seconds=duration_seconds,
        target_rps=target_rps,
    )
    if binding.request_limit > available_requests:
        raise ValueError("requested rerun window exceeds the frozen source workload")
    return database.create_representative_performance_rerun(binding)


__all__ = [
    "ISOLATED_SMOKE_SCOPE",
    "REPRESENTATIVE_SCOPE",
    "SPLIT_SMOKE_SCOPE",
    "RepresentativeRerunBinding",
    "create_representative_rerun",
    "validate_representative_reuse",
]
