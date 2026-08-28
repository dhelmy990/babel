"""Closed, immutable publication of explicitly non-formal trial evidence."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID

from .hub import (
    AcceptedRunExists,
    UploadOperation,
    _download,
    _json_object,
    _reject_secret,
    _sha256,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPORT_FILES = {
    "edges.jsonl",
    "edges.parquet",
    "feedback.jsonl",
    "feedback.parquet",
    "manifest.json",
}
_ROOT_FILES = {
    "manifest.json",
    "model-lineage.json",
    "report.md",
    "trial-results.json",
    "trial-summary.json",
}


@dataclass(frozen=True, slots=True)
class _ConditionProfile:
    condition_index: int
    formal_condition_index: int
    topology: str
    training_enabled: bool
    activation_enabled: bool

    def identity(self) -> dict[str, object]:
        return {
            "topology": self.topology,
            "trainingEnabled": self.training_enabled,
            "activationEnabled": self.activation_enabled,
            "retrievalBackend": "pgvector",
        }


@dataclass(frozen=True, slots=True)
class _ScopeProfile:
    evidence_scope: str
    conditions: tuple[_ConditionProfile, ...]
    require_position_bindings: bool = False

    @property
    def condition_count(self) -> int:
        return len(self.conditions)


def _condition_profiles(
    topologies: tuple[str, ...], *, formal_start: int
) -> tuple[_ConditionProfile, ...]:
    identities = tuple(
        (topology, training_enabled, activation_enabled)
        for topology in topologies
        for training_enabled, activation_enabled in (
            (False, False),
            (True, False),
            (True, True),
        )
    )
    return tuple(
        _ConditionProfile(
            condition_index=index,
            formal_condition_index=formal_start + index - 1,
            topology=topology,
            training_enabled=training_enabled,
            activation_enabled=activation_enabled,
        )
        for index, (topology, training_enabled, activation_enabled) in enumerate(
            identities, start=1
        )
    )


_SCOPE_PROFILES = MappingProxyType(
    {
        "representative_same_process_vs_split": _ScopeProfile(
            evidence_scope="representative_same_process_vs_split",
            conditions=_condition_profiles(
                ("same_process", "same_host_split"), formal_start=1
            ),
        ),
        "representative_isolated_smoke": _ScopeProfile(
            evidence_scope="representative_isolated_smoke",
            conditions=_condition_profiles(("same_host_isolated",), formal_start=7),
            require_position_bindings=True,
        ),
    }
)


def _scope_profile(scope: object) -> _ScopeProfile:
    if not isinstance(scope, str) or scope not in _SCOPE_PROFILES:
        raise ValueError("representative evidence scope is unsupported")
    return _SCOPE_PROFILES[scope]


@dataclass(frozen=True, slots=True)
class RepresentativeRunBundle:
    root: Path
    trial_id: UUID
    evidence_scope: str
    manifest_path: Path
    checksums_path: Path
    artifact_sha256: str

    @property
    def bundle_path(self) -> str:
        return f"representative-runs/{self.trial_id}/{self.artifact_sha256}"


@dataclass(frozen=True, slots=True)
class RepresentativeRunPublicationReceipt:
    repository: str
    commit_sha: str
    bundle_path: str
    artifact_sha256: str
    trial_id: UUID
    evidence_scope: str
    verified_files: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ValidatedSources:
    manifest: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    feedback_rows: int
    edge_rows: int
    profile: _ScopeProfile


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _reject_symlinked_path(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"representative evidence path contains a symbolic link: {current}"
            )


def _regular_files(root: Path) -> dict[str, Path]:
    _reject_symlinked_path(root)
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("representative bundle may not contain symbolic links")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
        elif not path.is_dir():
            raise ValueError("representative bundle may contain only regular files")
    return files


def _validate_relative_name(name: object, expected: str) -> str:
    if name != expected:
        raise ValueError(f"representative export {expected} path differs")
    path = PurePosixPath(str(name))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("representative export path escapes its root")
    return str(name)


def _parquet_rows(path: Path) -> int:
    import pyarrow.parquet as pq

    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception as error:
        raise ValueError(
            f"representative Parquet is unreadable: {path.name}"
        ) from error


def _validate_parquet(
    export_root: Path,
    manifest: dict[str, Any],
    *,
    field: str,
    expected_name: str,
    top_count: str,
    top_digest: str,
) -> tuple[int, str]:
    declaration = manifest.get(field)
    if not isinstance(declaration, dict):
        raise TypeError(f"representative {expected_name} declaration is incomplete")
    _validate_relative_name(declaration.get("path"), expected_name)
    rows = declaration.get("rows")
    digest = declaration.get("sha256")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError(f"representative {expected_name} rows are invalid")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"representative {expected_name} checksum is invalid")
    path = export_root / expected_name
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"representative {expected_name} is missing")
    if _sha256(path) != digest or manifest.get(top_digest) != digest:
        raise ValueError(f"representative {expected_name} checksum differs")
    if _parquet_rows(path) != rows or manifest.get(top_count) != rows:
        raise ValueError(f"representative {expected_name} rows differ")
    return rows, digest


def _condition_path(evidence_root: Path, index: int) -> Path:
    candidates = [
        evidence_root / f"{index:02d}" / "live-evidence.json",
        evidence_root / str(index) / "live-evidence.json",
    ]
    present = [path for path in candidates if path.is_file()]
    if len(present) != 1:
        raise ValueError(
            f"representative condition {index} must have exactly one live-evidence.json"
        )
    return present[0]


def _validate_completed_condition(
    raw: dict[str, Any], *, condition: _ConditionProfile, request_count: int
) -> None:
    expected_identity = condition.identity()
    if raw.get("conditionIdentity") != expected_identity:
        raise ValueError(
            "representative evidence is not the ordered 2x3 or isolated scope matrix"
        )
    measurements = raw.get("measurements")
    if (
        not isinstance(measurements, list)
        or not measurements
        or any(
            not isinstance(row, dict) or row.get("outcome") != "success"
            for row in measurements
        )
        or sum(row.get("isWarmup") is False for row in measurements) != request_count
    ):
        raise ValueError("representative condition did not complete successfully")
    feedback = raw.get("feedbackKafka")
    final_state = (
        feedback.get("finalTrainerState") if isinstance(feedback, dict) else None
    )
    if (
        not isinstance(final_state, dict)
        or final_state.get("available") is not True
        or final_state.get("kafkaLag") != 0
    ):
        raise ValueError("representative condition must have zero final Kafka lag")
    record_count = feedback.get("recordCount")
    if (
        isinstance(record_count, bool)
        or not isinstance(record_count, int)
        or record_count < request_count
    ):
        raise ValueError("representative condition feedback evidence is incomplete")
    if (
        expected_identity["trainingEnabled"] is True
        and final_state.get("offsetsCoverPublishedRanges") is not True
    ):
        raise ValueError("representative training offsets are not completely covered")


def _validate_sources(
    *,
    trial_id: UUID,
    export_root: Path,
    evidence_root: Path,
) -> _ValidatedSources:
    _reject_symlinked_path(export_root)
    _reject_symlinked_path(evidence_root)
    export_files = _regular_files(export_root)
    if set(export_files) != _EXPORT_FILES:
        raise ValueError(
            "representative export inventory differs from the closed export"
        )
    for path in export_files.values():
        _reject_secret(path)
    manifest = _json_object(
        export_root / "manifest.json", "representative export manifest"
    )
    if manifest.get("experimentId") != str(trial_id):
        raise ValueError("representative export belongs to another trial")
    if manifest.get("formalPerformanceClaim") is not False:
        raise ValueError("representative formalPerformanceClaim must be exactly false")
    profile = _scope_profile(manifest.get("evidenceScope"))
    scope = profile.evidence_scope
    condition_count = profile.condition_count
    bindings = manifest.get("conditions")
    if (
        manifest.get("conditionCount") != condition_count
        or not isinstance(bindings, list)
        or len(bindings) != condition_count
    ):
        raise ValueError(
            "representative export condition count differs from its exact scope profile"
        )
    feedback_rows, _ = _validate_parquet(
        export_root,
        manifest,
        field="feedbackParquet",
        expected_name="feedback.parquet",
        top_count="records",
        top_digest="parquetSha256",
    )
    edge_rows, _ = _validate_parquet(
        export_root,
        manifest,
        field="edgesParquet",
        expected_name="edges.parquet",
        top_count="canonicalEdges",
        top_digest="edgeParquetSha256",
    )
    if manifest.get("jsonlSha256") != _sha256(
        export_root / "feedback.jsonl"
    ) or manifest.get("edgeJsonlSha256") != _sha256(export_root / "edges.jsonl"):
        raise ValueError("representative JSONL checksum differs")

    condition_paths = tuple(
        _condition_path(evidence_root, condition.condition_index)
        for condition in profile.conditions
    )
    evidence_files = _regular_files(evidence_root)
    expected_evidence_files = {
        path.relative_to(evidence_root).as_posix() for path in condition_paths
    }
    if set(evidence_files) != expected_evidence_files:
        raise ValueError(
            "representative condition evidence inventory differs from the exact scope profile"
        )

    evidence: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for condition, binding, path in zip(
        profile.conditions, bindings, condition_paths, strict=True
    ):
        index = condition.condition_index
        if not isinstance(binding, dict):
            raise TypeError("representative condition binding is invalid")
        positions = (
            binding.get("conditionIndex"),
            binding.get("formalConditionIndex"),
        )
        expected_positions = (index, condition.formal_condition_index)
        positions_declared = any(value is not None for value in positions)
        if positions != expected_positions and (
            profile.require_position_bindings or positions_declared
        ):
            raise ValueError(
                f"representative condition {index} formal position differs"
            )
        _reject_symlinked_path(path)
        _reject_secret(path)
        document = _json_object(path, f"representative condition {index}")
        run_id = document.get("runId")
        condition_id = document.get("conditionId")
        raw = document.get("rawEvidence")
        if (
            not isinstance(run_id, str)
            or not isinstance(condition_id, str)
            or binding.get("runId") != run_id
            or binding.get("conditionId") != condition_id
        ):
            raise ValueError(f"representative condition {index} identity differs")
        if run_id in seen_runs:
            raise ValueError("representative condition run identity is duplicated")
        seen_runs.add(run_id)
        if not isinstance(raw, dict) or raw.get("evidenceScope") != scope:
            raise ValueError(f"representative condition {index} scope differs")
        request_count = document.get("requestCount")
        p95_ms = document.get("p95Ms")
        if (
            isinstance(request_count, bool)
            or not isinstance(request_count, int)
            or request_count < 1
            or isinstance(p95_ms, bool)
            or not isinstance(p95_ms, (int, float))
            or float(p95_ms) < 0
        ):
            raise ValueError(f"representative condition {index} result is incomplete")
        _validate_completed_condition(
            raw, condition=condition, request_count=request_count
        )
        evidence.append(document)
    return _ValidatedSources(
        manifest=manifest,
        evidence=tuple(evidence),
        feedback_rows=feedback_rows,
        edge_rows=edge_rows,
        profile=profile,
    )


def _condition_result(
    condition: _ConditionProfile, evidence: dict[str, Any]
) -> dict[str, object]:
    raw = evidence["rawEvidence"]
    result: dict[str, object] = {
        "conditionIndex": condition.condition_index,
        "conditionId": evidence["conditionId"],
        "runId": evidence["runId"],
        "requestCount": evidence["requestCount"],
        "p95Ms": evidence["p95Ms"],
        "conditionIdentity": raw.get("conditionIdentity"),
        "finalTrainerState": (
            raw.get("feedbackKafka", {}).get("finalTrainerState")
            if isinstance(raw.get("feedbackKafka"), dict)
            else None
        ),
    }
    if condition.formal_condition_index != condition.condition_index:
        result["formalConditionIndex"] = condition.formal_condition_index
    return result


def _model_lineage(evidence: list[dict[str, Any]]) -> dict[str, object]:
    source_ids: set[str] = set()
    condition_models: list[dict[str, object]] = []
    for index, document in enumerate(evidence, start=1):
        raw = document["rawEvidence"]
        measurements = raw.get("measurements")
        if isinstance(measurements, list):
            source_ids.update(
                row["modelId"]
                for row in measurements
                if isinstance(row, dict) and isinstance(row.get("modelId"), str)
            )
        final = raw.get("finalServingIdentity")
        if isinstance(final, dict) and isinstance(final.get("modelId"), str):
            condition_models.append(
                {
                    "conditionIndex": index,
                    "conditionId": document["conditionId"],
                    "runId": document["runId"],
                    "finalServingIdentity": final,
                    "observedActivationTargets": raw.get(
                        "observedActivationTargets", []
                    ),
                }
            )
    if not source_ids or not condition_models:
        raise ValueError("representative model lineage is incomplete")
    return {
        "schemaVersion": 1,
        "sourceModelIds": sorted(source_ids),
        "conditionModels": condition_models,
    }


def build_representative_run_bundle(
    output_root: str | Path,
    *,
    trial_id: UUID,
    export_root: str | Path,
    evidence_root: str | Path,
    report_path: str | Path,
) -> RepresentativeRunBundle:
    """Validate and stage one completed exact representative trial profile."""
    source_export = Path(export_root)
    source_evidence = Path(evidence_root)
    source_report = Path(report_path)
    validated = _validate_sources(
        trial_id=trial_id,
        export_root=source_export,
        evidence_root=source_evidence,
    )
    manifest = validated.manifest
    evidence = validated.evidence
    profile = validated.profile
    condition_count = profile.condition_count
    _reject_symlinked_path(source_report)
    if not source_report.is_file() or source_report.is_symlink():
        raise ValueError("representative report markdown is missing")
    _reject_secret(source_report)
    if not source_report.read_text(encoding="utf-8").strip():
        raise ValueError("representative report markdown is empty")

    scope = str(manifest["evidenceScope"])
    parent = Path(output_root) / "representative-runs" / str(trial_id)
    _reject_symlinked_path(parent)
    partial = parent / ".partial"
    if partial.is_symlink():
        raise ValueError("representative partial path cannot be a symbolic link")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        (partial / "export").mkdir()
        for name in sorted(_EXPORT_FILES):
            shutil.copyfile(source_export / name, partial / "export" / name)
        for index in range(1, condition_count + 1):
            destination = partial / "conditions" / f"{index:02d}" / "live-evidence.json"
            destination.parent.mkdir(parents=True)
            shutil.copyfile(_condition_path(source_evidence, index), destination)

        results = {
            "schemaVersion": 1,
            "trialId": str(trial_id),
            "evidenceScope": scope,
            "formalPerformanceClaim": False,
            "conditions": [
                _condition_result(condition, document)
                for condition, document in zip(
                    profile.conditions, evidence, strict=True
                )
            ],
        }
        summary = {
            "schemaVersion": 1,
            "trialId": str(trial_id),
            "evidenceScope": scope,
            "formalPerformanceClaim": False,
            "creatorCohort": manifest.get("creatorCohort"),
            "conditionCount": condition_count,
            "feedbackRows": validated.feedback_rows,
            "edgeRows": validated.edge_rows,
        }
        bundle_manifest = {
            "schemaVersion": 1,
            "artifactType": "representative_performance_evidence",
            "namespace": "representative-runs",
            "trialId": str(trial_id),
            "evidenceScope": scope,
            "formalPerformanceClaim": False,
            "files": sorted(
                _ROOT_FILES
                | {f"export/{name}" for name in _EXPORT_FILES}
                | {
                    f"conditions/{index:02d}/live-evidence.json"
                    for index in range(1, condition_count + 1)
                }
            ),
        }
        (partial / "manifest.json").write_bytes(_canonical(bundle_manifest))
        (partial / "trial-summary.json").write_bytes(_canonical(summary))
        (partial / "trial-results.json").write_bytes(_canonical(results))
        lineage = _model_lineage(evidence)
        lineage.update(
            trialId=str(trial_id),
            evidenceScope=scope,
            formalPerformanceClaim=False,
        )
        (partial / "model-lineage.json").write_bytes(_canonical(lineage))
        shutil.copyfile(source_report, partial / "report.md")

        files = _regular_files(partial)
        checksums = {name: _sha256(path) for name, path in sorted(files.items())}
        checksums_path = partial / "checksums.json"
        checksums_path.write_bytes(_canonical(checksums))
        artifact_sha256 = _sha256(checksums_path)
        final = parent / artifact_sha256
        if os.path.lexists(final):
            raise AcceptedRunExists(
                f"accepted local representative run already exists: {artifact_sha256}"
            )
        os.replace(partial, final)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
    return load_representative_run_bundle(final, trial_id=trial_id)


def _validate_bundle_identity(
    bundle: RepresentativeRunBundle, manifest: dict[str, Any]
) -> _ScopeProfile:
    if (
        bundle.root.parent.name != str(bundle.trial_id)
        or bundle.root.parent.parent.name != "representative-runs"
        or bundle.root.name != bundle.artifact_sha256
    ):
        raise ValueError(
            "representative bundle must remain beneath representative-runs"
        )
    profile = _scope_profile(bundle.evidence_scope)
    if (
        manifest.get("artifactType") != "representative_performance_evidence"
        or manifest.get("namespace") != "representative-runs"
        or manifest.get("trialId") != str(bundle.trial_id)
        or manifest.get("formalPerformanceClaim") is not False
        or manifest.get("evidenceScope") != bundle.evidence_scope
    ):
        raise ValueError("representative bundle identity or namespace differs")
    return profile


def _validate_local_bundle(bundle: RepresentativeRunBundle) -> dict[str, str]:
    files = _regular_files(bundle.root)
    if "checksums.json" not in files:
        raise ValueError("representative bundle checksum inventory is missing")
    if _sha256(files["checksums.json"]) != bundle.artifact_sha256:
        raise ValueError("representative bundle immutable checksum inventory changed")
    document = _json_object(files["checksums.json"], "representative checksums")
    if set(files) != set(document) | {"checksums.json"}:
        raise ValueError("representative bundle file inventory differs")
    manifest = _json_object(files["manifest.json"], "representative manifest")
    profile = _validate_bundle_identity(bundle, manifest)
    condition_count = profile.condition_count
    expected = (
        _ROOT_FILES
        | {f"export/{name}" for name in _EXPORT_FILES}
        | {
            f"conditions/{index:02d}/live-evidence.json"
            for index in range(1, condition_count + 1)
        }
    )
    if set(document) != expected:
        raise ValueError("representative bundle checksum inventory is incomplete")
    checksums: dict[str, str] = {}
    for name, digest in document.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise ValueError(
                "representative checksum inventory contains an invalid entry"
            )
        if _sha256(files[name]) != digest:
            raise ValueError(f"representative bundle checksum mismatch: {name}")
        _reject_secret(files[name])
        checksums[name] = digest
    if manifest.get("files") != sorted(expected):
        raise ValueError("representative manifest inventory differs")
    validated = _validate_sources(
        trial_id=bundle.trial_id,
        export_root=bundle.root / "export",
        evidence_root=bundle.root / "conditions",
    )
    if validated.profile != profile:
        raise ValueError("representative bundle scope profile differs")
    for name in ("trial-summary.json", "trial-results.json", "model-lineage.json"):
        derived = _json_object(files[name], f"representative {name}")
        if (
            derived.get("trialId") != str(bundle.trial_id)
            or derived.get("evidenceScope") != bundle.evidence_scope
            or derived.get("formalPerformanceClaim") is not False
        ):
            raise ValueError(f"representative {name} identity differs")
    summary = _json_object(files["trial-summary.json"], "representative trial summary")
    results = _json_object(files["trial-results.json"], "representative trial results")
    expected_results = [
        _condition_result(condition, document)
        for condition, document in zip(
            profile.conditions, validated.evidence, strict=True
        )
    ]
    if (
        summary.get("feedbackRows") != validated.feedback_rows
        or summary.get("edgeRows") != validated.edge_rows
        or summary.get("conditionCount") != condition_count
        or results.get("conditions") != expected_results
        or validated.manifest.get("formalPerformanceClaim") is not False
        or len(validated.evidence) != condition_count
        or not files["report.md"].read_text(encoding="utf-8").strip()
    ):
        raise ValueError("representative derived evidence is incomplete")
    return checksums


def load_representative_run_bundle(
    bundle_root: str | Path, *, trial_id: UUID
) -> RepresentativeRunBundle:
    root = Path(bundle_root)
    _reject_symlinked_path(root)
    if (
        root.parent.name != str(trial_id)
        or root.parent.parent.name != "representative-runs"
        or not _SHA256.fullmatch(root.name)
    ):
        raise ValueError(
            "representative bundle must remain beneath representative-runs"
        )
    manifest_path = root / "manifest.json"
    checksums_path = root / "checksums.json"
    manifest = _json_object(manifest_path, "representative manifest")
    scope = manifest.get("evidenceScope")
    if not isinstance(scope, str):
        raise TypeError("representative evidence scope is invalid")
    bundle = RepresentativeRunBundle(
        root=root,
        trial_id=trial_id,
        evidence_scope=scope,
        manifest_path=manifest_path,
        checksums_path=checksums_path,
        artifact_sha256=root.name,
    )
    _validate_local_bundle(bundle)
    return bundle


def _operations(api: Any, bundle: RepresentativeRunBundle) -> list[Any]:
    paths = [path for _, path in sorted(_regular_files(bundle.root).items())]
    if type(api).__module__.startswith("huggingface_hub"):
        from huggingface_hub import CommitOperationAdd

        return [
            CommitOperationAdd(
                path_in_repo=(
                    f"{bundle.bundle_path}/{path.relative_to(bundle.root).as_posix()}"
                ),
                path_or_fileobj=path,
            )
            for path in paths
        ]
    return [
        UploadOperation(
            f"{bundle.bundle_path}/{path.relative_to(bundle.root).as_posix()}",
            str(path),
        )
        for path in paths
    ]


def publish_representative_run_bundle(
    api: Any,
    bundle: RepresentativeRunBundle,
    *,
    repo_id: str,
    token: str | None,
    revision: str = "main",
) -> RepresentativeRunPublicationReceipt:
    """Publish once under representative-runs and verify the returned commit."""
    if not token:
        raise ValueError("a private Hugging Face token is required")
    _validate_local_bundle(bundle)
    info = api.dataset_info(repo_id=repo_id, revision=revision, token=token)
    if getattr(info, "private", None) is not True:
        raise ValueError("dataset repository privacy could not be proved private")
    prefix = bundle.bundle_path
    remote_files = api.list_repo_files(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    _validate_local_bundle(bundle)
    if any(path == prefix or path.startswith(prefix + "/") for path in remote_files):
        raise AcceptedRunExists(f"remote representative run already exists: {prefix}")
    result = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
        operations=_operations(api, bundle),
        commit_message=f"Publish immutable representative run {bundle.trial_id}",
        token=token,
    )
    commit_sha = str(
        getattr(result, "oid", None)
        or getattr(result, "commit_id", None)
        or getattr(result, "commit_sha", "")
    )
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise ValueError("Hugging Face did not return an immutable commit SHA")

    expected_remote = {f"{prefix}/{name}" for name in _regular_files(bundle.root)}
    committed_files = api.list_repo_files(
        repo_id=repo_id,
        repo_type="dataset",
        revision=commit_sha,
        token=token,
    )
    actual_remote = {
        name
        for name in committed_files
        if name == prefix or name.startswith(prefix + "/")
    }
    if actual_remote != expected_remote:
        raise ValueError(
            "remote representative inventory differs from the closed bundle"
        )
    remote_checksums = _download(
        api,
        repo_id=repo_id,
        filename=f"{prefix}/checksums.json",
        revision=commit_sha,
        token=token,
    )
    if _sha256(remote_checksums) != bundle.artifact_sha256:
        raise ValueError("remote representative checksum inventory differs")
    checksums = _json_object(remote_checksums, "remote representative checksums")
    verified: dict[str, str] = {}
    loaded: dict[str, Path] = {}
    for name, digest in checksums.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise TypeError("remote representative checksum entry is invalid")
        path = _download(
            api,
            repo_id=repo_id,
            filename=f"{prefix}/{name}",
            revision=commit_sha,
            token=token,
        )
        loaded[name] = path
        if _sha256(path) != digest:
            raise ValueError(f"remote representative checksum mismatch: {name}")
        verified[name] = digest
    remote_manifest = _json_object(
        loaded["manifest.json"], "remote representative manifest"
    )
    if (
        remote_manifest.get("namespace") != "representative-runs"
        or remote_manifest.get("trialId") != str(bundle.trial_id)
        or remote_manifest.get("formalPerformanceClaim") is not False
        or remote_manifest.get("evidenceScope") != bundle.evidence_scope
    ):
        raise ValueError("remote representative identity differs")
    for name in ("trial-summary.json", "trial-results.json", "model-lineage.json"):
        document = _json_object(loaded[name], f"remote representative {name}")
        if (
            document.get("trialId") != str(bundle.trial_id)
            or document.get("formalPerformanceClaim") is not False
            or document.get("evidenceScope") != bundle.evidence_scope
        ):
            raise ValueError(f"remote representative {name} identity differs")
    if not loaded["report.md"].read_text(encoding="utf-8").strip():
        raise ValueError("remote representative report is empty")
    return RepresentativeRunPublicationReceipt(
        repository=repo_id,
        commit_sha=commit_sha,
        bundle_path=prefix,
        artifact_sha256=bundle.artifact_sha256,
        trial_id=bundle.trial_id,
        evidence_scope=bundle.evidence_scope,
        verified_files={**verified, "checksums.json": bundle.artifact_sha256},
    )


__all__ = [
    "RepresentativeRunBundle",
    "RepresentativeRunPublicationReceipt",
    "build_representative_run_bundle",
    "load_representative_run_bundle",
    "publish_representative_run_bundle",
]
