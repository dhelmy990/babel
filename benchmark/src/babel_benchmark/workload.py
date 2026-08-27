"""Freeze one observed simulator workload for topology-paired replay.

The reference coordinator owns all decisions.  This module only records the
requests, feedback and traversal draws that actually happened, normalizes the
run identifier for semantic hashing, and rebinds that identifier for a paired
condition.  It never regenerates simulator decisions.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

from .scale import FrozenWorkloadReceipt


_NORMALIZED_RUN_ID = "00000000-0000-0000-0000-000000000000"
_PAYLOAD_FILES = (
    "requests.template.jsonl",
    "feedback.template.jsonl",
    "creator-schedule.jsonl",
    "event-mix.jsonl",
    "start-draws.jsonl",
    "continuation-draws.jsonl",
)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def normalize_workload_document(value: Any) -> Any:
    """Replace only fields named ``runId`` with the semantic sentinel."""
    value = _jsonable(value)
    if isinstance(value, dict):
        return {
            key: (
                _NORMALIZED_RUN_ID
                if key == "runId"
                else normalize_workload_document(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_workload_document(item) for item in value]
    return value


def _rebind_run_id(value: Any, run_id: UUID) -> Any:
    value = _jsonable(value)
    if isinstance(value, dict):
        return {
            key: (str(run_id) if key == "runId" else _rebind_run_id(item, run_id))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rebind_run_id(item, run_id) for item in value]
    return value


def _semantic_sha(rows: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_json_bytes(normalize_workload_document(row)))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenWorkloadBundle:
    path: Path
    receipt: FrozenWorkloadReceipt
    source_run_id: UUID

    @property
    def identity(self) -> tuple[str, ...]:
        return self.receipt.replay_identity

    @property
    def payload_files(self) -> tuple[str, ...]:
        return _PAYLOAD_FILES


@dataclass(frozen=True, slots=True)
class ConditionWorkloadBundle:
    path: Path
    receipt: FrozenWorkloadReceipt
    run_id: UUID

    @property
    def identity(self) -> tuple[str, ...]:
        return self.receipt.replay_identity

    @property
    def payload_files(self) -> tuple[str, ...]:
        return _PAYLOAD_FILES


class WorkloadTraceCollector:
    """Thread-safe sink for a single reference coordinator execution."""

    def __init__(self, *, schedule: Sequence[Any], target_rps: float) -> None:
        if not schedule:
            raise ValueError("workload schedule cannot be empty")
        if target_rps <= 0:
            raise ValueError("target_rps must be positive")
        indexes = [row.schedule_index for row in schedule]
        if indexes != list(range(len(schedule))):
            raise ValueError("workload schedule indexes must be contiguous")
        run_ids = {row.run_id for row in schedule}
        if len(run_ids) != 1:
            raise ValueError("workload schedule must belong to one run")
        self.schedule = tuple(schedule)
        self.target_rps = target_rps
        self.source_run_id = next(iter(run_ids))
        self._schedule_by_session = {
            row.traversal_session_id: row for row in self.schedule
        }
        self._requests: list[tuple[int, int, Any]] = []
        self._next_request: dict[UUID, int] = defaultdict(int)
        self._feedback: dict[UUID, Any] = {}
        self._rolls: dict[UUID, tuple[Any, ...]] = {}
        self._lock = Lock()

    def record_request(self, request: Any) -> None:
        with self._lock:
            scheduled = self._schedule_by_session.get(request.traversalSessionId)
            if scheduled is None or request.runId != self.source_run_id:
                raise ValueError("request is outside the frozen schedule/run")
            ordinal = self._next_request[request.traversalSessionId]
            self._next_request[request.traversalSessionId] += 1
            self._requests.append((scheduled.schedule_index, ordinal, request))

    def record_feedback(self, event: Any) -> None:
        with self._lock:
            if event.runId != self.source_run_id:
                raise ValueError("feedback is outside the frozen run")
            if event.requestId in self._feedback:
                raise ValueError("duplicate feedback request identity")
            self._feedback[event.requestId] = event

    def record_response(self, request: Any, response: Any, client_total_ns: int) -> None:
        """Validate live evidence without making condition timing part of the workload."""
        if client_total_ns < 0:
            raise ValueError("client latency cannot be negative")
        if response.requestId != request.requestId or response.runId != request.runId:
            raise ValueError("response identity differs from the recorded request")

    def record_rolls(self, traversal_session_id: UUID, rolls: Sequence[Any]) -> None:
        with self._lock:
            if traversal_session_id not in self._schedule_by_session:
                raise ValueError("roll evidence is outside the frozen schedule")
            if traversal_session_id in self._rolls:
                raise ValueError("duplicate traversal roll evidence")
            self._rolls[traversal_session_id] = tuple(rolls)

    def documents(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            ordered_requests = sorted(self._requests, key=lambda row: (row[0], row[1]))
            feedback = dict(self._feedback)
            rolls = dict(self._rolls)
        if not ordered_requests:
            raise ValueError("reference execution recorded no recommendation requests")
        request_ids = [request.requestId for _, _, request in ordered_requests]
        if len(set(request_ids)) != len(request_ids) or set(request_ids) != set(
            feedback
        ):
            raise ValueError(
                "each recorded request must have exactly one recorded feedback event"
            )
        if set(rolls) != set(self._schedule_by_session):
            raise ValueError("every scheduled traversal must record its roll evidence")

        interval_ns = max(1, round(1_000_000_000 / self.target_rps))
        request_rows = [
            {"scheduleOffsetNs": position * interval_ns, "request": _jsonable(request)}
            for position, (_, _, request) in enumerate(ordered_requests)
        ]
        feedback_rows = [_jsonable(feedback[request_id]) for request_id in request_ids]
        schedule_rows = [
            {
                "runId": str(row.run_id),
                "scheduleIndex": row.schedule_index,
                "creatorId": str(row.creator_id),
                "creatorEventNumber": row.creator_event_number,
                "traversalSessionId": str(row.traversal_session_id),
                "period": row.period,
                "sourceArticleKey": row.source_article_key,
                "rootBabelId": str(row.root_babel_id),
                "workId": str(row.work_id),
                "workloadSha256": row.workload_sha256,
            }
            for row in self.schedule
        ]
        event_mix_rows = []
        for event in feedback_rows:
            counts = Counter(row["action"] for row in event["candidateActions"])
            event_mix_rows.append(
                {
                    "runId": event["runId"],
                    "requestId": event["requestId"],
                    "eventId": event["eventId"],
                    "creatorId": event["creatorId"],
                    "includeCount": counts["include"],
                    "excludeCount": counts["exclude"],
                    "ignoreCount": counts["ignore"],
                    "actionSequence": [
                        row["action"] for row in event["candidateActions"]
                    ],
                }
            )
        draw_rows = {"start": [], "continuation": []}
        for scheduled in self.schedule:
            session_rolls = rolls[scheduled.traversal_session_id]
            starts = [row for row in session_rolls if row.kind == "start"]
            if len(starts) != 1:
                raise ValueError("every traversal must have exactly one start draw")
            for row in session_rolls:
                if row.probability != 0.4:
                    raise ValueError(
                        "formal start and continuation probabilities must both be 0.40"
                    )
                payload = {
                    "runId": str(self.source_run_id),
                    "traversalSessionId": str(scheduled.traversal_session_id),
                    "scheduleIndex": scheduled.schedule_index,
                    "drawIndex": row.draw_index,
                    "kind": row.kind,
                    "sourceBabelId": str(row.source_babel_id),
                    "targetBabelId": (
                        str(row.target_babel_id) if row.target_babel_id else None
                    ),
                    "targetRank": row.target_rank,
                    "sourceDepth": row.source_depth,
                    "drawValue": row.draw_value,
                    "probability": row.probability,
                    "rollSucceeded": row.roll_succeeded,
                    "outcome": row.outcome,
                }
                draw_rows[row.kind].append(payload)
        if not draw_rows["continuation"]:
            raise ValueError("reference workload must contain continuation draw evidence")
        return {
            "requests.template.jsonl": request_rows,
            "feedback.template.jsonl": feedback_rows,
            "creator-schedule.jsonl": schedule_rows,
            "event-mix.jsonl": event_mix_rows,
            "start-draws.jsonl": draw_rows["start"],
            "continuation-draws.jsonl": draw_rows["continuation"],
        }


def _write_documents(
    path: Path, documents: Mapping[str, Sequence[dict[str, Any]]]
) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {}
    for filename in _PAYLOAD_FILES:
        rows = list(documents[filename])
        payload = b"".join(_json_bytes(row) for row in rows)
        (path / filename).write_bytes(payload)
        evidence[filename] = {
            "rowCount": len(rows),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "semanticSha256": _semantic_sha(rows),
        }
    return evidence


def freeze_workload(
    collector: WorkloadTraceCollector, output_path: Path
) -> FrozenWorkloadBundle:
    documents = collector.documents()
    evidence = _write_documents(output_path, documents)
    receipt = FrozenWorkloadReceipt(
        request_count=len(documents["requests.template.jsonl"]),
        request_corpus_sha256=evidence["requests.template.jsonl"]["semanticSha256"],
        feedback_sha256=evidence["feedback.template.jsonl"]["semanticSha256"],
        creator_schedule_sha256=evidence["creator-schedule.jsonl"]["semanticSha256"],
        event_mix_sha256=evidence["event-mix.jsonl"]["semanticSha256"],
        start_draws_sha256=evidence["start-draws.jsonl"]["semanticSha256"],
        continuation_draws_sha256=evidence["continuation-draws.jsonl"][
            "semanticSha256"
        ],
        creator_schedule_scope="creator_local",
        start_probability=0.4,
        continuation_probability=0.4,
        independent_draw_streams=True,
    )
    manifest = {
        "schemaVersion": 1,
        "sourceRunId": str(collector.source_run_id),
        "files": evidence,
        "receipt": receipt.model_dump(mode="json"),
    }
    (output_path / "manifest.json").write_bytes(_json_bytes(manifest))
    return FrozenWorkloadBundle(output_path, receipt, collector.source_run_id)


def _read_documents(
    path: Path, manifest: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    documents: dict[str, list[dict[str, Any]]] = {}
    if set(manifest["files"]) != set(_PAYLOAD_FILES):
        raise ValueError("frozen workload manifest has an unexpected payload set")
    for filename in _PAYLOAD_FILES:
        payload = (path / filename).read_bytes()
        expected = manifest["files"][filename]
        if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
            raise ValueError(f"workload checksum mismatch: {filename}")
        rows = [json.loads(line) for line in payload.splitlines()]
        if (
            len(rows) != expected["rowCount"]
            or _semantic_sha(rows) != expected["semanticSha256"]
        ):
            raise ValueError(f"workload semantic checksum mismatch: {filename}")
        documents[filename] = rows
    return documents


def load_workload_documents(
    path: Path,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Load checksum-verified payloads for the live condition replay driver."""
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("schemaVersion") != 1:
        raise ValueError("unsupported frozen workload manifest")
    return {
        filename: tuple(rows)
        for filename, rows in _read_documents(path, manifest).items()
    }


def load_frozen_workload(path: Path) -> FrozenWorkloadBundle:
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("schemaVersion") != 1:
        raise ValueError("unsupported frozen workload manifest")
    _read_documents(path, manifest)
    receipt = FrozenWorkloadReceipt.model_validate(manifest["receipt"])
    semantic = tuple(
        manifest["files"][name]["semanticSha256"] for name in _PAYLOAD_FILES
    )
    if semantic != receipt.replay_identity:
        raise ValueError("manifest receipt does not match semantic payload checksums")
    return FrozenWorkloadBundle(path, receipt, UUID(manifest["sourceRunId"]))


def materialize_condition_workload(
    frozen: FrozenWorkloadBundle,
    *,
    run_id: UUID,
    output_path: Path,
) -> ConditionWorkloadBundle:
    verified = load_frozen_workload(frozen.path)
    manifest = json.loads((verified.path / "manifest.json").read_text())
    originals = _read_documents(verified.path, manifest)
    rebound = {
        filename: [_rebind_run_id(row, run_id) for row in rows]
        for filename, rows in originals.items()
    }
    evidence = _write_documents(output_path, rebound)
    semantic = tuple(evidence[name]["semanticSha256"] for name in _PAYLOAD_FILES)
    if semantic != verified.identity:
        raise RuntimeError("condition runId rebinding changed workload semantics")
    condition_manifest = {
        "schemaVersion": 1,
        "sourceRunId": str(verified.source_run_id),
        "conditionRunId": str(run_id),
        "files": evidence,
        "receipt": verified.receipt.model_dump(mode="json"),
    }
    (output_path / "manifest.json").write_bytes(_json_bytes(condition_manifest))
    return ConditionWorkloadBundle(output_path, verified.receipt, run_id)


__all__ = [
    "ConditionWorkloadBundle",
    "FrozenWorkloadBundle",
    "WorkloadTraceCollector",
    "freeze_workload",
    "load_frozen_workload",
    "load_workload_documents",
    "materialize_condition_workload",
    "normalize_workload_document",
]
