"""Synchronous loopback recommendation client."""

from __future__ import annotations

from typing import Any


class RecommendationClient:
    def __init__(
        self,
        endpoint: str,
        *,
        http_client: Any | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not endpoint.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("recommendation endpoint must be loopback")
        self.endpoint = endpoint.rstrip("/")
        self._client = http_client
        self.timeout_seconds = timeout_seconds

    def recommend(self, request: Any) -> Any:
        from babel_online.contracts import (
            RecommendationRequestV2,
            RecommendationResponseV1,
            RecommendationResponseV2,
        )

        is_v2 = isinstance(request, RecommendationRequestV2)

        client = self._client
        if client is None:
            import httpx

            client = httpx.Client()
            self._client = client
        response = client.post(
            f"{self.endpoint}/api/v{2 if is_v2 else 1}/recommendations",
            json=request.model_dump(mode="json"),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        response_type = RecommendationResponseV2 if is_v2 else RecommendationResponseV1
        return response_type.model_validate(response.json())

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()


__all__ = ["RecommendationClient"]
