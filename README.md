# dhelmy.stream

The public website is a Django study log with an integrated Babel galaxy at
`/galaxy`. Published Markdown articles, their directed reading relationships,
and private image assets are stored in PostgreSQL and the local media directory.

## Local setup

Use Python 3.13 and start the dedicated PostgreSQL 17 service on host port 5433:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements-dev.txt
docker compose up -d --wait db
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
galaxy or timeline, upload a UTF-8 Markdown file, assign logical paths to image
uploads, preview the exact rendered article, and publish it. Existing articles
use the same form at their Edit article link; an edit can retain its current
Markdown and images, while a replacement image wins at the same logical path.
Titles can change without changing the stable slug or original publication date.
The owner can archive an article from its page, which returns to the timeline
and removes the article from public reading and the galaxy.

The application serves uploaded images from private storage and does not need a
live Google account or OAuth configuration to run tests. Browser tests create
ordinary Django test sessions and use disposable test media.

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
