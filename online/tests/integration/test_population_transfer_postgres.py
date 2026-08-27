from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4, uuid5

import numpy as np
import pyarrow.parquet as pq
import pytest

from babel_online.runtime.database import RuntimeDatabase
from babel_online.transfer.database import import_population
from babel_online.transfer.parquet_bundle import (
    vector_f32le,
    verify_bundle,
)


@pytest.mark.pgvector
def test_population_transfer_round_trips_exact_vectors_and_activates_after_hnsw(
    tmp_path: Path,
) -> None:
    database_url = os.environ.get("BABEL_TEST_DATABASE_URL")
    artifact_source = os.environ.get("BABEL_TEST_MODEL_ARTIFACT_MANIFEST")
    bundle_root = os.environ.get("BABEL_TEST_TRANSFER_BUNDLE")
    bundle_digest = os.environ.get("BABEL_TEST_TRANSFER_DIGEST")
    operator_receipt_value = os.environ.get("BABEL_TEST_OPERATOR_RECEIPT")
    if not all(
        (
            database_url,
            artifact_source,
            bundle_root,
            bundle_digest,
            operator_receipt_value,
        )
    ):
        pytest.skip(
            "isolated database, verified bundle, receipt, and model manifest are required"
        )
    artifact_manifest = tmp_path / "artifact_manifest.json"
    shutil.copyfile(Path(artifact_source).resolve(), artifact_manifest)
    checkpoint_root = tmp_path / "checkpoint"
    checkpoint_root.mkdir()
    bundle = verify_bundle(Path(bundle_root), str(bundle_digest))
    operator_receipt = Path(str(operator_receipt_value))
    trial_id = uuid4()
    run_id = uuid5(trial_id, "population")

    receipt = import_population(
        database_url,
        bundle.root,
        bundle.digest,
        operator_receipt,
        trial_id,
        run_id,
        artifact_manifest,
        checkpoint_root,
        tmp_path / "frozen",
        tmp_path / "receipts" / "import.json",
    )

    assert receipt.state == "ready"
    database = RuntimeDatabase(database_url)
    with database._connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pe.population_ready,pe.status,er.status,count(eb.*),
                   min(eb.serving_model_id::text),max(eb.serving_model_id::text)
            FROM performance_experiments pe
            JOIN experiment_runs er ON er.id=pe.run_id
            JOIN babel_embeddings eb ON eb.run_id=er.id
            WHERE pe.id=%s
            GROUP BY pe.population_ready,pe.status,er.status
            """,
            (trial_id,),
        )
        state = cursor.fetchone()
        cursor.execute(
            """
            SELECT public.vector_send(embedding) FROM babel_embeddings
            WHERE run_id=%s ORDER BY babel_id LIMIT 1
            """,
            (run_id,),
        )
        wire = cursor.fetchone()[0]
    assert state == (
        True,
        "population_ready",
        "completed",
        10_000,
        str(bundle.manifest_contract.servingModelId),
        str(bundle.manifest_contract.servingModelId),
    )
    _wire, exact = RuntimeDatabase._decode_vector_send(wire)
    first = pq.read_table(bundle.embeddings).slice(0, 1).to_pylist()[0]
    expected = vector_f32le(np.asarray(first["vector"], dtype="<f4"))
    assert exact == expected
    assert hashlib.sha256(exact).hexdigest() == hashlib.sha256(
        expected
    ).hexdigest()

    resumed = import_population(
        database_url,
        bundle.root,
        bundle.digest,
        operator_receipt,
        trial_id,
        run_id,
        artifact_manifest,
        checkpoint_root,
        tmp_path / "frozen",
        tmp_path / "receipts" / "import.json",
    )
    assert resumed == receipt
