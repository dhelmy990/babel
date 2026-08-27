"""Authenticated loopback control plane called only by the C++ dashboard backend."""

from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Response, status


def _valid_token(token: str) -> bool:
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def create_control_app(manager: Any, *, token: str) -> FastAPI:
    if not _valid_token(token):
        raise ValueError("worker token must contain exactly 64 lowercase hex digits")
    app = FastAPI(title="Babel online worker control", version="1")

    def authorize(presented: str | None) -> None:
        if presented is None or not hmac.compare_digest(token, presented):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/runs/{run_id}/start", status_code=status.HTTP_202_ACCEPTED)
    def start(
        run_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        try:
            manager.start(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.post(
        "/v1/runs/{run_id}/graceful-stop", status_code=status.HTTP_202_ACCEPTED
    )
    def graceful_stop(
        run_id: UUID,
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        try:
            manager.request_stop(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.get("/v1/topology")
    def topology(
        x_babel_worker_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_babel_worker_token)
        try:
            placement = getattr(manager, "placement", None)
        except RuntimeError as error:
            raise HTTPException(status_code=404, detail="placement is not available") from error
        if placement is None:
            raise HTTPException(status_code=404, detail="placement is not available")
        return placement.as_document()

    @app.get("/v1/topology/status")
    def topology_status(
        x_babel_worker_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(x_babel_worker_token)
        runtime_status = getattr(manager, "status", None)
        if runtime_status is None:
            raise HTTPException(status_code=404, detail="topology status is unavailable")
        return dict(runtime_status)

    @app.post(
        "/v1/topology/trainer/stop", status_code=status.HTTP_202_ACCEPTED
    )
    def stop_trainer(
        x_babel_worker_token: str | None = Header(default=None),
    ) -> Response:
        authorize(x_babel_worker_token)
        stop = getattr(manager, "kill_trainer", None)
        if stop is None:
            raise HTTPException(status_code=409, detail="trainer is not independently placed")
        try:
            stop()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=status.HTTP_202_ACCEPTED)

    return app


__all__ = ["create_control_app"]
