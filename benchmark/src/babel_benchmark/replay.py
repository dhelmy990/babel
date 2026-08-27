"""Deterministic replay and created-candidate-universe validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .contracts import CreatedBabelV1, ReplayRequestV1, ReplayRequestV2, load_jsonl


@dataclass(frozen=True, slots=True)
class ReplayCorpus:
    rows: tuple[ReplayRequestV1 | ReplayRequestV2, ...]
    sha256: str

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        contract: type[ReplayRequestV1] | type[ReplayRequestV2] | None = None,
    ) -> "ReplayCorpus":
        source = Path(path)
        if contract is not None:
            rows = tuple(load_jsonl(source, contract))
        else:
            parsed: list[ReplayRequestV1 | ReplayRequestV2] = []
            for line_number, line in enumerate(source.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    document = json.loads(line)
                    request_version = document.get("request", {}).get("schemaVersion")
                    request_contract = (
                        ReplayRequestV2 if request_version == 2 else ReplayRequestV1
                    )
                    parsed.append(request_contract.model_validate(document))
                except (json.JSONDecodeError, ValueError) as error:
                    raise ValueError(
                        f"invalid {source} line {line_number}: {error}"
                    ) from error
            rows = tuple(parsed)
        if not rows:
            raise ValueError("replay corpus cannot be empty")
        versions = {row.request.schemaVersion for row in rows}
        if len(versions) != 1:
            raise ValueError("replay corpus cannot mix request schema versions")
        request_ids = [row.request.requestId for row in rows]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("replay request IDs must be unique")
        offsets = [row.scheduleOffsetNs for row in rows]
        if offsets[0] != 0 or any(
            left >= right for left, right in zip(offsets, offsets[1:])
        ):
            raise ValueError("replay offsets must start at zero and strictly increase")
        return cls(rows, hashlib.sha256(source.read_bytes()).hexdigest())

    @property
    def request_schema_version(self) -> int:
        return self.rows[0].request.schemaVersion


@dataclass(frozen=True, slots=True)
class CandidateUniverse:
    rows: tuple[CreatedBabelV1, ...]
    sha256: str
    by_id: dict[UUID, CreatedBabelV1]

    @classmethod
    def from_jsonl(
        cls, path: str | Path, contract=CreatedBabelV1
    ) -> "CandidateUniverse":
        source = Path(path)
        rows = tuple(load_jsonl(source, contract))
        by_id = {row.babelId: row for row in rows}
        if not rows or len(by_id) != len(rows):
            raise ValueError("created candidate universe must be nonempty and unique")
        run_ids = {row.runId for row in rows}
        if len(run_ids) != 1:
            raise ValueError("created candidate universe must belong to one run")
        return cls(rows, hashlib.sha256(source.read_bytes()).hexdigest(), by_id)

    def validate_candidates(self, *, requester: UUID, response_rows: tuple) -> None:
        for candidate in response_rows:
            created = self.by_id.get(candidate.babelId)
            if created is None:
                raise ValueError(
                    "response escaped the created synthetic Babel universe"
                )
            if created.creatorId != candidate.creatorId:
                raise ValueError(
                    "response candidate ownership differs from the frozen universe"
                )
            if created.creatorId == requester:
                raise ValueError("response contains the request creator's own Babel")


__all__ = ["CandidateUniverse", "ReplayCorpus"]
