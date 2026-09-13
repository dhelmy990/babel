# Study Reviews and Owner Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Schedule reviews from article completion, keep each reader's daily list at three, and email only Diego's outstanding daily reviews.

**Architecture:** Reader-profile row locks protect materialization and completion. A persisted daily list is shared by the website and a scheduled digest command, whose frozen delivery payload uses provider idempotency.

**Tech Stack:** Django/PostgreSQL, Python zoneinfo, browser IntersectionObserver, and Resend HTTP API. Requires [publishing](2026-09-14-website-01-publishing.md) and [notes](2026-09-14-website-02-notes.md); inherit the [master contracts](2026-09-14-personal-website.md).

## Global Constraints

First read schedules one day later; completed reviews schedule 2, 4, 8 days and
so on. Reading means reaching “Articles to read next.” At most three distinct
due articles per local day, oldest due first; never refill completed/cancelled
slots. Unfinished items carry over. Early rereads do not advance/reset intervals.
No review state for anonymous readers. Email only verified owner account at
09:00 Asia/Singapore. Tests must use fixed clocks and fake email delivery.

## Task R1: Durable review schedules and daily selection

**Files:** Create `study/models/reviews.py`, `study/migrations/0004_reviews.py`,
`study/services/reviews.py`, `study/views/reviews.py`, `tests/test_reviews.py`,
`tests/test_review_concurrency.py`. Modify `study/models/__init__.py`,
`study/services/content.py`, `study/views/identity.py`, `website/urls.py`.

**Interfaces:**

- `get_review_day(user, *, now) -> ReviewDay` materializes once per local date.
- `reading_context(user, article_id, *, now) -> dict` returns a signed token and
  eligibility status; no new schedule until completion.
- `complete_article(user, article_id, *, token, now) -> dict` returns
  `{status, next_due_date, interval_days, generation}`. Serialize interval_days
  as a decimal string so JavaScript never loses large-integer precision.
- `today_payload(user, *, now) -> dict` returns
  `{date, timezone, slots:[{ordinal,article_id,slug,title,completed,cancelled}]}`.
- API views supply `timezone.now()`; tests pass explicit aware datetimes.

- [x] **Write interval and daily-cap tests before services.**

  ```python
  from datetime import datetime, timedelta, timezone
  import pytest
  from study.services.reviews import reading_context, complete_article, get_review_day

  @pytest.mark.django_db
  def test_first_read_then_review_doubles_from_completion(reader, article_factory):
      article = article_factory(title="Ownership")
      start = datetime(2026, 9, 14, 2, tzinfo=timezone.utc)
      token = reading_context(reader, article.id, now=start)["token"]
      first = complete_article(reader, article.id, token=token, now=start)
      assert first["interval_days"] == "1"
      review_time = start + timedelta(days=1)
      day = get_review_day(reader, now=review_time)
      assert day.slots.count() == 1
      token = reading_context(reader, article.id, now=review_time)["token"]
      second = complete_article(reader, article.id, token=token, now=review_time)
      assert second["interval_days"] == "2"
      assert second["next_due_date"] == "2026-09-17"
  ```

  Add five overdue articles, select three, complete one, and assert the same
  three slot identities remain that day. Next day assert the two unfinished
  older articles precede later due work. Also test zero/one/two due items,
  duplicate completion, early reread, expired/wrong-user/wrong-article token,
  leap day, DST, first read just before midnight, overflow beyond year 9999,
  archival before completion, and API ownership. Run `pytest tests/test_reviews.py -q` red.

- [x] **Implement calendar arithmetic and generation tokens.** Store due dates
  in the reader's calendar, not UTC midnight. A day interval is a calendar-day
  increment from the actual completion date. The scheduling equation is:

  ```python
  from datetime import date, timedelta

  def next_due_date(completed_local_date, interval_days):
      remaining = (date.max - completed_local_date).days
      if interval_days > remaining:
          return None
      return completed_local_date + timedelta(days=int(interval_days))
  ```

  With no schedule, reading context signs `{user_id,article_id,generation:0}`.
  On first completion insert interval=1/generation=1 and next_due_date=tomorrow.
  Subsequent context signs the existing generation. A successful selected due
  review doubles the interval and increments generation once. Check signature,
  user/article binding, and token age of at most 24 hours using Django signing.
  Use `signing.Signer(salt="study-reading").sign_object(payload)` and its
  `unsign_object` counterpart; include `issued_at=now.timestamp()` and validate
  it against the service's `now` on completion. Reject negative age as well as
  expired age. This keeps fixed-clock tests independent of wall-clock signing.
  A stale generation is an idempotent no-op, not another review. Do not trust a
  client interval, date, timezone, or completion timestamp.

  Both reading_context and complete_article call get_review_day in its own
  completed transaction before acquiring a target article lock. Do not invoke
  daily selection while already holding an individual Article lock: selection
  acquires several article locks in sorted order. The completion transaction
  then locks ReaderProfile, its single Article, and relevant review rows, and
  rechecks today's saved slot/eligibility. Archived articles never create
  or advance schedules. Return `already_processed` for a stale generation,
  `not_due` for a future due date, `not_selected` for an overdue article outside
  today's three, and `first_read`/`reviewed` for real transitions. Each status is
  explicit; do not display a review-complete success for a no-op.

- [x] **Freeze daily lists and integrate archival.** Under the reader lock,
  get or create `ReviewDay(user, local_date)`. Only its creator selects up to
  three unsuspended published-article schedules with next_due_date<=today,
  ordered by next_due_date, last_completed_at, and article_id. Insert ordinals
  0..2 in the same transaction. Return existing slots without backfilling.
  Keep a completed slot with completed_at set; retain a cancelled slot if its
  article is archived. A new day reselects oldest outstanding schedules.
  Completing a selected review also fulfills any still-open, uncancelled slots
  for that same schedule on previously selected days, using the actual completion
  timestamp. Do not filter by local-date ordering, which can reverse after travel.
  This preserves slot identities and prevents a reused historical date from
  showing carried-over work that has already been completed; it never refills
  either day with a replacement.

  Extend archive_article while it holds the Article lock to set suspended=True
  on its ReviewSchedules and cancelled_at on uncompleted ReviewSlots. Archival
  never acquires profile locks; review transactions acquire profile then article,
  so neither transaction can reverse that lock order. Daily selection locks
  candidate Article rows in UUID order and rechecks published state before
  inserting slots. Do not count archived items in the visible outstanding list.

  Store the selected ReviewDay in ReaderProfile.active_day and its next local
  midnight as ReviewDay.next_boundary_at in UTC. Reuse it until that boundary
  even when a pending timezone would imply a different date. This explicit
  boundary prevents repeated timezone updates from rematerializing the day.
  Once a ReviewDay exists, timezone changes become pending until the next day
  boundary in its stored timezone. Apply the pending timezone on that boundary
  and use the unique user/local_date constraint to reuse any already existing
  target date. Do not let a timezone update clear slots or trigger a second daily
  allocation during the same frozen day. Preserve outstanding due dates as local
  calendar dates on timezone changes; display the active timezone in preferences.

  **Reactivating an existing date:** Preserve the reused ReviewDay's original
  timezone, boundary, and slots. If its timezone differs from the effective
  ReaderProfile.timezone, derive its active boundary from the next midnight
  after its local_date in that effective timezone; otherwise use the stored
  boundary. All rollover decisions use this shared effective-boundary helper.
  For example, September 14 UTC reused in Los Angeles at September 15 00:00 UTC
  stays frozen until 07:00 UTC, even though its historical boundary has passed.
  Return the reused day without looping against its historical boundary.
  Today responses and preferences display the effective profile timezone.
  A timezone update arriving after expiry first resolves the rollover under
  the profile lock, then stages the new preference for the next boundary.
  Before any day exists, the first timezone preference still applies immediately.

  The maximum representable local date has no subsequent boundary. Treat it as
  terminal; a nonnullable next_boundary_at may store aware UTC datetime.max as
  a sentinel recognized through local_date == date.max. Never convert that
  sentinel into another timezone, and guard local-date conversion overflow.
  Use Python integers for interval arithmetic before storing DecimalField
  values, avoiding the default Decimal context's 28-digit rounding.

- [x] **Verify concurrent behavior and commit.** In transaction tests use two
  independent database connections with synchronized starts: materialize the same
  day concurrently, complete the same token concurrently, and archive during
  completion. Assert one ReviewDay, at most three slots, one interval increment,
  and no active archived schedule. Use `pytest.mark.django_db(transaction=True)`;
  ordinary Django TestCase transactions cannot prove row-lock behavior. Run
  `pytest tests/test_reviews.py tests/test_review_concurrency.py -q` and migration
  consistency checks. Commit `feat: persist bounded daily study review schedules`.

## Task R2: End-of-article detection and the real Review today list

**Files:** Create `study/static/study/reviews.js`,
`study/templates/study/review_list.html`, `tests/e2e/test_reviews.py`.
Modify home/article/base templates, site.js/site.css, review views, and URLs.

**Interfaces:** `mountReadingCompletion({articleId, marker})` calls reading-context
and complete endpoints. `mountReviewList(container)` POSTs `/api/reviews/today`
and renders outstanding slots with ordinary article links. Navbar counts are
derived from that response, never a hardcoded 3. Context remains per page instance
and the server generation remains authoritative across tabs/reloads.

- [x] **Write end-of-page behavior tests.** Add a `long_article` fixture through
  article_factory using 80 repeated body paragraphs so the marker starts below
  the viewport. In browser tests, inspect the real ReviewSchedule table.

  ```python
  from study.models import ReviewSchedule

  def test_opening_does_not_count_but_reaching_successors_does(
      reader_page, reader, live_server, long_article
  ):
      page = reader_page
      page.goto(live_server.url + "/" + long_article.slug)
      assert not ReviewSchedule.objects.filter(user=reader, article=long_article).exists()
      page.get_by_role("heading", name="Articles to read next", exact=True).scroll_into_view_if_needed()
      page.get_by_role("status").filter(has_text="Added to your study reviews").wait_for()
      assert ReviewSchedule.objects.get(user=reader, article=long_article).interval_days == 1
  ```

  Add anonymous no-schedule, empty-successors completion, failed-request retry,
  expired context refresh, hidden-tab suppression, reload no-double-completion,
  review list max-three, and mobile interactions. Run `pytest tests/e2e/test_reviews.py -q` red.

- [x] **Implement the marker observer and list.** Put the marker on the actual
  next-articles section even when there are zero successors. Register only for
  signed-in users and published articles, after loading reading context.
  Observe intersection and document visibility; a marker visible in a background
  tab does not complete until the document is visible. An article short enough
  that its end is immediately visible legitimately counts as reached.

  ```javascript
  function observeArticleEnd(marker, onReached) {
    let reached = false;
    const report = () => {
      if (reached && document.visibilityState === 'visible') onReached();
    };
    const observer = new IntersectionObserver(entries => {
      reached = entries.some(entry => entry.isIntersecting);
      report();
    });
    observer.observe(marker);
    document.addEventListener('visibilitychange', report);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', report);
    };
  }
  ```

  The onReached handler has one in-flight request and one acknowledged flag.
  Reuse the same token on retries; never refetch a new generation merely to retry
  a success. After expiry refresh context and reevaluate eligibility. Keep the
  article readable on failure and expose a small Retry control. Announce the
  first-read or reviewed result unobtrusively. No scheduling call on archived
  articles, previews, or anonymous pages. On archived articles and publishing/
  preview pages, keep a plain Review today navigation link without fetching a
  count or materializing a day; following it loads the list on the homepage.

  Review today lives in the approved collapsible homepage section. Hide it for
  anonymous users; show an empty state for signed-in users with no work. Links
  navigate directly to article routes with the same notes sidebar. After a
  completion, remove it from outstanding display and update the count, but do
  not select a replacement; completed/cancelled slots stay in the database.

- [x] **Verify and commit.** Run R1 tests and `pytest tests/e2e/test_reviews.py -q`.
  Browser-check the real daily count, delayed images above the marker, sidebar
  opening near the end, and single-tap article navigation. Commit
  `feat: detect article completion and display daily reviews`.

## Task R3: Owner-only daily digest with bounded retries

**Files:** Create `study/services/digest.py`, `study/email_delivery.py`,
`study/management/commands/send_review_digest.py`, `study/templates/study/digest.txt`,
`study/templates/study/digest.html`, `tests/test_digest.py`, `tests/fakes.py`.
Modify review models/migration if Digest was not created with R1, settings,
`.env.example`, and README.

**Interfaces:** `prepare_owner_digest(*, now) -> Digest | None` and
`deliver_digest(digest_id, *, now, delivery) -> str` (`sent`, `retry`, `skipped`,
`failed`, or `unknown`). Delivery adapter exposes `send(payload: dict, idempotency_key: str) -> str`
returning the provider message id. Only the configured PublisherIdentity and
verified `dhelmy990@gmail.com` are eligible, regardless of caller-supplied data.

Digest.day is the Singapore delivery date, independent of the active ReviewDay's
calendar date or timezone. Its unique user/day constraint and idempotency key
enforce one digest per Singapore day while its content comes from the same
active review list as the owner's website. R1 may leave the Digest model for R3.

- [x] **Write owner-only and retry tests with a fake adapter.**

  ```python
  class FakeDelivery:
      def __init__(self):
          self.calls = []
          self.messages = {}

      def send(self, payload, idempotency_key):
          self.calls.append((payload, idempotency_key))
          self.messages.setdefault(idempotency_key, f"fake-{len(self.messages) + 1}")
          return self.messages[idempotency_key]
  ```

  Save this fake in `tests/fakes.py`. Test no digest before 09:00 Singapore,
  no outstanding reviews, absent publisher identity, wrong account, repeated
  prepare, repeated delivery, changed daily completion state before first send,
  same payload on retry, timeout after provider acceptance, concurrent senders,
  archived slot exclusion, and expired idempotency window. Assert the fake
  receives only Diego's address and at most one provider message per day.
  Run `pytest tests/test_digest.py -q` red.

- [x] **Implement daily preparation and durable delivery state.** The command
  runs every five minutes; prepare only at/after 09:00 and before the end of the
  current Singapore day. Catch up that day's digest after downtime; never send
  a backlog of old daily emails. Use the same get_review_day function as the UI.
  An expired, never-attempted digest becomes skipped; an attempted digest with
  an unresolved outcome becomes unknown after its retry deadline.

  If no outstanding published slots remain, create a skipped daily Digest so
  repeated scheduler runs do not reconsider a finished day. Otherwise store the
  day's digest identity once. On first delivery, while holding the relevant
  reader/article locks in master order, freeze remaining eligible titles and
  URLs, subject, sender, and recipient into payload; include no private note text.
  Mark the attempt and a two-minute lease before calling the provider outside
  database transactions. Use a 10-second HTTP timeout and the persisted key:

  ```python
  def digest_key(user_id, local_date):
      return f"study-review/{user_id}/{local_date.isoformat()}"
  ```

  Use exactly the same payload/key for every retry. A lost provider response
  retries safely within the provider's 24-hour retention window. Set retry_until
  to the earlier of first_attempt+23 hours and the end of that Singapore day;
  after that, mark unknown and require inspecting delivery history instead of
  blindly resending. Do not promise exactly-once delivery beyond that boundary.
  Stale leases can be retried only within retry_until. Confirmed sent/skipped
  states are terminal. A known permanent rejection (such as malformed data or
  denied authentication) returns terminal `failed`, distinct from `unknown`
  delivery outcome. Do not retry these failures automatically; expose last_error
  in command logs without payload secrets.

  A review/archive after first submission can make an already-frozen email
  stale; clicking still checks current article permissions and queue state.
  Never change the provider payload under a reused idempotency key. Fence each
  result update against the captured lease and its in-flight state, so a stale
  worker cannot overwrite a newer claim or terminal outcome. A matching claim
  may record a confirmed provider acceptance after its lease expires if no
  replacement or terminal transition occurred; this records known delivery and
  does not authorize a new provider request outside the retry window.

  Resend configuration: `RESEND_API_KEY` and
  `REVIEW_FROM_EMAIL=Study notes <reviews@dhelmy.stream>`; destination is fixed
  server-side. Use `https://api.resend.com/emails` via httpx and the
  `Idempotency-Key` header. Local default delivery is a console/fake adapter;
  production requires explicit Resend configuration. Disposable verification
  and isolated restore runs may explicitly select console delivery while keeping
  production security settings active; production must not silently default to
  console delivery.

- [x] **Verify and commit.** Run `pytest tests/test_digest.py -q` plus review
  concurrency tests. Run `python manage.py send_review_digest --dry-run` against
  test data and verify the output shows the daily selection without writing a
  delivery attempt or sending mail. Document dry-run behavior. Commit
  `feat: send owner daily study digest with idempotent retries`.

## Slice exit criteria

Read completion, daily selection, and email use one consistent durable state.
Cross-tab retries cannot exceed three selections or advance an interval twice.
Skipped days preserve oldest work, archives stop scheduling, and only the owner
is eligible for email.
