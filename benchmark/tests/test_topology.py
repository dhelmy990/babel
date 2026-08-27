from __future__ import annotations

from pathlib import Path

from babel_benchmark.contracts import (
    BenchmarkManifestV1,
    BenchmarkManifestV2,
    ConditionIdentityV2,
)
from babel_benchmark.topology import infer_v1_condition_identity


ROOT = Path(__file__).parents[2]


def test_condition_identity_generalizes_topology_and_keeps_v1_readers() -> None:
    identity = ConditionIdentityV2(
        topology="same_host_split",
        trainingEnabled=True,
        activationEnabled=False,
        retrievalBackend="pgvector",
    )
    assert identity.stable_key == "same_host_split.training.no_activation.pgvector"

    legacy = BenchmarkManifestV1.model_validate_json(
        (ROOT / "fixtures/performance/manifest.json").read_text()
    )
    inferred = [infer_v1_condition_identity(row) for row in legacy.conditions]
    assert [row.stable_key for row in inferred] == [
        "same_process.serving.no_activation.pgvector",
        "same_process.training.no_activation.pgvector",
        "same_process.training.activation.pgvector",
    ]


def test_retrieval_backend_is_not_a_topology_dimension() -> None:
    pgvector = ConditionIdentityV2(
        topology="same_host_isolated",
        trainingEnabled=False,
        activationEnabled=False,
        retrievalBackend="pgvector",
    )
    hnswlib = pgvector.model_copy(update={"retrievalBackend": "hnswlib"})
    assert pgvector.topology == hnswlib.topology
    assert pgvector.stable_key != hnswlib.stable_key


def test_v2_manifest_persists_generalized_condition_and_bounded_schedule() -> None:
    legacy = BenchmarkManifestV1.model_validate_json(
        (ROOT / "fixtures/performance/manifest.json").read_text()
    )
    source = legacy.model_dump(mode="json")
    source["schemaVersion"] = 2
    source["scheduleMode"] = "open_loop"
    source["maxInFlight"] = 8
    source["conditions"] = [
        {
            "identity": {
                "topology": "same_host_split",
                "trainingEnabled": False,
                "activationEnabled": False,
                "retrievalBackend": "pgvector",
            },
            "requestCorpusSha256": legacy.requestCorpusSha256,
            "scheduleOffsetsNs": list(legacy.scheduleOffsetsNs),
            "expectedModelId": str(legacy.conditions[0].expectedModelId),
            "expectedEmbeddingSpaceId": str(
                legacy.conditions[0].expectedEmbeddingSpaceId
            ),
            "expectedDatasetSnapshotSha256": legacy.candidateUniverseSha256,
            "expectedPgvectorSnapshotSha256": "a" * 64,
            "expectedBackendSnapshotSha256": "a" * 64,
        }
    ]
    manifest = BenchmarkManifestV2.model_validate(source)
    assert manifest.maxInFlight == 8
    assert manifest.conditions[0].name == (
        "same_host_split.serving.no_activation.pgvector"
    )
