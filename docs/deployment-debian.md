# Website releases on a Debian 13 home server

Push website releases to **`personal_website_deploy`** in `dhelmy990/babel`.
The `Website` workflow tests the commit, builds its Linux amd64 image, runs the
container/TLS/recovery checks, and then publishes **that same tested image** to:

```text
ghcr.io/dhelmy990/babel/website:sha-FULL_COMMIT_SHA
```

Other branches and pull requests run verification only. They cannot enter the
publication job. Publication has its own `packages: write` permission and uses
GitHub's automatic `GITHUB_TOKEN`; no personal token or server secret is required
for the build. Runs for the same branch are serialized without interrupting a
running release. GitHub may replace an older pending run with a newer push.

The Actions run summary records the immutable `ghcr.io/...@sha256:...` image
reference. Its `website-release-FULL_COMMIT_SHA` artifact contains `release.json`,
the matching tracked source archive, and checksums. Download and retain this small
artifact with your release records; Actions retains it for 30 days. The large
image handoff artifact is temporary (one day). Images live in GHCR; there is no
automatic package deletion policy in this workflow. A rerun may update a SHA tag,
so **install by the digest in that run's release record**, not by a moving tag.

After GHCR publication, the workflow attaches the verified image as `image.tar.gz`,
matching `source.tar.gz`, and `website-release.json` to a draft GitHub release. It
publishes the complete release as **latest** only while that commit remains the
head of `personal_website_deploy`. These public downloads contain no production
credentials. The separate release job has `contents: write`; pull requests cannot
enter that job. See [automatic server updates](deployment-updater.md) for the
installed outbound-only updater, activation and recovery instructions.

**Initially the server only stages releases.** Website startup requires real
provider configuration, a working public domain and an explicit enabled file on
the server. A GitHub push cannot create that file. No GitHub token, VPN, inbound
SSH or self-hosted Actions runner is needed for downloads. The bot remains separate.

## GitHub setup and releasing

Enable Actions for the repository, and allow the pinned official
`actions/upload-artifact` and `actions/download-artifact` actions. Repository or
organization policy must permit this workflow to write packages. New GHCR
packages default to private even when the source repository is public. If the
package already exists, grant this repository Actions access in its package
settings. Publication failure is visible as a failed `Publish website image` job.
See [GitHub's registry guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Treat `personal_website_deploy` as a production release branch: merge reviewed
website changes into it, then push. Only trusted maintainers should write the
branch. Pull requests do not publish, including pull requests targeting it.

```bash
# In the checkout holding personal_website_deploy, after reviewing the merge:
git merge personal-website
git push origin personal_website_deploy
```

Open that commit's `Website` Actions run. `verify`, `Publish website image`, and
`Publish home server download` must pass. A superseded commit can have its public
release skipped or left in draft; the job summary explains this. The public
release and the `website-release-FULL_COMMIT_SHA` Actions artifact both carry
matching source. Never pair an old Compose configuration with an arbitrary new
image. Save the run URL too.

## One-time Debian preparation

Use Debian 13 **amd64**, systemd, and a machine that remains powered and awake.
Keep the bot's directories, Python environment, SQLite database and browser
profiles separate. Babel's existing production Compose project is `dhelmy-stream`;
it publishes only ports 80/443 and keeps PostgreSQL internal.

Install Docker using [Docker's Debian instructions](https://docs.docker.com/engine/install/debian/).
On a fresh machine, the root-shell commands are:

```bash
sudo -i
apt-get update
apt-get install -y ca-certificates curl git python3 dnsutils netcat-openbsd
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'DOCKER_APT'
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_APT
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

If Docker is already installed for another application, review the official
conflicting-package guidance before changing its installation. Do not remove or
restart the bot as part of website setup.

For public HTTPS, point `dhelmy.stream` to the home's reachable public address and
forward TCP 80/443 to a reserved LAN address for this server. Confirm reachability
from outside the home network. With ISP carrier-grade NAT, direct IPv4 forwarding
will not work; a separately configured ingress solution is needed. The existing
Caddy configuration expects public HTTPS for `dhelmy.stream`. Do not expose
PostgreSQL or the bot's browser desktop to make the website reachable.

Configure Google OAuth, Resend, and `.env.production` using sections 4–5 of the
[operator runbook](deployment.md). Its GCP provisioning and Ubuntu installation
sections do not apply to this Debian computer. Production secrets stay on the
server in a root-owned mode-0600 file; do not put them in an image or source archive.

For a private GHCR package, authenticate **on the server as root** with a dedicated
read-only registry credential before pulling. GitHub currently documents a
personal access token (classic) with `read:packages` for this use. Supply it through
standard input, not as a command argument, and keep it out of shell history. If
you deliberately make the package public, an anonymous pull is possible.

## Alternative: install the first image manually without the updater

This alternative uses the Actions artifact and GHCR. Do not mix it with an
updater-managed installation. Copy the downloaded release artifact's three files (`source.tar.gz`,
`release.json`, `SHA256SUMS`) into a new root-owned directory on the server, for
example `/root/website-releases/FULL_COMMIT_SHA`. Verify that this is the intended
successful Actions run. Run the following in one root shell, replacing the one
artifact-directory value. The application directory must not already exist.

```bash
sudo -i
set -euo pipefail
release_dir='/root/website-releases/FULL_COMMIT_SHA'
cd "$release_dir"
sha256sum --check SHA256SUMS
release=$(python3 -c 'import json; print(json.load(open("release.json"))["commit"])')
image=$(python3 -c 'import json; print(json.load(open("release.json"))["image"])')
[[ $release =~ ^[a-f0-9]{40}$ ]]
[[ $image =~ ^ghcr\.io/dhelmy990/babel/website@sha256:[a-f0-9]{64}$ ]]
docker pull "$image"
test "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" = "$release"
test ! -e /opt/dhelmy-stream
install -d -m 0755 /opt/dhelmy-stream
tar --extract --gzip --file source.tar.gz --directory /opt/dhelmy-stream --no-same-owner
cd /opt/dhelmy-stream
install -o root -g root -m 0600 deploy/.env.production.example .env.production
editor .env.production
```

Fill the provider and database values, and set `STUDY_IMAGE` to the exact `image`
value from `release.json`. Preserve `.env.production` separately from future
source releases. Then, in the same root shell:

```bash
cd /opt/dhelmy-stream
export STUDY_IMAGE="$image"
docker compose --env-file .env.production -f compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.prod.yaml up -d --wait db
docker compose --env-file .env.production -f compose.prod.yaml run --rm --no-deps web python manage.py check --deploy --fail-level WARNING
docker compose --env-file .env.production -f compose.prod.yaml run --rm --no-deps web python manage.py migrate --noinput
docker compose --env-file .env.production -f compose.prod.yaml up -d --no-build web proxy
docker compose --env-file .env.production -f compose.prod.yaml ps
bash deploy/smoke.sh https://dhelmy.stream
unset STUDY_IMAGE
```

Finish owner acceptance, scheduler installation, and the first verified backup
using sections 7–9 of the operator runbook. Its backup scripts also run on this
Debian server; select your own off-machine backup destination instead of assuming
GCS. Confirm both applications return after reboot. `restart: unless-stopped`
already covers the website containers once Docker starts.

## Subsequent releases

For an existing installation, first retain the old source, exact image IDs,
release record and a verified paired database/media backup as described in the
operator runbook. Backups and migrations must not overlap another deployment.
Preserve the existing Compose project name, volumes, production environment,
Caddy state and scheduler paths. Stage the new release's source separately,
verify and pull its digest, then stop the website and its digest timer/service
while applying migrations and replacing its source/image. Resume the services
only after deployment checks pass, and run the HTTPS smoke check.

Do not apply the first-install extraction commands over a running installation,
run `docker compose down -v`, prune shared Docker resources, or restart the bot.
If a migration has run, changing back to an older image alone may be unsafe;
follow the runbook's paired recovery procedure. The automatic updater enforces
these backup, migration and health-check steps; see its dedicated guide above.
