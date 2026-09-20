"""Opt-in encrypted off-VM-format roundtrip with disposable data, never GCS."""
import os
from pathlib import Path
import re

import pytest

from tests.website.test_deployment import IMAGE, deployment, run

AGE = os.environ.get("STUDY_AGE_BINARY")
pytestmark = pytest.mark.skipif(not (IMAGE and AGE), reason="Set STUDY_DEPLOYMENT_IMAGE and STUDY_AGE_BINARY for encrypted recovery integration")


def test_encrypted_backup_and_exact_image_bundle_restore(deployment):
    compose, _, _, temporary, environment, project, env_file, override = deployment
    # Root identity is dummy and kept outside both encrypted artifacts. Only its
    # public recipient participates in the VM-side encryption workflow.
    identity = temporary / "dummy-identity.txt"
    recipient = temporary / "dummy-recipient.txt"
    run(str(Path(AGE).with_name("age-keygen")), "-o", str(identity))
    recipient.write_text(run(str(Path(AGE).with_name("age-keygen")), "-y", str(identity)).stdout)
    shim = temporary / "inactive-systemctl"
    shim.write_text('#!/bin/sh\ncase "$1" in show) echo inactive ;; stop) : ;; *) exit 1 ;; esac\n')
    shim.chmod(0o700)
    backup = temporary / "data-backup"
    script_env = {**environment, "STUDY_PROJECT": project, "STUDY_ENV_FILE": str(env_file),
                  "STUDY_COMPOSE_OVERRIDE": str(override), "STUDY_SYSTEMCTL": str(shim), "TMPDIR": str(temporary)}
    run("bash", "deploy/backup.sh", str(backup), env=script_env)
    release = temporary / "release-bundle"
    release.mkdir(mode=0o700)
    app_id = (backup / "image.txt").read_text().strip()
    pg_id = (backup / "database-image.txt").read_text().strip()
    run("docker", "image", "save", "--output", str(release / "images.tar"), app_id, pg_id)
    (release / "app-image.txt").write_text(app_id + "\n")
    (release / "postgres-image.txt").write_text(pg_id + "\n")
    (release / "release.txt").write_text((backup / "release.txt").read_text())
    run("bash", "-c", 'cd "$1" && sha256sum images.tar app-image.txt postgres-image.txt release.txt > SHA256SUMS', "bash", str(release))
    recovered = temporary / "recovered"
    recovered.mkdir(mode=0o700)
    for source in (backup, release):
        archive = temporary / (source.name + ".tar.gz")
        run("tar", "-C", str(temporary), "-czf", str(archive), source.name)
        encrypted = temporary / (archive.name + ".age")
        partial = encrypted.with_suffix(".partial")
        run(AGE, "-R", str(recipient), "-o", str(partial), str(archive))
        partial.rename(encrypted)
        decrypted = recovered / archive.name
        run(AGE, "-d", "-i", str(identity), "-o", str(decrypted), str(encrypted))
        assert decrypted.read_bytes() == archive.read_bytes()
        run("tar", "-C", str(recovered), "-xzf", str(decrypted))
        run("bash", "-c", 'cd "$1" && sha256sum --check --status SHA256SUMS', "bash", str(recovered / source.name))
    # Failed decryption leaves the source/encrypted artifacts intact.
    bad = run(AGE, "-d", "-i", str(recipient), "-o", str(recovered / "failed.partial"), str(encrypted), check=False)
    assert bad.returncode != 0 and encrypted.exists() and archive.exists()
    restored_release = recovered / release.name
    restored_backup = recovered / backup.name
    assert (restored_backup / "COMPLETE").exists()
    assert (restored_release / "app-image.txt").read_text() == (restored_backup / "image.txt").read_text()
    assert (restored_release / "postgres-image.txt").read_text() == (restored_backup / "database-image.txt").read_text()
    run("docker", "image", "load", "--input", str(restored_release / "images.tar"))
    for image in (app_id, pg_id):
        assert run("docker", "image", "inspect", "--format", "{{.Id}}", image).stdout.strip() == image
    config = None
    try:
        result = run("bash", "deploy/restore-test.sh", str(restored_backup), env={"PATH": os.environ["PATH"], "TMPDIR": str(temporary)})
        config = Path(re.search(r"Restore configuration: (.+)", result.stdout)[1])
        assert "Restore verified" in result.stdout
        restored = ["docker", "compose", "--env-file", "/dev/null", "-f", str(config), "-p", "study-restore"]
        run(*restored, "exec", "-T", "web", "python", "deploy/recovery.py", "verify", input=(restored_backup / "recovery.json").read_text())
    finally:
        if config:
            run("docker", "compose", "--env-file", "/dev/null", "-f", str(config), "-p", "study-restore", "down", "--volumes")
            config.unlink()
            config.parent.rmdir()
