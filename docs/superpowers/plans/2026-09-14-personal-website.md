# Personal Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved dhelmy.stream study website while preserving layout B and the original Babel rendering.

**Architecture:** One Django application serves HTML, authenticated JSON endpoints, and permission-checked image responses. PostgreSQL owns articles, graph edges, private notes, and review schedules; images live on a persistent private filesystem volume. A scheduled management command sends the owner's daily digest through Resend.

**Tech Stack:** Python 3.13, Django 5.2 LTS, django-allauth with Google, PostgreSQL 17, markdown-it-py, Pillow, Gunicorn, WhiteNoise, vanilla JavaScript, the existing Three.js/3d-force-graph renderer, Playwright, Docker Compose, and Caddy 2. Use supported patch releases within these lines and commit exact dependency locks and image digests during implementation.

## Global Constraints

The [approved specification](../specs/2026-09-14-personal-website-design.md) is authoritative. Every slice inherits it and these constraints:

- Preserve layout B from the current `index.html`; no frontend framework rewrite.
- Subtitle: `Diego Helmy's study notes`. Title: `dhelmy.stream`.
- Put the Galaxy button directly beneath the title.
- Introduction: `A compilation of my various learnings about C++, finance, and model serving and model development.`
- Icon links: `https://github.com/dhelmy990` and `https://www.linkedin.com/in/dhelmy990/`.
- `/`, `/galaxy`, and `/{article-slug}` are public reading routes.
- Exactly one article per Babel. Directed prerequisite edges only; no cycles or automatic transitive-edge pruning.
- Google sign-in; verified `dhelmy990@gmail.com` is the sole publishing account. Publishing mode grants no access to another reader's notes.
- Markdown plus images; no AI integration, AI API keys, rich-text author editor, or public empty Babels.
- Private sticky notes/text boxes, manually positioned, recoverable through the sidebar. No freehand drawing or paragraph anchoring.
- Read completion happens at “Articles to read next.” Review intervals are 1, 2, 4, 8 days from completion; at most three distinct due articles per local day.
- Keep completed daily slots; never refill a fourth article. Early rereads do not change schedules.
- Owner digest at 09:00 `Asia/Singapore`; no email for other users.
- Archive one stored article; retain notes and grant existing note owners access. Archive content and assets are not publicly readable.
- Preserve stable slugs and publication dates across edits.
- No recommender, GPU, embedding, benchmark, or training infrastructure.
- Tests must never use live Google sign-in or deliver operational email. Manual deployment verification uses the owner's account only.

## Starting checkout and preservation

Branch `personal-website` starts at `f11295f`. Spec commit: `6919266`.
The approved UI changes to `index.html`, `README.md`, and untracked `galaxy/`
are currently local changes, not part of that commit. Preserve them before
starting an isolated checkout; do not assume a new worktree contains them.
The `.env`, `.env.save`, `.gitignore`, `build/`, `results/`, `monlith.pdf`, and
ignored `prompts/` are existing user files. Do not read credentials, delete these
files, import old infrastructure, or use `git add .`.

Implementation should first commit only the approved UI files after reviewing
their diff. Work in this branch by default. Plan-writing itself changes only
documentation and does not install packages, provision resources, or send mail.

## Why this stack

Django provides the session, CSRF, template, migration, and transaction tools
this small site needs. Python 3.13 is supported by Django 5.2, which is an LTS
release. [Django release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/)

PostgreSQL row locks let us serialize a reader's daily selection and enforce
the three-item limit across tabs and retries. SQLite does not provide Django's
`select_for_update` behavior. Concurrency tests therefore use PostgreSQL, not an
SQLite substitute. [Django querysets](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)

Google login is delegated to allauth with session cookies and POST login
initiation. Keep OAuth credentials server-side and configure the Google
provider once through settings. [Google provider](https://docs.allauth.org/en/latest/socialaccount/providers/google.html),
[provider configuration](https://docs.allauth.org/en/dev/socialaccount/provider_configuration.html)

Markdown rendering disables raw HTML using `js-default`, with explicit image
and link URL validation. [markdown-it-py security](https://markdown-it-py.readthedocs.io/en/latest/security.html)

Caddy terminates HTTPS in front of Gunicorn on one CPU VM. This deployment
choice needs no separate frontend service, message broker, or distributed job
queue. [Caddy reverse proxy](https://caddyserver.com/docs/quick-starts/reverse-proxy)

Resend provides an idempotency key for email retries, retained for 24 hours.
The delivery task bounds retries to that window instead of claiming unlimited
exactly-once delivery. It requires a server-side delivery credential and a
verified sending domain; neither is an AI credential or a reader requirement.
[Resend idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys),
[domain verification](https://resend.com/docs/dashboard/domains/introduction)

## Ordered slice plans

| Order | Plan | Working deliverable |
| --- | --- | --- |
| 1 | [Publishing and public reading](2026-09-14-website-01-publishing.md) | Real articles/images, Google login, owner publishing, directed galaxy, archive state |
| 2 | [Private reader notes](2026-09-14-website-02-notes.md) | Saved private notes, manual placement, recovery, archive grants |
| 3 | [Study reviews and email](2026-09-14-website-03-reviews.md) | Durable daily queues, completion scheduling, owner's digest |
| 4 | [Deployment](2026-09-14-website-04-deployment.md) | Reproducible production image, backups, domain/OAuth/email runbook |

Execute in order. Each task ends with focused checks and one scoped commit.
No implementation is included in this documentation commit.

## File boundaries

| Path | Responsibility |
| --- | --- |
| `manage.py`, `website/settings.py`, `website/urls.py`, `website/wsgi.py` | Django boot, environment configuration, HTTP routing |
| `study/models/accounts.py` | Reader profile and verified publishing identity |
| `study/models/content.py` | Article, image asset, graph lock/edges, archival access |
| `study/models/notes.py` | Reader note data and optimistic update versions |
| `study/models/reviews.py` | Review schedule, frozen daily lists, digest attempts |
| `study/models/__init__.py` | Explicit model exports for Django discovery |
| `study/services/identity.py`, `content.py`, `graph.py`, `notes.py`, `reviews.py`, `digest.py` | Transactions and authorization for each domain |
| `study/markdown.py`, `study/storage.py` | Markdown/image preparation and durable private asset writes |
| `study/views/` | HTML/JSON adaptation; services own business rules |
| `study/templates/study/` | Approved homepage, article, galaxy, upload form, error pages |
| `study/static/study/site.css`, `site.js`, `notes.js`, `reviews.js`, `publishing.js` | Extracted approved appearance and progressive enhancement |
| `js/config.js`, `rendering.js`, `animation.js`, `level-circles.js` | Reused Babel visual modules |
| `js/website-graph.js`, `js/graph-utils.js` | Website graph initialization and strict directed algorithms |
| `tests/`, `tests/e2e/` | Django/transaction tests and browser flows |
| `deploy/`, `compose.yaml`, `Dockerfile`, `docs/deployment.md` | Runtime, backup/restore, and operator instructions |

The website does not load `js/editor.js`, the Electron preload, or legacy
localStorage persistence. Leave existing desktop files available until all
website acceptance checks pass; later removal is a separate cleanup decision.

## Shared HTTP contracts

Use Django CSRF protection for all session-authenticated writes. JSON errors:
`{"error":{"code":"cycle","message":"This connection creates a prerequisite loop."}}`.
Return 400 for invalid input, 401 for unsigned JSON writes, 403 for publishing
denial, 404 for inaccessible notes/articles/assets, and 409 for a stale revision
or graph conflict. Never put private responses in a shared cache. Internal UUIDs
are immutable; URL slugs are stable human-readable article identifiers.

| Method/path | Response or request |
| --- | --- |
| `GET /` | Server-rendered published timeline; real empty state when no articles |
| `GET /galaxy` | Galaxy shell and published article graph |
| `GET /{slug}` | Full article or permitted archived copy; unknown paths 404 |
| `GET /assets/{asset_uuid}` | FileResponse after the same article visibility check |
| `GET /api/session` | `{authenticated, can_publish, mode, timezone}`; never provider tokens |
| `POST /api/mode` | `{mode:"reader"|"admin"}`; owner only for admin mode |
| `POST /api/timezone` | `{timezone: IANA_name}`; validated server-side |
| `GET /api/graph` | `{nodes:[{id,slug,title,excerpt,color}],edges:[{source,target}]}` |
| `POST /api/articles/preview` | Multipart title/color/markdown/images; returns rendered preview, saves no Article |
| `POST /api/articles` | Same multipart fields plus `submission_id`; returns article id/slug/revision |
| `POST /api/articles/{id}` | Multipart revision, Markdown, title/color, replacement images; stable slug/date |
| `POST /api/articles/{id}/archive` | Idempotent archive; suspends reviews and grants existing note owners access |
| `POST /api/edges` | `{source,target}`; atomic DAG validation and insertion |
| `DELETE /api/edges/{source}/{target}` | Removes only the selected edge |
| `GET /api/articles/{id}/notes` | Current reader's notes only, including on authorized archives |
| `POST /api/articles/{id}/notes` | `{id,kind,text,x,y}`; client UUID makes creation retries idempotent |
| `PATCH /api/notes/{id}` | `{version,text,x,y}`; returns incremented version |
| `DELETE /api/notes/{id}` | Current reader only |
| `POST /api/reviews/today` | Materializes/reads the current daily list under a reader lock |
| `GET /api/articles/{id}/reading-context` | Signed completion token for the current schedule generation |
| `POST /api/articles/{id}/complete` | `{token}`; first read or selected due review, idempotent by generation |

Use allauth's `/accounts/` endpoints for Google sign-in/callback and POST logout.
Register static application routes before the final slug route. Reserve
`galaxy`, `api`, `accounts`, `assets`, `static`, `healthz`, and `robots.txt`.

## Shared schema decisions

All timestamps are aware UTC values; local dates use `zoneinfo.ZoneInfo`.
No model exposing another user's notes is registered in a publishing admin.

| Record | Required fields and invariants |
| --- | --- |
| ReaderProfile | unique user FK, timezone (default UTC), pending_timezone nullable, active_day nullable FK added in R1; row is the serialization lock for reader operations |
| PublisherIdentity | singleton pk=1, unique Google subject, user FK; bound only from verified owner Google login |
| Article | UUID pk, unique stable slug, title, color, markdown, rendered_html, excerpt, sources JSON list of label/url pairs, published_at, updated_at, archived_at nullable, revision>=1, unique submission_id, submission_hash |
| Asset | UUID pk, article FK, logical relative name, storage key, media type, SHA256; each write uses an immutable key |
| GraphState | singleton pk=1 inserted by migration; locks publication/edge changes/archive operations |
| Edge | source/target Article FKs, unique pair, DB self-edge check; cycles rejected transactionally |
| ArchiveAccess | user/article unique pair, granted_at; persists if the user later deletes all notes |
| Note | client UUID pk, user/article FKs, kind sticky/text, text, x/y either both null (sidebar-only) or finite nonnegative CSS pixels, version>=1, created_at, updated_at |
| ReviewSchedule | user/article unique pair, interval_days numeric(100,0), next_due_date nullable, last_completed_at, generation>=1, suspended flag; index user/suspended/next_due_date |
| ReviewDay | user/local_date unique pair, timezone snapshot, created_at, next_boundary_at UTC timestamp |
| ReviewSlot | day/ordinal unique with ordinal 0..2, day/article unique, schedule FK, completed_at nullable, cancelled_at nullable |
| Digest | unique user/day, payload JSON, idempotency key, status, first_attempt_at, lease_until, provider_id, retry_until, last_error |

Reading-context token payload is `{user_id,article_id,generation,issued_at}`;
generation 0 means that no ReviewSchedule exists yet. `issued_at` is supplied
by the service clock and included in the signed payload. It is not a client time.

Large doubling intervals retain their exact decimal value. When the computed
date exceeds year 9999, store `next_due_date=NULL` (not due within representable
calendar time), rather than wrapping or silently resetting the schedule.

## Cross-slice transaction rules

- Graph writes lock `GraphState(pk=1)`, then affected Article rows in UUID order.
- Notes create/update and archive lock the Article first, so archive grants and
  note creation cannot race. Updates additionally compare Note.version.
- Review operations lock ReaderProfile first, then Article rows in UUID order,
  then review records. Content transactions never acquire reader-profile locks.
  Materialize a day before the separate completion transaction; never nest
  multi-article daily selection beneath an already-held target-article lock.
- All mutation transactions use real PostgreSQL in tests. Retried HTTP requests
  must not publish duplicates, increment review generations twice, or exceed
  three daily slots.
- Files are written to unique private keys before publication commits. On a
  database failure remove only new keys from that attempt; interrupted attempts
  leave unreachable files cleaned after a 24-hour grace period.

## Specification coverage

| Requirement | Task owner |
| --- | --- |
| Layout B, real routes, visual preservation | P1, P3, P5 |
| Google identity and owner/reader mode | P2 |
| Markdown, images, labels, publication/update | P3, P5 |
| Directed prerequisites and immediate neighbors | P4 |
| Archive state and protected images | P3, P4, N1 |
| Private note isolation, drag/recovery, mobile drawer | N1, N2 |
| Read detection, schedule intervals, daily cap, retries | R1, R2 |
| Owner-only 09:00 digest | R3 |
| Domain, OAuth secrets, storage, email configuration | D1, D2 |
| Failure recovery and production verification | Each owning task plus D1/D2 |

## Completion gate

Run each slice's focused checks, then the combined Django and Playwright suites
once after integration. Verify migration consistency, the production image,
and backup restoration into a disposable database. Report deployment separately
from local completion. Implementation is complete only when real content and
reader state have replaced sample data and every coverage row above passes.
