"""Tunnel bindings and real proxy behavior; no live Cloudflare credentials."""
import json
import re
import time
from uuid import uuid4

import httpx
import pytest

from tests.test_deployment import IMAGE, ROOT, deployment, run
from tests.test_production_settings import production_env


def test_tunnel_compose_binds_only_loopback():
    environment = {
        **production_env(), "POSTGRES_DB": "study", "POSTGRES_USER": "study",
        "POSTGRES_PASSWORD": "disposable", "STUDY_RUNTIME_ENV_FILE": "/dev/null",
        "STUDY_PROXY_BIND": "127.0.0.1", "STUDY_PROXY_HTTP_PORT": "8080",
        "STUDY_PROXY_HTTPS_PORT": "8443", "STUDY_CADDYFILE": "./deploy/Caddyfile.tunnel",
    }
    config = json.loads(run("docker", "compose", "--env-file", "/dev/null", "-f",
                            "compose.prod.yaml", "config", "--format", "json", env=environment).stdout)
    proxy = config["services"]["proxy"]
    assert {(port.get("host_ip"), str(port["published"])) for port in proxy["ports"]} == {
        ("127.0.0.1", "8080"), ("127.0.0.1", "8443"),
    }
    assert any(mount["source"].endswith("/deploy/Caddyfile.tunnel") for mount in proxy["volumes"])
    assert not config["services"]["db"].get("ports")
    assert not config["services"]["web"].get("ports")


@pytest.mark.skipif(not IMAGE, reason="Set STUDY_DEPLOYMENT_IMAGE for real tunnel proxy integration")
def test_tunnel_preserves_https_identity_and_redirects_insecure_requests(deployment):
    _, _, _, _, _, project, *_ = deployment
    caddy_image = re.search(r"image: (caddy:\S+)", (ROOT / "compose.prod.yaml").read_text())[1]
    container = None
    try:
        container = run("docker", "create", "--name", "study-tunnel-" + uuid4().hex,
                        "--network", project + "_default", "-p", "127.0.0.1::80", caddy_image).stdout.strip()
        run("docker", "cp", str(ROOT / "deploy/Caddyfile.tunnel"), container + ":/etc/caddy/Caddyfile")
        run("docker", "start", container)
        origin = "http://" + run("docker", "port", container, "80").stdout.strip()
        with httpx.Client(base_url=origin, follow_redirects=False, timeout=3, trust_env=False) as client:
            secure = {"Host": "dhelmy.stream", "X-Forwarded-Proto": "https"}
            for _ in range(50):
                try:
                    response = client.get("/healthz", headers=secure)
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Tunnel proxy did not serve the app over forwarded HTTPS")
            assert response.json() == {"status": "ok"}
            for scheme in (None, "http", "https,http"):
                headers = {"Host": "dhelmy.stream"}
                if scheme is not None:
                    headers["X-Forwarded-Proto"] = scheme
                response = client.get("/healthz?probe=1", headers=headers)
                assert response.status_code in (301, 308)
                assert response.headers["location"] == "https://dhelmy.stream/healthz?probe=1"
            assert client.get("/healthz", headers={**secure, "Host": "evil.invalid"}).status_code == 404
            assert client.get("/.env", headers=secure).status_code == 404
            response = client.get("/api/session", headers=secure)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            response = client.get("/", headers=secure)
            assert response.status_code == 200
            cookie = response.headers.get("set-cookie", "")
            assert "csrftoken=" in cookie and "secure" in cookie.lower()
    finally:
        if container:
            run("docker", "rm", "--force", "--volumes", container)
