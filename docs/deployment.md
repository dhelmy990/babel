# Deployment and recovery runbook

This guide is for a future operator deployment of `dhelmy.stream`. Cloud creation,
DNS changes, OAuth setup, mail delivery, host service installation and off-VM uploads
have **not** been executed by repository verification. The local verification at the
end uses disposable accounts, databases, TLS certificates and encryption keys.

## 1. Select the project, identity and recovery destination

Record these inputs in the owner's private operations record before provisioning:

| Input | Owner must select and record |
| --- | --- |
| Compute project and region/zone | One billing-enabled GCP project, region and zone with E2 capacity |
| SSH identity | Google account with OS Login administrator access and a working SSH key; project provisioner also needs resource-creation permissions |
| Operator IPv4 | Current trusted workstation public IPv4 as a `/32`, for the dedicated SSH firewall rule |
| Approved release | Repository URL accessible to the VM and full approved commit SHA already available there; never a moving branch |
| Recovery destination | A dedicated private GCS bucket, prefix, project, upload principal and separate restore principal |
| Key custody | Trusted workstation location of age private identity and an independent offline recovery copy; neither on VM nor in GCS |
| Recovery record | Exact encrypted data/image object names and generations, checksums, image IDs, release SHA, retention decision and last successful restore date |

Do not replace these choices with guessed account/project names. The VM, persistent
disk, static external IPv4, network egress and backup storage incur recurring
charges. Check the selected region's current prices and quotas; stopping the VM
does not remove disk/IP/storage charges. Budget for temporary image bundles and
backups as well as application data; a 30 GiB disk needs free-space monitoring.

## 2. Provision one CPU VM from the operator workstation

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) on the
trusted workstation and explicitly authenticate the selected provisioner there.
The commands below are future operator commands, not commands to run through an
unreviewed existing cloud login.

Use an E2 **custom 2-vCPU, 4096-MiB** machine (`e2-custom-2-4096`), a 30 GiB balanced
persistent boot disk, and Ubuntu 24.04 LTS x86_64. No GPU is needed. This avoids
confusing the shared-core `e2-medium` CPU entitlement with two full vCPUs.
See [custom machine types](https://docs.cloud.google.com/compute/docs/instances/creating-instance-with-custom-machine-type),
[Ubuntu GCE images](https://documentation.ubuntu.com/gcp/en/latest/google-how-to/gce/find-ubuntu-images/)
and [VM creation](https://docs.cloud.google.com/compute/docs/instances/create-start-instance).

Create a **new dedicated custom-mode network** with only the targeted ingress
rules below. An allow rule scoped to your IP does not override a broad existing
SSH allow rule. Do not reuse the default network or delete shared-project rules.
Have the project administrator review any inherited hierarchical/network firewall
policy before proceeding. This design uses direct SSH from the selected `/32`,
not IAP. Public web traffic is restricted to TCP 80/443; PostgreSQL is never public.
[Firewall rule CLI](https://docs.cloud.google.com/sdk/gcloud/reference/compute/firewall-rules/create).

```bash
# Trusted workstation: replace quoted REPLACE values before executing.
PROJECT_ID='REPLACE_WITH_OWNER_SELECTED_PROJECT'
REGION='REPLACE_WITH_REGION'
ZONE='REPLACE_WITH_ZONE_IN_REGION'
SSH_PRINCIPAL='REPLACE_WITH_GOOGLE_ACCOUNT_EMAIL'
OPERATOR_CIDR='REPLACE_WITH_CURRENT_PUBLIC_IPV4/32'
NETWORK='study-site'
SUBNET='study-site-subnet'
VM='study-site'
ADDRESS='study-site-ipv4'

gcloud services enable compute.googleapis.com --project="$PROJECT_ID"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="user:$SSH_PRINCIPAL" --role=roles/compute.osAdminLogin
# An external-organization identity may also require osLoginExternalUser from
# the organization administrator; follow the OS Login guide before connecting.
gcloud compute networks create "$NETWORK" --project="$PROJECT_ID" --subnet-mode=custom
gcloud compute networks subnets create "$SUBNET" --project="$PROJECT_ID" \
  --network="$NETWORK" --region="$REGION" --range=10.42.0.0/24
gcloud compute firewall-rules create study-site-ssh --project="$PROJECT_ID" \
  --network="$NETWORK" --direction=INGRESS --priority=1000 --allow=tcp:22 \
  --source-ranges="$OPERATOR_CIDR" --target-tags=study-site
gcloud compute firewall-rules create study-site-web --project="$PROJECT_ID" \
  --network="$NETWORK" --direction=INGRESS --priority=1000 --allow=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 --target-tags=study-site
gcloud compute addresses create "$ADDRESS" --project="$PROJECT_ID" --region="$REGION"
RESERVED_IPV4=$(gcloud compute addresses describe "$ADDRESS" --project="$PROJECT_ID" \
  --region="$REGION" --format='value(address)')
gcloud compute instances create "$VM" --project="$PROJECT_ID" --zone="$ZONE" \
  --machine-type=e2-custom-2-4096 --image-project=ubuntu-os-cloud \
  --image-family=ubuntu-2404-lts-amd64 --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced --no-boot-disk-auto-delete \
  --network="$NETWORK" --subnet="$SUBNET" --address="$RESERVED_IPV4" \
  --tags=study-site --metadata=enable-oslogin=TRUE --no-service-account --no-scopes
gcloud compute ssh "$VM" --project="$PROJECT_ID" --zone="$ZONE" --account="$SSH_PRINCIPAL"
```

The retained boot disk is intentional. VM deletion is not a backup. OS Login
administrator access permits `sudo`; the separate application Google login does
not grant VM access. See [OS Login setup](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin).
The VM initially has no cloud service account. Backup upload authentication is
an explicit later choice in section 9, not an implicit default service account.

## 3. Install Docker and pin the checkout on the VM

After SSH, enter one **root shell** with `sudo -i`. All subsequent VM commands in
this guide run in that root shell, including Compose, editing the root-owned secret
file, backups and systemd. Workstation commands remain explicitly labeled. This
keeps mode-0600 secret access consistent. Do not add the SSH user to the Docker group.

On a fresh Ubuntu 24.04 VM, use Docker's apt repository, following the
[official Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/).
If the VM is not fresh, first review that guide's conflicting-package list rather
than removing unrelated packages blindly.

```bash
sudo -i
apt-get update
apt-get install -y ca-certificates curl git python3 dnsutils netcat-openbsd
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'DOCKER_APT'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_APT
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl is-active docker.service
docker version
docker compose version

REPOSITORY_URL='REPLACE_WITH_ACCESSIBLE_APPROVED_REPOSITORY_URL'
RELEASE='REPLACE_WITH_FULL_APPROVED_COMMIT_SHA'
# Arrange a read-only repository credential for root if needed; never embed it in the URL.
git clone --no-checkout "$REPOSITORY_URL" /opt/dhelmy-stream
cd /opt/dhelmy-stream
git checkout --detach "$RELEASE"
test "$(git rev-parse HEAD)" = "$RELEASE"
install -o root -g root -m 0600 deploy/.env.production.example .env.production
# Use a root editor; do not print, source, or commit the filled secret file.
editor .env.production
stat -c '%U:%G %a' .env.production
```

Expected ownership is `root:root 600`. The selected commit must already be
available in the approved repository; this guide does not publish or push source.
There is no `git pull` in the release procedure.

## 4. Configure DNS, Google identity and Resend before starting the site

At the authoritative DNS provider set `@` **A → the reserved IPv4**, TTL **300
seconds** during cutover. Add AAAA only after routing and inbound IPv6 80/443 work;
a stale AAAA can break certificate issuance even when IPv4 works. On the trusted
workstation, `dig +short A dhelmy.stream` must return the reserved address;
`dig +short AAAA dhelmy.stream` must be empty for this IPv4 deployment.

Before starting Caddy, verify the firewall rules and public TCP reachability with
a temporary listener on this new VM. In two VM root terminals run respectively
`timeout 120 nc -4 -l -p 80` and `timeout 120 nc -4 -l -p 443`. From the workstation
run `nc -vz -w 5 "$RESERVED_IPV4" 80` and the same for `443`. Both must connect.
Stop only those test listeners, then confirm `ss -ltn '( sport = :80 or sport = :443 )'`
has no listener before starting Caddy. These probes test TCP, not HTTPS.

In Google Auth Platform for the owner-selected project, configure branding and
consent for authorized domain `dhelmy.stream`, an **external/public audience in
production**, and an OAuth client of type **Web application**. Set exactly:

| Setting | Values |
| --- | --- |
| Authorized JavaScript origins | `https://dhelmy.stream` and `http://127.0.0.1:8000` |
| Authorized redirect URIs | `https://dhelmy.stream/accounts/google/login/callback/` and `http://127.0.0.1:8000/accounts/google/login/callback/` |
| Identity data | Profile and email only; no mail, Drive or AI API permissions |

Keep `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in the server secret file. The
verified `dhelmy990@gmail.com` account becomes the sole publisher through its
bound Google subject. Ordinary readers may sign in to keep **their own** private
notes and reviews; they gain no publishing authority. The local callback is for
local development, not an alternate public production origin.
[allauth Google setup](https://docs.allauth.org/en/latest/socialaccount/providers/google.html),
[Google web OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

In Resend, add the sending domain and publish exactly its supplied SPF/DKIM DNS
records (and any requested return-path records); wait for **Verified**. Use the
verified sender `Study notes <reviews@dhelmy.stream>` or an explicitly verified
replacement, and put a sending API key in `RESEND_API_KEY`. Only the server uses
that key. The destination is fixed in code to `dhelmy990@gmail.com`; no browser or
recipient setting can redirect it. See [Resend domain verification](https://resend.com/docs/dashboard/domains/introduction).
Do not enable the digest timer until deliberate live delivery is approved.

## 5. Runtime configuration reference

Fill every `REPLACE` value in `.env.production`; placeholders passing a nonempty
check are still unsuitable for public use. Compose reads this as dotenv data;
single-quote values containing `$`, spaces or shell characters. Do not source it.
Missing/empty required runtime configuration fails naming the variable, without
printing its value. Never print expanded `docker compose config`; use `--quiet`.

| Variable | Purpose and requirement/default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required long random server secret; preserve securely for session/signature continuity |
| `DJANGO_DEBUG` | Must be `false`; production rejects `true` |
| `DJANGO_ALLOWED_HOSTS` | Default `dhelmy.stream`; keep that hostname for this deployment |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Required application database credentials; pair exactly with the three `POSTGRES_*` values |
| `DB_HOST`, `DB_PORT` | Required; Compose fixes these to `db`, `5432`, internal network only |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Required Compose initialization values matching `DB_NAME`, `DB_USER`, `DB_PASSWORD`; changing them does not reset an existing volume/database |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Required server OAuth Web application credentials, not browser API keys |
| `RESEND_API_KEY` | Required nonempty server key when production uses Resend |
| `REVIEW_FROM_EMAIL` | Verified sender; default `Study notes <reviews@dhelmy.stream>`; does not select recipient |
| `PUBLIC_BASE_URL` | Trusted absolute origin for email article links; production default `https://dhelmy.stream` |
| `MEDIA_ROOT` | Compose fixes `/app/media`, the named private media volume; paired with database in backups |
| `DJANGO_SETTINGS_MODULE` | Image/Compose fixes `website.production_settings`; `website.build_settings` is collection-only and must never serve requests |
| `STUDY_IMAGE` | Set versioned image tag `dhelmy-stream:FULL_COMMIT`; Compose otherwise defaults to `dhelmy-stream:local`, unsuitable for release tracking |
| `STUDY_RELEASE` | **Build argument**, exact commit SHA, sets OCI revision label; not a runtime secret; default `unknown` is rejected by backup |
| `STUDY_RUNTIME_ENV_FILE` | Compose runtime env-file path override; default `.env.production`; use only a deliberate trusted alternative |
| `REVIEW_EMAIL_DELIVERY` | Production default `resend`; explicit `console` only for disposable verification/isolated restore; never silently substitute it for missing production mail configuration |

## 6. Build, migrate once, then start HTTPS

After DNS and provider setup, in the VM root shell at `/opt/dhelmy-stream`:

```bash
RELEASE=$(git rev-parse HEAD)
# Ensure .env.production STUDY_IMAGE is dhelmy-stream:$RELEASE before continuing.
docker compose --env-file .env.production -f compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.prod.yaml build --build-arg "STUDY_RELEASE=$RELEASE" web
docker image inspect "dhelmy-stream:$RELEASE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose --env-file .env.production -f compose.prod.yaml up -d --wait db
docker compose --env-file .env.production -f compose.prod.yaml run --rm --no-deps web python manage.py migrate --noinput
docker compose --env-file .env.production -f compose.prod.yaml run --rm --no-deps web python manage.py check --deploy --fail-level WARNING
docker compose --env-file .env.production -f compose.prod.yaml up -d --no-build web proxy
docker compose --env-file .env.production -f compose.prod.yaml ps
docker compose --env-file .env.production -f compose.prod.yaml logs --tail=100 proxy web
bash deploy/smoke.sh https://dhelmy.stream
```

The database healthcheck waits for the final TCP server, not PostgreSQL's temporary
socket-only initialization server. Migrations are an explicit one-off operation;
Gunicorn workers never migrate on startup. Caddy requires correct public DNS,
reachable TCP 80/443 and persistent `caddy_data`/`caddy_config` volumes. It obtains
and renews certificates automatically; retain those volumes across upgrades.
[Automatic HTTPS conditions](https://caddyserver.com/docs/automatic-https).
Expected smoke result: `Smoke checks passed for https://dhelmy.stream`, HTTP 301/308
to the exact HTTPS `/healthz`, then HTTP 200 JSON `{"status":"ok"}` with a trusted
certificate. Do not bypass certificate verification while diagnosing a failure.

## 7. Owner acceptance and deliberate scheduling

Sign in as the verified owner and confirm the Reader/Admin toggle. Publish only
an intentional first Markdown article: preview headings, uploaded images, labeled
sources and the Galaxy link. Add a prerequisite only when a real related article
exists. Confirm the Galaxy direction and rejected cycles. Add a private note,
reload and verify it persists; reach the article end to schedule a review. Check
that daily reviews cap at three and completion does not refill the frozen day.
Archive only an article you intend to retire: it leaves public reading/Galaxy,
while previous note owners retain archive access and their private notes. An
archived schedule stops appearing as outstanding. Do not create throwaway Babel
content or request someone else's credentials for these checks.

Cross-user behavior is covered with fake local accounts, including
[`test_other_users_including_publisher_cannot_access_notes`](../tests/website/test_notes.py),
[`test_archive_permissions_and_access_grant`](../tests/website/test_archive.py),
and reader-switch/browser review checks in [`tests/website/e2e/test_reviews.py`](../tests/website/e2e/test_reviews.py).
These are automated local assertions; they do not prove live OAuth configuration.

First inspect the digest without sending (VM root shell):

```bash
cd /opt/dhelmy-stream
docker compose --env-file .env.production -f compose.prod.yaml exec -T web python manage.py send_review_digest --dry-run
# Install units now so backup can inspect their inactive state; leave timer disabled.
install -o root -g root -m 0644 deploy/review-digest.service deploy/review-digest.timer /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/review-digest.service /etc/systemd/system/review-digest.timer
systemctl show --property=ActiveState review-digest.service review-digest.timer
```

Dry-run can materialize/freeze the same ReviewDay as the UI; it creates no Digest
attempt and calls no adapter. Before 09:00 Singapore, without a bound owner or when
no review is eligible, it reports that state. Complete the first encrypted backup
and isolated restore in section 9 before enabling delivery.

**Only when email is ready and live owner delivery is intended**, run:

```bash
systemctl enable --now review-digest.timer
systemctl list-timers --all review-digest.timer
journalctl -u review-digest.service -n 50 --no-pager
```

The supplied root-run oneshot uses `/opt/dhelmy-stream`, apt Docker's
`docker.service`, and the same secret file. Never add `RemainAfterExit`. The timer
runs every five minutes; enabling it permits real email at/after 09:00 Singapore,
catching up only the current Singapore date. To pause: `systemctl stop review-digest.timer`
and `systemctl stop review-digest.service`; to keep it disabled across boots use
`systemctl disable review-digest.timer`. Retries freeze payload/key, stop by the
same day's end (or 23 hours), and log safe error categories. `failed` is a permanent
rejection; `unknown` is uncertain acceptance after the deadline. Neither is
automatically reconsidered. Inspect provider history before any operator decision;
do not reset the Digest to force another send.

## 8. Maintenance, updates and rollback

Before an update, announce downtime, record service/timer states and take the
matched backup below. Then stop the timer, wait/stop its oneshot and stop web.
Fetch only the reviewed commit already published by the release process, detach
at that SHA, set its versioned `STUDY_IMAGE`, build with its `STUDY_RELEASE`, migrate
once and start web/proxy using section 6. Keep the prior exact images and backup.
Run HTTPS smoke and restore the previously recorded timer state only after checks.

A rollback may select an old image only if it is compatible with the current
schema. Otherwise restore the matched database/media/release set during a planned
outage after an isolated restore rehearsal. Never run a blind migration downgrade,
reset a database, or delete volumes to repair an application issue. Isolated restore
below verifies recoverability; promoting that recovered data to production requires
an explicit reviewed cutover with correct live secrets and DNS, not reusing the
console/dummy restore project as production.

## 9. Matched backup, encrypted off-VM retention and first restore

The owner must first select the actual destination and custody entries from section
1. Install `age` from the official pinned release or trusted OS package on the VM
and trusted recovery machine, plus `tar`, `sha256sum`, Docker/Compose and the
[Google Cloud CLI](https://cloud.google.com/sdk/docs/install). The verified local
roundtrip used [age v1.3.2](https://github.com/FiloSottile/age/releases/tag/v1.3.2)
Linux amd64; this installs only into a selected tools directory:

```bash
# VM root shell or trusted Linux amd64 recovery machine; choose an unused directory.
set -euo pipefail
umask 077
AGE_TOOLS='REPLACE_WITH_NEW_PRIVATE_ABSOLUTE_TOOLS_DIRECTORY'
mkdir -m 0700 "$AGE_TOOLS"
curl --fail --silent --show-error --location --max-time 60 \
  https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz \
  -o "$AGE_TOOLS/age.tar.gz"
printf '%s  %s\n' cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10 "$AGE_TOOLS/age.tar.gz" | sha256sum --check
tar -xzf "$AGE_TOOLS/age.tar.gz" -C "$AGE_TOOLS"
export PATH="$AGE_TOOLS/age:$PATH"
age --version
```

**Trusted workstation only:** generate the private identity and retain an offline
copy in the recorded custody location. Copy only `recipient.txt` to the VM (for
example by approved SCP), never `identity.txt`:

```bash
set -euo pipefail
umask 077
KEY_DIRECTORY='REPLACE_WITH_NEW_TRUSTED_OFFLINE_KEY_DIRECTORY'
mkdir -m 0700 "$KEY_DIRECTORY"
age-keygen -o "$KEY_DIRECTORY/identity.txt"
age-keygen -y "$KEY_DIRECTORY/identity.txt" > "$KEY_DIRECTORY/recipient.txt"
```

The identity is independent of VM/GCS survival. See [age usage](https://github.com/FiloSottile/age)
and [format](https://age-encryption.org/v1). Use a dedicated private bucket with
uniform bucket-level access and public-access prevention. A bucket administrator
must grant the selected upload principal bucket-scoped permissions needed by
`gcloud storage cp`; Google's documented general role is Storage Object User
(`roles/storage.objectUser`), which also permits deletion. Record that breadth and
keep it confined to this dedicated bucket; the restore principal can use Object
Viewer. Do not grant project-wide Storage Admin just for uploads. Configure the
chosen principal deliberately (for example workstation impersonation or a dedicated
VM service account with bucket-only access and cloud-platform scope), without
putting a private service-account key in the repository. The base VM above has no
service account; uploads cannot work until this choice is made.
[Upload permissions](https://cloud.google.com/storage/docs/uploading-objects),
[Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles).

**VM root shell:** with installed but possibly inactive timer units and a healthy
DB, create a new owner-only destination outside the repository. Backup briefly
stops web and pauses timer/oneshot, including an activating oneshot. It restores
the exact prior active/inactive state on success or failure; an in-progress
oneshot is restarted and may finish, so maintenance can resume live work. DB stays
up. `COMPLETE` is written last, after DB dump, media, hash snapshot, migration list,
app/PG image IDs, release and checksums. Failed/incomplete directories are preserved
for diagnosis and must not be restored.

```bash
set -euo pipefail
cd /opt/dhelmy-stream
umask 077
BACKUP_ROOT='/var/backups/dhelmy-stream'
install -d -m 0700 "$BACKUP_ROOT"
BACKUP="$BACKUP_ROOT/data-$(date -u +%Y%m%dT%H%M%SZ)"
bash deploy/backup.sh "$BACKUP"
test -f "$BACKUP/COMPLETE"
(cd "$BACKUP" && sha256sum --check --status SHA256SUMS)
APP_IMAGE=$(cat "$BACKUP/image.txt")
PG_IMAGE=$(cat "$BACKUP/database-image.txt")
RELEASE=$(cat "$BACKUP/release.txt")
```

Once per **exact app + PostgreSQL image pair**, save both images. Do not rebuild
from a SHA during disaster recovery: dependency availability can change. A later
backup sharing the same pair may reuse this immutable bundle after verifying its
checksums and recording that association. Never remove the old bundle while a
retained data backup depends on it.

```bash
set -euo pipefail
umask 077
BUNDLE="$BACKUP_ROOT/release-$RELEASE-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 0700 "$BUNDLE"
docker image save --output "$BUNDLE/images.tar.partial" "$APP_IMAGE" "$PG_IMAGE"
mv -T "$BUNDLE/images.tar.partial" "$BUNDLE/images.tar"
printf '%s\n' "$APP_IMAGE" > "$BUNDLE/app-image.txt"
printf '%s\n' "$PG_IMAGE" > "$BUNDLE/postgres-image.txt"
printf '%s\n' "$RELEASE" > "$BUNDLE/release.txt"
(cd "$BUNDLE" && sha256sum images.tar app-image.txt postgres-image.txt release.txt > SHA256SUMS)
```

Encrypt both completed directories; use a new unique basename each time. Shell
`pipefail` makes a failed `tar` fail the pipeline even if age exits successfully.
Run each backup, image-save, encryption and recovery block as a complete shell
sequence: each enables fail-fast behavior before creating or consuming artifacts.
A failure exits that sequence before rename, extraction or image loading.
The final name appears only on success. Never delete sources/partials/encrypted
files on an upload/encryption failure. Use an unused directory and do not rerun
over an existing target; retain the failure and choose a new identifier.

```bash
set -euo pipefail
umask 077
RECIPIENT_FILE='/root/REPLACE_WITH_PUBLIC_RECIPIENT_FILE'
GCS_PROJECT='REPLACE_WITH_SELECTED_BACKUP_PROJECT'
BUCKET='REPLACE_WITH_PRIVATE_BUCKET'
PREFIX='REPLACE_WITH_PRIVATE_PREFIX'
UPLOAD_PRINCIPAL='REPLACE_WITH_CONFIGURED_UPLOAD_ACCOUNT'
EXPORT="$BACKUP_ROOT/export-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 0700 "$EXPORT"
for kind in data images; do
  if [[ $kind == data ]]; then source_dir=$BACKUP; else source_dir=$BUNDLE; fi
  tar -C "$(dirname "$source_dir")" -czf - "$(basename "$source_dir")" \
    | age -R "$RECIPIENT_FILE" -o "$EXPORT/$kind.tar.gz.age.partial"
  mv -T "$EXPORT/$kind.tar.gz.age.partial" "$EXPORT/$kind.tar.gz.age"
done
(cd "$EXPORT" && sha256sum data.tar.gz.age images.tar.gz.age > SHA256SUMS)
# Association manifest: keep with BOTH objects and in the private operations record.
printf 'data_directory=%s\nimage_directory=%s\nrelease=%s\napp_image=%s\npostgres_image=%s\n' \
  "$(basename "$BACKUP")" "$(basename "$BUNDLE")" "$RELEASE" "$APP_IMAGE" "$PG_IMAGE" > "$EXPORT/association.txt"
OBJECT_PREFIX="$PREFIX/$(basename "$EXPORT")"
for artifact in data.tar.gz.age images.tar.gz.age SHA256SUMS association.txt; do
  gcloud storage cp "$EXPORT/$artifact" "gs://$BUCKET/$OBJECT_PREFIX/$artifact" \
    --project="$GCS_PROJECT" --account="$UPLOAD_PRINCIPAL" --if-generation-match=0
done
# Record exact URI, generation and size for each successful upload.
gcloud storage objects describe "gs://$BUCKET/$OBJECT_PREFIX/data.tar.gz.age" \
  --project="$GCS_PROJECT" --account="$UPLOAD_PRINCIPAL" --format='yaml(name,generation,size,crc32c)'
gcloud storage objects describe "gs://$BUCKET/$OBJECT_PREFIX/images.tar.gz.age" \
  --project="$GCS_PROJECT" --account="$UPLOAD_PRINCIPAL" --format='yaml(name,generation,size,crc32c)'
```

`--if-generation-match=0` prevents replacing an existing object. If any step fails,
record which objects succeeded; do not overwrite them or claim the set complete.
The owner's recovery record includes the two exact encrypted object URIs, manifest,
checksums and generations, and the retained bundle association. The loop above
uploads both for the first release; later data backups can reference a previously
verified image object pair explicitly instead of uploading it again.
[Copy CLI](https://docs.cloud.google.com/sdk/gcloud/reference/storage/cp),
[generation-zero precondition](https://cloud.google.com/storage/docs/request-preconditions#zero).

**Trusted recovery machine / replacement host:** download the recorded objects to
a new private directory, verify encrypted checksums, decrypt using the separately
held identity, extract, verify inner checksums, then load **both** exact images.
The example uses the recorded first-release export layout; for reused image bundles
retrieve the exact image/checksum/manifest objects recorded for that data backup.

```bash
set -euo pipefail
umask 077
RESTORE_ROOT='REPLACE_WITH_NEW_PRIVATE_ABSOLUTE_RECOVERY_DIRECTORY'
GCS_PROJECT='REPLACE_WITH_SELECTED_BACKUP_PROJECT'
RESTORE_PRINCIPAL='REPLACE_WITH_CONFIGURED_RESTORE_ACCOUNT'
OBJECT_PREFIX='gs://REPLACE_WITH_RECORDED_BUCKET_AND_EXPORT_PREFIX'
IDENTITY_FILE='REPLACE_WITH_OFFLINE_IDENTITY_PATH'
mkdir -m 0700 "$RESTORE_ROOT"
for artifact in data.tar.gz.age images.tar.gz.age SHA256SUMS association.txt; do
  gcloud storage cp "$OBJECT_PREFIX/$artifact" "$RESTORE_ROOT/$artifact" \
    --project="$GCS_PROJECT" --account="$RESTORE_PRINCIPAL"
done
(cd "$RESTORE_ROOT" && sha256sum --check --status SHA256SUMS)
for kind in data images; do
  age --decrypt -i "$IDENTITY_FILE" -o "$RESTORE_ROOT/$kind.tar.gz.partial" "$RESTORE_ROOT/$kind.tar.gz.age"
  mv -T "$RESTORE_ROOT/$kind.tar.gz.partial" "$RESTORE_ROOT/$kind.tar.gz"
  tar -C "$RESTORE_ROOT" -xzf "$RESTORE_ROOT/$kind.tar.gz"
done
# Read the verified association record; never execute/source downloaded manifests.
DATA_DIRECTORY='REPLACE_WITH_RECORDED_DATA_DIRECTORY_BASENAME'
IMAGE_DIRECTORY='REPLACE_WITH_RECORDED_IMAGE_DIRECTORY_BASENAME'
BACKUP="$RESTORE_ROOT/$DATA_DIRECTORY"
BUNDLE="$RESTORE_ROOT/$IMAGE_DIRECTORY"
test -f "$BACKUP/COMPLETE"
(cd "$BACKUP" && sha256sum --check --status SHA256SUMS)
(cd "$BUNDLE" && sha256sum --check --status SHA256SUMS)
cmp "$BACKUP/image.txt" "$BUNDLE/app-image.txt"
cmp "$BACKUP/database-image.txt" "$BUNDLE/postgres-image.txt"
cmp "$BACKUP/release.txt" "$BUNDLE/release.txt"
docker image load --input "$BUNDLE/images.tar"
docker image inspect "$(cat "$BACKUP/image.txt")" --format '{{.Id}}'
docker image inspect "$(cat "$BACKUP/database-image.txt")" --format '{{.Id}}'
# Use the approved release's recovery tooling; no source rebuild is required.
cd /opt/dhelmy-stream
bash deploy/restore-test.sh "$BACKUP"
```

If decrypting on a workstation, transfer only the decrypted verified artifacts to
the isolated replacement host over the approved secure channel; the private age
identity stays on the trusted machine. Preserve the exact release's source checkout
or Git bundle off-VM with the recovery record so these scripts remain available,
independently of the failed VM and upstream repository availability.

`restore-test.sh` requires both locally available exact image IDs. It refuses
existing `study-restore` containers/networks/volumes, creates a fresh project and
volumes, uses dummy credentials and explicit console delivery, and binds web only
to loopback port **18000**. It has no proxy/timer and does not send mail. It compares
restored database/media fingerprints against `recovery.json` and prints `Restore
verified`, its retained generated configuration, and the exact cleanup command.
If Snap Docker is used on a disposable host, set `TMPDIR` to a new mode-0700 directory
under home because Snap cannot read the host's `/tmp` namespace.

Record the first successful isolated restore **before** relying on backup retention.
Check restored state after restart using the retained configuration:

```bash
set -euo pipefail
RESTORE_CONFIG='REPLACE_WITH_EXACT_PRINTED_COMPOSE_PATH'
docker compose --env-file /dev/null -f "$RESTORE_CONFIG" -p study-restore restart web
docker compose --env-file /dev/null -f "$RESTORE_CONFIG" -p study-restore exec -T web \
  python deploy/recovery.py verify < "$BACKUP/recovery.json"
# Only after preserving evidence, clean up this restore's resources:
docker compose --env-file /dev/null -f "$RESTORE_CONFIG" -p study-restore down --volumes
rm -- "$RESTORE_CONFIG"
rmdir -- "$(dirname "$RESTORE_CONFIG")"
```

Trusted backup-tool overrides are separate from normal configuration:
`STUDY_ENV_FILE` (backup Compose env file), `STUDY_COMPOSE_FILE` (base Compose),
`STUDY_COMPOSE_OVERRIDE` (controlled isolated override), `STUDY_PROJECT` (resource
identity), and `STUDY_SYSTEMCTL` (test-only state shim). Defaults target this real
release and host systemd; never accept these from a browser/request. Restore fixes
its project to `study-restore`. Do not use overrides to bypass ownership refusals.

## 10. Read-only smoke, troubleshooting and local evidence

`bash deploy/smoke.sh https://dhelmy.stream` requires Bash, curl and Python 3. It
validates one DNS/IPv4 HTTPS origin (optional numeric port, no credentials/path),
checks HTTP→HTTPS by default, certificate trust, health JSON, home/Galaxy, a real
hashed static file, parsed graph JSON and both missing-route/private-file 404s.
It follows no redirects. Every request has a five-second connection timeout and
20-second total timeout. It creates no sessions, articles, notes or delivery attempts.

For isolated Caddy `tls internal` only, `SMOKE_CURL_CA_BUNDLE` may name its readable
public root certificate and `SMOKE_HTTP_BASE` a validated HTTP origin with the local
HTTP port. The redirect must still target the exact positional HTTPS origin and
`/healthz`. Without an override the HTTP origin is derived from the HTTPS authority;
custom TLS-only ports therefore need the explicit HTTP port. No insecure flag or
global trust-store installation is used. The test fixture serves SAN `localhost`,
sets upstream Host `dhelmy.stream`/forwarded HTTPS, copies only `root.crt`, and removes
its own container/volumes/CA. [Caddy local TLS](https://caddyserver.com/docs/caddyfile/directives/tls).

| Symptom | Preserve evidence and next action |
| --- | --- |
| OAuth `redirect_uri_mismatch` | Compare exact scheme/host/port/path and trailing slash against section 4; check the configured Web client ID; do not change callback to a wildcard |
| Resend unverified sender/auth rejection | Verify domain DNS and sender, inspect the sanitized command error and provider configuration; terminal `failed` is not an automatic retry loop |
| DNS/certificate failure | Check `dig` A/AAAA, reserved IP, TCP 80/443 and `docker compose --env-file .env.production -f compose.prod.yaml logs --tail=100 proxy`; preserve Caddy volumes |
| DB unavailable | Run Compose `ps`, then `docker compose --env-file .env.production -f compose.prod.yaml logs --tail=100 db`; verify paired credential names in the private editor and internal TCP health; do not recreate the DB volume |
| Disk full | Check `df -h`, `df -i` and `docker system df`; pause writes/timer, retain failed backup/partial files, move verified encrypted backups off-VM; do not run blanket prune or delete data volumes |
| Failed private note save | Keep typed text and page open/copy text locally before navigation; inspect the visible retry/error state, restore network/session access and retry; conflict needs reload/reconciliation rather than silent overwrite |
| Digest `unknown` | Inspect `journalctl -u review-digest.service` and provider history for the recorded key/day; acceptance might have occurred, so do not reset/reissue after deadline |
| Bad release | Select a schema-compatible exact image or rehearse the paired DB/media/images restore before planned cutover; never reset DB or blindly downgrade migrations |

Local verification commands (from the repository, disposable resources only):

```bash
for script in deploy/*.sh; do bash -n "$script"; done
# Without these opt-ins, Docker/encryption tests skip during the ordinary suite.
STUDY_DEPLOYMENT_IMAGE=study-d1:verification \
STUDY_AGE_BINARY='/REPLACE_WITH_VERIFIED_LOCAL_AGE_BINARY' \
  .venv/bin/python -m pytest -c pytest.website.ini tests/website/test_deployment.py tests/website/test_smoke.py tests/website/test_recovery_bundle.py -q
```

The test fixture supplies owner-only dummy Compose env files and uses `config
--quiet`. Scheduler units are validated with `systemd-analyze verify` in an isolated
unit path containing a dummy Docker service; no host timer/service is installed or
started. CI repeats those checks and the real TLS/encrypted recovery tests. The TLS
checks exercise the real production image behind Caddy, unlike a synthetic browser
route. Encrypted recovery saves/loads both exact image IDs, encrypts/decrypts a dummy
matched DB/media backup, verifies checksums and runs the first isolated restore.
Cloud provisioning, DNS/provider setup, GCS uploads and live delivery remain future
operator work; local tests do not claim to have verified those external accounts.
