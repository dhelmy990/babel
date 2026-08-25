"""Hidden graph, behavior, archetype, and backend seed builders."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence


_UUID_NAMESPACE = uuid.UUID("6db43f2d-a1dc-5d73-9aeb-9b9d6d79d72b")
_WEIGHTS = (0.4, 0.3, 0.2, 0.1)
_PROFILE_ROWS = (
    ("distributed-systems", "Distributed Systems Creator", ("Distributed computing", "Consensus (computer science)", "Operating system", "Database")),
    ("machine-learning-systems", "Machine Learning Systems Creator", ("Machine learning", "Recommender system", "Graphics processing unit", "Artificial neural network")),
    ("programming-languages", "Programming Languages Creator", ("Programming language", "Compiler", "Type system", "Functional programming")),
    ("cybersecurity-networks", "Cybersecurity and Networks Creator", ("Computer security", "Cryptography", "Computer network", "Malware")),
    ("cpu-performance", "Low-Latency CPU and Performance Creator", ("Central processing unit", "CPU cache", "Branch predictor", "Instruction pipelining")),
    ("digital-art", "Digital Art Creator", ("Digital art", "Computer graphics", "Generative art", "Animation")),
    ("classical-visual-arts", "Classical Visual Arts Creator", ("Painting", "Renaissance art", "Sculpture", "Art history")),
    ("film-cinema", "Film and Cinema Creator", ("Film", "Cinematography", "Film editing", "Screenwriting")),
    ("literature-poetry", "Literature and Poetry Creator", ("Literature", "Novel", "Poetry", "Literary criticism")),
    ("theatre-performance", "Theatre and Performance Creator", ("Theatre", "Acting", "Stagecraft", "Play (theatre)")),
    ("music-composition", "Music and Composition Creator", ("Music", "Music theory", "Musical composition", "Electronic music")),
    ("photography-design", "Photography and Graphic Design Creator", ("Photography", "Graphic design", "Typography", "Visual arts")),
    ("computational-neuroscience", "Computational Neuroscience Creator", ("Computational neuroscience", "Neural coding", "Artificial neural network", "Visual perception")),
    ("cognitive-neuroscience", "Cognitive Neuroscience Creator", ("Cognitive neuroscience", "Memory", "Attention", "Functional magnetic resonance imaging")),
    ("quantitative-finance", "Quantitative Finance Creator", ("Algorithmic trading", "Financial market", "Derivative (finance)", "Portfolio (finance)")),
    ("macroeconomics-markets", "Macroeconomics and Markets Creator", ("Monetary policy", "Inflation", "Interest rate", "Central bank")),
    ("corporate-finance", "Corporate Finance and Valuation Creator", ("Corporate finance", "Valuation (finance)", "Financial statement", "Stock")),
    ("public-policy", "Public Policy and Institutions Creator", ("Public policy", "Constitution", "Governance", "Regulation")),
    ("international-relations", "International Relations Creator", ("International relations", "Diplomacy", "Geopolitics", "International trade")),
    ("political-economy", "Political Economy Creator", ("Political economy", "Economic inequality", "Tax", "Regulation")),
)


def _uuid_v5(name: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, name))


def build_graph(articles: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Build a directed ring plus fixed jump without duplicates or self-loops."""
    ordered = sorted(articles, key=lambda row: str(row["article_key"]))
    if len(ordered) < 8:
        raise ValueError("at least eight articles are required for the demo graph")
    period = str(ordered[0]["period"])
    if any(row.get("period") != period for row in ordered):
        raise ValueError("graph articles must belong to one period")
    keys = [str(row["article_key"]) for row in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("graph article keys must be unique")
    pairs = {
        (key, keys[(index + offset) % len(keys)])
        for index, key in enumerate(keys)
        for offset in (1, 7)
    }
    return [
        {
            "period": period,
            "source_article_key": source,
            "target_article_key": target,
        }
        for source, target in sorted(pairs)
        if source != target
    ]


def build_clickstream(
    edges: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Attach deterministic link counts and documented log normalization."""
    ordered = sorted(
        edges,
        key=lambda row: (str(row["source_article_key"]), str(row["target_article_key"])),
    )
    if not ordered:
        return []
    counts = [10 + index % 91 for index in range(len(ordered))]
    scale = max(math.log1p(count) for count in counts)
    return [
        {
            "period": str(edge["period"]),
            "source_article_key": str(edge["source_article_key"]),
            "target_article_key": str(edge["target_article_key"]),
            "type": "link",
            "n": count,
            "normalized_weight": math.log1p(count) / scale,
        }
        for edge, count in zip(ordered, counts, strict=True)
    ]


def build_archetypes(
    articles: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Resolve the authoritative 20-by-4 roster by deterministic fixture order."""
    ordered = sorted(articles, key=lambda row: int(row["page_id"]))
    if len(ordered) != 80:
        raise ValueError("the archetype fixture requires exactly 80 articles")
    archetypes: list[dict[str, object]] = []
    for profile_index, (slug, display_name, titles) in enumerate(_PROFILE_ROWS):
        creator_id = _uuid_v5(f"creator:{slug}")
        seeds = []
        for seed_index, (title, weight) in enumerate(zip(titles, _WEIGHTS, strict=True)):
            article = ordered[profile_index * 4 + seed_index]
            seeds.append(
                {
                    "assignment_id": _uuid_v5(f"seed:{slug}:{title}"),
                    "declared_title": title,
                    "article_key": article["article_key"],
                    "page_id": article["page_id"],
                    "canonical_title": article["canonical_title"],
                    "weight": weight,
                }
            )
        archetypes.append(
            {
                "creator_id": creator_id,
                "archetype_slug": slug,
                "display_name": display_name,
                "seeds": seeds,
            }
        )
    return archetypes


def build_seed_catalog(
    archetypes: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Flatten hidden archetypes into the backend's deterministic seed catalog."""
    result: list[dict[str, object]] = []
    for archetype in archetypes:
        seeds = archetype.get("seeds")
        if not isinstance(seeds, list):
            raise ValueError("archetype seeds must be an array")
        for seed in seeds:
            if not isinstance(seed, Mapping):
                raise ValueError("archetype seed must be an object")
            result.append(
                {
                    "assignment_id": seed["assignment_id"],
                    "creator_id": archetype["creator_id"],
                    "creator_slug": archetype["archetype_slug"],
                    "display_name": archetype["display_name"],
                    "declared_title": seed["declared_title"],
                    "article_key": seed["article_key"],
                    "page_id": seed["page_id"],
                    "canonical_title": seed["canonical_title"],
                    "weight": seed["weight"],
                }
            )
    return result
