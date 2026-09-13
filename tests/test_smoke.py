"""Read-only smoke checks, including real local TLS with an isolated Caddy CA."""
import os
from pathlib import Path
import re
import socket
import time
from uuid import uuid4

import pytest

from tests.test_deployment import IMAGE, ROOT, deployment, run


def smoke(origin, **overrides):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SMOKE_")}
    return run("bash", "deploy/smoke.sh", origin, env={**environment, **overrides}, check=False)


@pytest.mark.parametrize("origin", ["http://localhost", "https://localhost/path", "https://user@localhost", "https://localhost?x", "https://localhost:0", "https://localhost:65536", "https://-invalid", "https://localhost\n", "https://localhost:", "https://localhost?", "https://localhost#"])
def test_invalid_origin_rejected_before_network(origin):
    result = smoke(origin)
    assert result.returncode == 2
    assert "origin" in result.stderr.lower()


@pytest.fixture(scope="module")
def tls_site(deployment):
    _, _, _, temporary, _, project, *_ = deployment
    name = "study-d2-tls-" + uuid4().hex
    owner = uuid4().hex
    # Reserve two unused loopback ports until Docker is ready to claim them.
    sockets = [socket.socket(), socket.socket()]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    http_port, https_port = [sock.getsockname()[1] for sock in sockets]
    origin = f"https://localhost:{https_port}"
    http_origin = f"http://localhost:{http_port}"
    caddy_image = re.search(r"image: (caddy:\S+)", (ROOT / "compose.prod.yaml").read_text())[1]
    config = temporary / "D2.Caddyfile"
    ca = temporary / "local-root.crt"
    container = None

    def configure(fault=""):
        redirect = "https://example.invalid{uri}" if fault == "redirect" else origin + "{uri}"
        extra = {
            "graph": 'respond /api/graph `{"nodes": broken}` 200',
            "health": 'respond /healthz `broken {"status":"ok"}` 200',
            "missing": 'respond /api/__smoke_missing__ "unexpected existing route" 200',
            "private": 'respond /.env "unexpected private file" 200',
            "status": 'respond /galaxy "unavailable" 503',
            "https_redirect": 'redir /galaxy https://example.invalid 302',
        }.get(fault, "")
        config.write_text(f'''{{
    auto_https disable_redirects
    skip_install_trust
}}
http://localhost:80 {{
    redir {redirect} 308
}}
https://localhost:443 {{
    tls internal
    {extra}
    reverse_proxy web:8000 {{
        header_up Host dhelmy.stream
        header_up X-Forwarded-Proto https
    }}
}}
''')
        if container:
            run("docker", "cp", str(config), container + ":/etc/caddy/Caddyfile")
            run("docker", "exec", container, "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")

    configure()
    try:
        for sock in sockets:
            sock.close()
        container = run("docker", "create", "--name", name, "--label", "study.d2.owner=" + owner,
                        "--network", project + "_default", "-p", f"127.0.0.1:{http_port}:80",
                        "-p", f"127.0.0.1:{https_port}:443", caddy_image).stdout.strip()
        run("docker", "cp", str(config), container + ":/etc/caddy/Caddyfile")
        run("docker", "start", container)
        for _ in range(100):
            copied = run("docker", "cp", container + ":/data/caddy/pki/authorities/local/root.crt", str(ca), check=False)
            if copied.returncode == 0:
                ready = run("curl", "--silent", "--show-error", "--cacert", str(ca), "--max-time", "2", origin + "/healthz", check=False)
                if ready.returncode == 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Disposable Caddy TLS endpoint did not become ready")
        yield origin, {"SMOKE_HTTP_BASE": http_origin, "SMOKE_CURL_CA_BUNDLE": str(ca)}, configure
    finally:
        for sock in sockets:
            sock.close()
        if container:
            assert run("docker", "inspect", "--format", '{{index .Config.Labels "study.d2.owner"}}', container).stdout.strip() == owner
            run("docker", "rm", "--force", "--volumes", container)
        ca.unlink(missing_ok=True)
        config.unlink(missing_ok=True)


@pytest.mark.skipif(not IMAGE, reason="Set STUDY_DEPLOYMENT_IMAGE for real Caddy TLS integration")
def test_real_tls_smoke_success_and_certificate_trust(tls_site):
    origin, options, configure = tls_site
    configure()
    result = smoke(origin, **options)
    assert result.returncode == 0, result.stderr
    assert "Smoke checks passed" in result.stdout
    assert not result.stderr, "Expected 404 responses must not print curl transport errors"
    # The derived HTTP origin shares this custom port; it cannot reach the HTTP
    # listener. Success here would mean the mandatory redirect check was omitted.
    assert smoke(origin, SMOKE_CURL_CA_BUNDLE=options["SMOKE_CURL_CA_BUNDLE"]).returncode != 0
    for ca in (None, "/etc/ssl/certs/ca-certificates.crt"):
        untrusted = dict(options)
        if ca is None:
            del untrusted["SMOKE_CURL_CA_BUNDLE"]
        else:
            untrusted["SMOKE_CURL_CA_BUNDLE"] = ca
        rejected = smoke(origin, **untrusted)
        assert rejected.returncode != 0
        assert "certificate" in rejected.stderr.lower()


@pytest.mark.skipif(not IMAGE, reason="Set STUDY_DEPLOYMENT_IMAGE for real Caddy TLS integration")
@pytest.mark.parametrize("fault", ["redirect", "graph", "health", "missing", "private", "status", "https_redirect"])
def test_real_tls_rejects_wrong_behavior(tls_site, fault):
    origin, options, configure = tls_site
    configure(fault)
    try:
        result = smoke(origin, **options)
        assert result.returncode != 0, f"Smoke accepted {fault}: {result.stdout}"
        assert result.stderr
    finally:
        configure()


@pytest.mark.parametrize("override", ["https://localhost", "http://user@localhost", "http://localhost/path", "http://localhost:65536"])
def test_invalid_http_override_rejected_before_network(override):
    result = smoke("https://localhost", SMOKE_HTTP_BASE=override)
    assert result.returncode == 2
    assert "origin" in result.stderr.lower()
