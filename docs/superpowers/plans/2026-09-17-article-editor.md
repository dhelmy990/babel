# Direct article editing implementation plan

**Goal:** Edit articles in place through a clean visual Markdown editor and paste images into the text, then push the verified change to the deployment branch.

**Design:** Keep Markdown as the stored document and the server renderer as the authority for preview and publication. Use a self-hosted Tiptap editor with a rounded formatting toolbar and a Markdown source mode. Retain upload/import controls for existing workflows. Preserve the original Markdown until an actual text edit occurs. Existing image nodes resolve logical paths to authorized asset URLs; newly pasted images use temporary browser URLs but retain logical paths in Markdown. Send those files with the existing multipart save request. No database migration or new upload endpoint is needed.

**Constraints:** Publisher authorization, image validation, revision conflicts, stable slugs, publication dates, and submission retry identity continue through the existing APIs. Failed saves preserve text and files. Warn before leaving unsaved edits. Images support PNG, JPEG, and WebP up to 10 MB, matching server limits. No external editor service or telemetry.

## Tasks

- [x] Add browser regressions for direct text editing, formatting, Markdown mode, inline clipboard images, image retention, failed saves, and source round trips. Run them against the current screen to establish failures.
- [x] Add pinned editor dependencies and a reproducible vendor bundle through `scripts/vendor.mjs`, including the Docker build inputs and dependency notices.
- [x] Build `study/static/study/article-editor.js` to own editor state, mode switching, image previews, and pending image files. Integrate with `publishing.js`, keeping preview/save and import compatibility.
- [x] Add accessible toolbar/editor markup and responsive styles. Supply existing image URL mappings through `study/views/content.py`.
- [x] Run publishing/browser regressions, the full suite, Django checks, and production static collection. Inspect desktop/mobile screenshots and review the final diff.
- [ ] Update publishing documentation, commit only task files, and push to the confirmed deployment branch. Verify the resulting remote commit and available deployment checks.

## Verification cases

1. Open an existing article, edit its body without a file, save, and reload the public URL.
2. Paste two images at the cursor, see local previews, preview on the server, save, and reopen with durable `/assets/` URLs.
3. Edit Markdown source, switch modes, apply formatting, and preserve tables/code/Sources and existing images.
4. A failed or conflicting save leaves both typed text and pasted images available for retry.
5. A metadata-only save preserves the original Markdown byte for byte.
6. Legacy Markdown and image import still works; editor changes after import win over the imported file.

## Review refinements

The independent review identified Markdown compatibility issues in the library defaults. Explicit task-list nodes preserve checklists, HTML tokenization is disabled to match the server's literal HTML rendering, and custom serializers preserve links/image destinations containing spaces and escaped labels. The server image renderer now derives alt text from parsed text and escape tokens. Regression tests first reproduced each issue and passed after the fixes. Form controls remain disabled until the editor and event handlers finish loading.

The image parser extracts plain alt text before serialization, and image-only paragraphs retain their paragraph wrapper for the inline image schema. File selections made during module loading are reconciled when initialization completes.

## Verification

- Full suite: 396 passed, 12 opt-in tests skipped.
- Production image: built successfully with hashed static assets; the disposable production browser test opened the editor, retained its stored image, edited the document, saved, and verified the public rendering.
- Django system checks and migration check passed; no migration required.
- Desktop (1280 px) and mobile (390 px) screenshots reviewed; no horizontal overflow.
- Production npm dependency audit: zero vulnerabilities.
