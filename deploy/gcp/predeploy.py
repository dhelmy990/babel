#!/usr/bin/env python3
"""Fail-closed verification of the imported GCP population before rollout."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, RFC_4122, uuid5


ORIGIN_TRIAL_ID = UUID("ce8e54ff-e317-4a89-b7db-90327e02dc43")
ORIGIN_RUN_ID = UUID("7f4ad291-e6d0-5bb9-9658-3605c634a3a9")
MODEL_ID = UUID("2c4c48d5-3dcf-5ab9-8191-cd4edc2cbf67")
EMBEDDING_SPACE_ID = UUID("f3665769-b470-5228-8df4-08004e252aa4")
MODEL_REPOSITORY = "dhelmy990/babel-qwen-navigation-2016-interview"
MODEL_REVISION = "57d949cd634b920cc1a46f27c9b21df094b5240e"
ARTIFACT_ID = "3f6b43e574eb2bcac55c4ddf95f624e3f42153f97437cfeba703c9b3b110a1f8"
ARTIFACT_MANIFEST_SHA256 = (
    "5e04eeb0d04f6a15fc1eda2ad7a6034fad82f7a3da648179dbc2e0cf71b68a2f"
)
MODEL_MANIFEST_SHA256 = (
    "174e5109b5f34808b2d3814b12a6b2a452da1f1828f43561d392aa58844a8f09"
)
TRAINING_DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
TRAINING_DATASET_REVISION = "b440e98b04ab77afed7caf0455eca3189235fc3b"
DATASET_REPOSITORY = "dhelmy990/babel-wikipedia-experiment"
DATASET_CONFIG = "crosswalk_2026_06_07"
DATASET_REVISION = "0d1ab2c7f0e2295682288fcf10077d2d776bf559"
DATASET_MANIFEST_SHA256 = (
    "069c84e32195d7e175968aa0c569fe5bebc3a148247dbf9e7e34918ef3a22c0f"
)
HNSW_INDEX_NAME = "babel_embeddings_cosine_hnsw"
EXPECTED_COUNT = 10_000
EXPECTED_DIMENSION = 100
SHA256 = re.compile(r"^[a-f0-9]{64}$")
ARTICLE_KEY = re.compile(r"^enwiki:[1-9][0-9]*$")


class PredeployValidationError(ValueError):
    """The live database differs from the accepted imported population."""


METADATA_SQL = """
WITH expected AS (
  SELECT %s::uuid AS trial_id, %s::uuid AS run_id
),
trial_row AS (
  SELECT p.* FROM performance_experiments AS p
  JOIN expected AS e ON p.id=e.trial_id
),
run_row AS (
  SELECT r.* FROM experiment_runs AS r
  JOIN expected AS e ON r.id=e.run_id
),
model_row AS (
  SELECT m.* FROM recommender_models AS m
  JOIN run_row AS r ON m.id=r.starting_model_id
),
state_row AS (
  SELECT s.* FROM run_embedding_states AS s
  JOIN expected AS e ON s.run_id=e.run_id
),
index_row AS (
  SELECT am.amname AS method, i.indisvalid AS is_valid, i.indisready AS is_ready
  FROM pg_catalog.pg_class AS idx
  JOIN pg_catalog.pg_index AS i ON i.indexrelid=idx.oid
  JOIN pg_catalog.pg_class AS tbl ON tbl.oid=i.indrelid
  JOIN pg_catalog.pg_namespace AS ns ON ns.oid=tbl.relnamespace
  JOIN pg_catalog.pg_am AS am ON am.oid=idx.relam
  WHERE ns.nspname='public' AND tbl.relname='babel_embeddings'
    AND idx.relname='babel_embeddings_cosine_hnsw'
)
SELECT
  (SELECT count(*) FROM trial_row) AS trial_row_count,
  (SELECT id FROM trial_row) AS trial_id,
  (SELECT status FROM trial_row) AS trial_status,
  (SELECT population_ready FROM trial_row) AS trial_population_ready,
  (SELECT run_id FROM trial_row) AS trial_run_id,
  (SELECT starting_model_id FROM trial_row) AS trial_starting_model_id,
  (SELECT model_repository FROM trial_row) AS trial_model_repository,
  (SELECT model_revision FROM trial_row) AS trial_model_revision,
  (SELECT dataset_repository FROM trial_row) AS trial_dataset_repository,
  (SELECT dataset_revision FROM trial_row) AS trial_dataset_revision,
  (SELECT retrieval_backend FROM trial_row) AS trial_retrieval_backend,
  (SELECT target_created_babels FROM trial_row) AS trial_target_count,
  (SELECT population_vector_count FROM trial_row) AS trial_population_count,
  (SELECT population_vector_sha256 FROM trial_row)
    AS trial_population_vector_sha256,
  (SELECT population_model_repository FROM trial_row)
    AS trial_population_model_repository,
  (SELECT population_model_revision FROM trial_row)
    AS trial_population_model_revision,
  (SELECT population_model_sha256 FROM trial_row)
    AS trial_population_model_sha256,
  (SELECT population_dataset_repository FROM trial_row)
    AS trial_population_dataset_repository,
  (SELECT population_dataset_revision FROM trial_row)
    AS trial_population_dataset_revision,
  (SELECT population_dataset_sha256 FROM trial_row)
    AS trial_population_dataset_sha256,
  (SELECT population_manifest_sha256 FROM trial_row)
    AS trial_population_manifest_sha256,
  (SELECT count(*) FROM run_row) AS run_row_count,
  (SELECT id FROM run_row) AS run_id,
  (SELECT status FROM run_row) AS run_status,
  (SELECT retrieval_backend FROM run_row) AS run_retrieval_backend,
  (SELECT contract_version FROM run_row) AS run_contract_version,
  (SELECT scenario FROM run_row) AS run_scenario,
  (SELECT environment_sequence FROM run_row) AS run_environment_sequence,
  (SELECT target_created_babels FROM run_row) AS run_target_count,
  (SELECT created_babel_count FROM run_row) AS run_created_count,
  (SELECT starting_model_id FROM run_row) AS run_starting_model_id,
  (SELECT active_model_id FROM run_row) AS run_active_model_id,
  (SELECT active_model_version FROM run_row) AS run_active_model_version,
  (SELECT dataset_repository FROM run_row) AS run_dataset_repository,
  (SELECT dataset_config FROM run_row) AS run_dataset_config,
  (SELECT dataset_revision FROM run_row) AS run_dataset_revision,
  (SELECT count(*) FROM model_row) AS model_row_count,
  (SELECT id FROM model_row) AS model_id,
  (SELECT parent_model_id FROM model_row) AS model_parent_model_id,
  (SELECT producing_run_id FROM model_row) AS model_producing_run_id,
  (SELECT encoder_repo FROM model_row) AS model_encoder_repository,
  (SELECT encoder_revision FROM model_row) AS model_encoder_revision,
  (SELECT dataset_repo FROM model_row) AS model_dataset_repository,
  (SELECT dataset_revision FROM model_row) AS model_dataset_revision,
  (SELECT training_examples FROM model_row) AS model_training_examples,
  (SELECT checkpoint_sha256 FROM model_row) AS model_checkpoint_sha256,
  (SELECT embedding_space FROM model_row) AS model_embedding_space,
  (SELECT immutable FROM model_row) AS model_immutable,
  (SELECT count(*) FROM state_row) AS state_row_count,
  (SELECT active_model_id FROM state_row) AS state_active_model_id,
  (SELECT active_model_version FROM state_row) AS state_active_model_version,
  (SELECT embedding_space_id FROM state_row) AS state_embedding_space_id,
  (SELECT pgvector_snapshot_sha256 FROM state_row)
    AS state_pgvector_snapshot_sha256,
  (SELECT backend_snapshot_sha256 FROM state_row)
    AS state_backend_snapshot_sha256,
  (SELECT count(*) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id) AS catalog_count,
  (SELECT count(DISTINCT xb.babel_id) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id) AS catalog_unique_babel_count,
  (SELECT count(*) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id AND xb.finalized_at IS NOT NULL
     AND xb.article_text IS NOT NULL AND char_length(xb.article_text)>0
     AND xb.catalog_content_hash ~ '^[a-f0-9]{64}$'
     AND xb.source_article_key ~ '^enwiki:[1-9][0-9]*$') AS catalog_valid_count,
  (SELECT count(*) FROM babel_embeddings AS eb, expected AS e
   WHERE eb.run_id=e.run_id) AS embedding_count,
  (SELECT count(DISTINCT eb.babel_id) FROM babel_embeddings AS eb, expected AS e
   WHERE eb.run_id=e.run_id) AS embedding_unique_babel_count,
  (SELECT count(*) FROM babel_embeddings AS eb, expected AS e, state_row AS s
   WHERE eb.run_id=e.run_id AND eb.serving_model_id=s.active_model_id
     AND eb.materialized_model_version=s.active_model_version
     AND eb.embedding_space_id=s.embedding_space_id)
    AS embedding_exact_active_count,
  (SELECT count(*) FROM babel_embeddings AS eb, expected AS e
   WHERE eb.run_id=e.run_id AND public.vector_dims(eb.embedding)=100
     AND eb.embedding::text !~* '(nan|infinity)')
    AS embedding_valid_vector_count,
  (SELECT count(*) FROM babel_embeddings AS eb
   JOIN experiment_babels AS xb
     ON xb.run_id=eb.run_id AND xb.babel_id=eb.babel_id
   JOIN expected AS e ON eb.run_id=e.run_id
   WHERE eb.creator_id=xb.creator_id
     AND eb.catalog_content_hash=xb.catalog_content_hash)
    AS embedding_catalog_match_count,
  (SELECT count(*) FROM index_row) AS hnsw_index_count,
  (SELECT min(method) FROM index_row) AS hnsw_index_method,
  (SELECT bool_and(is_valid) FROM index_row) AS hnsw_index_valid,
  (SELECT bool_and(is_ready) FROM index_row) AS hnsw_index_ready
"""


VECTOR_SQL = """
SELECT eb.babel_id,xb.creator_id,eb.creator_id,eb.embedding_space_id,
       eb.serving_model_id,eb.catalog_content_hash,xb.source_article_key,
       eb.materialized_model_version,public.vector_send(eb.embedding)
FROM babel_embeddings AS eb
JOIN experiment_babels AS xb
  ON xb.run_id=eb.run_id AND xb.babel_id=eb.babel_id
WHERE eb.run_id=%s
ORDER BY eb.babel_id
"""


# Subsequent deployments trust the immutable ready import receipt and stored
# population hashes.  This query deliberately never selects, casts, sends, or
# hashes the vector payload column.
REUSE_METADATA_SQL = """
WITH expected AS (
  SELECT %s::uuid AS trial_id, %s::uuid AS run_id
),
trial_row AS (
  SELECT p.* FROM performance_experiments AS p
  JOIN expected AS e ON p.id=e.trial_id
),
run_row AS (
  SELECT r.* FROM experiment_runs AS r
  JOIN expected AS e ON r.id=e.run_id
),
model_row AS (
  SELECT m.* FROM recommender_models AS m
  JOIN run_row AS r ON m.id=r.starting_model_id
),
state_row AS (
  SELECT s.* FROM run_embedding_states AS s
  JOIN expected AS e ON s.run_id=e.run_id
),
index_row AS (
  SELECT am.amname AS method, i.indisvalid AS is_valid, i.indisready AS is_ready
  FROM pg_catalog.pg_class AS idx
  JOIN pg_catalog.pg_index AS i ON i.indexrelid=idx.oid
  JOIN pg_catalog.pg_class AS tbl ON tbl.oid=i.indrelid
  JOIN pg_catalog.pg_namespace AS ns ON ns.oid=tbl.relnamespace
  JOIN pg_catalog.pg_am AS am ON am.oid=idx.relam
  WHERE ns.nspname='public' AND tbl.relname='babel_embeddings'
    AND idx.relname='babel_embeddings_cosine_hnsw'
)
SELECT
  (SELECT count(*) FROM trial_row) AS trial_row_count,
  (SELECT id FROM trial_row) AS trial_id,
  (SELECT status FROM trial_row) AS trial_status,
  (SELECT population_ready FROM trial_row) AS trial_population_ready,
  (SELECT run_id FROM trial_row) AS trial_run_id,
  (SELECT starting_model_id FROM trial_row) AS trial_starting_model_id,
  (SELECT model_repository FROM trial_row) AS trial_model_repository,
  (SELECT model_revision FROM trial_row) AS trial_model_revision,
  (SELECT dataset_repository FROM trial_row) AS trial_dataset_repository,
  (SELECT dataset_revision FROM trial_row) AS trial_dataset_revision,
  (SELECT retrieval_backend FROM trial_row) AS trial_retrieval_backend,
  (SELECT target_created_babels FROM trial_row) AS trial_target_count,
  (SELECT population_vector_count FROM trial_row) AS trial_population_count,
  (SELECT population_vector_sha256 FROM trial_row) AS trial_population_vector_sha256,
  (SELECT population_model_repository FROM trial_row) AS trial_population_model_repository,
  (SELECT population_model_revision FROM trial_row) AS trial_population_model_revision,
  (SELECT population_model_sha256 FROM trial_row) AS trial_population_model_sha256,
  (SELECT population_dataset_repository FROM trial_row) AS trial_population_dataset_repository,
  (SELECT population_dataset_revision FROM trial_row) AS trial_population_dataset_revision,
  (SELECT population_dataset_sha256 FROM trial_row) AS trial_population_dataset_sha256,
  (SELECT population_manifest_sha256 FROM trial_row) AS trial_population_manifest_sha256,
  (SELECT count(*) FROM run_row) AS run_row_count,
  (SELECT id FROM run_row) AS run_id,
  (SELECT status FROM run_row) AS run_status,
  (SELECT retrieval_backend FROM run_row) AS run_retrieval_backend,
  (SELECT contract_version FROM run_row) AS run_contract_version,
  (SELECT scenario FROM run_row) AS run_scenario,
  (SELECT environment_sequence FROM run_row) AS run_environment_sequence,
  (SELECT target_created_babels FROM run_row) AS run_target_count,
  (SELECT created_babel_count FROM run_row) AS run_created_count,
  (SELECT starting_model_id FROM run_row) AS run_starting_model_id,
  (SELECT active_model_id FROM run_row) AS run_active_model_id,
  (SELECT active_model_version FROM run_row) AS run_active_model_version,
  (SELECT dataset_repository FROM run_row) AS run_dataset_repository,
  (SELECT dataset_config FROM run_row) AS run_dataset_config,
  (SELECT dataset_revision FROM run_row) AS run_dataset_revision,
  (SELECT count(*) FROM model_row) AS model_row_count,
  (SELECT id FROM model_row) AS model_id,
  (SELECT parent_model_id FROM model_row) AS model_parent_model_id,
  (SELECT producing_run_id FROM model_row) AS model_producing_run_id,
  (SELECT encoder_repo FROM model_row) AS model_encoder_repository,
  (SELECT encoder_revision FROM model_row) AS model_encoder_revision,
  (SELECT dataset_repo FROM model_row) AS model_dataset_repository,
  (SELECT dataset_revision FROM model_row) AS model_dataset_revision,
  (SELECT training_examples FROM model_row) AS model_training_examples,
  (SELECT checkpoint_sha256 FROM model_row) AS model_checkpoint_sha256,
  (SELECT embedding_space FROM model_row) AS model_embedding_space,
  (SELECT immutable FROM model_row) AS model_immutable,
  (SELECT count(*) FROM state_row) AS state_row_count,
  (SELECT active_model_id FROM state_row) AS state_active_model_id,
  (SELECT active_model_version FROM state_row) AS state_active_model_version,
  (SELECT embedding_space_id FROM state_row) AS state_embedding_space_id,
  (SELECT pgvector_snapshot_sha256 FROM state_row) AS state_pgvector_snapshot_sha256,
  (SELECT backend_snapshot_sha256 FROM state_row) AS state_backend_snapshot_sha256,
  (SELECT count(*) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id) AS catalog_count,
  (SELECT count(DISTINCT xb.babel_id) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id) AS catalog_unique_babel_count,
  (SELECT min(xb.creator_id::text) FROM experiment_babels AS xb, expected AS e
   WHERE xb.run_id=e.run_id) AS sample_creator_id,
  (SELECT count(*) FROM babel_embeddings AS eb, expected AS e
   WHERE eb.run_id=e.run_id) AS embedding_count,
  (SELECT count(DISTINCT eb.babel_id) FROM babel_embeddings AS eb, expected AS e
   WHERE eb.run_id=e.run_id) AS embedding_unique_babel_count,
  (SELECT count(*) FROM index_row) AS hnsw_index_count,
  (SELECT min(method) FROM index_row) AS hnsw_index_method,
  (SELECT bool_and(is_valid) FROM index_row) AS hnsw_index_valid,
  (SELECT bool_and(is_ready) FROM index_row) AS hnsw_index_ready
"""


EXPLAIN_SQL = """
EXPLAIN (FORMAT JSON)
SELECT eb.babel_id
FROM babel_embeddings AS eb
WHERE eb.run_id=%s AND eb.serving_model_id=%s
  AND eb.materialized_model_version=%s AND eb.embedding_space_id=%s
ORDER BY eb.embedding <=> %s::public.vector
LIMIT 10
"""


def canonical_json(value: object) -> bytes:
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


def _default_connect(database_url: str):
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - deployment packaging
        raise RuntimeError("predeploy verification requires psycopg") from error
    return psycopg.connect(database_url)


def read_database_snapshot(
    database_url: str,
    *,
    trial_id: UUID,
    run_id: UUID,
    connect: Callable[[str], Any] | None = None,
) -> tuple[dict[str, object], list[tuple[object, ...]]]:
    """Read all evidence in a transaction that PostgreSQL marks read-only."""
    connector = connect or _default_connect
    with connector(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        cursor.execute(METADATA_SQL, (trial_id, run_id))
        row = cursor.fetchone()
        if row is None or cursor.description is None:
            raise PredeployValidationError("metadata query returned no evidence")
        columns = [description.name for description in cursor.description]
        metadata = dict(zip(columns, row, strict=True))
        cursor.execute(VECTOR_SQL, (run_id,))
        vectors = list(cursor.fetchall())
        if not vectors:
            raise PredeployValidationError("active population has no vector for HNSW probe")
        probe = _vector_literal(vectors[0][8])
        cursor.execute("SET LOCAL enable_seqscan=off")
        cursor.execute(
            EXPLAIN_SQL,
            (run_id, MODEL_ID, 0, EMBEDDING_SPACE_ID, probe),
        )
        plan = cursor.fetchone()
        metadata["hnsw_plan_uses_named_index"] = bool(
            plan is not None and HNSW_INDEX_NAME in json.dumps(plan, sort_keys=True)
        )
    return metadata, vectors


def read_database_reuse_snapshot(
    database_url: str,
    *,
    trial_id: UUID,
    run_id: UUID,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, object]:
    """Read provenance/count/index metadata without touching vector payloads."""
    connector = connect or _default_connect
    with connector(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute(REUSE_METADATA_SQL, (trial_id, run_id))
        row = cursor.fetchone()
        if row is None or cursor.description is None:
            raise PredeployValidationError("reuse metadata query returned no evidence")
        columns = [description.name for description in cursor.description]
        return dict(zip(columns, row, strict=True))


def _value(metadata: Mapping[str, object], field: str) -> object:
    try:
        return metadata[field]
    except KeyError as error:
        raise PredeployValidationError(f"{field} is absent") from error


def _uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise PredeployValidationError(f"{field} is not a UUID") from error


def _expect(
    metadata: Mapping[str, object], field: str, expected: object
) -> None:
    actual = _value(metadata, field)
    if isinstance(expected, UUID):
        actual = _uuid(actual, field)
    elif isinstance(expected, bool):
        if not isinstance(actual, bool):
            raise PredeployValidationError(f"{field} differs from accepted value")
    if actual != expected:
        raise PredeployValidationError(f"{field} differs from accepted value")


def _require_sha(value: object, field: str) -> str:
    text = str(value)
    if SHA256.fullmatch(text) is None:
        raise PredeployValidationError(f"{field} is not a lowercase SHA-256")
    return text


def _accepted_embedding_space() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "dimension": EXPECTED_DIMENSION,
        "distance": "cosine",
        "distilledEncoderArtifact": (
            f"hf://{MODEL_REPOSITORY}@{MODEL_REVISION}/artifacts/{ARTIFACT_ID}"
        ),
        "datasetRevision": TRAINING_DATASET_REVISION,
        "compatibilityVersion": "babel-qwen-100d-v1",
    }


def _validate_ids(trial_id: UUID, run_id: UUID) -> None:
    if trial_id.version != 4 or trial_id.variant != RFC_4122:
        raise PredeployValidationError("trial_id must be a fresh RFC 4122 UUIDv4")
    if trial_id == ORIGIN_TRIAL_ID or run_id == ORIGIN_RUN_ID:
        raise PredeployValidationError("GCP IDs must differ from origin IDs")
    if run_id != uuid5(trial_id, "population"):
        raise PredeployValidationError("run_id must be UUIDv5(trial_id, 'population')")


def _expected_metadata(
    *,
    trial_id: UUID,
    run_id: UUID,
    expected_vector_sha256: str,
    expected_snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "trial_row_count": 1,
        "trial_id": trial_id,
        "trial_status": "population_ready",
        "trial_population_ready": True,
        "trial_run_id": run_id,
        "trial_starting_model_id": MODEL_ID,
        "trial_model_repository": MODEL_REPOSITORY,
        "trial_model_revision": MODEL_REVISION,
        "trial_dataset_repository": DATASET_REPOSITORY,
        "trial_dataset_revision": DATASET_REVISION,
        "trial_retrieval_backend": "pgvector",
        "trial_target_count": EXPECTED_COUNT,
        "trial_population_count": EXPECTED_COUNT,
        "trial_population_vector_sha256": expected_vector_sha256,
        "trial_population_model_repository": MODEL_REPOSITORY,
        "trial_population_model_revision": MODEL_REVISION,
        "trial_population_model_sha256": MODEL_MANIFEST_SHA256,
        "trial_population_dataset_repository": DATASET_REPOSITORY,
        "trial_population_dataset_revision": DATASET_REVISION,
        "trial_population_dataset_sha256": DATASET_MANIFEST_SHA256,
        "run_row_count": 1,
        "run_id": run_id,
        "run_status": "completed",
        "run_retrieval_backend": "pgvector",
        "run_contract_version": 2,
        "run_scenario": "june_to_july",
        "run_environment_sequence": ["2026-06", "2026-07"],
        "run_target_count": EXPECTED_COUNT,
        "run_created_count": EXPECTED_COUNT,
        "run_starting_model_id": MODEL_ID,
        "run_active_model_id": MODEL_ID,
        "run_active_model_version": 0,
        "run_dataset_repository": DATASET_REPOSITORY,
        "run_dataset_config": DATASET_CONFIG,
        "run_dataset_revision": DATASET_REVISION,
        "model_row_count": 1,
        "model_id": MODEL_ID,
        "model_parent_model_id": None,
        "model_producing_run_id": None,
        "model_encoder_repository": MODEL_REPOSITORY,
        "model_encoder_revision": MODEL_REVISION,
        "model_dataset_repository": TRAINING_DATASET_REPOSITORY,
        "model_dataset_revision": TRAINING_DATASET_REVISION,
        "model_training_examples": 50_000,
        "model_checkpoint_sha256": ARTIFACT_MANIFEST_SHA256,
        "model_embedding_space": _accepted_embedding_space(),
        "model_immutable": True,
        "state_row_count": 1,
        "state_active_model_id": MODEL_ID,
        "state_active_model_version": 0,
        "state_embedding_space_id": EMBEDDING_SPACE_ID,
        "state_pgvector_snapshot_sha256": expected_snapshot_sha256,
        "state_backend_snapshot_sha256": expected_snapshot_sha256,
        "catalog_count": EXPECTED_COUNT,
        "catalog_unique_babel_count": EXPECTED_COUNT,
        "catalog_valid_count": EXPECTED_COUNT,
        "embedding_count": EXPECTED_COUNT,
        "embedding_unique_babel_count": EXPECTED_COUNT,
        "embedding_exact_active_count": EXPECTED_COUNT,
        "embedding_valid_vector_count": EXPECTED_COUNT,
        "embedding_catalog_match_count": EXPECTED_COUNT,
        "hnsw_index_count": 1,
        "hnsw_index_method": "hnsw",
        "hnsw_index_valid": True,
        "hnsw_index_ready": True,
        "hnsw_plan_uses_named_index": True,
    }


def _validate_metadata(
    metadata: Mapping[str, object],
    *,
    trial_id: UUID,
    run_id: UUID,
    expected_vector_sha256: str,
    expected_snapshot_sha256: str,
) -> None:
    expected = _expected_metadata(
        trial_id=trial_id,
        run_id=run_id,
        expected_vector_sha256=expected_vector_sha256,
        expected_snapshot_sha256=expected_snapshot_sha256,
    )
    for field, value in expected.items():
        _expect(metadata, field, value)
    _require_sha(
        _value(metadata, "trial_population_manifest_sha256"),
        "trial_population_manifest_sha256",
    )


def _decode_vector(value: object, row_number: int) -> bytes:
    try:
        wire = bytes(value)
    except (TypeError, ValueError) as error:
        raise PredeployValidationError(
            f"vector row {row_number} has no binary payload"
        ) from error
    if len(wire) != 404:
        raise PredeployValidationError(f"vector row {row_number} is not 100d")
    dimension, unused = struct.unpack(">hh", wire[:4])
    if dimension != EXPECTED_DIMENSION or unused != 0:
        raise PredeployValidationError(f"vector row {row_number} has invalid header")
    values = struct.unpack(">100f", wire[4:])
    if any(not math.isfinite(value) for value in values):
        raise PredeployValidationError(f"vector row {row_number} is not finite")
    return b"".join(
        wire[offset : offset + 4][::-1] for offset in range(4, len(wire), 4)
    )


def _vector_literal(value: object) -> str:
    """Produce a finite 100d vector parameter for the read-only ANN plan probe."""
    try:
        wire = bytes(value)
    except (TypeError, ValueError) as error:
        raise PredeployValidationError("HNSW probe vector has no binary payload") from error
    if len(wire) != 404 or struct.unpack(">hh", wire[:4]) != (100, 0):
        raise PredeployValidationError("HNSW probe vector is not 100d")
    values = struct.unpack(">100f", wire[4:])
    if any(not math.isfinite(value) for value in values):
        raise PredeployValidationError("HNSW probe vector is not finite")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _vector_evidence(
    rows: Sequence[tuple[object, ...]],
) -> tuple[str, str, UUID]:
    if len(rows) != EXPECTED_COUNT:
        raise PredeployValidationError("active vector row count is not exactly 10000")
    vector_digest = hashlib.sha256()
    snapshot_digest = hashlib.sha256()
    previous_babel_id = ""
    creators: set[UUID] = set()
    for row_number, row in enumerate(rows, 1):
        if len(row) != 9:
            raise PredeployValidationError(f"vector row {row_number} has invalid shape")
        babel_id = _uuid(row[0], f"vector row {row_number} babel_id")
        catalog_creator = _uuid(row[1], f"vector row {row_number} catalog creator")
        embedding_creator = _uuid(row[2], f"vector row {row_number} embedding creator")
        embedding_space = _uuid(row[3], f"vector row {row_number} embedding space")
        serving_model = _uuid(row[4], f"vector row {row_number} serving model")
        babel_text = str(babel_id)
        if babel_text <= previous_babel_id:
            raise PredeployValidationError(
                "active Babel IDs are duplicate or not strictly ordered"
            )
        previous_babel_id = babel_text
        if catalog_creator != embedding_creator:
            raise PredeployValidationError(
                f"vector row {row_number} creator binding differs"
            )
        if embedding_space != EMBEDDING_SPACE_ID:
            raise PredeployValidationError(
                f"vector row {row_number} embedding space differs"
            )
        if serving_model != MODEL_ID or row[7] != 0:
            raise PredeployValidationError(
                f"vector row {row_number} active model identity differs"
            )
        content_hash = _require_sha(
            row[5], f"vector row {row_number} catalog content hash"
        )
        source_article_key = str(row[6])
        if ARTICLE_KEY.fullmatch(source_article_key) is None:
            raise PredeployValidationError(
                f"vector row {row_number} source article key differs"
            )
        f32le = _decode_vector(row[8], row_number)
        vector_digest.update(f32le)
        snapshot = {
            "babelId": babel_text,
            "catalogContentHash": content_hash,
            "creatorId": str(catalog_creator),
            "embeddingSpaceId": str(embedding_space),
            "materializedModelVersion": 0,
            "servingModelId": str(serving_model),
            "sourceArticleKey": source_article_key,
            "vectorSha256": hashlib.sha256(f32le).hexdigest(),
        }
        snapshot_digest.update(canonical_json(snapshot))
        creators.add(catalog_creator)
    if not creators:
        raise PredeployValidationError("active population has no creator")
    return vector_digest.hexdigest(), snapshot_digest.hexdigest(), min(creators)


def validate_snapshot(
    metadata: Mapping[str, object],
    vector_rows: Sequence[tuple[object, ...]],
    *,
    trial_id: UUID,
    run_id: UUID,
    expected_vector_sha256: str,
    expected_snapshot_sha256: str,
) -> dict[str, object]:
    """Validate all stored identities and independently recompute population hashes."""
    _validate_ids(trial_id, run_id)
    expected_vector_sha256 = _require_sha(
        expected_vector_sha256, "expected ordered vector SHA-256"
    )
    expected_snapshot_sha256 = _require_sha(
        expected_snapshot_sha256, "expected snapshot SHA-256"
    )
    _validate_metadata(
        metadata,
        trial_id=trial_id,
        run_id=run_id,
        expected_vector_sha256=expected_vector_sha256,
        expected_snapshot_sha256=expected_snapshot_sha256,
    )
    vector_sha256, snapshot_sha256, sample_creator_id = _vector_evidence(vector_rows)
    if vector_sha256 != expected_vector_sha256:
        raise PredeployValidationError("recomputed ordered vector SHA-256 differs")
    if snapshot_sha256 != expected_snapshot_sha256:
        raise PredeployValidationError("recomputed pgvector snapshot SHA-256 differs")
    return {
        "schemaVersion": 1,
        "verified": True,
        "trialId": str(trial_id),
        "runId": str(run_id),
        "sampleCreatorId": str(sample_creator_id),
        "catalogCount": EXPECTED_COUNT,
        "activeEmbeddingCount": EXPECTED_COUNT,
        "embeddingDimension": EXPECTED_DIMENSION,
        "modelId": str(MODEL_ID),
        "modelRepository": MODEL_REPOSITORY,
        "modelRevision": MODEL_REVISION,
        "datasetRepository": DATASET_REPOSITORY,
        "datasetConfig": DATASET_CONFIG,
        "datasetRevision": DATASET_REVISION,
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "materializedModelVersion": 0,
        "orderedVectorSha256": vector_sha256,
        "pgvectorSnapshotSha256": snapshot_sha256,
        "hnswIndexName": HNSW_INDEX_NAME,
        "hnswIndexValid": True,
        "hnswIndexReady": True,
    }


def _load_ready_import_receipt(path: Path) -> tuple[dict[str, object], str]:
    if path.is_symlink():
        raise PredeployValidationError("import receipt must not be a symlink")
    try:
        details = path.stat()
        payload = path.read_bytes()
        document = json.loads(payload)
    except (OSError, json.JSONDecodeError) as error:
        raise PredeployValidationError("ready import receipt is unavailable") from error
    if not stat.S_ISREG(details.st_mode) or not isinstance(document, dict):
        raise PredeployValidationError("ready import receipt is not a regular JSON object")
    if canonical_json(document) != payload:
        raise PredeployValidationError("ready import receipt is not canonical JSON")
    expected_keys = {
        "schemaVersion",
        "state",
        "importAttemptId",
        "originTrialId",
        "originRunId",
        "freshTrialId",
        "freshPopulationRunId",
        "rowCount",
        "sampleCount",
        "orderedVectorSha256",
        "snapshotSha256",
        "hnswIndex",
        "bundleDigest",
        "frozenManifestSha256",
        "modelCheckpointRoot",
        "modelArtifactManifestPath",
    }
    if set(document) != expected_keys:
        raise PredeployValidationError("ready import receipt schema differs")
    return document, hashlib.sha256(payload).hexdigest()


def validate_reuse_snapshot(
    metadata: Mapping[str, object],
    *,
    import_receipt_path: Path,
    trial_id: UUID,
    run_id: UUID,
    expected_vector_sha256: str,
    expected_snapshot_sha256: str,
) -> dict[str, object]:
    """Validate an accepted import without reading or rehashing vector payloads."""
    _validate_ids(trial_id, run_id)
    expected_vector_sha256 = _require_sha(
        expected_vector_sha256, "expected ordered vector SHA-256"
    )
    expected_snapshot_sha256 = _require_sha(
        expected_snapshot_sha256, "expected snapshot SHA-256"
    )
    expected = _expected_metadata(
        trial_id=trial_id,
        run_id=run_id,
        expected_vector_sha256=expected_vector_sha256,
        expected_snapshot_sha256=expected_snapshot_sha256,
    )
    full_scan_only = {
        "catalog_valid_count",
        "embedding_exact_active_count",
        "embedding_valid_vector_count",
        "embedding_catalog_match_count",
        "hnsw_plan_uses_named_index",
    }
    reuse_expected = {
        field: value
        for field, value in expected.items()
        if field not in full_scan_only
    }
    missing = set(reuse_expected) - set(metadata)
    if missing:
        raise PredeployValidationError(
            f"reuse metadata is incomplete: {sorted(missing)[0]}"
        )
    for field, value in reuse_expected.items():
        _expect(metadata, field, value)
    sample_creator_id = _uuid(
        _value(metadata, "sample_creator_id"), "sample_creator_id"
    )
    manifest_sha = _require_sha(
        _value(metadata, "trial_population_manifest_sha256"),
        "trial_population_manifest_sha256",
    )
    receipt, receipt_sha = _load_ready_import_receipt(import_receipt_path)
    receipt_expected = {
        "schemaVersion": 1,
        "state": "ready",
        "originTrialId": str(ORIGIN_TRIAL_ID),
        "originRunId": str(ORIGIN_RUN_ID),
        "freshTrialId": str(trial_id),
        "freshPopulationRunId": str(run_id),
        "rowCount": EXPECTED_COUNT,
        "sampleCount": 100,
        "orderedVectorSha256": expected_vector_sha256,
        "snapshotSha256": expected_snapshot_sha256,
        "hnswIndex": HNSW_INDEX_NAME,
        "frozenManifestSha256": manifest_sha,
    }
    for field, value in receipt_expected.items():
        if receipt.get(field) != value:
            raise PredeployValidationError(f"import receipt {field} differs")
    for field in ("importAttemptId", "bundleDigest"):
        value = receipt[field]
        if field == "importAttemptId":
            _uuid(value, "import receipt importAttemptId")
        else:
            _require_sha(value, "import receipt bundleDigest")
    return {
        "schemaVersion": 1,
        "verified": True,
        "validationMode": "reuse_without_vector_rehash",
        "trialId": str(trial_id),
        "runId": str(run_id),
        "sampleCreatorId": str(sample_creator_id),
        "catalogCount": EXPECTED_COUNT,
        "activeEmbeddingCount": EXPECTED_COUNT,
        "embeddingDimension": EXPECTED_DIMENSION,
        "modelId": str(MODEL_ID),
        "modelRepository": MODEL_REPOSITORY,
        "modelRevision": MODEL_REVISION,
        "datasetRepository": DATASET_REPOSITORY,
        "datasetConfig": DATASET_CONFIG,
        "datasetRevision": DATASET_REVISION,
        "embeddingSpaceId": str(EMBEDDING_SPACE_ID),
        "materializedModelVersion": 0,
        "orderedVectorSha256": expected_vector_sha256,
        "pgvectorSnapshotSha256": expected_snapshot_sha256,
        "importReceiptSha256": receipt_sha,
        "hnswIndexName": HNSW_INDEX_NAME,
        "hnswIndexValid": True,
        "hnswIndexReady": True,
    }


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise PredeployValidationError(f"{field} must be a UUID") from error
    if str(parsed) != value:
        raise PredeployValidationError(f"{field} must be a canonical lowercase UUID")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="predeploy.py")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--population-vector-sha256", required=True)
    parser.add_argument("--population-snapshot-sha256", required=True)
    parser.add_argument("--reuse-import-receipt", type=Path)
    arguments = parser.parse_args(argv)
    try:
        database_url = os.environ.get("BABEL_DATABASE_URL")
        if not database_url:
            raise PredeployValidationError("BABEL_DATABASE_URL is required")
        trial_id = _parse_uuid(arguments.trial_id, "trial ID")
        run_id = _parse_uuid(arguments.run_id, "run ID")
        if arguments.reuse_import_receipt is not None:
            metadata = read_database_reuse_snapshot(
                database_url, trial_id=trial_id, run_id=run_id
            )
            evidence = validate_reuse_snapshot(
                metadata,
                import_receipt_path=arguments.reuse_import_receipt,
                trial_id=trial_id,
                run_id=run_id,
                expected_vector_sha256=arguments.population_vector_sha256,
                expected_snapshot_sha256=arguments.population_snapshot_sha256,
            )
        else:
            metadata, vectors = read_database_snapshot(
                database_url, trial_id=trial_id, run_id=run_id
            )
            evidence = validate_snapshot(
                metadata,
                vectors,
                trial_id=trial_id,
                run_id=run_id,
                expected_vector_sha256=arguments.population_vector_sha256,
                expected_snapshot_sha256=arguments.population_snapshot_sha256,
            )
    except PredeployValidationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # Database exceptions can include connection context; never echo a DSN.
        print("predeploy database verification failed", file=sys.stderr)
        return 1
    sys.stdout.write(canonical_json(evidence).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
