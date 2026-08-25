"""Deterministic hidden simulation producing observable feedback."""

from .client import RecommendationClient
from .decisions import ActionProbabilities, action_probabilities, decide_candidate
from .engine import SimulationArticle, SimulationEngine, SimulationStepResult
from .sampling import EligibleSupportExhausted, SourceSampler

__all__ = [
    "ActionProbabilities",
    "EligibleSupportExhausted",
    "RecommendationClient",
    "SimulationArticle",
    "SimulationEngine",
    "SimulationStepResult",
    "SourceSampler",
    "action_probabilities",
    "decide_candidate",
]
