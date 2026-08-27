"""Deterministic, database-free Parquet population bundle construction."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import numpy as np

from .contracts import (
    CATALOG_ARROW_SCHEMA,
    EMBEDDINGS_ARROW_SCHEMA,
    PARQUET_WRITER_SETTINGS,
    POPULATION_HASH_DERIVATIONS,
    PayloadMetadataV1,
    PopulationTransferManifestV1,
    PopulationTransferMetadataV1,
)
from ..contracts import canonical_pgvector_snapshot_sha256


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ARTICLE_KEY = re.compile(r"^enwiki:[1-9][0-9]*$")
_BUNDLE_NAMES = frozenset(
    {
        "SHA256SUMS",
        "babel_catalog.parquet",
        "babel_embeddings.parquet",
        "import_population.py",
        "manifest.json",
    }
)
_CHECKSUM_NAMES = tuple(sorted(_BUNDLE_NAMES - {"SHA256SUMS"}))


class PopulationTransferIntegrityError(ValueError):
    """Bundle rows, bytes, or declared identities violate the closed contract."""


@dataclass(frozen=True, slots=True)
class PopulationTransferRow:
    babel_id: str
    creator_id: str
    serving_model_id: str
    materialized_model_version: int
    embedding_space_id: str
    catalog_content_hash: str
    model_artifact_id: str
    dataset_revision: str
    vector: object
    source_article_key: str
    title: str
    article_text: str
    event_number: int
    created_at_ns: int
    finalized_at_ns: int
    schedule_index: int
    creator_event_number: int
    period: str
    root_babel_id: str
    traversal_session_id: str
    work_id: str
    workload_sha256: str
    schedule_created_at_ns: int
    dataset_repository: str
    dataset_configuration: str


@dataclass(frozen=True, slots=True)
class PopulationTransferBundleInput:
    metadata: PopulationTransferMetadataV1
    rows: tuple[PopulationTransferRow, ...]


@dataclass(frozen=True, slots=True)
class BundleFiles:
    root: Path
    embeddings: Path
    catalog: Path
    manifest: Path
    launcher: Path
    checksums: Path
    digest: str
    manifest_contract: PopulationTransferManifestV1


@dataclass(frozen=True, slots=True)
class _ValidatedPopulation:
    rows: list[PopulationTransferRow]
    vectors: list[bytes]
    norms: list[float]
    hashes: dict[str, str]


def vector_f32le(values: object) -> bytes:
    """Return one finite, unit-normalized 100-dimensional little-endian vector."""

    try:
        vector = np.asarray(values, dtype="<f4")
    except (TypeError, ValueError) as error:
        raise PopulationTransferIntegrityError("vector cannot be represented as f32le") from error
    if vector.shape != (100,):
        raise PopulationTransferIntegrityError("vector shape must be exactly (100,)")
    if not bool(np.isfinite(vector).all()):
        raise PopulationTransferIntegrityError("vector values must all be finite")
    norm = float(np.linalg.norm(vector.astype(np.float64)))
    if not 0.99999 <= norm <= 1.00001:
        raise PopulationTransferIntegrityError("vector L2 norm is outside [0.99999, 1.00001]")
    encoded = vector.tobytes(order="C")
    if len(encoded) != 400:
        raise PopulationTransferIntegrityError("vector encoding is not exactly 400 bytes")
    return encoded


def _canonical_uuid(value: str, field: str) -> str:
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise PopulationTransferIntegrityError(f"{field} is not a UUID") from error
    if value != canonical:
        raise PopulationTransferIntegrityError(f"{field} is not canonical lowercase UUID text")
    return value


def _sha_text(value: str, field: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise PopulationTransferIntegrityError(f"{field} is not a lowercase SHA-256")
    return value


def _launcher_bytes() -> bytes:
    return Path(__file__).with_name("import_population.py").read_bytes()


def _embedding_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("babel_id", pa.string(), nullable=False),
            pa.field("creator_id", pa.string(), nullable=False),
            pa.field("serving_model_id", pa.string(), nullable=False),
            pa.field("materialized_model_version", pa.int32(), nullable=False),
            pa.field("embedding_space_id", pa.string(), nullable=False),
            pa.field("catalog_content_hash", pa.string(), nullable=False),
            pa.field("model_artifact_id", pa.string(), nullable=False),
            pa.field("dataset_revision", pa.string(), nullable=False),
            pa.field(
                "vector",
                pa.list_(pa.float32(), 100),
                nullable=False,
            ),
            pa.field("vector_sha256", pa.string(), nullable=False),
        ]
    )


def _catalog_schema():
    import pyarrow as pa

    fields = [
        ("babel_id", pa.string()),
        ("creator_id", pa.string()),
        ("source_article_key", pa.string()),
        ("title", pa.string()),
        ("article_text", pa.string()),
        ("catalog_content_hash", pa.string()),
        ("event_number", pa.int64()),
        ("created_at_ns", pa.int64()),
        ("finalized_at_ns", pa.int64()),
        ("schedule_index", pa.int32()),
        ("creator_event_number", pa.int32()),
        ("period", pa.string()),
        ("root_babel_id", pa.string()),
        ("traversal_session_id", pa.string()),
        ("work_id", pa.string()),
        ("workload_sha256", pa.string()),
        ("schedule_created_at_ns", pa.int64()),
        ("dataset_repository", pa.string()),
        ("dataset_configuration", pa.string()),
        ("dataset_revision", pa.string()),
        ("dataset_row_reference", pa.string()),
    ]
    return pa.schema([pa.field(name, kind, nullable=False) for name, kind in fields])


def _canonical_json_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _population_hashes(rows: list[PopulationTransferRow], vectors: list[bytes]) -> dict[str, str]:
    content = bytearray()
    schedule = bytearray()
    snapshots: list[dict[str, object]] = []
    for ordinal, (row, vector) in enumerate(zip(rows, vectors, strict=True)):
        vector_sha = hashlib.sha256(vector).hexdigest()
        content.extend(
            _canonical_json_line(
                {
                    "babelId": row.babel_id,
                    "catalogContentHash": row.catalog_content_hash,
                    "createdAtNs": row.created_at_ns,
                    "creatorId": row.creator_id,
                    "eventNumber": row.event_number,
                    "ordinal": ordinal,
                    "sourceArticleKey": row.source_article_key,
                    "text": row.article_text,
                    "title": row.title,
                }
            )
        )
        schedule.extend(
            _canonical_json_line(
                {
                    "creatorEventNumber": row.creator_event_number,
                    "creatorId": row.creator_id,
                    "ordinal": ordinal,
                    "period": row.period,
                    "rootBabelId": row.root_babel_id,
                    "scheduleIndex": row.schedule_index,
                    "sourceArticleKey": row.source_article_key,
                    "traversalSessionId": row.traversal_session_id,
                    "workId": row.work_id,
                    "workloadSha256": row.workload_sha256,
                }
            )
        )
        snapshots.append(
            {
                "babelId": row.babel_id,
                "creatorId": row.creator_id,
                "sourceArticleKey": row.source_article_key,
                "catalogContentHash": row.catalog_content_hash,
                "embeddingSpaceId": row.embedding_space_id,
                "servingModelId": row.serving_model_id,
                "materializedModelVersion": row.materialized_model_version,
                "vectorSha256": vector_sha,
            }
        )
    hashes = {
        "orderedPopulationSha256": hashlib.sha256(b"".join(vectors)).hexdigest(),
        "snapshotSha256": canonical_pgvector_snapshot_sha256(snapshots),
        "scheduleSha256": hashlib.sha256(schedule).hexdigest(),
        "contentSha256": hashlib.sha256(content).hexdigest(),
    }
    hashes["frozenPopulationSha256"] = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return hashes


def _require_integer(value: object, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise PopulationTransferIntegrityError(f"{field} must be a nonnegative integer")
    return value


def _validate_population_rows(
    rows: list[PopulationTransferRow],
    metadata: PopulationTransferMetadataV1 | PopulationTransferManifestV1,
) -> _ValidatedPopulation:
    if len(rows) != 10_000:
        raise PopulationTransferIntegrityError(
            "population bundle must contain exactly 10,000 rows"
        )
    pairs = [(row.babel_id, row.model_artifact_id) for row in rows]
    if len(set(pairs)) != len(pairs):
        raise PopulationTransferIntegrityError("duplicate embedding pair")
    identifiers = [row.babel_id for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise PopulationTransferIntegrityError("duplicate Babel ID")
    if identifiers != sorted(identifiers):
        raise PopulationTransferIntegrityError(
            "population rows must use lowercase Babel-ID order"
        )
    vectors: list[bytes] = []
    norms: list[float] = []
    for row in rows:
        for field in (
            "babel_id",
            "creator_id",
            "serving_model_id",
            "embedding_space_id",
            "root_babel_id",
            "traversal_session_id",
            "work_id",
        ):
            _canonical_uuid(getattr(row, field), field)
        for field in ("catalog_content_hash", "model_artifact_id", "workload_sha256"):
            _sha_text(getattr(row, field), field)
        _require_integer(
            row.materialized_model_version,
            "materialized model version",
            2_147_483_647,
        )
        _require_integer(row.event_number, "event number", 9_223_372_036_854_775_807)
        _require_integer(row.schedule_index, "schedule index", 2_147_483_647)
        _require_integer(row.creator_event_number, "creator event number", 2_147_483_647)
        _require_integer(row.created_at_ns, "created timestamp", 9_223_372_036_854_775_807)
        _require_integer(
            row.schedule_created_at_ns,
            "schedule timestamp",
            9_223_372_036_854_775_807,
        )
        _require_integer(row.finalized_at_ns, "finalized timestamp", 9_223_372_036_854_775_807)
        if row.created_at_ns > row.finalized_at_ns:
            raise PopulationTransferIntegrityError("timestamp order is invalid")
        if not isinstance(row.title, str) or not row.title.strip():
            raise PopulationTransferIntegrityError("title must be non-empty")
        if not isinstance(row.article_text, str) or not row.article_text.strip():
            raise PopulationTransferIntegrityError("article text must be non-empty")
        if _ARTICLE_KEY.fullmatch(row.source_article_key) is None:
            raise PopulationTransferIntegrityError("source article key is not canonical")
        if row.period not in ("2026-06", "2026-07"):
            raise PopulationTransferIntegrityError("period must be 2026-06 or 2026-07")
        if row.root_babel_id != row.babel_id:
            raise PopulationTransferIntegrityError("root Babel ID differs from Babel ID")
        if row.event_number != row.schedule_index:
            raise PopulationTransferIntegrityError("event number differs from schedule index")
        if row.serving_model_id != str(metadata.servingModelId):
            raise PopulationTransferIntegrityError("row serving model differs from metadata")
        if row.materialized_model_version != metadata.materializedModelVersion:
            raise PopulationTransferIntegrityError("row model version differs from metadata")
        if row.embedding_space_id != str(metadata.embeddingSpaceId):
            raise PopulationTransferIntegrityError("row embedding space differs from metadata")
        if row.model_artifact_id != metadata.modelArtifactId:
            raise PopulationTransferIntegrityError("row model artifact differs from metadata")
        if row.dataset_revision != metadata.datasetRevision:
            raise PopulationTransferIntegrityError("row dataset revision differs from metadata")
        if (
            row.dataset_repository != metadata.datasetRepository
            or row.dataset_configuration != metadata.datasetConfiguration
        ):
            raise PopulationTransferIntegrityError("row dataset identity differs from metadata")
        expected_work_id = uuid5(
            metadata.originRunId,
            f"work:{row.creator_id}:{row.creator_event_number}",
        )
        expected_traversal_id = uuid5(
            metadata.originRunId,
            f"traversal:{row.creator_id}:{row.creator_event_number}",
        )
        if row.work_id != str(expected_work_id):
            raise PopulationTransferIntegrityError("work ID differs from schedule identity")
        if row.traversal_session_id != str(expected_traversal_id):
            raise PopulationTransferIntegrityError(
                "traversal ID differs from schedule identity"
            )
        workload = {
            "creatorEventNumber": row.creator_event_number,
            "creatorId": row.creator_id,
            "period": row.period,
            "rootBabelId": row.root_babel_id,
            "runId": str(metadata.originRunId),
            "sourceArticleKey": row.source_article_key,
            "workId": row.work_id,
        }
        expected_workload = hashlib.sha256(
            json.dumps(workload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if row.workload_sha256 != expected_workload:
            raise PopulationTransferIntegrityError(
                "workload hash differs from schedule identity"
            )
        encoded = vector_f32le(row.vector)
        vectors.append(encoded)
        norms.append(float(np.linalg.norm(np.frombuffer(encoded, dtype="<f4").astype(np.float64))))
    if len({row.creator_id for row in rows}) != 50:
        raise PopulationTransferIntegrityError(
            "population bundle must contain exactly 50 creators"
        )
    if dict(sorted(Counter(row.period for row in rows).items())) != {
        "2026-06": 5_000,
        "2026-07": 5_000,
    }:
        raise PopulationTransferIntegrityError(
            "population bundle must contain exactly 5,000 rows per period"
        )
    if sorted(row.event_number for row in rows) != list(range(10_000)):
        raise PopulationTransferIntegrityError("event numbers must be globally contiguous")
    if sorted(row.schedule_index for row in rows) != list(range(10_000)):
        raise PopulationTransferIntegrityError("schedule indexes must be globally contiguous")
    creator_events: dict[str, int] = {}
    creator_sources: set[tuple[str, str]] = set()
    for row in sorted(rows, key=lambda item: item.schedule_index):
        expected_event = creator_events.get(row.creator_id, 0)
        if row.creator_event_number != expected_event:
            raise PopulationTransferIntegrityError(
                "creator event numbers must be contiguous"
            )
        creator_events[row.creator_id] = expected_event + 1
        source_identity = (row.creator_id, row.source_article_key)
        if source_identity in creator_sources:
            raise PopulationTransferIntegrityError(
                "creator source identities must be unique"
            )
        creator_sources.add(source_identity)
    return _ValidatedPopulation(
        rows=rows,
        vectors=vectors,
        norms=norms,
        hashes=_population_hashes(rows, vectors),
    )


def _checked_rows(source: PopulationTransferBundleInput) -> _ValidatedPopulation:
    rows = sorted(source.rows, key=lambda row: row.babel_id)
    return _validate_population_rows(rows, source.metadata)


def _write_parquet(
    rows: list[PopulationTransferRow], vectors: list[bytes], root: Path
) -> tuple[Path, Path]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    embedding_documents: list[dict[str, Any]] = []
    catalog_documents: list[dict[str, Any]] = []
    for row, vector in zip(rows, vectors, strict=True):
        embedding_documents.append(
            {
                "babel_id": row.babel_id,
                "creator_id": row.creator_id,
                "serving_model_id": row.serving_model_id,
                "materialized_model_version": row.materialized_model_version,
                "embedding_space_id": row.embedding_space_id,
                "catalog_content_hash": row.catalog_content_hash,
                "model_artifact_id": row.model_artifact_id,
                "dataset_revision": row.dataset_revision,
                "vector": np.frombuffer(vector, dtype="<f4").tolist(),
                "vector_sha256": hashlib.sha256(vector).hexdigest(),
            }
        )
        row_reference = json.dumps(
            {
                "catalogContentHash": row.catalog_content_hash,
                "period": row.period,
                "sourceArticleKey": row.source_article_key,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        catalog_documents.append(
            {
                "babel_id": row.babel_id,
                "creator_id": row.creator_id,
                "source_article_key": row.source_article_key,
                "title": row.title,
                "article_text": row.article_text,
                "catalog_content_hash": row.catalog_content_hash,
                "event_number": row.event_number,
                "created_at_ns": row.created_at_ns,
                "finalized_at_ns": row.finalized_at_ns,
                "schedule_index": row.schedule_index,
                "creator_event_number": row.creator_event_number,
                "period": row.period,
                "root_babel_id": row.root_babel_id,
                "traversal_session_id": row.traversal_session_id,
                "work_id": row.work_id,
                "workload_sha256": row.workload_sha256,
                "schedule_created_at_ns": row.schedule_created_at_ns,
                "dataset_repository": row.dataset_repository,
                "dataset_configuration": row.dataset_configuration,
                "dataset_revision": row.dataset_revision,
                "dataset_row_reference": row_reference,
            }
        )
    embeddings = root / "babel_embeddings.parquet"
    catalog = root / "babel_catalog.parquet"
    settings = dict(
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        use_compliant_nested_type=True,
        store_schema=True,
        row_group_size=10_000,
    )
    pq.write_table(
        pa.Table.from_pylist(embedding_documents, schema=_embedding_schema()),
        embeddings,
        **settings,
    )
    pq.write_table(
        pa.Table.from_pylist(catalog_documents, schema=_catalog_schema()),
        catalog,
        **settings,
    )
    return embeddings, catalog


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    source: PopulationTransferBundleInput,
    rows: list[PopulationTransferRow],
    norms: list[float],
    hashes: dict[str, str],
    root: Path,
) -> Path:
    import pyarrow as pa

    payloads = {
        name: PayloadMetadataV1(sha256=_sha256(root / name), bytes=(root / name).stat().st_size)
        for name in ("babel_catalog.parquet", "babel_embeddings.parquet", "import_population.py")
    }
    period_counts = dict(sorted(Counter(row.period for row in rows).items()))
    norm_array = np.asarray(norms, dtype=np.float64)
    metadata = source.metadata.model_dump()
    manifest = PopulationTransferManifestV1(
        **metadata,
        **hashes,
        schemaVersion=1,
        bundleFormatVersion=1,
        rowCount=len(rows),
        creatorCount=len({row.creator_id for row in rows}),
        periodCounts=period_counts,
        vectorDimension=100,
        vectorDtype="float32",
        byteOrder="little",
        normalization="l2",
        normalizationTolerance=1e-5,
        vectorNormMin=float(norm_array.min()),
        vectorNormMean=float(norm_array.mean()),
        vectorNormP01=float(np.percentile(norm_array, 1)),
        vectorNormMedian=float(np.median(norm_array)),
        vectorNormP99=float(np.percentile(norm_array, 99)),
        vectorNormMax=float(norm_array.max()),
        arrowSchemas={
            "babel_embeddings": [dict(field) for field in EMBEDDINGS_ARROW_SCHEMA],
            "babel_catalog": [dict(field) for field in CATALOG_ARROW_SCHEMA],
        },
        writerSettings={
            **PARQUET_WRITER_SETTINGS,
            "pyarrowVersion": pa.__version__,
        },
        hashDerivations=dict(POPULATION_HASH_DERIVATIONS),
        payloads=payloads,
    )
    document = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    path = root / "manifest.json"
    path.write_bytes(document)
    return path


def _write_checksums(root: Path) -> tuple[Path, str]:
    checksums = root / "SHA256SUMS"
    value = "".join(
        f"{_sha256(root / name)}  {name}\n" for name in _CHECKSUM_NAMES
    ).encode("ascii")
    checksums.write_bytes(value)
    return checksums, hashlib.sha256(value).hexdigest()


def _paths(root: Path, digest: str, manifest: PopulationTransferManifestV1) -> BundleFiles:
    return BundleFiles(
        root=root,
        embeddings=root / "babel_embeddings.parquet",
        catalog=root / "babel_catalog.parquet",
        manifest=root / "manifest.json",
        launcher=root / "import_population.py",
        checksums=root / "SHA256SUMS",
        digest=digest,
        manifest_contract=manifest,
    )


def _stable_bundle_bytes(directory: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    try:
        root_lstat = directory.lstat()
    except OSError as error:
        raise PopulationTransferIntegrityError("bundle root is unavailable") from error
    if stat.S_ISLNK(root_lstat.st_mode):
        raise PopulationTransferIntegrityError("bundle root must not be a symlink")
    if not stat.S_ISDIR(root_lstat.st_mode):
        raise PopulationTransferIntegrityError("bundle root must be a directory")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(directory, directory_flags)
    try:
        opened_root = os.fstat(directory_fd)
        if (opened_root.st_dev, opened_root.st_ino) != (
            root_lstat.st_dev,
            root_lstat.st_ino,
        ):
            raise PopulationTransferIntegrityError("bundle root changed while opening")
        names = set(os.listdir(directory_fd))
        if names != _BUNDLE_NAMES:
            raise PopulationTransferIntegrityError(
                "bundle does not contain exactly five files"
            )
        payloads: dict[str, bytes] = {}
        modes: dict[str, int] = {}
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for name in sorted(names):
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise PopulationTransferIntegrityError(
                    f"bundle file {name} must not be a symlink"
                )
            if not stat.S_ISREG(before.st_mode):
                raise PopulationTransferIntegrityError(
                    f"bundle file {name} must be a regular file"
                )
            file_fd = os.open(name, file_flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(file_fd)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise PopulationTransferIntegrityError(
                        f"bundle file {name} changed while opening"
                    )
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(file_fd, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(file_fd)
                if (
                    after.st_size != opened.st_size
                    or after.st_mtime_ns != opened.st_mtime_ns
                    or after.st_ctime_ns != opened.st_ctime_ns
                ):
                    raise PopulationTransferIntegrityError(
                        f"bundle file {name} changed while reading"
                    )
                value = b"".join(chunks)
                if len(value) != opened.st_size:
                    raise PopulationTransferIntegrityError(
                        f"bundle file {name} size changed while reading"
                    )
                payloads[name] = value
                modes[name] = stat.S_IMODE(opened.st_mode)
            finally:
                os.close(file_fd)
        return payloads, modes
    finally:
        os.close(directory_fd)


def write_bundle_payloads(
    rows: PopulationTransferBundleInput, root: str | Path
) -> BundleFiles:
    """Write, reload, verify, and atomically install one deterministic bundle."""

    destination = Path(root)
    if destination.exists():
        raise PopulationTransferIntegrityError("bundle destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    temporary.chmod(0o700)
    try:
        validated = _checked_rows(rows)
        _write_parquet(validated.rows, validated.vectors, temporary)
        launcher = temporary / "import_population.py"
        launcher.write_bytes(_launcher_bytes())
        launcher.chmod(0o700)
        _write_manifest(
            rows,
            validated.rows,
            validated.norms,
            validated.hashes,
            temporary,
        )
        _, digest = _write_checksums(temporary)
        verify_bundle(temporary, digest)
        os.replace(temporary, destination)
        return verify_bundle(destination, digest)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _verify_parquet(files: BundleFiles, payloads: dict[str, bytes]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if files.manifest_contract.writerSettings.pyarrowVersion != pa.__version__:
        raise PopulationTransferIntegrityError("PyArrow writer/runtime version mismatch")
    parquet_files = {
        files.embeddings: pq.ParquetFile(
            pa.BufferReader(payloads[files.embeddings.name])
        ),
        files.catalog: pq.ParquetFile(pa.BufferReader(payloads[files.catalog.name])),
    }
    if parquet_files[files.embeddings].schema_arrow != _embedding_schema():
        raise PopulationTransferIntegrityError("babel_embeddings schema mismatch")
    if parquet_files[files.catalog].schema_arrow != _catalog_schema():
        raise PopulationTransferIntegrityError("babel_catalog schema mismatch")
    for path, parquet_file in parquet_files.items():
        metadata = parquet_file.metadata
        if metadata.format_version != "2.6":
            raise PopulationTransferIntegrityError("Parquet version mismatch")
        if (
            metadata.num_rows != 10_000
            or metadata.num_row_groups != 1
            or metadata.row_group(0).num_rows != 10_000
        ):
            raise PopulationTransferIntegrityError(
                "Parquet must contain one exact 10,000-row group"
            )
        if (
            path == files.embeddings
            and metadata.schema.column(8).path != "vector.list.element"
        ):
            raise PopulationTransferIntegrityError(
                "Parquet compliant nested vector layout mismatch"
            )
        for group_index in range(metadata.num_row_groups):
            group = metadata.row_group(group_index)
            for column_index in range(group.num_columns):
                column = group.column(column_index)
                if column.compression != "ZSTD" or column.statistics is None:
                    raise PopulationTransferIntegrityError("Parquet writer settings mismatch")
                if "RLE_DICTIONARY" in column.encodings or "PLAIN_DICTIONARY" in column.encodings:
                    raise PopulationTransferIntegrityError(
                        "Parquet dictionary encoding is forbidden"
                    )
        if not metadata.metadata or b"ARROW:schema" not in metadata.metadata:
            raise PopulationTransferIntegrityError("stored Arrow schema is missing")


def _verify_contents(files: BundleFiles, payloads: dict[str, bytes]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    embeddings = pq.read_table(
        pa.BufferReader(payloads[files.embeddings.name])
    ).to_pylist()
    catalog = pq.read_table(pa.BufferReader(payloads[files.catalog.name])).to_pylist()
    manifest = files.manifest_contract
    if len(embeddings) != manifest.rowCount or len(catalog) != manifest.rowCount:
        raise PopulationTransferIntegrityError("Parquet row count differs from manifest")
    embedding_ids = [row["babel_id"] for row in embeddings]
    catalog_ids = [row["babel_id"] for row in catalog]
    if embedding_ids != sorted(embedding_ids) or catalog_ids != embedding_ids:
        raise PopulationTransferIntegrityError("Parquet rows are not in canonical Babel-ID order")
    if len(set(embedding_ids)) != len(embedding_ids):
        raise PopulationTransferIntegrityError("duplicate Babel ID in payload")
    semantic_rows: list[PopulationTransferRow] = []
    for embedding, catalog_row in zip(embeddings, catalog, strict=True):
        encoded = vector_f32le(embedding["vector"])
        if hashlib.sha256(encoded).hexdigest() != embedding["vector_sha256"]:
            raise PopulationTransferIntegrityError("vector checksum mismatch")
        if (
            embedding["babel_id"] != catalog_row["babel_id"]
            or embedding["creator_id"] != catalog_row["creator_id"]
            or embedding["catalog_content_hash"] != catalog_row["catalog_content_hash"]
            or embedding["dataset_revision"] != catalog_row["dataset_revision"]
        ):
            raise PopulationTransferIntegrityError("catalog and embedding identities differ")
        expected_reference = json.dumps(
            {
                "catalogContentHash": catalog_row["catalog_content_hash"],
                "period": catalog_row["period"],
                "sourceArticleKey": catalog_row["source_article_key"],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if catalog_row["dataset_row_reference"] != expected_reference:
            raise PopulationTransferIntegrityError("dataset row reference mismatch")
        semantic_rows.append(
            PopulationTransferRow(
                babel_id=embedding["babel_id"],
                creator_id=embedding["creator_id"],
                serving_model_id=embedding["serving_model_id"],
                materialized_model_version=embedding["materialized_model_version"],
                embedding_space_id=embedding["embedding_space_id"],
                catalog_content_hash=embedding["catalog_content_hash"],
                model_artifact_id=embedding["model_artifact_id"],
                dataset_revision=embedding["dataset_revision"],
                vector=embedding["vector"],
                source_article_key=catalog_row["source_article_key"],
                title=catalog_row["title"],
                article_text=catalog_row["article_text"],
                event_number=catalog_row["event_number"],
                created_at_ns=catalog_row["created_at_ns"],
                finalized_at_ns=catalog_row["finalized_at_ns"],
                schedule_index=catalog_row["schedule_index"],
                creator_event_number=catalog_row["creator_event_number"],
                period=catalog_row["period"],
                root_babel_id=catalog_row["root_babel_id"],
                traversal_session_id=catalog_row["traversal_session_id"],
                work_id=catalog_row["work_id"],
                workload_sha256=catalog_row["workload_sha256"],
                schedule_created_at_ns=catalog_row["schedule_created_at_ns"],
                dataset_repository=catalog_row["dataset_repository"],
                dataset_configuration=catalog_row["dataset_configuration"],
            )
        )
    validated = _validate_population_rows(semantic_rows, manifest)
    for field, value in validated.hashes.items():
        if getattr(manifest, field) != value:
            raise PopulationTransferIntegrityError(f"{field} differs from payload")
    norm_array = np.asarray(validated.norms, dtype=np.float64)
    actual = (
        float(norm_array.min()),
        float(norm_array.mean()),
        float(np.percentile(norm_array, 1)),
        float(np.median(norm_array)),
        float(np.percentile(norm_array, 99)),
        float(norm_array.max()),
    )
    declared = (
        manifest.vectorNormMin,
        manifest.vectorNormMean,
        manifest.vectorNormP01,
        manifest.vectorNormMedian,
        manifest.vectorNormP99,
        manifest.vectorNormMax,
    )
    if actual != declared or any(not math.isfinite(value) for value in actual):
        raise PopulationTransferIntegrityError("vector norm statistics mismatch")


def verify_bundle(root: str | Path, trusted_digest: str) -> BundleFiles:
    """Verify exact file bytes, strict manifest, schemas, settings, and row content."""

    directory = Path(root)
    try:
        if _SHA256.fullmatch(trusted_digest) is None:
            raise PopulationTransferIntegrityError("trusted digest is not a SHA-256")
        payloads, modes = _stable_bundle_bytes(directory)
        checksum_bytes = payloads["SHA256SUMS"]
        if hashlib.sha256(checksum_bytes).hexdigest() != trusted_digest:
            raise PopulationTransferIntegrityError("trusted digest does not match SHA256SUMS")
        lines = checksum_bytes.decode("ascii").splitlines()
        expected_lines = [
            f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}"
            for name in _CHECKSUM_NAMES
        ]
        if lines != expected_lines:
            raise PopulationTransferIntegrityError("bundle checksum coverage or value mismatch")
        if payloads["import_population.py"] != _launcher_bytes() or modes[
            "import_population.py"
        ] != 0o700:
            raise PopulationTransferIntegrityError("import launcher bytes or mode mismatch")
        manifest = PopulationTransferManifestV1.model_validate_json(
            payloads["manifest.json"]
        )
        for name, payload in manifest.payloads.items():
            value = payloads[name]
            if (
                len(value) != payload.bytes
                or hashlib.sha256(value).hexdigest() != payload.sha256
            ):
                raise PopulationTransferIntegrityError(
                    "manifest payload checksum or size mismatch"
                )
        files = _paths(directory, trusted_digest, manifest)
        _verify_parquet(files, payloads)
        _verify_contents(files, payloads)
        return files
    except PopulationTransferIntegrityError:
        raise
    except Exception as error:
        raise PopulationTransferIntegrityError(f"bundle verification failed: {error}") from error
