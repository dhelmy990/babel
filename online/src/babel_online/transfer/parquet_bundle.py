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
from uuid import UUID

import numpy as np

from .contracts import (
    CATALOG_ARROW_SCHEMA,
    EMBEDDINGS_ARROW_SCHEMA,
    PARQUET_WRITER_SETTINGS,
    PayloadMetadataV1,
    PopulationTransferManifestV1,
    PopulationTransferMetadataV1,
)


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
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


def _checked_rows(
    source: PopulationTransferBundleInput,
) -> tuple[list[PopulationTransferRow], list[bytes], list[float]]:
    metadata = source.metadata
    if len(source.rows) != 10_000:
        raise PopulationTransferIntegrityError(
            "population bundle must contain exactly 10,000 rows"
        )
    rows = sorted(source.rows, key=lambda row: row.babel_id)
    pairs = [(row.babel_id, row.model_artifact_id) for row in rows]
    if len(set(pairs)) != len(pairs):
        raise PopulationTransferIntegrityError("duplicate embedding pair")
    identifiers = [row.babel_id for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise PopulationTransferIntegrityError("duplicate Babel ID")
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
        if not 0 <= row.materialized_model_version <= 2_147_483_647:
            raise PopulationTransferIntegrityError("materialized_model_version is outside int32")
        if not 0 <= row.schedule_index <= 2_147_483_647:
            raise PopulationTransferIntegrityError("schedule_index is outside int32")
        if not 0 <= row.creator_event_number <= 2_147_483_647:
            raise PopulationTransferIntegrityError("creator_event_number is outside int32")
        if not row.source_article_key or not row.title or not row.article_text:
            raise PopulationTransferIntegrityError("catalog text identity fields must be non-empty")
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
        if row.dataset_repository != metadata.datasetRepository or row.dataset_configuration != metadata.datasetConfiguration:
            raise PopulationTransferIntegrityError("row dataset identity differs from metadata")
        encoded = vector_f32le(row.vector)
        vectors.append(encoded)
        norms.append(float(np.linalg.norm(np.frombuffer(encoded, dtype="<f4").astype(np.float64))))
    return rows, vectors, norms


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
    root: Path,
) -> Path:
    payloads = {
        name: PayloadMetadataV1(sha256=_sha256(root / name), bytes=(root / name).stat().st_size)
        for name in ("babel_catalog.parquet", "babel_embeddings.parquet", "import_population.py")
    }
    period_counts = dict(sorted(Counter(row.period for row in rows).items()))
    norm_array = np.asarray(norms, dtype=np.float64)
    metadata = source.metadata.model_dump()
    manifest = PopulationTransferManifestV1(
        **metadata,
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
            "babel_embeddings": EMBEDDINGS_ARROW_SCHEMA,
            "babel_catalog": CATALOG_ARROW_SCHEMA,
        },
        writerSettings=PARQUET_WRITER_SETTINGS,
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
    value = "".join(f"{_sha256(root / name)}  {name}\n" for name in _CHECKSUM_NAMES).encode("ascii")
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
        checked, vectors, norms = _checked_rows(rows)
        _write_parquet(checked, vectors, temporary)
        launcher = temporary / "import_population.py"
        launcher.write_bytes(_launcher_bytes())
        launcher.chmod(0o700)
        _write_manifest(rows, checked, norms, temporary)
        _, digest = _write_checksums(temporary)
        verify_bundle(temporary, digest)
        os.replace(temporary, destination)
        return verify_bundle(destination, digest)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _verify_parquet(files: BundleFiles) -> None:
    import pyarrow.parquet as pq

    if pq.read_schema(files.embeddings) != _embedding_schema():
        raise PopulationTransferIntegrityError("babel_embeddings schema mismatch")
    if pq.read_schema(files.catalog) != _catalog_schema():
        raise PopulationTransferIntegrityError("babel_catalog schema mismatch")
    for path in (files.embeddings, files.catalog):
        metadata = pq.ParquetFile(path).metadata
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
                    raise PopulationTransferIntegrityError("Parquet dictionary encoding is forbidden")
        if not metadata.metadata or b"ARROW:schema" not in metadata.metadata:
            raise PopulationTransferIntegrityError("stored Arrow schema is missing")


def _verify_contents(files: BundleFiles) -> None:
    import pyarrow.parquet as pq

    embeddings = pq.read_table(files.embeddings).to_pylist()
    catalog = pq.read_table(files.catalog).to_pylist()
    manifest = files.manifest_contract
    if len(embeddings) != manifest.rowCount or len(catalog) != manifest.rowCount:
        raise PopulationTransferIntegrityError("Parquet row count differs from manifest")
    embedding_ids = [row["babel_id"] for row in embeddings]
    catalog_ids = [row["babel_id"] for row in catalog]
    if embedding_ids != sorted(embedding_ids) or catalog_ids != embedding_ids:
        raise PopulationTransferIntegrityError("Parquet rows are not in canonical Babel-ID order")
    if len(set(embedding_ids)) != len(embedding_ids):
        raise PopulationTransferIntegrityError("duplicate Babel ID in payload")
    norms: list[float] = []
    for embedding, catalog_row in zip(embeddings, catalog, strict=True):
        for row, fields in (
            (
                embedding,
                ("babel_id", "creator_id", "serving_model_id", "embedding_space_id"),
            ),
            (
                catalog_row,
                (
                    "babel_id",
                    "creator_id",
                    "root_babel_id",
                    "traversal_session_id",
                    "work_id",
                ),
            ),
        ):
            for field in fields:
                _canonical_uuid(row[field], field)
        if (
            embedding["serving_model_id"] != str(manifest.servingModelId)
            or embedding["materialized_model_version"]
            != manifest.materializedModelVersion
            or embedding["embedding_space_id"] != str(manifest.embeddingSpaceId)
            or embedding["model_artifact_id"] != manifest.modelArtifactId
            or embedding["dataset_revision"] != manifest.datasetRevision
        ):
            raise PopulationTransferIntegrityError(
                "embedding identity differs from manifest"
            )
        if (
            catalog_row["dataset_repository"] != manifest.datasetRepository
            or catalog_row["dataset_configuration"]
            != manifest.datasetConfiguration
            or catalog_row["dataset_revision"] != manifest.datasetRevision
        ):
            raise PopulationTransferIntegrityError("catalog identity differs from manifest")
        encoded = vector_f32le(embedding["vector"])
        if hashlib.sha256(encoded).hexdigest() != embedding["vector_sha256"]:
            raise PopulationTransferIntegrityError("vector checksum mismatch")
        norms.append(float(np.linalg.norm(np.frombuffer(encoded, dtype="<f4").astype(np.float64))))
        if embedding["creator_id"] != catalog_row["creator_id"] or embedding["catalog_content_hash"] != catalog_row["catalog_content_hash"]:
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
    creators = {row["creator_id"] for row in catalog}
    periods = dict(sorted(Counter(row["period"] for row in catalog).items()))
    if len(creators) != manifest.creatorCount or periods != manifest.periodCounts:
        raise PopulationTransferIntegrityError("creator or period counts differ from manifest")
    norm_array = np.asarray(norms, dtype=np.float64)
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
        if not directory.is_dir() or {path.name for path in directory.iterdir()} != _BUNDLE_NAMES:
            raise PopulationTransferIntegrityError("bundle does not contain exactly five files")
        checksums = directory / "SHA256SUMS"
        if hashlib.sha256(checksums.read_bytes()).hexdigest() != trusted_digest:
            raise PopulationTransferIntegrityError("trusted digest does not match SHA256SUMS")
        lines = checksums.read_text(encoding="ascii").splitlines()
        expected_lines = [f"{_sha256(directory / name)}  {name}" for name in _CHECKSUM_NAMES]
        if lines != expected_lines:
            raise PopulationTransferIntegrityError("bundle checksum coverage or value mismatch")
        launcher = directory / "import_population.py"
        if launcher.read_bytes() != _launcher_bytes() or stat.S_IMODE(launcher.stat().st_mode) != 0o700:
            raise PopulationTransferIntegrityError("import launcher bytes or mode mismatch")
        manifest = PopulationTransferManifestV1.model_validate_json(
            (directory / "manifest.json").read_bytes()
        )
        for name, payload in manifest.payloads.items():
            path = directory / name
            if path.stat().st_size != payload.bytes or _sha256(path) != payload.sha256:
                raise PopulationTransferIntegrityError("manifest payload checksum or size mismatch")
        files = _paths(directory, trusted_digest, manifest)
        _verify_parquet(files)
        _verify_contents(files)
        return files
    except PopulationTransferIntegrityError:
        raise
    except Exception as error:
        raise PopulationTransferIntegrityError(f"bundle verification failed: {error}") from error
