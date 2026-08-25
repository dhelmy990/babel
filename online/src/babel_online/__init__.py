"""Observable online recommendation contracts and serving runtime."""

from .config import EnvironmentPeriod, RetrievalBackend, RunStatus, default_run_config
from .contracts import (
    ActivityLogV1,
    CandidateActionV1,
    EmbeddingSpaceV1,
    FeedbackEventV1,
    HnswSnapshotV1,
    ModelManifestV1,
    RecommendationCandidateV1,
    RecommendationRequestV1,
    RecommendationResponseV1,
    RunConfigV1,
    validate_contract,
)

__version__ = "0.1.0"

__all__ = [
    "ActivityLogV1",
    "CandidateActionV1",
    "EmbeddingSpaceV1",
    "EnvironmentPeriod",
    "FeedbackEventV1",
    "HnswSnapshotV1",
    "ModelManifestV1",
    "RecommendationCandidateV1",
    "RecommendationRequestV1",
    "RecommendationResponseV1",
    "RetrievalBackend",
    "RunConfigV1",
    "RunStatus",
    "default_run_config",
    "validate_contract",
]
