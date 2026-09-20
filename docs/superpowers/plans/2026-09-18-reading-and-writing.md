# Reading and Writing Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent editor task; coordinate review and integration in this session.

**Goal:** Implement deliberate pull-to-complete reviews, a simpler editor with reachable Save, public tables, and a private study calendar; push the verified result to deployment.

**Architecture:** Preserve Django's scheduling and publishing services. Isolate scroll presentation in a browser module and calendar queries in a read-only service. Reuse persisted Markdown, review schedules, timezone and session helpers.

**Tech Stack:** Django, PostgreSQL, vanilla JavaScript/CSS, Tiptap, pytest and Playwright.

## Global Constraints

- Completion requires a full additional scroll after the article end and server acknowledgement before success glow.
- Clipboard images and Markdown mode remain; remove import controls and + Image.
- Existing schedule eligibility and up-to-three daily selection remain unchanged.
- Calendar data is private, read-only, timezone-aware, and excludes archived/suspended schedules.
- No new dependencies or database migrations expected.
- User authorized implementation and deployment push; proceed without further design gates.

## Tasks

- [x] Editor and tables: update `study/templates/study/publishing.html`, `study/static/study/publishing.js`, `article-editor.js`, and scoped CSS; replace import-dependent browser tests with source/paste flows. Add failing viewport/save and table preview/public assertions, implement, then run `tests/e2e/test_article_editor.py` and `test_publishing.py`.
- [x] Pull completion: add `study/static/study/reading-reveal.js`, update `article.html`, `reviews.js`, and scoped CSS. Update `tests/e2e/test_reviews.py` to assert no schedule at article end/partial pull, then full pull schedules once. Preserve retries, lifecycle, image loading, visibility and timezone tests. Run the review browser suite after implementation.
- [x] Calendar: add `study/services/calendar.py`, private GET endpoint and page in `study/views/reviews.py`, routes in `website/urls.py`, `study/templates/study/review_calendar.html`, `study/static/study/review-calendar.js`, and isolated CSS. `calendar_payload(user, month, now=...)` returns month, today, timezone, scheduled items and overdue items without mutating review state. Test privacy, invalid months, archived/suspended exclusion and no frozen-day creation before implementation. Add browser month navigation/date-selection coverage.
- [x] Integration: review against the approved spec, run `.venv/bin/python -m pytest -q`, migration/system checks and production static collection. Inspect desktop/mobile screenshots. Fix material findings and rerun affected checks.
- [x] Heading navigation: add three editor heading levels and focused Ctrl +/- shortcuts; add a reader-only left outline with mobile collapse. Add keyboard, saved-heading, outline-jump and non-editor visibility regressions; retain title-duplicate handling without dropping authored sections.
- [ ] Release: document verified results, commit task files only, fetch/compare `origin/personal_website_deploy`, push a normal fast-forward update and inspect remote commit and CI status.

## Review refinements

- Initial review preparation errors now override reveal opacity so Retry remains visible.
- The calendar calls only its read-only endpoint, avoiding timezone synchronization that could materialize an expired review day.
- Completion returns actual calendar days remaining, including retries after midnight.
- Resizing during a partial pull resets intent; larger viewport geometry alone cannot complete a review.
- Browser coverage includes native touch input, reduced motion, keyboard completion, acknowledgement timing, and restored-page intent.
- Title suppression compares source body H1 counts with stored rendered body headings, preserving legacy duplicate suppression without dropping authored image headings or mistaking extracted Sources headings for body sections.
- Shared heading typography keeps editor preview/public reading consistent on mobile; browser restoration tests verify direct anchors separately from history scroll restoration.

## Verification

- Final full suite: 434 passed, 12 opt-in deployment tests skipped.
- Independent specification and code-quality reviews passed for editor/tables, pull completion/calendar, and heading navigation.
- Focused editor/outline/publishing/parser verification: 80 passed.
- Django system checks, production security checks, migration check, JavaScript syntax checks, and diff whitespace checks passed.
- Production Docker image built; disposable production browser smoke test passed, including hashed module imports, retained images and article save.
- Desktop/mobile screenshots reviewed for editor, reader outline, calendar and completion panel.
