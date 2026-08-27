"""Synchronous loopback recommendation service."""

from .app import SERVING_HOST, create_app
from .state import ServingSnapshot, ServingState

__all__ = ["SERVING_HOST", "ServingSnapshot", "ServingState", "create_app"]
