"""Opt-in checks use real disposable production containers, never host services.

Run with STUDY_DEPLOYMENT_IMAGE pointing at a locally built image. This suite
owns only study-d1-* and the fresh study-restore project it creates.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import time
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_production_settings import production_env

ROOT = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("STUDY_DEPLOYMENT_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Set STUDY_DEPLOYMENT_IMAGE for disposable Docker integration")


def run(*args, input=None, env=None, check=True):
    result = subprocess.run(args, cwd=ROOT, env=env, input=input, text=True, capture_output=True)
    if check:
        assert result.returncode == 0, result.stderr[-5000:] + result.stdout[-1000:]
    return result


def test_postgres_health_waits_for_final_tcp_server():
    """Hold the socket-only init server so an early healthy signal is deterministic."""
    config = (ROOT / "compose.prod.yaml").read_text()
    postgres = re.search(r"image: (postgres:\S+)", config)[1]
    health_command = re.search(r"'(pg_isready[^']+)'", config)[1].replace("$$", "$")
    container = None
    with tempfile.TemporaryDirectory(prefix="study-d1-readiness-", dir=Path.home()) as temporary:
        initializer = Path(temporary) / "hold-init.sh"
        initializer.write_text("#!/bin/sh\ntouch /tmp/study-init-held\nwhile [ ! -e /tmp/study-init-release ]; do sleep 0.1; done\n")
        try:
            container = run("docker", "create", "--name", "study-d1-readiness-" + uuid4().hex,
                            "--network", "none", "--env", "POSTGRES_DB=study", "--env", "POSTGRES_USER=study",
                            "--env", "POSTGRES_PASSWORD=disposable", "--health-cmd", health_command,
                            "--health-interval", "1s", "--health-timeout", "1s", "--health-retries", "1", postgres).stdout.strip()
            run("docker", "cp", str(initializer), container + ":/docker-entrypoint-initdb.d/hold-init.sh")
            run("docker", "start", container)
            for _ in range(300):
                if run("docker", "exec", container, "test", "-f", "/tmp/study-init-held", check=False).returncode == 0:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("PostgreSQL never reached the held initialization script")
            assert run("docker", "exec", container, "pg_isready", "-U", "study", "-d", "study").returncode == 0
            assert run("docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "study", "-d", "study", check=False).returncode != 0
            initial_log = json.loads(run("docker", "inspect", "--format", "{{json .State.Health.Log}}", container).stdout) or []
            previous_probes = {entry["Start"] for entry in initial_log}
            for _ in range(100):
                health = json.loads(run("docker", "inspect", "--format", "{{json .State.Health}}", container).stdout)
                # Two new probes exclude an already-running pre-hold probe and
                # prove continued unreadiness while initialization is blocked.
                if len({entry["Start"] for entry in health["Log"]} - previous_probes) >= 2:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Docker did not probe the held initialization server")
            assert health["Status"] == "unhealthy", "The socket-only temporary server must not satisfy readiness"
            run("docker", "exec", container, "touch", "/tmp/study-init-release")
            for _ in range(300):
                if run("docker", "inspect", "--format", "{{.State.Health.Status}}", container).stdout.strip() == "healthy":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Final PostgreSQL TCP server never became healthy")
            run("docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "study", "-d", "study")
        finally:
            if container:
                run("docker", "rm", "--force", "--volumes", container)


SEED = r'''
import base64, json
from uuid import uuid4
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from study.models import PublisherIdentity, ReaderProfile, ReviewSchedule, ReviewDay, ReviewSlot
from study.services.content import publish_article, archive_article
from study.services.notes import create_note, delete_note
users = [get_user_model().objects.create_user(username=name, email=email) for name,email in [('publisher','dhelmy990@gmail.com'), ('reader','reader@example.invalid'), ('other','other@example.invalid')]]
publisher, reader, other = users
EmailAddress.objects.create(user=publisher,email=publisher.email,verified=True,primary=True)
SocialAccount.objects.create(user=publisher,provider='google',uid='disposable-publisher')
PublisherIdentity.objects.create(user=publisher,google_subject='disposable-publisher')
for user in users: ReaderProfile.objects.create(user=user,timezone='Asia/Singapore')
from io import BytesIO
from PIL import Image
buffer=BytesIO(); Image.new('RGB',(3,3),'red').save(buffer,format='PNG'); png=buffer.getvalue()
def article(title):
    return publish_article(publisher,title=title,color='#112233',markdown='# '+title+'\n\n![Image](a.png)',images={'a.png':png},submission_id=uuid4())
public, archived = article('Recovery public'), article('Recovery archive')
note=create_note(reader,public.pk,note_id=uuid4(),kind='text',text='Private recovery text',x=None,y=None)
last=create_note(reader,archived.pk,note_id=uuid4(),kind='sticky',text='Delete after grant',x=1,y=2)
archive_article(publisher,archived.pk); delete_note(reader,last.pk)
now=timezone.now()
schedule=ReviewSchedule.objects.create(user=reader,article=public,last_completed_at=now,next_due_date=now.date())
day=ReviewDay.objects.create(user=reader,local_date=now.date(),timezone='Asia/Singapore',next_boundary_at=now+timedelta(days=1))
slot=ReviewSlot.objects.create(day=day,ordinal=0,article=public,schedule=schedule)
sessions={}
for user in users:
    session=SessionStore(); session.update({'_auth_user_id':str(user.pk),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend','_auth_user_hash':user.get_session_auth_hash(),'study.mode':'admin' if user==publisher else 'reader'});session.save();sessions[user.username]=session.session_key
print(json.dumps({'public':str(public.pk),'slug':public.slug,'archive':str(archived.pk),'asset':str(archived.assets.get().pk),'note':str(note.pk),'slot':slot.pk,'sessions':sessions}))
'''


@pytest.fixture(scope="module")
def deployment():
    # Snap Docker has a private /tmp; use a task-owned visible home directory.
    temporary = Path(tempfile.mkdtemp(prefix="study-d1-", dir=Path.home()))
    project = "study-d1-" + uuid4().hex[:10]
    environment = {**os.environ, **production_env(), "STUDY_IMAGE": IMAGE, "POSTGRES_DB": "study", "POSTGRES_USER": "study", "POSTGRES_PASSWORD": "disposable"}
    env_file = temporary / "runtime.env"
    values = {**production_env(), "DB_HOST": "db", "DB_PORT": "5432", "POSTGRES_DB": "study", "POSTGRES_USER": "study", "POSTGRES_PASSWORD": "disposable", "STUDY_IMAGE": IMAGE}
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items() if key != "PATH") + "\n")
    env_file.chmod(0o600)
    environment["STUDY_RUNTIME_ENV_FILE"] = str(env_file)
    override = temporary / "override.yaml"
    override.write_text('services:\n  web:\n    ports: ["127.0.0.1::8000"]\n')
    command = ["docker", "compose", "--env-file", str(env_file), "-f", "compose.prod.yaml", "-f", str(override), "-p", project]
    def compose(*args, **kwargs):
        return run(*command, *args, env=environment, **kwargs)
    compose("config", "--quiet")
    try:
        compose("up", "-d", "--wait", "db")
        compose("run", "--rm", "--no-deps", "-T", "web", "python", "manage.py", "migrate", "--noinput")
        compose("up", "-d", "--no-build", "web")
        for _ in range(60):
            result = compose("exec", "-T", "web", "python", "-c", "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/healthz',headers={'Host':'dhelmy.stream','X-Forwarded-Proto':'https'}); urllib.request.urlopen(r)", check=False)
            if result.returncode == 0: break
            time.sleep(1)
        else: pytest.fail("Disposable web did not become healthy")
        data = json.loads(compose("exec", "-T", "web", "python", "manage.py", "shell", "--no-imports", input=SEED).stdout)
        address = compose("port", "web", "8000").stdout.strip()
        yield compose, "http://" + address, data, temporary, environment, project, env_file, override
    finally:
        compose("down", "--volumes", "--remove-orphans")
        shutil.rmtree(temporary)


def test_built_image_http_security_and_single_module_initialization(deployment):
    compose, origin, data, *_ = deployment
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(extra_http_headers={"Host": "dhelmy.stream", "X-Forwarded-Proto": "https"})
        api = context.request
        assert api.get(origin + "/healthz").json() == {"status": "ok"}
        assert api.get(origin + "/healthz", headers={"Host": "evil.invalid", "X-Forwarded-Proto": "https"}).status == 400
        assert api.get(origin + "/healthz", headers={"Host": "dhelmy.stream", "X-Forwarded-Proto": "http"}, max_redirects=0).status == 301
        for path in ("/.env", "/.env.save", "/prompts/website.md", "/build/", "/media/assets/a", "/static/js/persistence.js"):
            assert api.get(origin + path).status == 404
        assert api.get(origin + "/assets/" + data["asset"]).status == 404
        anonymous_notes = api.get(origin + "/api/articles/" + data["public"] + "/notes")
        assert anonymous_notes.status == 401 and anonymous_notes.headers["cache-control"] == "private, no-store"
        session_response = api.get(origin + "/api/session", headers={"Cookie": "sessionid=" + data["sessions"]["publisher"]})
        assert session_response.json()["can_publish"] is True
        context.set_extra_http_headers({})
        # A test-only forwarding route supplies the trusted TLS-proxy headers.
        # The browser retains the real HTTPS origin and secure cookie behavior.
        upstream_cookies = {}
        def forward(route):
            response = route.fetch(url=origin + route.request.url.removeprefix("https://dhelmy.stream"), headers={**route.request.all_headers(), "Host": "dhelmy.stream", "X-Forwarded-Proto": "https"})
            if route.request.url.endswith("/api/mode"):
                upstream_cookies["session"] = response.headers.get("set-cookie", "")
            route.fulfill(response=response)
        context.route("https://dhelmy.stream/**", forward)
        context.add_cookies([{"name": "sessionid", "value": data["sessions"]["publisher"], "domain": "dhelmy.stream", "path": "/", "secure": True, "httpOnly": True}])
        page = context.new_page()
        page.add_init_script('''window.modeListeners = 0; const add = EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener = function(type, ...args) {
 if (type === 'click' && this.matches?.('[data-mode-toggle]')) window.modeListeners++;
 return add.call(this, type, ...args);
};''')
        requests, errors = [], []
        page.on("request", lambda request: requests.append(request.url))
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: errors.append(request.url + ": " + str(request.failure)))
        response = page.goto("https://dhelmy.stream/" + data["slug"])
        assert "data-mode-toggle" in response.text(), "Forwarded browser session was not authenticated"
        page.wait_for_load_state("networkidle")
        assert not errors
        site_requests = [url for url in requests if re.search(r"/study/site(?:\.[a-f0-9]+)?\.js$", url)]
        assert len(site_requests) == 1 and re.search(r"site\.[a-f0-9]+\.js$", site_requests[0])
        assert page.evaluate("window.modeListeners") == 1
        assert any(re.search(r"publishing\.[a-f0-9]+\.js$", url) for url in requests)
        assert any(re.search(r"notes\.[a-f0-9]+\.js$", url) for url in requests)
        assert any(re.search(r"reviews\.[a-f0-9]+\.js$", url) for url in requests)
        with page.expect_response(lambda response: response.url.endswith("/api/mode")) as changed, page.expect_navigation(wait_until="networkidle"):
            page.get_by_role("button", name="Reader mode", exact=True).click()
        assert changed.value.status == 200
        session_cookie = upstream_cookies["session"]
        assert "sessionid=" in session_cookie and "HttpOnly" in session_cookie and "Secure" in session_cookie
        response = api.get(origin + "/", headers={"Host": "dhelmy.stream", "X-Forwarded-Proto": "https"})
        csrf = response.headers.get("set-cookie", "")
        # Cookies are asserted without weakening production transport settings.
        assert "Secure" in csrf and "HttpOnly" not in csrf.split("csrftoken=")[-1].split("\n")[0]
        # The owner editor also loads through hashed dynamic imports in the
        # production image, and saved Markdown survives the real HTTP path.
        page.goto("https://dhelmy.stream/publish/" + data["public"])
        editor = page.get_by_role("textbox", name="Article body", exact=True)
        editor.wait_for()
        assert editor.locator("img[data-image-path]").get_attribute("src").startswith("/assets/")
        # Click actual text, then create a body paragraph after the heading.
        # Verify the paragraph before saving: native cursor updates after a
        # padding click and Ctrl+End can lag behind zero-delay keystrokes.
        editor.get_by_role("heading", name="Recovery public", exact=True).click()
        expect(editor).to_be_focused()
        editor.press("End")
        editor.press("Enter")
        editor.press_sequentially("Edited through the production bundle.")
        expect(editor.locator("p").filter(has_text="Edited through the production bundle.")).to_be_visible()
        assert not errors
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_url("https://dhelmy.stream/" + data["slug"])
        expect(page.locator(".article-body")).to_contain_text("Edited through the production bundle.")
        expect(page.locator(".article-body img")).to_have_attribute("src", re.compile("/assets/"))
        page.wait_for_load_state("networkidle")
        assert any(re.search(r"/vendor/article-editor\.[a-f0-9]+\.js$", url) for url in requests)
        assert not errors
        browser.close()
    compose("exec", "-T", "web", "python", "manage.py", "check", "--deploy", "--fail-level", "WARNING")


def test_real_backup_restore_restart_and_failure_state_recovery(deployment):
    compose, _, _, temporary, environment, project, env_file, override = deployment
    state = temporary / "service-state.json"
    shim = temporary / "systemctl"
    shim.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path=Path(os.environ['SHIM_STATE']); state=json.loads(path.read_text())
action, unit=sys.argv[1],sys.argv[-1]
if action=='is-active': sys.exit(0 if state[unit]=='active' else 3)
if action=='show': print(state[unit]); sys.exit(0)
if action=='stop' and os.environ.get('SHIM_FAIL')==unit: sys.exit(42)
if action=='start': state['started'].append(unit)
# systemctl start waits for a oneshot to finish; without RemainAfterExit its
# resulting state is inactive. Its in-progress ExecStart state is activating.
state[unit]='active' if action=='start' and unit.endswith('.timer') else 'inactive'
path.write_text(json.dumps(state))
''')
    shim.chmod(0o700)
    script_env = {**environment, "TMPDIR": str(temporary), "STUDY_PROJECT": project, "STUDY_ENV_FILE": str(env_file), "STUDY_COMPOSE_OVERRIDE": str(override), "STUDY_SYSTEMCTL": str(shim), "SHIM_STATE": str(state)}
    for active in (True, False):
        states = {"review-digest.timer": "active" if active else "inactive", "review-digest.service": "inactive", "started": []}
        state.write_text(json.dumps(states))
        if not active: compose("stop", "web")
        failed = run("bash", "deploy/backup.sh", str(temporary / ("failure-" + str(active))), env={**script_env, "SHIM_FAIL": "review-digest.service"}, check=False)
        assert failed.returncode == 42
        assert json.loads(state.read_text()) == {**states, "started": ["review-digest.timer"] if active else []}
        assert bool(compose("ps", "-q", "web").stdout.strip()) == active
    compose("start", "web")
    running_oneshot = {"review-digest.timer": "active", "review-digest.service": "activating", "started": []}
    restored_oneshot = {"review-digest.timer": "active", "review-digest.service": "inactive", "started": ["review-digest.service", "review-digest.timer"]}
    state.write_text(json.dumps(running_oneshot))
    assert run(str(shim), "is-active", "--quiet", "review-digest.service", env=script_env, check=False).returncode != 0
    assert run(str(shim), "show", "--property=ActiveState", "--value", "review-digest.service", env=script_env).stdout.strip() == "activating"
    # Fail only the media helper, after real pg_dump and web shutdown.
    bin_dir = temporary / "bin"
    bin_dir.mkdir()
    docker_shim = bin_dir / "docker"
    docker_shim.write_text("#!/usr/bin/env python3\nimport os,sys\nif 'SHIM_BAD_REVISION' in os.environ and sys.argv[1:3]==['image','inspect']: print(os.environ['SHIM_BAD_REVISION']); sys.exit(0)\nif sys.argv[1:2]==['run']: sys.exit(43)\nos.execv(" + repr(shutil.which("docker")) + ", [" + repr(shutil.which("docker")) + ", *sys.argv[1:]])\n")
    docker_shim.chmod(0o700)
    for invalid in ("", "<no value>", "unknown"):
        rejected = run("bash", "deploy/backup.sh", str(temporary / "invalid-release"), env={**script_env, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "SHIM_BAD_REVISION": invalid}, check=False)
        assert rejected.returncode != 0 and "org.opencontainers.image.revision" in rejected.stderr
        assert not (temporary / "invalid-release").exists()
    failed_destination = temporary / "failed-media"
    failed = run("bash", "deploy/backup.sh", str(failed_destination), env={**script_env, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}, check=False)
    assert failed.returncode == 43
    assert (failed_destination / "database.dump").stat().st_size > 0
    assert not (failed_destination / "COMPLETE").exists()
    assert compose("ps", "-q", "web").stdout.strip()
    assert json.loads(state.read_text()) == restored_oneshot
    state.write_text(json.dumps(running_oneshot))
    backup = temporary / "backup"
    run("bash", "deploy/backup.sh", str(backup), env=script_env)
    assert (backup / "COMPLETE").exists()
    assert re.fullmatch(r"[a-f0-9]{7,40}\n", (backup / "release.txt").read_text())
    assert backup.stat().st_mode & 0o077 == 0
    assert json.loads(state.read_text()) == restored_oneshot
    assert compose("ps", "-q", "web").stdout.strip()
    inactive = {"review-digest.timer": "inactive", "review-digest.service": "inactive", "started": []}
    state.write_text(json.dumps(inactive))
    compose("stop", "web")
    run("bash", "deploy/backup.sh", str(temporary / "backup-inactive"), env=script_env)
    assert json.loads(state.read_text()) == inactive
    assert not compose("ps", "-q", "web").stdout.strip()
    compose("start", "web")
    incomplete = temporary / "incomplete"
    incomplete.mkdir()
    assert run("bash", "deploy/restore-test.sh", str(incomplete), env={"PATH": os.environ["PATH"]}, check=False).returncode != 0
    compose("restart", "web")
    compose("exec", "-T", "web", "python", "deploy/recovery.py", "verify", input=(backup / "recovery.json").read_text())
    restore_config = None
    try:
        restore_env = {"PATH": os.environ["PATH"], "TMPDIR": str(temporary)}
        if run("docker", "volume", "inspect", "study-restore_media", "--format", "{{.Name}}", check=False).returncode == 0:
            pytest.skip("Existing study-restore_media belongs to another operation")
        owner = uuid4().hex
        run("docker", "volume", "create", "--label", "study.d1.owner=" + owner, "study-restore_media")
        assert run("docker", "volume", "inspect", "study-restore_media", "--format", '{{index .Labels "study.d1.owner"}}').stdout.strip() == owner
        volume_command = ["docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "python", "--mount", "type=volume,src=study-restore_media,dst=/media", IMAGE, "-c"]
        try:
            run(*volume_command, "from pathlib import Path; Path('/media/sentinel').write_text(" + repr(owner) + ")")
            before = run("docker", "volume", "inspect", "study-restore_media", "--format", "{{.CreatedAt}}").stdout
            refusal = run("bash", "deploy/restore-test.sh", str(backup), env=restore_env, check=False)
            assert refusal.returncode != 0 and "already in use" in refusal.stderr
            assert run("docker", "volume", "inspect", "study-restore_media", "--format", "{{.CreatedAt}}").stdout == before
            assert run(*volume_command, "from pathlib import Path; print(Path('/media/sentinel').read_text())").stdout.strip() == owner
        finally:
            assert run("docker", "volume", "inspect", "study-restore_media", "--format", '{{index .Labels "study.d1.owner"}}').stdout.strip() == owner
            run("docker", "volume", "rm", "study-restore_media")
        result = run("bash", "deploy/restore-test.sh", str(backup), env=restore_env)
        restore_config = re.search(r"Restore configuration: (.+)", result.stdout)[1]
        restored = ["docker", "compose", "--env-file", "/dev/null", "-f", restore_config, "-p", "study-restore"]
        run(*restored, "restart", "web")
        run(*restored, "exec", "-T", "web", "python", "deploy/recovery.py", "verify", input=(backup / "recovery.json").read_text())
        assert run("bash", "deploy/restore-test.sh", str(backup), env=restore_env, check=False).returncode != 0
        assert run("bash", "deploy/restore-test.sh", str(backup), env={**restore_env, "STUDY_PROJECT": "production"}, check=False).returncode != 0
    finally:
        if restore_config:
            run("docker", "compose", "--env-file", "/dev/null", "-f", restore_config, "-p", "study-restore", "down", "--volumes")
            Path(restore_config).unlink()
            Path(restore_config).parent.rmdir()
