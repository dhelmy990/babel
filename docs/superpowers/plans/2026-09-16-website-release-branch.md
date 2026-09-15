# Website release branch implementation plan

**Goal:** Pushes to `personal_website_deploy` verify and publish the exact website
image for installation on the owner's Debian 13 amd64 home server.

**Design:** Extend the existing Website workflow. Keep verification on pushes and
pull requests. Pass the tested Docker image between jobs as a checksummed artifact;
only the publication job gets package-write access. Publish a commit tag to GHCR
and record its immutable digest with matching tracked source. Server connection
details are absent, so document Debian setup and installation without inventing
credentials or claiming automatic server deployment.

**Scope:** Website workflow and deployment documentation only. Work in the isolated
`personal_website_deploy` checkout; preserve the original checkout's smoke-script
edit and untracked PDF. No bot changes or production service operations.

- [x] Inspect existing verification, image labels, Compose, and deployment guide.
- [x] Create the requested local branch in an isolated worktree.
- [x] Add branch/event guards and a separately permissioned GHCR publication job.
- [x] Transfer the verified image without rebuilding; check archive hashes and OCI revision.
- [x] Record the published digest and matching source in a release artifact.
- [x] Document Debian bootstrap, package access, installation, and remaining server connection.
- [x] Validate workflow expressions and shell syntax with actionlint; check documentation shell syntax.
- [x] Exercise image transfer/tagging and release-record generation locally without publishing.
- [x] Review the final diff, including branch guards, token scope, checksums and revision matching.

**Activation:** Commit only these requested changes, publish the new branch, and
observe its first Actions run. The run URL and conclusion belong in the task's
completion report rather than a follow-up documentation-only release.

**Validation:** The application source and Dockerfile are unchanged. Use actionlint
for the workflow, shell syntax checks for documented commands, a local exact-image
handoff rehearsal, and the real branch-triggered CI run for integrated verification.
Do not introduce tests that merely duplicate YAML values. No production server
rollout can be verified until its access and runtime configuration are supplied.
