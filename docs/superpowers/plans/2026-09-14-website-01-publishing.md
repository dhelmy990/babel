# Publishing and Public Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace sample articles with published Markdown and images, preserve layout B, and connect a strictly directed Babel galaxy to article pages.

**Architecture:** Django renders the approved shell; content and graph services perform authorized transactional writes. Google authentication establishes reader identity and the sole publishing account.

**Tech Stack:** The [master plan](2026-09-14-personal-website.md) defines the stack, shared schema, HTTP contracts, and file boundaries.

## Global Constraints

Inherit every master-plan constraint and the approved specification. Keep the
existing HTML/CSS appearance, exact introduction, social links, and relocated
Galaxy button. Keep archives out of every public content query. Use PostgreSQL
for transaction tests. No live OAuth, public uploads, or deployment in tests.

## Task P1: Serve the approved shell through Django

**Files:** Create `manage.py`, `website/__init__.py`, `settings.py`, `urls.py`,
`wsgi.py`; `study/__init__.py`, `apps.py`, `models/__init__.py`, `views/pages.py`;
`study/templates/study/base.html`, `home.html`, `404.html`, `500.html`;
`study/static/study/site.css`, `site.js`; `requirements.in`,
`requirements-dev.in`, their `.txt` locks, `pytest.ini`, `compose.yaml`,
`.env.example`, `tests/conftest.py`, `tests/test_pages.py`.
Modify `package.json`, `package-lock.json`, `.gitignore`, and `README.md`.

**Interfaces:** Django settings module is `website.settings`. All Python tests
run from the repository root. `/healthz` returns `{"status":"ok"}` after a
database query. `GET /` uses the real home template and an empty article list
until P3 introduces records. `npm run start:desktop` preserves the old Electron
command; `npm start` launches `python manage.py runserver 127.0.0.1:8000`.

- [x] **Preserve the approved UI and establish the test harness.** Review and
  commit only `index.html`, `galaxy/index.html`, and `README.md`. Add Python 3.13
  environment setup, Django 5.2, allauth, psycopg 3, markdown-it-py, Pillow,
  Gunicorn, WhiteNoise, and httpx; dev dependencies are pytest, pytest-django, pytest-playwright,
  and pip-tools. Resolve exact versions into hash-locked requirements with
  `pip-compile --generate-hashes`. Install test Chromium with `python -m playwright install chromium`.
  Do not reuse the legacy `.env`; new development settings use environment
  variables `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`. Defaults are local-only development
  credentials named `study_dev`; production settings require explicit values.

  Initial Compose configuration:

  ```yaml
  services:
    db:
      image: postgres:17
      environment:
        POSTGRES_DB: study_dev
        POSTGRES_USER: study_dev
        POSTGRES_PASSWORD: study_dev
      ports:
        - "127.0.0.1:5433:5432"
      volumes:
        - study_db:/var/lib/postgresql/data
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U study_dev -d study_dev"]
        interval: 2s
        timeout: 2s
        retries: 20
  volumes:
    study_db:
  ```

  Pin the resolved PostgreSQL image digest before committing. Configure test
  access to this database at `127.0.0.1:5433`; never connect tests to historical
  databases. Ignore `.venv/`, `.env*` except `.env.example`, uploaded media,
  collected static files, browser output, and Python caches. Preserve existing
  ignore lines and existing user files.

- [x] **Write the first behavior test and run it red.**

  ```python
  import pytest

  @pytest.mark.django_db
  def test_home_is_public_and_has_the_approved_identity(client):
      response = client.get("/")
      assert response.status_code == 200
      html = response.content.decode()
      assert "Diego Helmy's study notes" in html
      assert 'href="/galaxy"' in html
      assert "A compilation of my various learnings" in html
      assert "Choose this layout" not in html
      assert "Who owns this memory?" not in html
  ```

  Run `pytest tests/test_pages.py -q`; expect missing public route/content.

- [x] **Implement the rendered shell without redesign.** Extract the approved
  CSS and functional shell markup from `index.html`; retain the original as the
  visual reference until final integration. Replace timeline samples with a
  template loop and a quiet empty state. Replace mock navigation with real
  links and hide personal controls for anonymous users. Strip sample notes,
  counters, mock notices, and layout-study remnants from served templates.

  ```python
  # study/views/pages.py
  from django.shortcuts import render

  def home(request):
      return render(request, "study/home.html", {"articles": []})
  ```

  `APPEND_SLASH=False`; define exact public routes. Put assets before the final
  slug route later. Split inline event handlers into external JS while keeping
  existing CSS classes. Make `site.js` handle the owner mode and timezone UI
  only after P2 supplies the session contract. Do not ship dummy logged-in state.

- [x] **Verify and commit.** Run `pytest tests/test_pages.py -q`,
  `python manage.py check`, and `git diff --check`. Run the page at 1440×1100 and
  390×844 and verify the introduction/galaxy order and absence of horizontal
  overflow. Commit the explicit files with `feat: serve approved website shell`.

## Task P2: Google identity and the publishing mode

**Files:** Create `study/models/accounts.py`, `study/services/__init__.py`,
`study/services/identity.py`, `study/adapters.py`, `study/views/identity.py`,
`study/migrations/0001_accounts.py`, `tests/test_identity.py`,
`study/templates/account/login.html`, `study/templates/socialaccount/login.html`.
Modify `study/models/__init__.py`, `website/settings.py`, `website/urls.py`,
`study/templates/study/base.html`, `study/static/study/site.js`, and `.env.example`.

**Interfaces:** `is_publisher(user) -> bool`, `require_publisher(user) -> None`
(raises `PermissionDenied`), `profile_for(user) -> ReaderProfile`. Publisher
identity binds the verified Google subject and user, not a browser email string
or `is_staff` flag. Allauth's adapter is `study.adapters.GoogleAccountAdapter`.
Create ReaderProfile on first verified login; accept a validated IANA timezone
from the authenticated browser, using `Asia/Singapore` for Diego.

- [x] **Write identity tests before privileged routes.** Use allauth's real
  `SocialAccount`, `EmailAddress`, and a test publisher binding; `force_login`
  exercises session authorization without calling Google.

  ```python
  import pytest
  from django.contrib.auth import get_user_model
  from study.services.identity import is_publisher

  @pytest.mark.django_db
  def test_an_email_string_or_staff_flag_is_not_publishing_authority():
      user = get_user_model().objects.create_user(
          username="impostor", email="dhelmy990@gmail.com", is_staff=True
      )
      assert not is_publisher(user)
  ```

  Add tests for verified correct subject, unverified owner email, a different
  subject/email, reader admin-mode requests, GET login initiation, missing CSRF,
  POST logout, and unauthenticated session reads. Run `pytest tests/test_identity.py -q` red.

- [x] **Implement allauth and the owner binding.** Follow current allauth
  quickstart for installed apps, authentication backends, request context
  processor, and account middleware. Register Google only, use `profile` and
  `email` scopes, PKCE, online access, and no refresh-token storage. Configure one
  app in settings through `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
  Set `SOCIALACCOUNT_LOGIN_ON_GET=False` and `ACCOUNT_EMAIL_VERIFICATION="mandatory"`.
  Expose social-only sign-in, not local password signup/reset.

  In the successful Google login adapter, inspect the validated provider
  identity and verified EmailAddress for the configured owner. Bind
  `PublisherIdentity(pk=1)` once using an atomic transaction; never automatically
  rebind an existing subject. Keep publishing mode in the server session but
  authorize every publishing service independently.

  ```python
  # Core predicate in study/services/identity.py
  from allauth.socialaccount.models import SocialAccount
  from allauth.account.models import EmailAddress
  from study.models import PublisherIdentity

  def is_publisher(user):
      if not user.is_authenticated:
          return False
      binding = PublisherIdentity.objects.filter(pk=1, user=user).first()
      if binding is None:
          return False
      return (
          SocialAccount.objects.filter(
              user=user, provider="google", uid=binding.google_subject
          ).exists()
          and EmailAddress.objects.filter(
              user=user, email__iexact="dhelmy990@gmail.com", verified=True
          ).exists()
      )
  ```

  Do not register notes or review models with Django admin. The website's
  custom publishing UI is the supported admin interface. Add a real mode toggle
  and sign-out control to the approved navigation.

- [x] **Verify and commit.** Run `pytest tests/test_identity.py tests/test_pages.py -q`.
  Use `Client(enforce_csrf_checks=True)` for explicit CSRF cases, rather than
  relying on the permissive default test client. Commit `feat: add Google reader and publisher identity`.

## Task P3: Publish Markdown with images and serve real articles

**Files:** Create `study/models/content.py`, `study/migrations/0002_content.py`,
`study/markdown.py`, `study/storage.py`, `study/services/content.py`,
`study/views/content.py`, `study/forms.py`, `study/templates/study/article.html`,
`tests/test_publishing.py`, `tests/test_markdown.py`, `tests/test_assets.py`,
`study/management/commands/cleanup_unreferenced_assets.py`.
Modify `study/models/__init__.py`, `study/views/pages.py`, `website/urls.py`.

**Interfaces:** `prepare_article(markdown: str, images: dict[str, bytes]) -> PreparedArticle`
returns safe HTML, excerpt, source label/URL pairs, and validated image bytes;
`publish_article(user, *, title, color, markdown, images, submission_id) -> Article`;
`update_article(user, article_id, *, expected_revision, title, color, markdown, images) -> Article`;
`can_read_article(user, article) -> bool`; `asset_response(request, asset_id)`.
PreparedArticle is a frozen dataclass defined in `study/markdown.py`. `images`
maps normalized Markdown-relative paths such as `images/layout.png` to bytes.

- [x] **Write a meaningful publication test.**

  ```python
  from uuid import uuid4
  import pytest
  from study.models import Article
  from study.services.content import publish_article

  @pytest.mark.django_db
  def test_failed_image_reference_does_not_publish(publisher):
      with pytest.raises(ValueError, match="Missing image"):
          publish_article(
              publisher, title="Ownership", color="#1a5276",
              markdown="# Ownership\n\n![Sketch](images/missing.png)",
              images={}, submission_id=uuid4(),
          )
      assert Article.objects.count() == 0
  ```

  Define `publisher` in `tests/conftest.py` using a real user, verified
  EmailAddress, Google SocialAccount, PublisherIdentity, and ReaderProfile.
  Add happy publication, repeated submission, duplicate slug, revision conflict,
  storage failure, unsafe Markdown, non-owner request, stable slug/date, and
  asset-access tests. Run the three new test files red.

- [x] **Implement models, validation, rendering, and private asset writes.**
  Use master-plan fields and constraints. Insert GraphState(pk=1) in the data
  migration. Slugify the initial title, reject empty/reserved/conflicting slugs,
  validate title 1..200 characters and color `#[0-9a-fA-F]{6}`. Bound Markdown
  to 2 MiB UTF-8, each image to 10 MiB, at most 20 images, and the request to
  64 MiB. Fail validation before persistent publication.

  Persist a digest of the canonical title/color/Markdown/image-byte hashes with
  submission_id. An exact repeated submission returns its existing Article;
  reuse of the same id with different content returns 409. The same GraphState
  lock serializes publish, update, graph writes, and archive operations.

  Require a top-level heading or supplied title; the upload form's title wins
  and the leading H1 is omitted from the body to avoid a duplicate page title.
  Treat a final `## Sources` section of Markdown links as labeled citation pills.
  Other links remain inline. Extract the first body paragraph as plain-text
  excerpt (maximum 240 characters). The preview and publication use the same
  parser, with no AI processing.

  ```python
  from markdown_it import MarkdownIt

  def markdown_parser():
      return MarkdownIt("js-default", {"html": False}).enable("table")
  ```

  Traverse parser tokens to rewrite image references, not regular expressions
  over HTML. Reject absolute local paths, `..`, duplicate normalized names,
  executable URL schemes, and SVG/HTML uploads. Permit JPEG/PNG/WebP after Pillow
  decodes/re-encodes their actual bytes; reject decompression bombs. Remote
  links may use HTTP(S), but article images must resolve to supplied files or
  previously stored assets for that same article. Do not fetch remote images.

  Write every new image to a fresh UUID key beneath a private MEDIA_ROOT and
  close it before committing the Article/Asset transaction. Never overwrite an
  existing asset. On failure remove only that attempt's files. Keep existing
  files on edits; archived content freezes at the current revision. Resolve
  replacement images by logical name in the new body. Add a management command
  `cleanup_unreferenced_assets` that removes only unreferenced files older than
  24 hours; test with a temporary media directory.

  `can_read_article` permits published articles to anyone and archives only to
  the publisher or an ArchiveAccess holder. The asset endpoint applies the same
  predicate, returns 404 on denial, sets `Cache-Control: private, no-store`, and
  uses `FileResponse` with verified MIME and `X-Content-Type-Options: nosniff`.
  Never expose MEDIA_ROOT via a public static route.

- [x] **Wire real pages and verify.** Home orders by `-published_at,-id`; fetch
  20 articles per page and render older pages through a regular next link.
  Article routes include source pills, neighbor regions, and a persistent empty
  “Articles to read next” section even for terminal nodes. Escape all metadata;
  only render the trusted parser result as safe HTML. Return protected archive
  responses with private/no-store caching. Test direct article refresh and missing
  routes. Run focused tests and `python manage.py makemigrations --check --dry-run`.
  Commit `feat: publish Markdown articles and private image assets`.

## Task P4: Directed graph mutations, archival, and the existing renderer

**Files:** Create `study/services/graph.py`, `study/views/graph.py`,
`study/templates/study/galaxy.html`, `js/website-graph.js`,
`tests/test_graph.py`, `tests/test_archive.py`, `tests/test_graph_concurrency.py`.
Modify `js/graph-utils.js`, `study/services/content.py`, `website/urls.py`,
`study/templates/study/article.html`. Add migration constraints if necessary.

**Interfaces:** `add_edge(user, source_id, target_id) -> Edge`,
`remove_edge(user, source_id, target_id) -> None`,
`archive_article(user, article_id) -> Article`, `published_graph() -> dict`.
Directed edges are authoritative server state; loading the galaxy never edits
them. Preserve explicitly chosen redundant edges: they still mean an immediate
prerequisite in this product.

- [x] **Write cycle and immediate-neighbor tests.**

  ```python
  import pytest
  from study.services.graph import add_edge

  @pytest.mark.django_db
  def test_three_article_cycle_is_rejected(publisher, article_factory):
      a, b, c = [article_factory(title=t) for t in ("A", "B", "C")]
      add_edge(publisher, a.id, b.id)
      add_edge(publisher, b.id, c.id)
      with pytest.raises(ValueError, match="cycle"):
          add_edge(publisher, c.id, a.id)
  ```

  `article_factory` in `tests/conftest.py` calls `publish_article` with the
  publisher fixture, a UUID submission id, empty images, body `# {title}`, and
  color `#1a5276`. Add self/reverse-edge tests, duplicate insertion idempotency,
  explicit transitive edge preservation, absent-node 404, and published-only
  neighbor tests. Use separate DB connections and a thread barrier for concurrent
  A→B/B→A and multi-edge cycle attempts; exactly one conflicting write succeeds.

- [x] **Implement serialized graph writes.**

  ```python
  def path_exists(edges, start, destination):
      adjacency = {}
      for source, target in edges:
          adjacency.setdefault(source, []).append(target)
      pending, visited = [start], set()
      while pending:
          current = pending.pop()
          if current == destination:
              return True
          if current not in visited:
              visited.add(current)
              pending.extend(adjacency.get(current, []))
      return False
  ```

  Inside `transaction.atomic`, authorize publisher, lock GraphState(pk=1), lock
  endpoint articles in UUID order, reject archives, then reject `source==target`
  or `path_exists(existing_edges, target, source)`. Commit only the requested
  edge. Reject a cycle as HTTP 409. Remove the selected edge only on DELETE.
  Archival uses the same graph lock and article lock, marks archived_at once,
  prevents later content edits, and excludes the node/incident edges from public
  results without deleting or rewiring them. N1 adds note-owner grants and R1
  adds review suspension to this same transaction when those records exist.

- [x] **Adapt the graph entry point.** Preserve rendering/lighting/shaders,
  animation, hierarchy circles, camera dragging, hover effects, and configuration
  in the current visual modules. `website-graph.js` loads those modules plus the
  API graph and initializes ForceGraph3D with the existing node/link factories.
  Vendor the currently used Three.js 0.160.0, d3 7, and 3d-force-graph 1.73.0
  bundles into `study/static/vendor/` with exact npm lock versions, license
  notices, and a `scripts/vendor.mjs` copy script. Do not upgrade the rendering
  libraries during this adaptation. Public pages load no Quill or Electron code.

  Use a single click to `location.assign('/' + node.slug)`. Hover uses escaped
  title/excerpt previews. Admin mode retains hold-R and comparison selection
  to choose a directed edge; reader mode has no create, edit, delete, import,
  or edge controls. Provide a labeled New article button as a touch/keyboard
  alternative to hold-R. WebGL failure shows a linked list of the same articles.

  Remove mutual-edge filtering from the website algorithms and stop calling
  legacy `cleanGraphOnLoad`/`pruneTransitiveEdges`. Do not save graph snapshots
  to localStorage or import Electron data automatically. Handle failed API loads
  with retry and retain the last visibly loaded graph during a failed mutation.

- [x] **Verify and commit.** Run graph/archive tests including PostgreSQL
  concurrency cases. Browser-check graph rendering, article click, archive
  exclusion, reverse-cycle error, and return-to-timeline navigation. Commit
  `feat: connect articles through a directed Babel galaxy`.

## Task P5: Complete the owner upload/edit flow in the approved UI

**Files:** Create `study/static/study/publishing.js`,
`study/templates/study/publishing.html`, `tests/e2e/test_publishing.py`,
`tests/e2e/conftest.py`, `tests/e2e/test_public_reading.py`.
Modify galaxy/home/article templates, site CSS, content views/forms, and README.

**Interfaces:** A single upload form contains title, Babel color, Markdown file,
image files with editable logical paths, Preview, and Publish/Save buttons.
Reusing the form for edits carries article id/revision; submission UUID stays
stable across retries. `publishing.js` uses the shared CSRF-protected API helper
`requestJSON(url, options)` from `site.js`; export that helper as an ES module.

- [ ] **Write an end-to-end publication test.** `publisher_page` signs in by
  installing a test Django session cookie from the real publisher fixture;
  create no testing authentication endpoint in the production app.

  ```python
  def test_owner_publishes_then_reader_opens_article(publisher_page, live_server):
      page = publisher_page
      page.goto(live_server.url + "/galaxy")
      page.get_by_role("button", name="Admin mode", exact=True).click()
      page.get_by_role("button", name="New article", exact=True).click()
      page.get_by_label("Title", exact=True).fill("Ownership")
      page.get_by_label("Markdown file").set_input_files({
          "name": "ownership.md", "mimeType": "text/markdown",
          "buffer": b"# Ownership\n\nA clear lifetime.",
      })
      page.get_by_role("button", name="Preview", exact=True).click()
      page.get_by_role("button", name="Publish", exact=True).click()
      page.wait_for_url("**/ownership")
      assert page.get_by_role("heading", name="Ownership", exact=True).is_visible()
  ```

  Add image upload, edit conflict, failed-save input retention, anonymous reading,
  source labels, hover/focus preview, and mobile single-tap tests. Run
  `pytest tests/e2e/test_publishing.py tests/e2e/test_public_reading.py -q` red.

- [ ] **Implement the complete interaction.** Form preview uses the exact P3
  rendering pipeline without durable publication. Files are kept selected on
  network failure. Disable duplicate submit while in flight, re-enable on failure,
  and announce a successful write only after the server confirms it. Navigate to
  the returned stable slug after publication. Edits show revision conflicts and
  preserve input rather than overwriting a newer version. Archive has an explicit
  user action labeled Archive and returns to the timeline after success.

  Graph relationship editing remains in the galaxy, with selected source/target
  titles and clear prerequisite direction. On an article, hover/focus previews
  never delay anchor navigation. Empty successor lists still render the section
  needed by R2's read detector. Maintain normal browser back/forward behavior
  through real URLs. Hide notes/review features until their respective slices
  supply real data instead of leaving sample controls live.

- [ ] **Verify and commit.** Run all publishing slice tests and capture desktop
  and mobile screenshots against the approved reference. Check browser console
  errors and link destinations. Update README with Django development commands,
  PostgreSQL setup, and supported publishing behavior. Commit
  `feat: complete owner publishing and public article navigation`.

## Slice exit criteria

The site can publish and update a real Markdown article with images, read it
anonymously, navigate via Babels, reject graph cycles, and archive it. All data
comes from PostgreSQL/private storage. Public routes contain no sample review
counts, fake notes, AI controls, or local-only mutation paths.
