# Private Reader Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let signed-in readers save, move, recover, and edit only their own sticky notes and text boxes.

**Architecture:** Article-scoped note services enforce ownership and serialize against archival. The approved sidebar inventories every note while a separate overlay renders manually placed copies in article coordinates.

**Tech Stack:** Django/PostgreSQL and vanilla JavaScript, using the [master contracts](2026-09-14-personal-website.md). Requires publishing tasks P1–P5.

## Global Constraints

No reader, including Diego in admin mode, can read or modify another reader's
notes. Notes are plain text in black squares with white borders. Placement is
manual, never paragraph-anchored. Every saved note remains in the article's
sidebar even when its position is outside the revised article's visible area.
No freehand drawing, shared annotation, or edits to the author's body.

## Task N1: Private note storage and archive entitlements

**Files:** Create `study/models/notes.py`, `study/migrations/0003_notes.py`,
`study/services/notes.py`, `study/views/notes.py`, `tests/test_notes.py`,
`tests/test_note_archive_races.py`. Modify `study/models/__init__.py`,
`study/services/content.py`, `website/urls.py`, and `tests/conftest.py`.

**Interfaces:**

- `list_notes(user, article_id) -> list[Note]`, ordered by created_at then id.
- `create_note(user, article_id, *, note_id, kind, text, x, y) -> Note`.
- `update_note(user, note_id, *, expected_version, text, x, y) -> Note`.
- `delete_note(user, note_id) -> None`.
- JSON note: `{id, article_id, kind, text, x, y, version, updated_at}`.
- `grant_archive_access(article)` inserts ArchiveAccess for distinct existing
  note owners while `archive_article` already holds the Article lock.

Only the authenticated session supplies user identity. Requests cannot set
`user_id`, change `article_id` on an existing note, or elevate access via mode.
Missing and inaccessible note IDs both return 404. API note responses use
`Cache-Control: private, no-store`.

- [ ] **Write ownership tests and run them red.** Add `reader` and `other_reader`
  fixtures using real users/ReaderProfiles. Give no reader publishing authority.

  ```python
  from uuid import uuid4
  import pytest
  from study.services.notes import create_note

  @pytest.mark.django_db
  def test_publisher_cannot_read_another_readers_note(
      client, publisher, reader, article_factory
  ):
      article = article_factory(title="Ownership")
      note = create_note(
          reader, article.id, note_id=uuid4(), kind="sticky",
          text="Only mine", x=40, y=300,
      )
      client.force_login(publisher)
      response = client.get(f"/api/articles/{article.id}/notes")
      assert response.json()["notes"] == []
      response = client.patch(
          f"/api/notes/{note.id}",
          data='{"version":1,"text":"changed","x":40,"y":300}',
          content_type="application/json",
      )
      assert response.status_code == 404
      note.refresh_from_db()
      assert note.text == "Only mine"
  ```

  Cover anonymous reads/writes, other-reader list/PATCH/DELETE, forged owner
  fields, stale version, repeated create id, malformed coordinates, and CSRF.
  Run `pytest tests/test_notes.py -q` red.

- [ ] **Implement the note service and optimistic updates.** Use `Note` fields
  from the master schema and `kind` choices `sticky`/`text`. Limit text to 20,000
  characters; permit x/y both null for a sidebar-only note, otherwise validate
  finite x/y in [0, 1,000,000]. Reject a single null coordinate. Store positioned
  x/y as CSS-pixel offsets from the article annotation surface, independent of
  scroll position. Include this both-null-or-both-finite rule in migration 0003.
  For create, return the existing owned note only if the same id/article/content
  is retried; reject a reused id with different data as 409.

  The update transaction performs this compare-and-swap after article access
  and ownership checks; include `updated_at=timezone.now()` in the real update:

  ```python
  from django.db.models import F
  from study.models import Note

  def update_owned_note(user, note_id, expected_version, text, x, y):
      return Note.objects.filter(
          pk=note_id, user=user, version=expected_version
      ).update(text=text, x=x, y=y, version=F("version") + 1)
  ```

  Before that write, lock its Article within `transaction.atomic`, check
  `can_read_article`, and distinguish inaccessible notes (404) from a stale
  owned note (409). Validate before acquiring locks. Return the persisted note
  and new version. Delete only an owned note and preserve ArchiveAccess grants.
  Services enforce these checks independently of the HTTP layer.

- [ ] **Integrate archival atomically.** Extend P4's archive transaction:
  while holding the article lock, snapshot distinct Note.user IDs into
  ArchiveAccess with the unique pair constraint, then mark the article archived.
  New-note creation takes that same article lock before checking visibility.
  This ensures a concurrent first note either commits before archive and earns
  access, or sees an archive and is denied. Existing note owners may continue
  editing or creating personal notes on their authorized archive. The publisher
  can manage only their own notes there.

  Test note deletion after archive retains access, a reader without notes gets
  404 for both article and asset, author edits leave coordinates unchanged, and
  archived Markdown remains the content at archival. Use PostgreSQL transaction
  tests with two independent connections for archive/create races; either valid
  serialization is accepted, but no note owner is stranded.

- [ ] **Verify and commit.** Run
  `pytest tests/test_notes.py tests/test_archive.py tests/test_note_archive_races.py -q`
  and `python manage.py makemigrations --check --dry-run`. Commit
  `feat: persist private notes and archive access`.

## Task N2: Sidebar inventory, manual dragging, and recovery

**Files:** Create `study/static/study/notes.js`,
`tests/e2e/test_notes.py`. Modify `study/templates/study/article.html`,
`study/static/study/site.css`, `site.js`, `tests/e2e/conftest.py`.

**Interfaces:** `mountNotes({articleId, surface, sidebar, toggle})` initializes a
single article. It consumes P1/P5's `requestJSON` helper and N1's HTTP contract.
The sidebar has a persistent list item for every note, plus New sticky note and
New text box controls. Each item offers Edit, Place here, Return to sidebar, and
Delete. Returning to sidebar hides its positioned copy without deleting text;
store `x=null,y=null` for sidebar-only notes using N1's nullable coordinate pair.
This is a placement state,
not a separate note record.

- [ ] **Write browser tests first.** `reader_page` uses the test session-cookie
  helper from P5 and a real ReaderProfile. The article fixture is persisted
  through P3's service, and the test database is shared via Django live_server.

  ```python
  def test_notes_survive_reload_and_are_listed_in_the_sidebar(
      reader_page, live_server, article_factory
  ):
      article = article_factory(title="Ownership")
      page = reader_page
      page.goto(live_server.url + "/" + article.slug)
      page.get_by_role("button", name="My notes", exact=True).click()
      page.get_by_role("button", name="New sticky note", exact=True).click()
      page.get_by_label("Note text").fill("Draw the owner first.")
      page.get_by_role("button", name="Save note", exact=True).click()
      page.get_by_role("status").filter(has_text="Saved").wait_for()
      page.reload()
      page.get_by_role("button", name="My notes", exact=True).click()
      assert page.get_by_text("Draw the owner first.", exact=True).is_visible()
  ```

  Add a drag-save-reload test; a shortened-article recovery test; account-switch
  isolation; save failure with retained text; stale-update conflict with retained
  attempted text; and a 390-pixel drawer test. Run `pytest tests/e2e/test_notes.py -q` red.

- [ ] **Implement the approved interaction.** Use textContent/textarea values,
  never raw innerHTML for note text. Note controls are visible only for signed-in
  readers; anonymous users have a Google sign-in affordance near the notes toggle.
  Toggle aria-expanded, close on Escape, and return focus to the opener. On mobile
  the drawer overlays the reading area and supports touch editing.

  Pointer dragging updates the position in memory and saves once on pointerup.
  Cancel restores the last confirmed coordinates. Compute article-space movement
  from the surface rect and use pointer capture; render the overlay with
  `pointer-events:none` and interactive notes with `pointer-events:auto`.

  ```javascript
  function articlePosition(event, surface) {
    const box = surface.getBoundingClientRect();
    return {
      x: Math.max(0, event.clientX - box.left),
      y: Math.max(0, event.clientY - box.top)
    };
  }
  ```

  Keep drag grip offsets so a note does not jump under the pointer. Support
  keyboard arrow movement (10 px; Shift+arrow 1 px) and a Place here action at
  the current visible article position. Clamp interaction to the surface without
  rewriting saved positions on viewport resize or article edits.

  Desktop note copies whose saved positions lie outside current bounds are
  hidden from the overlay, but always present in the sidebar with a reposition
  action. On mobile keep notes in the drawer unless explicitly placed in the
  visible article area. A narrow viewport must not cause horizontal scrolling
  due to an offscreen stored note.

  Serialize each note's writes to preserve version order. Show Saving while in
  flight and Saved only after confirmation; show retry on network errors and
  preserve typed input in the current tab. For 409 show the current server copy
  alongside the unsaved local text and require an explicit retry against the
  refreshed version. Do not persist private note contents in shared browser
  storage, service workers, or static HTML caches.

- [ ] **Verify and commit.** Run the N1 tests plus
  `pytest tests/e2e/test_notes.py -q`. Manually compare sidebar borders/black
  backgrounds with the approved article mockup and verify both note kinds,
  pointer and keyboard movement, archive access, and mobile recovery. Commit
  `feat: add recoverable private note sidebar and placement`.

## Slice exit criteria

Two authenticated readers see disjoint note inventories on the same article;
Diego cannot bypass that isolation. Notes survive reload, manual movement,
content edits, and archival, and remain recoverable after layout changes.
