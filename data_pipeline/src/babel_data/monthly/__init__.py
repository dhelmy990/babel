"""Deterministic builders for the representative monthly demo fixture."""

from .catalog import (
    HIDDEN_ARTICLE_FIELDS,
    RELEASE_SCOPE,
    SOURCE_SNAPSHOT,
    build_period_articles,
    canonical_jsonl,
    content_sha256,
)
from .crosswalk import build_crosswalk_expectations
from .hidden import build_archetypes, build_clickstream, build_graph, build_seed_catalog
from .profiles import (
    MultistreamRange,
    ProfileAssignment,
    build_profile_catalog,
    extract_profile_manifest_assignments,
    normalize_backend_title,
    plan_multistream_ranges,
)
from .build import HiddenRelations, build_hidden_relations, build_observable_catalog
from .selection import (
    CandidateIdentity,
    EngineeringSnapshotPolicyV1,
    JointSelection,
    freeze_joint_selection,
)
from .sources import MonthlySourcePin, SourcePolicyError, assert_semantic_read_allowed

__all__ = [
    "HIDDEN_ARTICLE_FIELDS",
    "CandidateIdentity",
    "EngineeringSnapshotPolicyV1",
    "HiddenRelations",
    "JointSelection",
    "MonthlySourcePin",
    "MultistreamRange",
    "ProfileAssignment",
    "RELEASE_SCOPE",
    "SOURCE_SNAPSHOT",
    "build_archetypes",
    "build_clickstream",
    "build_crosswalk_expectations",
    "build_graph",
    "build_hidden_relations",
    "build_observable_catalog",
    "build_period_articles",
    "build_profile_catalog",
    "build_seed_catalog",
    "canonical_jsonl",
    "content_sha256",
    "extract_profile_manifest_assignments",
    "normalize_backend_title",
    "plan_multistream_ranges",
    "SourcePolicyError",
    "assert_semantic_read_allowed",
    "freeze_joint_selection",
]
