"""Opt-in rollout through a disposable PostgreSQL/media stack and real local TLS."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from deploy import release_updater
from tests.test_deployment import IMAGE, ROOT, deployment, run
from tests.test_smoke import tls_site

pytestmark = pytest.mark.skipif(not IMAGE, reason="Set STUDY_DEPLOYMENT_IMAGE for disposable rollout integration")


def test_rollout_preserves_private_data_with_real_backup_migration_and_tls(deployment, tls_site):
    compose, _, _, temporary, environment, project, env_file, override = deployment
    origin, tls_options, _ = tls_site
    state, config = temporary / "updater", temporary / "updater-config"
    state.mkdir(); config.mkdir()
    shutil.copy2(env_file, config / "production.env")
    image_id = run("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE).stdout.strip()
    commit = run("docker", "image", "inspect", "--format",
                 '{{index .Config.Labels "org.opencontainers.image.revision"}}', IMAGE).stdout.strip()
    records = []
    for number in (1, 2):
        record = {"schema": 1, "repository": "dhelmy990/babel", "commit": commit,
                  "run_number": number, "run_id": str(100 + number), "run_attempt": 1,
                  "tag": f"website-{commit}-{100 + number}-1", "image_id": image_id,
                  "image": "ghcr.io/dhelmy990/babel/website@sha256:" + "b" * 64,
                  "image_sha256": "c" * 64, "source_sha256": "d" * 64}
        source = state / "releases" / record["tag"] / "source"
        (source / "deploy").mkdir(parents=True)
        shutil.copy2(ROOT / "compose.prod.yaml", source / "compose.prod.yaml")
        shutil.copy2(ROOT / "deploy/backup.sh", source / "deploy/backup.sh")
        records.append((record, source))
    old, old_source = records[0]; new, new_source = records[1]
    link = temporary / "canonical-site"; link.symlink_to(old_source)
    release_updater.write_json(state / "current.json", old)
    shim = temporary / "updater-systemctl"
    shim.write_text('#!/bin/sh\nif [ "$1" = show ]; then echo inactive; fi\n')
    shim.chmod(0o700)
    before = compose("exec", "-T", "web", "python", "deploy/recovery.py", "snapshot").stdout
    calls = []

    def execute(args, *, env=None, pass_fds=(), timeout=600):
        args = list(args); calls.append(args.copy())
        if args[0] == "systemctl":
            args[0] = str(shim)
        elif args[:2] == ["docker", "compose"]:
            args[args.index("-p") + 1] = project
            index = args.index("-p")
            args[index:index] = ["-f", str(override)]
            # The fixture's standalone Caddy is already serving real TLS on
            # random loopback ports. Never start the production 80/443 proxy.
            if "proxy" in args:
                args.remove("proxy")
        elif args[0] == "bash" and str(args[1]).endswith("smoke.sh"):
            args = ["bash", str(ROOT / "deploy/smoke.sh"), origin]
        child_env = {**os.environ, **environment, **(env or {}), **tls_options,
                     "STUDY_PROJECT": project, "STUDY_COMPOSE_OVERRIDE": str(override),
                     "STUDY_SYSTEMCTL": str(shim), "TMPDIR": str(temporary)}
        result = subprocess.run(args, env=child_env, pass_fds=pass_fds, timeout=timeout,
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
        return result.stdout.strip()

    app = release_updater.Updater(state, config, link, execute=execute, lock_path=temporary / "updater.lock")
    with app.locked():
        app.apply(new, new_source)
    assert link.resolve() == new_source
    assert json.loads((state / "current.json").read_text()) == new
    backups = list((state / "backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "COMPLETE").exists()
    assert json.loads((backups[0] / "recovery.json").read_text()) == json.loads(before)
    compose("exec", "-T", "web", "python", "deploy/recovery.py", "verify", input=before)
    assert any("migrate" in args for args in calls)
    assert not (state / "failed.json").exists()
