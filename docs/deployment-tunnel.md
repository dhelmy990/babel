# Public website through Cloudflare Tunnel

The Debian server runs cloudflared as a host service. Visitors reach Cloudflare
over HTTPS; its tunnel reaches Caddy at `http://127.0.0.1:8080` on Debian. Caddy
forwards to the web container on the private Compose network. No inbound router
ports or fixed public IP are required. Keep PostgreSQL and web without host ports.

## Cloudflare account setup

In Cloudflare's dashboard use **Networking → Tunnels** (older dashboards use
**Zero Trust → Networks → Connectors**). Create a tunnel dedicated to the website,
or reuse one only after identifying its existing connectors and routes. Install
the Debian 64-bit connector using the dashboard-generated commands on the Debian
server itself. Those commands contain a tunnel token: run them in the owner's
terminal, never paste the token in chat or commit it. A healthy connector on a
different computer does not make the Debian server reachable. Avoid connecting
two machines to the same tunnel unless both can serve every configured route.

Add a **Published application** route (sometimes called Public hostname):

| Field | Value |
| --- | --- |
| Subdomain | Empty |
| Domain | `dhelmy.stream` |
| Path | Empty |
| Service type | HTTP |
| Service URL | `127.0.0.1:8080` |

If the UI uses one combined Service URL field, enter `http://127.0.0.1:8080`.
Use the default original Host header; do not override it to localhost. If DNS
conflicts, inspect the existing apex A/AAAA/CNAME records and replace only the
website's old route. Preserve unrelated routes and services.

The website is public. A Cloudflare Access application covering this hostname
would add a second login and block public health checks. Inspect existing Access
applications and remove or narrow only the website's coverage; retain policies
protecting other services. Reader sign-in and private notes remain protected by
the app's Google login. Enable **SSL/TLS → Edge Certificates → Always Use HTTPS**
for the website and avoid cache rules that cache HTML or `/api/*` responses.

Reference: [Cloudflare dashboard setup](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/).

## Configure the Debian origin

From a reviewed copy of this release, before first production activation:

```bash
sudo bash deploy/configure-tunnel.sh
```

This preserves OAuth/database secrets and writes:

```dotenv
STUDY_PROXY_BIND=127.0.0.1
STUDY_PROXY_HTTP_PORT=8080
STUDY_PROXY_HTTPS_PORT=8443
STUDY_CADDYFILE=./deploy/Caddyfile.tunnel
REVIEW_EMAIL_DELIVERY=disabled
RESEND_API_KEY=''
```

The generic Compose configuration retains its two port mappings; tunnel Caddy
listens only on HTTP port 80 inside the container. The unused HTTPS mapping is
also restricted to loopback. Both mappings use the same private runtime file as
the updater and backups, so these values survive releases. The script refuses an
already enabled site or existing website containers and leaves activation off.

Cloudflare overwrites `X-Forwarded-Proto` with the visitor's scheme. Tunnel Caddy
redirects missing/non-HTTPS schemes to the public HTTPS origin, then explicitly
sets HTTPS for Django. This preserves secure cookies and OAuth callback URLs
without a redirect loop. Do not use this Caddyfile on a public or LAN listener.

## First activation

First confirm the release with `Caddyfile.tunnel` has staged, the connector runs on
Debian, the route targets the loopback URL above, Google credentials are filled,
and Cloudflare Access no longer intercepts the public hostname. Then:

```bash
sudo touch /etc/dhelmy-stream/enabled
sudo systemctl start website-update.service
cat /run/dhelmy-stream/status.json
```

The updater checks production settings, starts PostgreSQL, migrates, starts
web/Caddy and runs the public HTTPS smoke check. If it fails, inspect the existing
[updater recovery instructions](deployment-updater.md) instead of repeatedly
restarting a failed deployment. Do not enable before the route is ready: public
health failures deliberately stop the website and require operator recovery.

After success, open `https://dhelmy.stream` in the owner's browser and sign in
with Google. The OAuth callback remains
`https://dhelmy.stream/accounts/google/login/callback/`.

The bot, its Cloudflare routes if any, and its containers are managed separately.
