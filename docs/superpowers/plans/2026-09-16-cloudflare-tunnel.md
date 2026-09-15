# Cloudflare Tunnel implementation plan

**Goal:** Serve dhelmy.stream from Debian through a user-created Cloudflare Tunnel,
with Google login and on-site reviews, without router port forwarding or email.

**Architecture:** cloudflared runs as its own host service and connects to a Caddy
HTTP listener published only on 127.0.0.1:8080. A separate Caddy configuration
redirects requests unless Cloudflare reports the original scheme as HTTPS, then
sets the trusted HTTPS header for Django. Existing direct HTTPS deployment remains
the default Compose configuration; the host selects the tunnel configuration using
its private runtime environment. The release updater and backups read that same
environment. No database changes, tunnel tokens in GitHub, or bot changes.

**Tech stack:** Existing Docker Compose, Caddy, Django and systemd; Cloudflare's
official cloudflared installation from its dashboard. Work inline in the isolated
personal_website_deploy worktree; no agents in this side conversation.

- [x] Add a failing Compose test: tunnel environment selects the tunnel Caddyfile
  and binds only loopback 8080/8443; PostgreSQL and web have no host ports.
- [x] Add a real Caddy/application integration test: forwarded HTTPS returns 200
  without a loop, HTTP or absent scheme redirects to the exact public HTTPS URL,
  unknown hosts/private files are rejected, secure CSRF cookies remain enabled.
- [x] Parameterize proxy bind/ports/Caddyfile in compose.prod.yaml, add
  deploy/Caddyfile.tunnel, and include the integration test in CI.
- [x] Add a reviewed root configuration script that updates only tunnel environment
  values atomically, preserves credentials, refuses active production changes,
  and leaves activation disabled. Document dashboard route and activation steps.
- [ ] Verify locally, publish the branch, confirm CI and server staging. Stage the
  configuration script for the user's sudo execution.
- [ ] After the user creates the connector and public route, verify configuration
  without exposing secrets, enable the website, and run the public smoke check.
  Google browser sign-in requires the user's own browser session.

Local verification: 30 deployment, TLS, backup/recovery, updater and tunnel tests passed.
