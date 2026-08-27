"""Deterministic bounded breadth-first recommendation walks."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class WalkNode:
    babel_id: UUID
    source_article_key: str


@dataclass(frozen=True, slots=True)
class IncludedWalkTarget:
    node: WalkNode
    rank: int

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise ValueError("included target rank must be positive")


@dataclass(frozen=True, slots=True)
class WalkExpansion:
    request_id: UUID
    included: tuple[IncludedWalkTarget, ...]


@dataclass(frozen=True, slots=True)
class WalkRequest:
    source: WalkNode
    request_id: UUID
    parent_request_id: UUID | None
    source_depth: int


@dataclass(frozen=True, slots=True)
class WalkEdge:
    source_babel_id: UUID
    target_babel_id: UUID
    request_id: UUID
    target_depth: int


@dataclass(frozen=True, slots=True)
class WalkTrace:
    started: bool
    requests: tuple[WalkRequest, ...]
    edges: tuple[WalkEdge, ...]

    @property
    def request_count(self) -> int:
        return len(self.requests)


WalkDraw = Callable[..., float]
RecommendNode = Callable[[WalkNode, UUID, UUID | None, int], WalkExpansion]


class RecommendationWalk:
    """Execute one bounded traversal while leaving decisions to the simulator."""

    def __init__(
        self,
        *,
        start_probability: float = 0.4,
        continuation_probability: float = 0.4,
        max_depth: int = 2,
        max_requests: int = 10,
        draw: WalkDraw,
    ) -> None:
        if not 0.0 <= start_probability <= 1.0:
            raise ValueError("start probability must be between zero and one")
        if not 0.0 <= continuation_probability <= 1.0:
            raise ValueError("continuation probability must be between zero and one")
        if max_depth != 2:
            raise ValueError("the scaled experiment fixes traversal depth at two")
        if not 1 <= max_requests <= 10:
            raise ValueError("walk request cap must be between one and ten")
        self.start_probability = start_probability
        self.continuation_probability = continuation_probability
        self.max_depth = max_depth
        self.max_requests = max_requests
        self.draw = draw

    def run(
        self,
        *,
        run_id: UUID,
        creator_id: UUID,
        session_id: UUID,
        root: WalkNode,
        recommend: RecommendNode,
    ) -> WalkTrace:
        start = self.draw(
            "start", run_id, creator_id, session_id, root.babel_id, None, 0
        )
        if not 0.0 <= start < 1.0:
            raise ValueError("walk draw must be in [0, 1)")
        if start >= self.start_probability:
            return WalkTrace(started=False, requests=(), edges=())

        pending = deque([(root, 0, None)])
        visited = {root.babel_id}
        requests: list[WalkRequest] = []
        edges: list[WalkEdge] = []
        edge_keys: set[tuple[UUID, UUID]] = set()
        while pending and len(requests) < self.max_requests:
            source, source_depth, parent_request_id = pending.popleft()
            if source_depth not in {0, 1}:
                raise RuntimeError("recommendation requests may only use depths zero and one")
            expansion = recommend(
                source, session_id, parent_request_id, source_depth
            )
            requests.append(
                WalkRequest(
                    source=source,
                    request_id=expansion.request_id,
                    parent_request_id=parent_request_id,
                    source_depth=source_depth,
                )
            )
            target_depth = source_depth + 1
            for included in expansion.included:
                target = included.node
                edge_key = (source.babel_id, target.babel_id)
                if source.babel_id != target.babel_id and edge_key not in edge_keys:
                    edge_keys.add(edge_key)
                    edges.append(
                        WalkEdge(
                            source_babel_id=source.babel_id,
                            target_babel_id=target.babel_id,
                            request_id=expansion.request_id,
                            target_depth=target_depth,
                        )
                    )
                continuation = self.draw(
                    "continuation",
                    run_id,
                    creator_id,
                    session_id,
                    source.babel_id,
                    target.babel_id,
                    included.rank,
                )
                if not 0.0 <= continuation < 1.0:
                    raise ValueError("walk draw must be in [0, 1)")
                if (
                    continuation < self.continuation_probability
                    and target_depth < self.max_depth
                    and target.babel_id not in visited
                ):
                    visited.add(target.babel_id)
                    pending.append((target, target_depth, expansion.request_id))
        return WalkTrace(started=True, requests=tuple(requests), edges=tuple(edges))


__all__ = [
    "IncludedWalkTarget",
    "RecommendationWalk",
    "WalkEdge",
    "WalkExpansion",
    "WalkNode",
    "WalkRequest",
    "WalkTrace",
]
