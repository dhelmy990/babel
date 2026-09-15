# Home server updater plan

**Goal:** Verified pushes to `personal_website_deploy` produce public downloadable
releases that the Debian home server fetches without a GitHub token or inbound SSH.

**Approved choices:** Public source and release downloads; server-initiated polling;
Google OAuth, Resend and the public domain are not configured yet. Prepare and
stage releases now, but require an explicit server-local enabled file before
starting production. Keep credentials exclusively in the server's root-owned file.

**Design:** Extend the existing publication job to attach the verified image,
matching source and a checksummed manifest to a draft GitHub release. Publish it as
latest only after upload succeeds and the deployment branch still points to that
commit. A root-owned systemd timer polls the latest manifest every minute. The
updater validates identity, sequence, hashes, image revision and archive paths.
It stages each release in an immutable directory, preserves the fixed Compose
project and volumes, and switches the canonical source symlink on deployment.

Upgrades stop the website/digest, save the existing paired database/media backup,
apply migrations once, start the new image, then perform HTTPS smoke checks.
Failure after migration begins stops the website and blocks further automatic
changes until an operator resolves it. No automatic schema downgrade, pruning,
or changes to the bot. Backup and updater share a lifecycle lock.

**Files:** `deploy/release_updater.py`, `deploy/install-updater.sh`, timer/service
units, `deploy/backup.sh`, `.github/workflows/website.yml`,
`tests/test_release_updater.py`, `docs/deployment-debian.md`.

**Execution:** Implement inline; subagents are unavailable in this conversation.
Write tests for validation, staging, disabled startup, sequence handling, backup
ordering and failures first. Exercise real downloads and image loading against the
published release on the Debian host; production activation remains pending real
credentials and domain readiness. Validate shell, systemd and workflow syntax.
Commit and push only this checkout's changes, observe CI, stage the reviewed
installer on the server, and have the owner run its bounded sudo command.
