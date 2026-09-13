# Website Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the completed website, document a single-VM deployment at dhelmy.stream, and verify recovery of articles, images, private notes, and schedules.

**Architecture:** Caddy handles HTTPS and proxies to Gunicorn. Django/WhiteNoise serve application and static content; Django authorizes private image requests. PostgreSQL and media use persistent volumes, and a systemd timer runs the existing digest management command.

**Tech Stack:** Master-plan stack plus WhiteNoise for collected static assets and a small Ubuntu 24.04 CPU VM on Google Compute Engine. The app remains portable to another Docker-capable VM.

## Global Constraints

Inherit the [master plan](2026-09-14-personal-website.md). Deploy the actual
website application, not the old Electron/C++/GPU experiment. Public endpoints
must enforce the same ownership and archive checks as development. Preserve
uploaded files across application replacement. Never package existing `.env`,
`.env.save`, build/results directories, or private backups into the image.
Planning does not create a VM, change DNS, configure accounts, or send email.

## Task D1: Production packaging, checks, and repeatable backups

**Files:** Create `Dockerfile`, `.dockerignore`, `compose.prod.yaml`,
`deploy/Caddyfile`, `deploy/entrypoint.sh`, `deploy/backup.sh`,
`deploy/restore-test.sh`, `deploy/review-digest.service`,
`deploy/review-digest.timer`, `tests/test_production_settings.py`,
`tests/test_deployment.py`, `.github/workflows/website.yml`.
Modify settings, requirements locks, README, and `.env.example`.

**Interfaces:** The image starts with `gunicorn website.wsgi:application --bind
0.0.0.0:8000 --workers 2 --threads 2 --timeout 60`. `/healthz` checks database
availability and returns no secrets. Startup migrations are a separate one-off
command, not a race between workers. Static collection happens at build time.
Media directory is `/app/media`, writable by application UID 10001.

- [ ] **Write deployment behavior tests before production configuration.**

  ```python
  import pytest

  @pytest.mark.django_db
  def test_repository_files_are_not_public(client):
      for path in ("/.env", "/.env.save", "/prompts/website.md", "/build/"):
          assert client.get(path).status_code == 404
  ```

  Test `DEBUG=False` host validation, secure session/CSRF cookies, missing
  required secrets refusing startup, Google callback route registration,
  authenticated note/asset cache headers, and absence of public media fallback.
  HTTP integration checks must exercise the built image, not just Django's
  development server. Run new tests red before adding deployment configuration.

- [ ] **Build a minimal image and Compose runtime.** Add WhiteNoise immediately
  after Django SecurityMiddleware, use CompressedManifestStaticFilesStorage,
  and serve only collected static files through it. Private media stays behind
  the application. Follow [WhiteNoise's Django setup](https://whitenoise.readthedocs.io/en/stable/django.html).

  The Docker build uses a Node build stage for `npm ci` and `npm run vendor`,
  then copies only the resulting licensed renderer bundles into the Python
  runtime. Explicitly COPY `manage.py`, `website/`, `study/`, required `js/`
  visual modules, and locked requirements; do not COPY the repository root.
  Create `/app/media` as UID 10001 before switching USER. Install requirements
  with `pip install --require-hashes`. Use a dedicated build-settings mode for
  collectstatic with no OAuth/database connections or production credentials;
  runtime production mode still rejects missing secrets. Pin base image digests.

  Compose service contract:

  | Service | Exposed ports | Persistent storage | Startup |
  | --- | --- | --- | --- |
  | db | none on host | PostgreSQL data | pg_isready health check |
  | web | 8000 on internal network only | private media | starts after healthy db |
  | proxy | 80 and 443 | Caddy certificate data/config | reverse proxy to web |

  Use `env_file: .env.production` for runtime secrets and no checked-in values.
  Set the application's `DB_HOST=db` and map PostgreSQL's own database/user/password
  variables explicitly in the private environment file. Use
  `depends_on: db: condition: service_healthy`, as documented by
  [Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/).

  ```caddyfile
  dhelmy.stream {
      request_body {
          max_size 64MB
      }
      reverse_proxy web:8000
  }
  ```

  Django production settings: `DEBUG=False`, `ALLOWED_HOSTS=["dhelmy.stream"]`,
  `CSRF_TRUSTED_ORIGINS=["https://dhelmy.stream"]`, secure HTTP-only session
  cookies, secure CSRF cookie, and trusted proxy HTTPS indication from Caddy.
  Only Caddy can reach the web service from outside its private network; overwrite
  forwarded scheme headers at the proxy. Use Django templates/static tags, not
  hardcoded unhashed asset filenames. Check the deployment settings with
  `python manage.py check --deploy`.
  [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)

- [ ] **Add a scheduler and backup/restore commands.** Deploy under
  `/opt/dhelmy-stream`; the host service executes the existing management command
  in the running web container:

  ```ini
  # deploy/review-digest.service
  [Unit]
  Description=Prepare and deliver the owner's study review digest
  After=docker.service
  Requires=docker.service

  [Service]
  Type=oneshot
  WorkingDirectory=/opt/dhelmy-stream
  ExecStart=/usr/bin/docker compose -f compose.prod.yaml exec -T web python manage.py send_review_digest
  ```

  ```ini
  # deploy/review-digest.timer
  [Unit]
  Description=Check study digest eligibility every five minutes

  [Timer]
  OnCalendar=*:0/5
  Persistent=true
  Unit=review-digest.service

  [Install]
  WantedBy=timers.target
  ```

  R3 determines the 09:00 Singapore eligibility; the host timezone does not.
  Add `deploy/backup.sh DESTINATION` with `set -euo pipefail` and an exit trap
  that restores services after failure. It stops the digest timer and web service,
  leaves PostgreSQL running, creates `pg_dump -Fc`, archives the private media
  volume through a read-only helper container, and saves schema migration state
  plus image/release identifiers. Restart web and timer afterward. This brief
  maintenance window gives a consistent database/media pair. Store backups
  outside the repo with owner-only permissions and copy an encrypted backup off
  the VM; document the actual destination configured during deployment.

  Implement `deploy/restore-test.sh BACKUP_DIRECTORY` using a distinct Compose
  project named `study-restore` and new volumes, without starting the public proxy
  or email timer. Restore the database and media, start the restored application
  on loopback port 18000, and check article/asset access, note ownership, and
  schedules. The script refuses to operate on the production Compose project.
  Verify that restored archived images still return 404 anonymously.

- [ ] **Verify and commit packaging.** CI starts PostgreSQL 17, installs locked
  dependencies, runs all Python tests, runs Chromium browser flows, checks
  migrations, vendors assets, and builds the production image. It uses dummy
  OAuth configuration and fake delivery only; no cloud or email secrets.

  ```bash
  pytest -q
  python manage.py makemigrations --check --dry-run
  python manage.py check --deploy
  npm ci
  npm run vendor
  docker build -t dhelmy-stream:local .
  ```

  Supply explicit test-production settings to `check --deploy`; never print
  environment files in CI output. Run backup/restore against disposable seeded
  volumes and test restarts preserve notes/images and daily slot identities.
  Run `systemd-analyze verify` on both timer units. Commit
  `build: package study website and verify backup recovery`.

## Task D2: Domain, Google login, email setup, and deployment runbook

**Files:** Create `docs/deployment.md`, `deploy/.env.production.example`,
`deploy/smoke.sh`. Modify README with deployment and recovery links.

**Interfaces:** Configuration requires the owner's selected Google Cloud project,
an SSH-capable deployment identity, control of dhelmy.stream DNS, a Google OAuth
web client, and a Resend sending-domain credential. These external values are
runtime inputs; do not guess credentials or reuse keys from the old experiment.
The runbook uses a new CPU-only VM and a pinned application release.

- [ ] **Write the concrete operator runbook.** Include these ordered actions:

  1. In the owner's chosen Google Cloud project, create one Ubuntu 24.04 x86_64
     CPU instance, initially 2 vCPU/4 GiB RAM with a 30 GiB persistent disk, and
     reserve a static external IPv4 address for it. No GPU. Restrict SSH to the
     owner's access method; allow HTTP/HTTPS to Caddy. Describe expected ongoing
     VM/disk/network charges without inventing a price quote. Link
     [Compute Engine instance creation](https://docs.cloud.google.com/compute/docs/instances/create-start-instance).
  2. Install Docker Engine and Compose using
     [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
     Place the pinned checkout in `/opt/dhelmy-stream` and create the runtime
     environment file with mode 0600 from the documented variable list.
  3. Point the apex `dhelmy.stream` A record to the reserved address. Add an AAAA
     record only if the VM actually has working IPv6. Verify DNS resolution and
     reachability of ports 80/443 before starting public HTTPS. The registrar's
     UI varies; identify A-record host `@`, address, and TTL explicitly.
  4. Configure a Google OAuth Web application and the consent-screen domain.
     Use `https://dhelmy.stream/accounts/google/login/callback/` as the production
     redirect and `http://127.0.0.1:8000/accounts/google/login/callback/` for local
     development. Use the matching origins. Configure public audience for reader
     sign-in; a testing-only OAuth audience is not the final public launch state.
     Request profile/email only. Store client credentials server-side and
     explain that Google sign-in supplies identity, not access to mail or Drive.
     Follow [allauth's Google registration guide](https://docs.allauth.org/en/latest/socialaccount/providers/google.html).
  5. Verify a sending domain in Resend using its generated DNS records, set
     `RESEND_API_KEY`, and set `REVIEW_FROM_EMAIL` to the verified sender.
     The only recipient is `dhelmy990@gmail.com`. Never make that key available
     to the browser. Link [Resend domain verification](https://resend.com/docs/dashboard/domains/introduction).
  6. Build the pinned image, start PostgreSQL, run migrations once, start web and
     Caddy, and check the HTTPS health route. Caddy obtains/renews the certificate
     when the DNS/port conditions are met.
     [Caddy HTTPS](https://caddyserver.com/docs/automatic-https)

     ```bash
     docker compose -f compose.prod.yaml build web
     docker compose -f compose.prod.yaml up -d db
     docker compose -f compose.prod.yaml run --rm web python manage.py migrate
     docker compose -f compose.prod.yaml up -d web proxy
     curl --fail https://dhelmy.stream/healthz
     ```

  7. Sign in as Diego and verify the publisher binding and reader/admin toggle.
     Publish an intentional first article through the supported UI. Check
     Markdown/image rendering, source labels, Galaxy navigation, and private notes.
     Use local fake accounts for cross-user automated checks; do not ask for
     access to another real reader's account or annotations.
  8. Run the digest command with `--dry-run`, verify the selected owner/date,
     install the timer units, and enable the timer only after email configuration
     is ready. Check `systemctl list-timers` and service logs. Live delivery is
     a separate explicit deployment action; automated checks remain fake.
  9. Configure the actual off-VM encrypted backup destination, take a first
     backup, and run the isolated restore check. Record where the encryption key
     is retained outside the VM. Document maintenance downtime and restoration.

  Explain all required environment variables by name and purpose:
  `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `RESEND_API_KEY`, `REVIEW_FROM_EMAIL`, `PUBLIC_BASE_URL`, and `MEDIA_ROOT`.
  Defaults in checked-in examples must be nonsecret and unsuitable for a public
  deployment. Production settings must fail with a named missing variable.

- [ ] **Implement a read-only smoke script and recovery documentation.**

  ```bash
  # Core of deploy/smoke.sh; SITE_BASE is its validated positional argument.
  curl --fail --silent --show-error "$SITE_BASE/healthz"
  curl --fail --silent --show-error "$SITE_BASE/" -o /dev/null
  curl --fail --silent --show-error "$SITE_BASE/galaxy" -o /dev/null
  ```

  Add assertions for HTTPS redirect, certificate validity, real static asset
  responses, public graph JSON, nonexistent route 404, and private-file 404.
  For authenticated publication, note saving, archive access, and scheduled
  reviews, list the corresponding already-passing browser tests plus a short
  owner manual smoke flow. The public smoke script creates no articles and
  sends no email.

  Document failures with concrete next actions: OAuth redirect mismatch,
  unverified sending domain, failed DNS/certificate issuance, database unavailable,
  upload disk full, failed note save, and digest status unknown after the retry
  window. Preserve files and show retry; do not reset databases as troubleshooting.
  Rollback means redeploying the prior image only when its schema is compatible;
  otherwise restore the matched database/media backup during maintenance. Do
  not run production database downgrade migrations blindly.

- [ ] **Verify and commit the runbook.** Check links, shell syntax with
  `bash -n deploy/*.sh`, compose configuration without expanding secrets into
  output, timer validation, and smoke behavior on the disposable deployment.
  Capture the verified commands and expected statuses in `docs/deployment.md`.
  Commit `docs: document dhelmy.stream deployment and recovery`.

## Execution boundary and exit criteria

This plan prepares configuration and a runnable deployment procedure. Creating
paid infrastructure, changing the real domain, obtaining OAuth/email secrets,
and launching operational email happen during a separately authorized deployment
step. Preparing code and running local/disposable checks does not require those
accounts. Report “ready to deploy” separately from “deployed at dhelmy.stream.”

The deployed website is complete after public HTTPS, owner Google login,
intentional publishing, private notes, study scheduling, owner email, and a
backup restoration have all been verified at the intended domain.
