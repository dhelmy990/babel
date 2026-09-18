# dhelmy.stream

For production deployment, recovery, DNS, OAuth, email, and encrypted off-VM backup procedures, see [the operator runbook](docs/deployment.md).

The public website is a Django study log with an integrated Babel galaxy at
`/galaxy`. Published Markdown articles, their directed reading relationships,
and private image assets are stored in PostgreSQL and the local media directory.

## Local setup

Use Python 3.13 and start the dedicated PostgreSQL 17 service on host port 5433:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements-dev.txt
docker compose --env-file /dev/null up -d --wait db
python manage.py migrate
npm ci
npm run vendor
.venv/bin/python -m playwright install chromium
npm start
```

Open http://127.0.0.1:8000/. `npm start` calls the virtual environment's Python
directly. `npm run vendor` copies the browser graph dependencies into the
versioned static-vendor layout. To open the preserved Electron app, use
`npm run start:desktop`.

On a new Linux development machine, install the Chromium system libraries first
with `.venv/bin/python -m playwright install-deps chromium` when Playwright
reports missing dependencies.

Development defaults use the `study_dev` database and credentials. Settings read
only process environment variables and do not load a local `.env` file. Copy
`.env.example` when explicit values are useful. With `DJANGO_DEBUG=false`, the
secret key and every database setting are required.

## Tests and dependency locks

```bash
source .venv/bin/activate
pytest tests/test_pages.py -q
pytest -q
python manage.py check
```

The checked-in requirements files contain exact versions and hashes. Regenerate
them with pip-tools-compatible `uv pip compile`:

```bash
uv pip compile --generate-hashes --output-file requirements.txt requirements.in
uv pip compile --generate-hashes --output-file requirements-dev.txt requirements-dev.in
```

## Publishing study notes

The verified publisher can switch to Admin mode, open **New article** from the
galaxy or timeline, and write directly in the visual article editor. The pill
toolbar formats headings, emphasis, lists, quotes, code, and links. Select
H1, H2, or H3 with the heading buttons. While typing in the visual body,
**Ctrl +** grows a paragraph through H3 → H2 → H1; **Ctrl −** shrinks it back.
Switch to **Markdown** to edit or paste the source. Paste PNG, JPEG, or WebP images into the text,
or drop them at the cursor; images appear immediately and
upload when the article is saved (10 MB per image, up to 20 images per article).
Use **Preview** to see the exact server rendering before publishing. Preview,
Save/Publish, and status stay in the bottom action bar while you scroll.
Tables have bordered cells and scroll horizontally inside previews and articles.
On the reading page, **On this page** lists body sections in a sticky left outline,
highlights the current section, and jumps to headings. On small screens, open the
compact outline above the article. The editor and preview do not show this outline.

Existing articles open with their current text and images at **Edit article**.
Save explicitly to publish edits; failed saves retain the
draft and images in the open tab, and leaving with unsaved changes shows a warning.
Titles can change without changing the stable slug or original publication date.
The owner can archive an article from its page, which returns to the timeline
and removes the article from public reading and the galaxy.

The application serves uploaded images from private storage and does not need a
live Google account or OAuth configuration to run tests. Browser tests create
ordinary Django test sessions and use disposable test media.

## Reading and study calendar

Signed-in readers complete a review by continuing to scroll beyond the article's
end. A wide double chevron leads into a rising panel; only the full pull records
the reading. You can also focus the chevron button and press Enter. Confirmation
glows in the article's Babel colour and shows the days until the next scheduled
read. Reduced-motion preferences suppress movement and glow animation. Failed
requests show a Retry button and do not claim a completed review.

**Study calendar** shows your scheduled article due dates by month, with a list
for each selected day and a separate overdue list. It uses your stored scheduling
timezone and does not create or advance review days. **Review today** still
selects up to three articles; calendar due dates are not a promise of selection.

## Owner review digest

The digest goes only to `dhelmy990@gmail.com`, and only while that user has the
verified publisher email and matching Google identity binding. It contains up to
three outstanding, published articles from the same active daily selection as
**Your reviews**. Private notes never enter the message. Browser timezone changes
can affect the review selection; the email's daily identity always uses the
Singapore calendar date.

Run the command every five minutes through the deployment scheduler:

```bash
.venv/bin/python manage.py send_review_digest
```

It sends at or after 09:00 Singapore time and catches up only within the current
Singapore day. It never sends an old backlog. A day with no remaining reviews is
recorded as skipped and is not reconsidered automatically.

Development defaults to console delivery, which prints the message without
contacting an email provider. Production (`DJANGO_DEBUG=false`) defaults to
Resend and requires a nonempty `RESEND_API_KEY`. Set the trusted public site
origin with `PUBLIC_BASE_URL=https://dhelmy.stream`; the default production origin
is that address. `REVIEW_FROM_EMAIL` defaults to
`Study notes <reviews@dhelmy.stream>` and must be an approved sender at Resend.
Neither setting controls the recipient. Keep the provider key only in the server
environment. `.env.example` documents the names; Django does not load `.env`.

An explicit server-side `REVIEW_EMAIL_DELIVERY=console` override is available for
disposable verification and isolated restores, including with production security
settings enabled. It suppresses real email and permits an empty Resend key. It
must be deliberately removed or changed to `resend` for actual delivery. This is
not a browser setting.

To inspect disposable test data without attempting delivery:

```bash
REVIEW_EMAIL_DELIVERY=console .venv/bin/python manage.py send_review_digest --dry-run
```

Dry-run shows the selected owner, Singapore date, titles, and URLs. It may create
or freeze the owner's `ReviewDay`, just as opening the review interface does, but
it creates no `Digest`, delivery attempt, or provider request.

At the first delivery claim, the sender, recipient, subject, article titles,
URLs, and text/HTML are frozen. Every retry uses the identical canonical JSON
payload and `study-review/{user_id}/{Singapore_date}` idempotency key. Requests
have a ten-second HTTP timeout; workers claim a two-minute lease. Retries stop
at the earlier of 23 hours after the first attempt or the end of the Singapore
day. Resend documents a 24-hour idempotency window:
[Resend idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys).
This provides safe bounded retries after a lost response; it does not promise
exactly-once delivery beyond the provider's retention window.

Confirmed acceptance becomes `sent`. Permanent malformed/authentication
rejections, including an idempotency payload mismatch, become terminal `failed`.
Transient provider/network failures can retry within the window. An attempted
delivery whose acceptance cannot be confirmed by the deadline becomes terminal
`unknown`; it is not resent automatically. An expired digest that was never
attempted becomes `skipped`. The command also retires expired unresolved records
without contacting the provider. A matching late response may confirm acceptance
only while its claim has not been replaced or made terminal. Logs store safe
error categories rather than raw provider bodies or credentials.

## Production image and local recovery verification

The production image runs as UID/GID 10001, stores private uploads in `/app/media`,
serves only collected static files through WhiteNoise, and starts Gunicorn with
2 workers, 2 threads and a 60-second timeout. Its default settings module is
`website.production_settings`, which refuses debug mode and missing or empty
secret, database, and Google configuration. `website.build_settings` is only for
static collection; it uses a dummy database and must never serve requests.
The runtime lock includes allauth's social-account dependencies.

Prepare a private `.env.production` (mode 0600) from the variable descriptions in
`.env.example`. Set `DJANGO_SECRET_KEY`, all `DB_*` values, Google client ID/secret,
and matching `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`. Set production
URLs, sender, and `RESEND_API_KEY` before using the `resend` backend. Disposable
verification must explicitly use `REVIEW_EMAIL_DELIVERY=console` and dummy OAuth.
Django does not load environment files itself. Always pass Compose an explicit
`--env-file`: its service `env_file` does not supply `${...}` interpolation.

```bash
docker compose --env-file .env.production -f compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.prod.yaml build --build-arg STUDY_RELEASE="$(git rev-parse HEAD)" web
docker compose --env-file .env.production -f compose.prod.yaml up -d --wait db
docker compose --env-file .env.production -f compose.prod.yaml run --rm web python manage.py migrate
docker compose --env-file .env.production -f compose.prod.yaml up -d web proxy
```

Migrations are an explicit one-off step. The production Compose file exposes
only Caddy's ports 80/443; it never merges development database port mappings.
Caddy overwrites forwarded scheme headers and limits request bodies to 64 MB.
The pinned base images support CPU-only amd64. The image build allowlist excludes
secrets, private media, documents, tests, and the Electron/experiment application.
Node vendors the three licensed renderer bundles with `npm ci --omit=dev`.

Backups require the running database, an existing web container, the matching
release image, and installed digest units. The backup stops the timer, an active
digest service, and web before creating a matched database/media pair. It restores
prior running states on success or failure; this is a maintenance window.

```bash
deploy/backup.sh /var/backups/dhelmy-stream/2026-09-14
deploy/restore-test.sh /var/backups/dhelmy-stream/2026-09-14
```

Use a new backup destination outside the repository. Artifacts and checksums have
owner-only permissions; `COMPLETE` is written last. Preserve the exact recorded
application and PostgreSQL images with the release: restore refuses unavailable
image IDs and never silently substitutes newer images or runs downgrade migrations.
Copy each completed backup to an encrypted off-VM destination and retain its key
outside the VM. Choosing that destination and configuring real infrastructure are
separate deployment steps.

Restore creates fresh `study-restore` resources, refuses any existing project
resources or production target, and runs console delivery without a Resend key.
It compares restored records and image hashes, then checks real HTTP article,
asset and note permissions for anonymous and local session users. Schedule and
frozen slot identities are included in the record hashes. Production security
stays enabled; the helper supplies `Host: dhelmy.stream` and
`X-Forwarded-Proto: https` over the isolated loopback transport. The script prints
the temporary dummy Compose file and exact cleanup command after success. Keep
that file until removing the disposable containers/volumes; then delete its
containing temporary directory. Never reuse its settings for public deployment.

The scheduler units target `/opt/dhelmy-stream` with an apt-installed Docker
service. `/usr/bin/env docker` resolves the executable. Local snap Docker lacks
`docker.service`; local unit validation uses an isolated `SYSTEMD_UNIT_PATH`
containing a dummy dependency and does not install/start any host unit.

Local verification uses real disposable containers and a controlled systemctl
shim. `STUDY_ENV_FILE`, `STUDY_COMPOSE_FILE`, `STUDY_COMPOSE_OVERRIDE`,
`STUDY_PROJECT`, and `STUDY_SYSTEMCTL` are trusted operator/test overrides for
backup invocation. Default production invocation needs none of them.

```bash
source .venv/bin/activate
python -m pytest -q
python manage.py makemigrations --check --dry-run
docker build --build-arg STUDY_RELEASE="$(git rev-parse HEAD)" -t study-d1:verification .
STUDY_DEPLOYMENT_IMAGE=study-d1:verification python -m pytest tests/test_deployment.py -q
for script in deploy/*.sh; do bash -n "$script"; done
```

The opt-in deployment tests build no images themselves. They exercise the selected
image's HTTP security, secure cookies, hashed ES-module imports and single
initialization in Chromium, followed by real backup, isolated restore, restart
persistence, and failure-state recovery. They remove only resources they create.
CI runs the full Python/browser suite, locked vendor build, migration checks,
production deployment checks, image build, and this disposable recovery suite.
