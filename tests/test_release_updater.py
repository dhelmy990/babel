"""Release input and lifecycle contracts; no production resources are touched."""
import hashlib
import io
import json
import fcntl
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from deploy import release_updater as updater


def manifest(number=1):
    commit = f"{number:040x}"
    return {
        "schema": 1, "repository": "dhelmy990/babel", "commit": commit,
        "run_number": number, "run_id": str(number + 100), "run_attempt": 1,
        "tag": f"website-{commit}-{number + 100}-1",
        "image_id": "sha256:" + "a" * 64,
        "image": "ghcr.io/dhelmy990/babel/website@sha256:" + "b" * 64,
        "image_sha256": "c" * 64, "source_sha256": "d" * 64,
    }


@pytest.mark.parametrize("key,value", [
    ("repository", "attacker/babel"), ("commit", "../escape"),
    ("tag", "../../escape"), ("run_number", True), ("run_number", 0),
    ("run_id", "1\n"), ("run_attempt", -1), ("schema", 2),
    ("image_id", "latest"), ("image_sha256", "x" * 64),
    ("source_sha256", "a" * 63),
    ("image", "ghcr.io/attacker/image@sha256:" + "b" * 64),
])
def test_reject_untrusted_or_malformed_release(key, value):
    data = manifest()
    data[key] = value
    with pytest.raises(ValueError):
        updater.validate_manifest(data)


def test_manifest_requires_all_fields_and_matching_tag():
    data = manifest()
    assert updater.validate_manifest(data) == data
    del data["commit"]
    with pytest.raises(ValueError):
        updater.validate_manifest(data)


@pytest.mark.parametrize("name,kind", [
    ("../escape", "file"), ("/absolute", "file"), ("link", "symlink"),
    ("hardlink", "hardlink"), ("device", "device"), (".env.production", "file"),
])
def test_source_archive_rejects_escape_links_and_runtime_secrets(tmp_path, name, kind):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        item = tarfile.TarInfo(name)
        if kind == "symlink":
            item.type, item.linkname = tarfile.SYMTYPE, "/etc"
        elif kind == "hardlink":
            item.type, item.linkname = tarfile.LNKTYPE, "../escape"
        elif kind == "device":
            item.type = tarfile.CHRTYPE
        output.addfile(item, io.BytesIO(b""))
    with pytest.raises(ValueError):
        updater.extract_source(archive, tmp_path / "source")
    assert not (tmp_path / "escape").exists()


class Host:
    """Observe lifecycle effects, and inject failure at an external boundary."""
    def __init__(self):
        self.calls = []
        self.fail_migration = False
        self.fail_backup = False
        self.timer_active = True
        self.digest_active = False
        self.fail_smoke = False

    def __call__(self, args, *, env=None, pass_fds=(), timeout=600):
        self.calls.append(list(args))
        if args[:2] == ["systemctl", "show"]:
            if args[-1] == "review-digest.service" and self.digest_active:
                return "activating"
            return "active" if self.timer_active and args[-1] == "review-digest.timer" else "inactive"
        if "smoke.sh" in str(args) and self.fail_smoke:
            raise RuntimeError("public HTTPS failed")
        if "backup.sh" in str(args):
            if self.fail_backup:
                raise RuntimeError("backup failed")
            path = Path(args[-1]); path.mkdir(parents=True)
            (path / "COMPLETE").touch()
        if "migrate" in args and self.fail_migration:
            raise RuntimeError("migration failed")
        if args[:3] == ["docker", "image", "inspect"]:
            return "1" * 40
        return ""


@pytest.fixture
def site(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    config = tmp_path / "config"; config.mkdir()
    (config / "production.env").write_text("GOOGLE_CLIENT_ID=real-client\n")
    host = Host()
    app = updater.Updater(state, config, tmp_path / "current", execute=host,
                          lock_path=tmp_path / "lifecycle.lock")
    return app, host


def staged(app, data):
    path = app.state / "releases" / data["tag"]
    source = path / "source"; (source / "deploy").mkdir(parents=True)
    (source / "compose.prod.yaml").write_text("name: dhelmy-stream\n")
    (source / "deploy" / "backup.sh").write_text("#!/bin/bash\n")
    updater.write_json(path / "manifest.json", data)
    return source


def install_previous(app):
    old = manifest(1)
    source = staged(app, old)
    app.link.symlink_to(source)
    updater.write_json(app.state / "current.json", old)
    return old, source


def test_disabled_server_stages_without_starting_or_migrating(site, monkeypatch):
    app, host = site
    data = manifest(2)
    source = staged(app, data)
    monkeypatch.setattr(app, "stage", lambda record: source)
    app.tick(data)
    assert json.loads((app.state / "staged.json").read_text()) == data
    assert not host.calls
    assert not app.link.exists()


def test_stage_only_never_activates_even_when_enabled(site, monkeypatch):
    app, host = site
    (app.config / "enabled").touch()
    data = manifest(2)
    source = staged(app, data)
    monkeypatch.setattr(app, "stage", lambda record: source)
    app.tick(data, stage_only=True)
    assert not host.calls


def test_upgrade_backs_up_before_migration_and_preserves_project(site):
    app, host = site
    _, old_source = install_previous(app)
    new = manifest(2); source = staged(app, new)
    app.apply(new, source)
    calls = host.calls
    backup = next(i for i, args in enumerate(calls) if "backup.sh" in str(args))
    migrate = next(i for i, args in enumerate(calls) if "migrate" in args)
    assert backup < migrate
    assert any("stop" in args and "web" in args for args in calls[:backup])
    assert app.link.resolve() == source
    assert old_source.exists()
    assert json.loads((app.state / "current.json").read_text()) == new
    assert not (app.state / "in-progress.json").exists()
    assert ["systemctl", "start", "review-digest.timer"] in calls
    for args in calls:
        if args[:2] == ["docker", "compose"]:
            assert "--env-file" in args and "-p" in args
            assert args[args.index("-p") + 1] == "dhelmy-stream"
            assert "down" not in args and "--volumes" not in args


def test_migration_failure_stops_site_and_blocks_automatic_retry(site):
    app, host = site
    old, old_source = install_previous(app)
    new = manifest(2); source = staged(app, new)
    host.fail_migration = True
    with pytest.raises(RuntimeError, match="migration failed"):
        app.apply(new, source)
    assert app.link.resolve() == old_source
    assert json.loads((app.state / "current.json").read_text()) == old
    assert (app.state / "failed.json").exists()
    assert not any("start" in args for args in host.calls)
    count = len(host.calls)
    with pytest.raises(RuntimeError, match="operator"):
        app.tick(new)
    assert len(host.calls) == count


def test_backup_failure_resumes_old_site_without_migrating(site):
    app, host = site
    _, old_source = install_previous(app)
    new = manifest(2); source = staged(app, new)
    host.fail_backup = True
    with pytest.raises(RuntimeError, match="backup failed"):
        app.apply(new, source)
    assert app.link.resolve() == old_source
    assert not any("migrate" in args for args in host.calls)
    assert any("start" in args and "web" in args for args in host.calls)
    assert ["systemctl", "start", "review-digest.timer"] in host.calls


def test_older_release_never_rolls_back(site):
    app, host = site
    current = manifest(3)
    updater.write_json(app.state / "current.json", current)
    app.tick(manifest(2))
    assert not host.calls
    assert not (app.state / "staged.json").exists()


def test_uncertain_interrupted_deployment_requires_operator(site):
    app, host = site
    updater.write_json(app.state / "in-progress.json", manifest(2))
    with pytest.raises(RuntimeError, match="operator"):
        app.tick(manifest(3))
    assert not host.calls


def test_first_install_refuses_existing_unmanaged_project(site):
    app, host = site
    new = manifest(2); source = staged(app, new)
    def existing(args, **kwargs):
        host.calls.append(list(args))
        return "existing-volume" if "ls" in args else ""
    app.execute = existing
    with pytest.raises(RuntimeError, match="existing"):
        app.apply(new, source)
    assert not any("migrate" in args or "stop" in args for args in host.calls)


def test_placeholder_configuration_does_not_touch_services(site):
    app, host = site
    (app.config / "production.env").write_text("GOOGLE_CLIENT_ID=REPLACE_WITH_CLIENT\n")
    source = staged(app, manifest())
    with pytest.raises(RuntimeError, match="configuration"):
        app.apply(manifest(), source)
    assert not host.calls


def test_public_health_failure_stops_new_site_and_does_not_claim_success(site, monkeypatch):
    app, host = site
    old, _ = install_previous(app)
    new = manifest(2); source = staged(app, new)
    host.fail_smoke = True
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="public HTTPS"):
        app.apply(new, source)
    assert json.loads((app.state / "current.json").read_text()) == old
    assert (app.state / "failed.json").exists()
    assert any("stop" in args and "web" in args for args in host.calls)
    assert not any(args[:2] == ["systemctl", "start"] for args in host.calls)


def test_manual_digest_oneshot_is_resumed_after_success(site):
    app, host = site
    install_previous(app)
    host.digest_active, host.timer_active = True, False
    new = manifest(2); source = staged(app, new)
    app.apply(new, source)
    assert ["systemctl", "start", "review-digest.service"] in host.calls
    assert ["systemctl", "start", "review-digest.timer"] not in host.calls


def test_checksum_failure_never_promotes_download(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: io.BytesIO(b"bad archive"))
    destination = tmp_path / "image.tar.gz"
    with pytest.raises(ValueError, match="checksum"):
        updater.download("https://github.com/fixture", destination, "0" * 64, 1024)
    assert not destination.exists()
    assert not destination.with_suffix(".gz.partial").exists()


def test_download_size_limit(tmp_path, monkeypatch):
    body = b"archive too big"
    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: io.BytesIO(body))
    with pytest.raises(ValueError, match="size"):
        updater.download("https://github.com/fixture", tmp_path / "image.tar.gz",
                         hashlib.sha256(body).hexdigest(), 2)


def test_source_archive_extracts_real_regular_files(tmp_path):
    archive = tmp_path / "source.tar.gz"
    body = b"name: dhelmy-stream\n"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("compose.prod.yaml"); member.size = len(body)
        output.addfile(member, io.BytesIO(body))
    updater.extract_source(archive, tmp_path / "source")
    assert (tmp_path / "source/compose.prod.yaml").read_bytes() == body


def test_staging_downloads_and_checks_revision_without_starting_containers(site, monkeypatch):
    app, host = site
    data = manifest(2)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in ("compose.prod.yaml", "deploy/backup.sh", "deploy/smoke.sh"):
            body = b"fixture"
            member = tarfile.TarInfo(name); member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    source = buffer.getvalue(); image = b"checked image fixture"
    data["source_sha256"] = hashlib.sha256(source).hexdigest()
    data["image_sha256"] = hashlib.sha256(image).hexdigest()
    monkeypatch.setattr(updater, "urlopen", lambda request, **kwargs:
                        io.BytesIO(source if request.full_url.endswith('/source.tar.gz') else image))
    def docker(args, **kwargs):
        host.calls.append(args)
        return data["commit"] if "inspect" in args else ""
    app.execute = docker
    app.tick(data)
    assert json.loads((app.state / "staged.json").read_text()) == data
    assert all(args[:2] in (["docker", "load"], ["docker", "image"]) for args in host.calls)
    assert not app.link.exists()
    assert (app.state / "releases" / data["tag"] / "source/.env.production").is_symlink()


def test_public_status_contains_no_private_runtime_values(site, tmp_path):
    app, host = site
    updater.write_json(app.state / "staged.json", manifest(2))
    (app.config / "production.env").write_text("DB_PASSWORD=secret-never-for-status\n")
    destination = tmp_path / "status.json"
    app.publish_status(destination)
    assert "secret-never-for-status" not in destination.read_text()
    assert json.loads(destination.read_text())["staged_commit"] == manifest(2)["commit"]
    assert destination.stat().st_mode & 0o777 == 0o644


def test_backup_and_updater_exclude_each_other(site, tmp_path):
    app, host = site
    script = Path(__file__).resolve().parents[1] / "deploy/backup.sh"
    with app.locked():
        environment = {**os.environ, "STUDY_LIFECYCLE_LOCK": str(app.lock_path)}
        environment.pop("STUDY_LIFECYCLE_FD", None)
        blocked = subprocess.run(["bash", str(script)], env=environment, capture_output=True, text=True)
        assert blocked.returncode == 1
        assert "lifecycle operation is active" in blocked.stderr
        environment["STUDY_LIFECYCLE_FD"] = str(app.lock_fd)
        inherited = subprocess.run(["bash", str(script)], env=environment,
                                   pass_fds=(app.lock_fd,), capture_output=True, text=True)
        assert inherited.returncode == 2
        assert "Usage:" in inherited.stderr  # Passed the shared lock, then rejected missing args.


def test_parallel_updater_does_not_start_work(site):
    app, host = site
    fd = os.open(app.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            app.tick(manifest())
        assert not host.calls
    finally:
        os.close(fd)
