# Automatic updates on the home server

The server is Debian 13 amd64, reached from the owner's laptop with `ssh homeserver`.
Reserve its LAN IP in the router for the active network adapter. Keep the actual
LAN address and adapter MAC in the owner's private operations record, and check
the reservation again if the network adapter changes.

`deploy/home-server-setup.sh` backs up existing configuration, fixes `[Login]` lid
handling, disables sleep, installs core TLP and Debian Docker/Compose, and adds the
server account to the Docker group. Reboot afterwards. The script leaves the Wi-Fi
manager in place: optional `tlp-rdw` would install NetworkManager and `tlp-pd` is not
available in stable trixie's TLP 1.8. Original configuration is retained under
`/var/backups/dhelmy-homeserver.*`. Battery thresholds depend on hardware support;
inspect `sudo tlp-stat -b` before changing them. A closed-lid connectivity test
requires physically closing the lid and reconnecting from the laptop.

## Install the updater once

From a reviewed copy of `personal_website_deploy` on the server:

```bash
sudo bash deploy/install-updater.sh
systemctl status website-update.timer --no-pager
sudo journalctl -u website-update.service -n 30 --no-pager
cat /run/dhelmy-stream/status.json
```

The installer copies the updater to `/usr/local/lib/dhelmy-stream`, installs the
systemd service/timer, and creates root-owned mode-0600
`/etc/dhelmy-stream/production.env`. It generates Django and database secrets
locally, without displaying them. OAuth remains an explicit placeholder. Review
email defaults to `disabled`; no Resend account, key or email DNS records are needed.
Re-running it preserves existing environment values and the activation choice.
The updater implementation changes only when this reviewed installer is run again;
ordinary application releases do not replace the installed controller.

The timer polls GitHub's latest public `website-release.json` once a minute, with
up to ten seconds of scheduling tolerance plus network/cache delay. This is a
public download URL, not a GitHub API request consuming an API token quota.
It downloads releases into `/var/lib/dhelmy-stream/releases`, validates repository
identity, monotonic workflow sequence, SHA-256 hashes, Docker image ID/commit label,
and safe source paths. It stages source and loads the image but starts **no website
containers** while activation is disabled. No inbound connection from GitHub or
GitHub token is required. The bot is unaffected.

## Enable production after credentials and DNS are ready

Complete Google OAuth and domain reachability as described in
[the Debian guide](deployment-debian.md). Then run:

```bash
sudoedit /etc/dhelmy-stream/production.env
# Finish provider configuration and external dhelmy.stream reachability first.
sudo touch /etc/dhelmy-stream/enabled
sudo systemctl start website-update.service
sudo journalctl -u website-update.service -n 50 --no-pager
```

The updater refuses configuration containing `REPLACE_`. It uses the local verified
image ID, so the GHCR package can remain private while public release downloads
work without registry login. `/opt/dhelmy-stream` becomes a symlink to the active
source. The fixed `dhelmy-stream` Compose project and database, media and Caddy
volumes persist across upgrades. Database-image upgrades remain manual;
application updates retain the existing database container.

The digest service/timer stays disabled. On-site reviews work independently of
email. For an environment created before email became optional, set
`REVIEW_EMAIL_DELIVERY=disabled`, remove the `RESEND_API_KEY` placeholder (or leave
its value empty), and run `sudo systemctl disable --now review-digest.timer`.
The disabled digest command exits without database access or provider calls.
Use the main [operator runbook](deployment.md) for owner acceptance and encrypted
backup/restore verification; its optional email activation section can be skipped.

## Upgrade and failure behavior

A lifecycle lock excludes concurrent backups and deployments. The updater stops
the digest timer/service and web process, saves the existing paired backup under
`/var/lib/dhelmy-stream/backups`, runs migrations, switches the source link, starts
the new web/proxy, and verifies internal health plus public HTTPS. It resumes
previously active digest scheduling only after success. Backups, old source and
images are retained; monitor free disk space and copy encrypted backups off the
machine using the main runbook. No automatic pruning or bot restart occurs.

If migration or health checks fail, the updater stops the website and creates
`failed.json`. An interrupted deployment leaves `in-progress.json`. Either record
blocks automatic changes. Inspect those root-owned records and the service journal,
then follow the main runbook's paired recovery procedure. **Do not just run the old
image after a migration.** Preserve the failure records with your incident notes;
remove both markers only after recovery, then start the updater again. A failure
before migration resumes the old site when one exists, but still requires operator
review before another attempt. Failed and interrupted first installs also require
inspection of any volumes created before retrying; the updater will not overwrite
an unmanaged project.

To disable activation while continuing to download releases:

```bash
sudo rm -f /etc/dhelmy-stream/enabled
```

This does not interrupt an in-flight deployment or stop a running site. Stop the
timer too with `sudo systemctl stop website-update.timer` if polling should pause.
It is enabled at boot; already deployed containers have Docker restart policies.

For a download rehearsal without touching production, choose a new directory
owned by the server account and run from the reviewed source:

```bash
rehearsal=$(mktemp -d "$HOME/website-download-check.XXXXXXXX")
python3 deploy/release_updater.py --stage-only --state "$rehearsal/state" \
  --config "$rehearsal/config" --link "$rehearsal/current" --lock "$rehearsal/lock"
```

This downloads the public release and loads its verified image into Docker. It
does not start containers, apply migrations, enable production, or install services.
Retain or remove only that rehearsal directory afterwards; do not prune shared images.
