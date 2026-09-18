# Reading and writing improvements

Approved in conversation: implement now without a visual mockup, then push to the deployment branch.

## Deliberate review completion

Reaching the end of an article no longer records a read. A wide double-down chevron invites further scrolling. An additional viewport-relative scroll area reveals a rising completion panel while the article fades. Only finishing this additional scroll records the review. Partial progress reverses when scrolling back up. Native scrolling supports wheel, touch, keyboard, and scrollbar input without capturing browser gestures. A keyboard-accessible chevron button scrolls through the same reveal.

The panel initially asks the reader to continue. After successful server acknowledgement it displays “Review complete, number of days till the next read: X” and briefly glows in the article's Babel colour. Use the confirmed schedule; never claim success before acknowledgement. Preserve existing eligibility, first-read scheduling, interval doubling, retries, generation tokens, image layout readiness, timezone acknowledgement, authentication and archive restrictions. A restored page or an initially visible short article must not schedule without fresh intent. Reduced-motion users receive the same action and confirmation without animation.

## Writing

Remove the separate Markdown file/image import area, existing image paths, and + Image button. Keep visual editing, Markdown source mode, existing images, and clipboard image insertion. Keep Markdown as the persisted format and the existing validated upload APIs. Place Preview, Save/Publish, and status in a fixed bottom action bar with sufficient page clearance. Failed saves retain the draft and pasted files.

Tables receive shared public/preview styling with borders, headings, readable cell spacing and horizontal scrolling inside the article width. Confirm table persistence through visual editing and publishing.

## Study calendar

Add an authenticated Study calendar page linked from navigation and completion. Show a navigable month grid with counts and selectable dates; list the selected day's article titles with links. Use the reader's stored due dates, timezone, and existing schedules, excluding suspended/archived content and other readers. Include a separate overdue list. Explain that due dates are planned and Review today selects up to three; the calendar does not promise a future frozen selection. Calendar reads must not create schedules or freeze review days. No drag-to-reschedule or manual scheduling controls.

## Heading navigation (added by user during implementation)

The editor offers three heading levels, H1/H2/H3. With the article body focused, Ctrl + grows the heading from paragraph through H3, H2, H1; Ctrl − shrinks it back toward paragraph. Browser zoom elsewhere is unaffected. On the read-only article, a left-side sticky outline lists subsections with indentation and anchor navigation. Mobile uses a compact collapsible outline. The outline does not appear in the editor or preview. Preserve unrelated leading H1 sections when publishing while continuing to suppress a duplicate article title.

## Verification and release

Regression coverage includes partial/full pulls, reverse scroll, short pages, retries, restored pages, reduced motion, keyboard completion, calendar privacy and month boundaries, image paste/save, removed imports, fixed Save visibility, and public/preview tables. Run the full existing suite, Django checks, production static collection and an independent review. Preserve unrelated files. Confirm the deployment ref and push a normal fast-forward update after verification.
