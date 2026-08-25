from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from babel_online.runtime.control import create_control_app


RUN = UUID("00000000-0000-5000-8000-000000000001")


class Manager:
    def __init__(self) -> None:
        self.started = []
        self.stopped = []

    def start(self, run_id):
        self.started.append(run_id)

    def request_stop(self, run_id):
        self.stopped.append(run_id)


def test_control_api_is_authenticated_and_only_accepts_persisted_run_identity() -> None:
    manager = Manager()
    client = TestClient(create_control_app(manager, token="a" * 64))

    assert client.post(f"/v1/runs/{RUN}/start").status_code == 403
    started = client.post(
        f"/v1/runs/{RUN}/start", headers={"X-Babel-Worker-Token": "a" * 64}
    )
    stopped = client.post(
        f"/v1/runs/{RUN}/graceful-stop",
        headers={"X-Babel-Worker-Token": "a" * 64},
    )

    assert started.status_code == stopped.status_code == 202
    assert manager.started == [RUN]
    assert manager.stopped == [RUN]


def test_control_api_rejects_non_hex_token_configuration() -> None:
    try:
        create_control_app(Manager(), token="secret")
    except ValueError as error:
        assert "64 lowercase hex" in str(error)
    else:
        raise AssertionError("invalid worker token was accepted")
