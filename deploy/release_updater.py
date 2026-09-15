#!/usr/bin/env python3
"""Fetch verified public releases; activate only when the operator enables it."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPOSITORY = "dhelmy990/babel"
LATEST = f"https://github.com/{REPOSITORY}/releases/latest/download/website-release.json"
PROJECT = "dhelmy-stream"
HEX = r"[a-f0-9]{64}"


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("Release manifest must be an object")
    required = {"schema", "repository", "commit", "run_number", "run_id", "run_attempt",
                "tag", "image_id", "image", "image_sha256", "source_sha256"}
    if not required <= data.keys() or type(data["schema"]) is not int or data["schema"] != 1:
        raise ValueError("Unsupported release manifest")
    if data["repository"] != REPOSITORY:
        raise ValueError("Unexpected release repository")
    for key in ("run_number", "run_attempt"):
        if type(data[key]) is not int or data[key] < 1:
            raise ValueError("Invalid release sequence")
    patterns = {"commit": r"[a-f0-9]{40}", "run_id": r"[1-9][0-9]*",
                "image_id": "sha256:" + HEX, "image_sha256": HEX, "source_sha256": HEX,
                "image": r"ghcr\.io/dhelmy990/babel/website@sha256:" + HEX}
    for key, pattern in patterns.items():
        if not isinstance(data[key], str) or not re.fullmatch(pattern, data[key]):
            raise ValueError(f"Invalid release field: {key}")
    if data["tag"] != f"website-{data['commit']}-{data['run_id']}-{data['run_attempt']}":
        raise ValueError("Release tag does not match its identity")
    return data


def sequence(data):
    return data["run_number"], data["run_attempt"]


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def fetch_manifest():
    request = Request(LATEST, headers={"User-Agent": "dhelmy-stream-updater/1"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(65537)
    except HTTPError as error:
        if error.code == 404:
            return None  # No published website release yet.
        raise RuntimeError(f"Release lookup returned HTTP {error.code}") from None
    if len(raw) > 65536:
        raise ValueError("Release manifest is too large")
    return validate_manifest(json.loads(raw))


def download(url, path, expected, limit):
    """Write a bounded, hashed download atomically; a bad file is never promoted."""
    request = Request(url, headers={"User-Agent": "dhelmy-stream-updater/1"})
    temporary = path.with_suffix(path.suffix + ".partial")
    digest = hashlib.sha256()
    size = 0
    started = time.monotonic()
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > limit or time.monotonic() - started > 600:
                    raise ValueError("Release download exceeds size or time limit")
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != expected:
            raise ValueError("Release download checksum mismatch")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_source(archive, destination):
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 20000 or sum(item.size for item in members) > 100 * 1024 * 1024:
            raise ValueError("Source archive is too large")
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or
                    not (member.isfile() or member.isdir()) or
                    path.as_posix() in {".env", ".env.production", ".env.save"}):
                raise ValueError("Unsafe source archive member")
        destination.mkdir(parents=True, exist_ok=False)
        source.extractall(destination, filter="data")


def execute(args, *, env=None, pass_fds=(), timeout=600):
    environment = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                   "LANG": "C.UTF-8", **(env or {})}
    result = subprocess.run(args, env=environment, pass_fds=pass_fds, timeout=timeout,
                            text=True, capture_output=True)
    if result.returncode:
        # Never echo a subprocess environment or provider values into journald.
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(map(str, args))}")
    return result.stdout.strip()


class Updater:
    def __init__(self, state, config, link, *, execute=execute, lock_path=None):
        self.state, self.config, self.link = Path(state), Path(config), Path(link)
        self.execute = execute
        self.lock_path = Path(lock_path or "/tmp/study-lifecycle-dhelmy-stream.lock")
        self.lock_fd = None

    @contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.geteuid():
                raise RuntimeError("Lifecycle lock belongs to another account")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd = fd
            yield
        finally:
            self.lock_fd = None
            os.close(fd)

    def environment(self, data):
        environment = {"STUDY_IMAGE": data["image_id"],
                       "STUDY_RUNTIME_ENV_FILE": str(self.config / "production.env"),
                       "STUDY_ENV_FILE": str(self.config / "production.env"),
                       "STUDY_PROJECT": PROJECT, "STUDY_LIFECYCLE_LOCK": str(self.lock_path)}
        if self.lock_fd is not None:
            environment["STUDY_LIFECYCLE_FD"] = str(self.lock_fd)
        return environment

    def compose(self, source, data, *args):
        return self.execute(["docker", "compose", "--env-file", str(self.config / "production.env"),
                             "-f", str(source / "compose.prod.yaml"), "-p", PROJECT, *args],
                            env=self.environment(data))

    def stage(self, data):
        releases = self.state / "releases"
        releases.mkdir(exist_ok=True)
        final = releases / data["tag"]
        if final.exists():
            if read_json(final / "manifest.json") != data:
                raise RuntimeError("Existing staged release has different metadata")
        else:
            with tempfile.TemporaryDirectory(prefix=".staging-", dir=releases) as temporary:
                path = Path(temporary)
                base = f"https://github.com/{REPOSITORY}/releases/download/{data['tag']}"
                download(base + "/source.tar.gz", path / "source.tar.gz", data["source_sha256"], 100 * 1024 * 1024)
                download(base + "/image.tar.gz", path / "image.tar.gz", data["image_sha256"], 2 * 1024**3)
                extract_source(path / "source.tar.gz", path / "source")
                for name in ("compose.prod.yaml", "deploy/backup.sh", "deploy/smoke.sh"):
                    if not (path / "source" / name).is_file():
                        raise ValueError("Release is missing deployment files")
                write_json(path / "manifest.json", data)
                os.rename(path, final)
        self.execute(["docker", "load", "--input", str(final / "image.tar.gz")])
        revision = self.execute(["docker", "image", "inspect", "--format",
                                 '{{index .Config.Labels "org.opencontainers.image.revision"}}', data["image_id"]])
        if revision != data["commit"]:
            raise ValueError("Loaded image does not match the release commit")
        source = final / "source"
        runtime = source / ".env.production"
        if not runtime.is_symlink():
            runtime.symlink_to(self.config / "production.env")
        return source

    def apply(self, data, source):
        runtime = self.config / "production.env"
        if not runtime.is_file() or "REPLACE_" in runtime.read_text():
            raise RuntimeError("Production configuration is not ready")
        old = read_json(self.state / "current.json")
        old_source = self.state / "releases" / old["tag"] / "source" if old else None
        if old:
            if not self.link.is_symlink() or self.link.resolve() != old_source:
                raise RuntimeError("Current source does not match the deployment record")
        else:
            if self.link.exists() or self.link.is_symlink():
                raise RuntimeError("Refusing an existing unmanaged application directory")
            for resource in ("container", "volume", "network"):
                if self.execute(["docker", resource, "ls", "-q", "--filter", "label=com.docker.compose.project=" + PROJECT]):
                    raise RuntimeError("Refusing existing unmanaged website resources")
        self.compose(source, data, "config", "--quiet")
        self.compose(source, data, "pull", "db", "proxy")
        timer_active = self.execute(["systemctl", "show", "--property=ActiveState", "--value",
                                     "review-digest.timer"]) in {"active", "activating"}
        digest_active = self.execute(["systemctl", "show", "--property=ActiveState", "--value",
                                      "review-digest.service"]) in {"active", "activating", "reloading"}
        write_json(self.state / "in-progress.json", data)
        migration_started = False
        try:
            self.compose(source, data, "run", "--rm", "--no-deps", "-T", "web", "python", "manage.py",
                         "check", "--deploy", "--fail-level", "WARNING")
            self.execute(["systemctl", "stop", "review-digest.timer", "review-digest.service"])
            if old:
                self.compose(old_source, old, "stop", "-t", "75", "web")
                backup = self.state / "backups" / (data["tag"] + "-" + str(time.time_ns()))
                backup.parent.mkdir(exist_ok=True)
                self.execute(["bash", str(old_source / "deploy/backup.sh"), str(backup)],
                             env=self.environment(old), pass_fds=(() if self.lock_fd is None else (self.lock_fd,)))
                if not (backup / "COMPLETE").is_file():
                    raise RuntimeError("Backup did not complete")
            else:
                self.compose(source, data, "up", "-d", "--wait", "db")
            migration_started = True
            self.compose(source, data, "run", "--rm", "--no-deps", "-T", "web", "python", "manage.py", "migrate", "--noinput")
            self.link.parent.mkdir(parents=True, exist_ok=True)
            temporary_link = self.link.with_name(self.link.name + ".next")
            if temporary_link.exists() or temporary_link.is_symlink():
                raise RuntimeError("Unexpected pending source link")
            temporary_link.symlink_to(source)
            temporary_link.replace(self.link)
            self.compose(source, data, "up", "-d", "--no-build", "--no-deps", "web", "proxy")
            # Wait inside the container for its own app and database, then check public HTTPS.
            self.compose(source, data, "exec", "-T", "web", "python", "-c",
                         "import time,urllib.request\nfor attempt in range(60):\n try:\n  "
                         "r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/healthz',"
                         "headers={'Host':'dhelmy.stream','X-Forwarded-Proto':'https'}),timeout=2)\n  "
                         "assert r.status==200\n  break\n except Exception:\n  "
                         "time.sleep(1)\nelse:\n raise SystemExit('Website did not become healthy')")
            for attempt in range(6):
                try:
                    self.execute(["bash", str(source / "deploy/smoke.sh"), "https://dhelmy.stream"])
                    break
                except RuntimeError:
                    if attempt == 5:
                        raise
                    time.sleep(10)
            write_json(self.state / "current.json", data)
            if digest_active:
                self.execute(["systemctl", "start", "review-digest.service"])
            if timer_active:
                self.execute(["systemctl", "start", "review-digest.timer"])
            (self.state / "in-progress.json").unlink()
            print(f"Deployed website commit {data['commit']}")
        except Exception as error:
            write_json(self.state / "failed.json", {"release": data, "migration_started": migration_started,
                                                    "error": str(error), "timer_was_active": timer_active,
                                                    "digest_was_active": digest_active})
            if migration_started:
                self.compose(source, data, "stop", "-t", "75", "web")
                self.execute(["systemctl", "stop", "review-digest.timer", "review-digest.service"])
            elif old:
                self.compose(old_source, old, "start", "web")
                if digest_active:
                    self.execute(["systemctl", "start", "review-digest.service"])
                if timer_active:
                    self.execute(["systemctl", "start", "review-digest.timer"])
            raise

    def tick(self, data=None, *, stage_only=False):
        with self.locked():
            if any((self.state / name).exists() for name in ("failed.json", "in-progress.json")):
                raise RuntimeError("Deployment needs operator recovery before further updates")
            data = validate_manifest(data) if data is not None else fetch_manifest()
            if data is None:
                print("No public website release yet")
                return
            current, staged = read_json(self.state / "current.json"), read_json(self.state / "staged.json")
            if any(record and sequence(data) < sequence(record) for record in (current, staged)):
                print("Ignoring an older release")
                return
            if current and sequence(data) == sequence(current):
                if data != current:
                    raise ValueError("Release identity changed at the same sequence")
                print("Website is current")
                return
            if staged and sequence(data) == sequence(staged):
                if data != staged:
                    raise ValueError("Staged release identity changed")
                source = self.state / "releases" / data["tag"] / "source"
            else:
                source = self.stage(data)
                write_json(self.state / "staged.json", data)
            if stage_only or not (self.config / "enabled").is_file():
                print(f"Staged {data['commit']}; production activation is disabled")
                return
            # Restore a locally pruned image from the saved, verified archive and
            # recheck its revision before making any production change.
            source = self.stage(data)
            self.apply(data, source)

    def publish_status(self, path):
        current = read_json(self.state / "current.json")
        staged = read_json(self.state / "staged.json")
        write_json(path, {
            "checked_at": int(time.time()),
            "current_commit": current["commit"] if current else None,
            "staged_commit": staged["commit"] if staged else None,
            "activation_enabled": (self.config / "enabled").is_file(),
            "operator_recovery_required": any((self.state / name).exists()
                                               for name in ("failed.json", "in-progress.json")),
        })
        path.chmod(0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/dhelmy-stream"))
    parser.add_argument("--config", type=Path, default=Path("/etc/dhelmy-stream"))
    parser.add_argument("--link", type=Path, default=Path("/opt/dhelmy-stream"))
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--status-file", type=Path, default=Path("/run/dhelmy-stream/status.json"))
    args = parser.parse_args()
    if os.geteuid() != 0 and not args.stage_only:
        parser.error("Production updates must run as root; use --stage-only for a download rehearsal")
    os.umask(0o077)
    app = Updater(args.state, args.config, args.link, lock_path=args.lock)
    try:
        app.tick(stage_only=args.stage_only)
    except BlockingIOError:
        print("Another website lifecycle operation holds the lock")
    except Exception as error:
        parser.exit(1, f"Website updater: {error}\n")
    finally:
        if not args.stage_only:
            app.publish_status(args.status_file)


if __name__ == "__main__":
    main()
