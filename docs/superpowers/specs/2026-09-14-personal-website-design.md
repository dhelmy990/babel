# dhelmy.stream website design

Status: ready for user review. This document consolidates the brainstorming
decisions. Defaults explicitly marked below are proposed for approval.

## Purpose and current state

A personal collection of study articles about C++, finance, model serving, and
model development. Visitors explore a chronological timeline or a galaxy of
connected Babels. Signed-in readers keep private notes and revisit articles on
a spaced review schedule.

The `personal-website` branch starts from `f11295f` (May 24, 2026). The approved
layout B has already been promoted to `index.html`, and the original graph is
available at `galaxy/index.html`. The current articles, annotations, and review
list are sample content. Publishing, authentication, persistent private notes,
and review scheduling remain implementation work.

## Approved appearance and navigation

- Use the existing Babel aesthetic: near-black backgrounds, restrained color,
  minimalist controls, clean typography, and planet-like Babels.
- Preserve the approved layout B: introduction beside the timeline on desktop,
  above it on narrow screens. The promoted HTML is the visual reference.
- Subtitle above the title: `Diego Helmy's study notes`.
- Main title: `dhelmy.stream`.
- Put the Galaxy button directly beneath that title.
- Introduction: `A compilation of my various learnings about C++, finance, and model serving and model development.`
- Include icon links to https://github.com/dhelmy990 and
  https://www.linkedin.com/in/dhelmy990/.
- `/` is the homepage, with newest published articles first.
- `/galaxy` is the Babel view.
- `/{article-slug}` is an article's page. The intended domain throughout is
  `dhelmy.stream`.
- Keep the article and notes styling from the approved preview. Its illustrative
  article titles, dates, notes, counts, and relationships are not real user data.

## Accounts and privacy

Reading published articles, browsing the timeline, and exploring the galaxy
require no account. Google sign-in enables saved annotations and study reviews.

The verified Google account for `dhelmy990@gmail.com` receives publishing
controls and can switch between admin and reader modes. Other accounts receive
reader capabilities. The identity and publishing permission must be checked on
the server, rather than trusted from client-supplied email or UI mode.

Every reader can access only their own notes and review state. Diego has the
same restriction in reader mode; publishing permissions provide no application
access to other readers' annotations.

## Articles, Babels, and publishing

One Babel represents exactly one published article. They are one website item,
with a shared stable identity, rather than independently created records.
There are no public topic placeholders without an article.

Diego prepares Markdown outside this website, using whichever authoring or AI
tools he chooses. The website has no AI integration and requires no provider
API keys. Its admin flow accepts a Markdown file and accompanying images,
stores the images, resolves their Markdown references, and renders the result
using a consistent article layout.

The existing hold-R interaction in the galaxy opens creation in admin mode.
Publication makes the article and its Babel available together. A failed upload
or invalid article must not leave a public Babel without its article.

Each article presents:

1. Immediate prerequisites, represented by linked Babels.
2. The article body.
3. Clean source links with author-chosen labels, styled as pills.
4. Immediate successors under “Articles to read next.”

Hovering a prerequisite or successor shows a small preview. Clicking navigates
immediately. A touchscreen tap navigates immediately without requiring a preview
step. Keyboard focus should also expose preview information.

Connections are edited directly in the galaxy, including after publication.
Every directed edge means “read this before that.” Remove the old mutual-link
mode. Reject self-links, reverse links that create a cycle, and longer cycles.
Show immediate neighbors rather than all ancestors or descendants. Archiving
an intermediate article does not invent new connections between its neighbors.

## Private notes

Initial annotations are sticky notes and text boxes only. Exclude freehand
drawing and edits inserted into the author's article text.

A collapsible sidebar lists every note belonging to the current reader on the
current article. Use free-form notes rather than dedicated Cornell cue,
question, or summary sections. Notes are black with thin white borders.

Readers manually place and drag their notes. Notes do not attach to paragraphs
or automatically follow edits to the article. Saving a note retains its text
and manually chosen position. A moved or shortened article must not destroy
notes or make them unreachable: the sidebar retains the full inventory, and
the reader can recover and reposition any note.

The existing preview establishes the appearance and drag interaction; it does
not yet implement saving or account isolation.

## Read detection and study reviews

For a signed-in reader, reaching the “Articles to read next” section marks the
article as read. Opening the page alone does not count. Anonymous reading
creates no review state.

The first completion schedules a review one day later. Completing a due review
uses the same end-of-article trigger, removes it from the active queue, and
reschedules it from that completion using the next interval: 2, 4, 8 days,
and so on. This is the agreed doubling schedule, not a personalized memory model.

Maintain one schedule per reader/article pair. A database query ordered by due
time provides the desired min-heap behavior; no separate Python heap is needed.
Select the oldest due items into a daily list of at most three distinct
articles. Persist that selection so reloads or concurrent tabs do not introduce
extra selections or double-complete a review.

Completing an article does not pull a fourth distinct article into that day's
list. Unfinished reviews remain overdue and carry over with priority. Signed-in
readers see their list under “Review today.”

Diego also receives an email reminder. Other accounts receive only the on-site
list. Automatic email delivery is part of the future feature; writing this spec
does not send any messages.

## Editing and archiving

Editing an article updates the published content while preserving its identity
and readers' saved notes. Readers manually reposition annotations when needed.

Removal uses an archive flag on the existing article. Hide archived articles
from the public timeline, galaxy, and prerequisite/successor navigation, and
remove them from future review scheduling. Readers who already have saved notes
retain access to the archived article and those notes. Public visitors cannot
open an archive by guessing its URL.

Keep one stored article and its assets; do not create a copy for every reader.
The archived content is the version present when it was archived. Readers may
continue managing their own notes on it. This design does not require a general
article version-history system or a permanent-delete feature.

## Proposed defaults for approval

These fill operational gaps without adding new product features:

- Generate an article slug from its initial title and retain it when the title
  changes. Reserve application paths such as `galaxy`; reject conflicting slugs.
- Keep the original publication date after an edit so updates do not move an
  old article to the top of the timeline.
- Allow an admin to preview the uploaded Markdown before publishing. This is
  an unpublished form state, not a standalone public Babel or a draft library.
- Accept source labels using standard Markdown links. Derive Babel preview
  text from the article introduction; do not generate it with AI.
- A reread before its scheduled due time does not advance or reset the review
  interval. Repeated end-section observations count once for a given completion.
- Use the reader's local calendar day for daily selection, with a timezone
  recorded on their account. Default Diego to `Asia/Singapore`.
- Send Diego one digest at 09:00 Singapore time when his daily list contains
  outstanding reviews. The email and website use the same daily selection.
- On narrow screens, open the notes sidebar as a drawer. Keep all notes
  accessible there without silently rewriting their saved positions to fit
  the viewport; repositioning remains an explicit reader action.

## Implementation boundaries

Use a small website application with distinct responsibilities for published
content/graph relationships, identity/access control, reader notes, and review
scheduling. The database owns durable records and permissions; uploaded images
need durable asset storage. Scheduling needs a reliable daily email job for the
owner's account.

The renderer must treat uploaded Markdown and referenced assets as content,
not executable application code. Failed writes must retain editable input and
give the user a retryable error. Notes must not claim to be saved before the
server confirms the write. Unavailable or unauthorized content needs a clear
page state rather than an empty graph or silent failure.

Choose the framework, database, authentication integration, asset storage, and
hosting during implementation planning, checking their current documentation.
Deployment must include instructions for serving the finished application on
`dhelmy.stream`, Google sign-in configuration, image storage, and the owner email
job. Do not reintroduce the recommendation experiment's training, GPU,
simulation, vector-retrieval, or benchmark infrastructure.

## Acceptance and verification

- Match the approved layout at desktop and mobile widths; preserve the Babel
  view and the relocated Galaxy button.
- A published upload produces exactly one working article/Babel item with its
  images, source labels, and correct immediate graph neighbors.
- Invalid relationships and failed uploads cannot corrupt the public graph.
- Anonymous visitors can read; only Diego can publish. Two reader accounts
  cannot fetch, edit, or enumerate one another's notes or review state, including
  through direct API requests. Admin mode does not bypass note ownership.
- Notes survive reload, manual movement, article edits, and archival. Displaced
  notes remain recoverable from the sidebar.
- Verify first reads, review completion, early rereads, skipped days, timezone
  boundaries, concurrent requests, and the maximum of three daily selections.
- Verify archives disappear publicly while existing note owners retain access.
- Email tests use a fake delivery adapter and confirm owner-only delivery,
  shared daily selection, and no duplicate digest for the same day.

## Delivery sequence

1. Public content and publishing: real Markdown/images, article routes, Google
   sign-in, owner mode, directed galaxy relationships, and archive state.
2. Reader notes: ownership checks, durable content/positions, sidebar inventory,
   recovery after edits, and authorized archive access.
3. Study reviews: read detection, durable daily selections, rescheduling, and
   owner email digests.
4. Deployment and end-to-end verification on `dhelmy.stream`.

These are implementation slices of the agreed website. The promoted visual
shell is already present; the remaining slices should build on that appearance.
