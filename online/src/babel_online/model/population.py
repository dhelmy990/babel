"""Resumable real-Qwen population of finalized synthetic-created Babels."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import numpy as np

from ..contracts import ModelManifestV2
from ..observable import CreatedBabel, VectorRecord
from .artifact import model_manifest_sha256
from .qwen_encoder import Qwen100Encoder, format_article_input


class PopulationIntegrityError(ValueError):
    """Population state differs from its immutable source/model identity."""


@dataclass(frozen=True, slots=True)
class PopulationSource:
    babel: CreatedBabel
    catalog_content_hash: str

    def __post_init__(self) -> None:
        if (
            len(self.catalog_content_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.catalog_content_hash)
        ):
            raise ValueError("catalog content hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class PopulationIdentity:
    run_id: UUID
    dataset_revision: str
    model_id: UUID
    model_version: int
    model_manifest_sha256: str
    artifact_manifest_sha256: str
    artifact_repo: str
    artifact_revision: str
    artifact_id: str
    training_dataset_revision: str
    embedding_space_id: UUID
    embedding_space_version: str

    def __post_init__(self) -> None:
        if self.model_version < 0:
            raise ValueError("model version must be nonnegative")
        if (
            len(self.dataset_revision) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in self.dataset_revision)
        ):
            raise ValueError("population dataset revision must be a pinned checksum")
        if self.embedding_space_version != "babel-qwen-100d-v1":
            raise ValueError("population requires the real Qwen embedding space")
        for value in (self.model_manifest_sha256, self.artifact_manifest_sha256):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("population model checksums must be lowercase SHA-256")
        if self.artifact_repo != "dhelmy990/babel-qwen-navigation-2016-interview":
            raise ValueError("population requires the accepted private Qwen artifact")
        if (
            len(self.artifact_revision) != 40
            or len(self.artifact_id) != 64
            or len(self.training_dataset_revision) != 40
            or any(
                character not in "0123456789abcdef"
                for value in (
                    self.artifact_revision,
                    self.artifact_id,
                    self.training_dataset_revision,
                )
                for character in value
            )
        ):
            raise ValueError("population artifact identity must be commit-pinned")

    @classmethod
    def from_real_model(
        cls,
        *,
        run_id: UUID,
        dataset_revision: str,
        model: ModelManifestV2,
        model_version: int,
    ) -> "PopulationIdentity":
        if not isinstance(model, ModelManifestV2) or model.acceptance != "real_50k_qwen":
            raise TypeError("population requires the accepted real Qwen model manifest")
        return cls(
            run_id=run_id,
            dataset_revision=dataset_revision,
            model_id=model.modelId,
            model_version=model_version,
            model_manifest_sha256=model_manifest_sha256(model),
            artifact_manifest_sha256=model.artifactManifestSha256,
            artifact_repo=model.encoderRepo,
            artifact_revision=model.encoderRevision,
            artifact_id=model.artifactId,
            training_dataset_revision=model.datasetRevision,
            embedding_space_id=model.embeddingSpace.embeddingSpaceId,
            embedding_space_version=model.embeddingSpace.compatibilityVersion,
        )

    def document(self) -> dict[str, object]:
        value = asdict(self)
        for name in ("run_id", "model_id", "embedding_space_id"):
            value[name] = str(value[name])
        return value


@dataclass(frozen=True, slots=True)
class PopulationActivationEvidence:
    table_bytes: int
    index_bytes: int
    explain_plan: object | None


@dataclass(frozen=True, slots=True)
class PopulationReceipt:
    complete: bool
    created_count: int
    indexed_count: int
    failure_count: int
    formal_ready: bool
    snapshot_sha256: str | None
    created_content_manifest_sha256: str
    duration_seconds: float
    rows_per_second: float
    table_bytes: int
    index_bytes: int
    explain_plan: object | None
    hnsw_used: bool


class PopulationDatabase(Protocol):
    def population_sources(
        self, run_id: UUID, *, after_babel_id: UUID | None, limit: int
    ) -> Sequence[PopulationSource]: ...

    def write_population_batch(
        self, records: Sequence[VectorRecord], expected: PopulationIdentity
    ) -> None: ...

    def population_vectors(
        self,
        expected: PopulationIdentity,
        *,
        after_babel_id: UUID | None,
        limit: int,
    ) -> Sequence[VectorRecord]: ...

    def activate_population(
        self, expected: PopulationIdentity, *, snapshot_sha256: str
    ) -> PopulationActivationEvidence: ...


def _json_line(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _source_line(source: PopulationSource) -> bytes:
    babel = source.babel
    return _json_line(
        {
            "babelId": str(babel.babelId),
            "creatorId": str(babel.creatorId),
            "sourceArticleKey": babel.sourceArticleKey,
            "titleSha256": hashlib.sha256(babel.title.encode("utf-8")).hexdigest(),
            "textSha256": hashlib.sha256(babel.text.encode("utf-8")).hexdigest(),
            "catalogContentHash": source.catalog_content_hash,
        }
    )


def _vector_sha(record: VectorRecord) -> str:
    vector = np.asarray(record.vector, dtype="<f4")
    if vector.shape != (100,) or not np.isfinite(vector).all():
        raise PopulationIntegrityError("population vector is not finite 100d float32")
    return hashlib.sha256(vector.tobytes()).hexdigest()


def _snapshot_line(record: VectorRecord) -> bytes:
    return _json_line(
        {
            "babelId": str(record.babel.babelId),
            "creatorId": str(record.babel.creatorId),
            "sourceArticleKey": record.babel.sourceArticleKey,
            "catalogContentHash": record.catalogContentHash,
            "embeddingSpaceId": str(record.embeddingSpaceId),
            "servingModelId": str(record.servingModelId),
            "materializedModelVersion": record.materializedModelVersion,
            "vectorSha256": _vector_sha(record),
        }
    )


def _atomic_json(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(document, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _scan_sources(
    database: PopulationDatabase, run_id: UUID, batch_size: int
) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    after: UUID | None = None
    while True:
        rows = list(database.population_sources(run_id, after_babel_id=after, limit=batch_size))
        if not rows:
            break
        if any(row.babel.runId != run_id for row in rows):
            raise PopulationIntegrityError("population source crossed run boundary")
        identifiers = [str(row.babel.babelId) for row in rows]
        if identifiers != sorted(identifiers) or (
            after is not None and identifiers[0] <= str(after)
        ):
            raise PopulationIntegrityError("population sources are not strictly ID ordered")
        for row in rows:
            digest.update(_source_line(row))
        count += len(rows)
        after = rows[-1].babel.babelId
    return count, digest.hexdigest()


def _records(
    sources: Sequence[PopulationSource],
    encoder: Qwen100Encoder,
    identity: PopulationIdentity,
) -> list[VectorRecord]:
    texts = [format_article_input(row.babel.title, row.babel.text) for row in sources]
    vectors = encoder.encode(texts)
    if (
        not isinstance(vectors, np.ndarray)
        or vectors.dtype != np.dtype(np.float32)
        or vectors.shape != (len(sources), 100)
        or not vectors.flags.c_contiguous
        or not np.isfinite(vectors).all()
    ):
        raise PopulationIntegrityError("real Qwen population returned invalid float32 vectors")
    return [
        VectorRecord(
            babel=source.babel,
            catalogContentHash=source.catalog_content_hash,
            embeddingSpaceId=identity.embedding_space_id,
            servingModelId=identity.model_id,
            materializedModelVersion=identity.model_version,
            vector=tuple(float(value) for value in vector),
        )
        for source, vector in zip(sources, vectors, strict=True)
    ]


def _plan_names_hnsw(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            (key == "Index Name" and item == "babel_embeddings_cosine_hnsw")
            or _plan_names_hnsw(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_plan_names_hnsw(item) for item in value)
    return False


def _verify_snapshot(
    database: PopulationDatabase,
    identity: PopulationIdentity,
    *,
    batch_size: int,
) -> tuple[int, int, str]:
    created = indexed = 0
    digest = hashlib.sha256()
    source_after: UUID | None = None
    vector_after: UUID | None = None
    while True:
        sources = list(
            database.population_sources(
                identity.run_id, after_babel_id=source_after, limit=batch_size
            )
        )
        vectors = list(
            database.population_vectors(
                identity, after_babel_id=vector_after, limit=batch_size
            )
        )
        created += len(sources)
        indexed += len(vectors)
        if len(sources) != len(vectors):
            raise PopulationIntegrityError("created and indexed IDs are not exactly equal")
        if not sources:
            break
        for source, record in zip(sources, vectors, strict=True):
            if (
                source.babel != record.babel
                or source.catalog_content_hash != record.catalogContentHash
                or record.embeddingSpaceId != identity.embedding_space_id
                or record.servingModelId != identity.model_id
                or record.materializedModelVersion != identity.model_version
            ):
                raise PopulationIntegrityError("created and indexed IDs or identities differ")
            digest.update(_snapshot_line(record))
        source_after = sources[-1].babel.babelId
        vector_after = vectors[-1].babel.babelId
    return created, indexed, digest.hexdigest()


def populate_created_babel_vectors(
    *,
    database: PopulationDatabase,
    encoder: Qwen100Encoder,
    identity: PopulationIdentity,
    state_root: str | Path,
    batch_size: int = 32,
    formal_minimum: int = 10_000,
    stop_after_batches: int | None = None,
) -> PopulationReceipt:
    """Populate exact real-Qwen vectors, journaling only after each DB commit."""
    if not isinstance(encoder, Qwen100Encoder):
        raise TypeError("population requires the real Qwen100Encoder")
    contract = getattr(encoder, "contract", None)
    if contract is None or (
        getattr(contract, "artifactRepo", None) != identity.artifact_repo
        or getattr(contract, "artifactRevision", None) != identity.artifact_revision
        or getattr(contract, "artifactId", None) != identity.artifact_id
        or getattr(contract, "datasetRevision", None)
        != identity.training_dataset_revision
        or getattr(contract, "embeddingDimension", None) != 100
    ):
        raise PopulationIntegrityError(
            "Qwen encoder contract differs from population model/artifact identity"
        )
    if batch_size <= 0:
        raise ValueError("population batch size must be positive")
    if formal_minimum < 10_000:
        raise ValueError("formal population minimum cannot be lower than 10,000")
    started = time.monotonic()
    created_count, manifest_sha = _scan_sources(database, identity.run_id, batch_size)
    journal_path = Path(state_root) / str(identity.run_id) / "population" / "journal.json"
    existing: dict[str, object] | None = None
    if journal_path.exists():
        existing = json.loads(journal_path.read_text(encoding="utf-8"))
        if existing.get("identity") != identity.document():
            raise PopulationIntegrityError("population journal identity differs")
        if existing.get("created_content_manifest_sha256") != manifest_sha:
            raise PopulationIntegrityError("created-content manifest differs from journal")
    journal: dict[str, object] = existing or {
        "schema_version": 1,
        "identity": identity.document(),
        "created_content_manifest_sha256": manifest_sha,
        "last_committed_babel_id": None,
        "committed_count": 0,
        "committed_prefix_sha256": hashlib.sha256().hexdigest(),
        "failure_count": 0,
        "failure_attempt_count": 0,
        "unresolved_failure_count": 0,
        "complete": False,
    }
    # Historical failures remain telemetry, while a new attempt begins clean.
    journal.setdefault("failure_attempt_count", int(journal.get("failure_count", 0)))
    journal["failure_count"] = 0
    journal["unresolved_failure_count"] = 0
    _atomic_json(journal_path, journal)

    def record_current_failure() -> None:
        journal["failure_attempt_count"] = int(journal["failure_attempt_count"]) + 1
        journal["failure_count"] = 1
        journal["unresolved_failure_count"] = 1
        _atomic_json(journal_path, journal)

    committed_id = (
        UUID(str(journal["last_committed_babel_id"]))
        if journal.get("last_committed_babel_id")
        else None
    )
    prefix = hashlib.sha256()
    # On resume, re-encode every committed source in bounded batches.  The DB
    # writer must compare an existing row byte-for-byte instead of ignoring it.
    verify_after: UUID | None = None
    verified = 0
    try:
        while committed_id is not None and (
            verify_after is None or str(verify_after) < str(committed_id)
        ):
            rows = list(
                database.population_sources(
                    identity.run_id, after_babel_id=verify_after, limit=batch_size
                )
            )
            rows = [
                row for row in rows if str(row.babel.babelId) <= str(committed_id)
            ]
            if not rows:
                break
            records = _records(rows, encoder, identity)
            database.write_population_batch(records, identity)
            for record in records:
                prefix.update(_snapshot_line(record))
            verified += len(records)
            verify_after = rows[-1].babel.babelId
        if committed_id is not None and (
            verified != int(journal["committed_count"])
            or prefix.hexdigest() != journal["committed_prefix_sha256"]
        ):
            raise PopulationIntegrityError("existing vector committed prefix differs")
    except Exception:
        record_current_failure()
        raise

    after = committed_id
    batches = 0
    try:
        while True:
            sources = list(
                database.population_sources(
                    identity.run_id, after_babel_id=after, limit=batch_size
                )
            )
            if not sources:
                break
            records = _records(sources, encoder, identity)
            database.write_population_batch(records, identity)
            for record in records:
                prefix.update(_snapshot_line(record))
            after = sources[-1].babel.babelId
            journal.update(
                {
                    "last_committed_babel_id": str(after),
                    "committed_count": int(journal["committed_count"]) + len(records),
                    "committed_prefix_sha256": prefix.hexdigest(),
                }
            )
            # The database transaction is complete before this atomic rename.
            _atomic_json(journal_path, journal)
            batches += 1
            if stop_after_batches is not None and batches >= stop_after_batches:
                duration = time.monotonic() - started
                return PopulationReceipt(
                    complete=False,
                    created_count=created_count,
                    indexed_count=int(journal["committed_count"]),
                    failure_count=int(journal["failure_count"]),
                    formal_ready=False,
                    snapshot_sha256=None,
                    created_content_manifest_sha256=manifest_sha,
                    duration_seconds=duration,
                    rows_per_second=int(journal["committed_count"]) / max(duration, 1e-9),
                    table_bytes=0,
                    index_bytes=0,
                    explain_plan=None,
                    hnsw_used=False,
                )
    except Exception:
        record_current_failure()
        raise

    try:
        exact_created, indexed_count, snapshot_sha = _verify_snapshot(
            database, identity, batch_size=batch_size
        )
        if exact_created != created_count or indexed_count != created_count:
            raise PopulationIntegrityError("created and indexed IDs are not exactly equal")
        if int(journal["unresolved_failure_count"]) != 0:
            raise PopulationIntegrityError(
                "population has unresolved failures; activation withheld"
            )
        evidence = database.activate_population(
            identity, snapshot_sha256=snapshot_sha
        )
        plan = evidence.explain_plan
        sizes = {
            "table_bytes": evidence.table_bytes,
            "index_bytes": evidence.index_bytes,
        }
    except Exception:
        record_current_failure()
        raise
    duration = time.monotonic() - started
    journal.update(
        {
            "complete": True,
            "snapshot_sha256": snapshot_sha,
            "duration_seconds": duration,
            "rows_per_second": indexed_count / max(duration, 1e-9),
            "table_bytes": int(sizes.get("table_bytes", 0)),
            "index_bytes": int(sizes.get("index_bytes", 0)),
            "hnsw_used": _plan_names_hnsw(plan),
        }
    )
    _atomic_json(journal_path, journal)
    if created_count >= formal_minimum:
        _atomic_json(
            journal_path.with_name("explain.json"),
            {
                "identity": identity.document(),
                "snapshot_sha256": snapshot_sha,
                "plan": plan,
                "uses_babel_embeddings_cosine_hnsw": _plan_names_hnsw(plan),
            },
        )
    return PopulationReceipt(
        complete=True,
        created_count=created_count,
        indexed_count=indexed_count,
        failure_count=0,
        formal_ready=created_count >= formal_minimum,
        snapshot_sha256=snapshot_sha,
        created_content_manifest_sha256=manifest_sha,
        duration_seconds=duration,
        rows_per_second=indexed_count / max(duration, 1e-9),
        table_bytes=int(sizes.get("table_bytes", 0)),
        index_bytes=int(sizes.get("index_bytes", 0)),
        explain_plan=plan,
        hnsw_used=_plan_names_hnsw(plan),
    )


__all__ = [
    "PopulationActivationEvidence",
    "PopulationDatabase",
    "PopulationIdentity",
    "PopulationIntegrityError",
    "PopulationReceipt",
    "PopulationSource",
    "populate_created_babel_vectors",
]
