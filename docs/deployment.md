# Deployment and recovery runbook

This is an operator runbook. Commands use placeholders; this repository does not provision cloud resources, DNS, OAuth, mail, or backups.

## 1. VM and network

Choose `<GCP_PROJECT_ID>`, `<SSH_PRINCIPAL_OR_IAP_METHOD>`, `<REGION_ZONE>`, and a dedicated network or a targeted, priority-aware firewall policy. Do not assume a narrow SSH rule defeats an existing broad `default-allow-ssh`; inspect effective policy and avoid changing shared-project rules. Create one Ubuntu 24.04 x86_64 CPU VM with 2 vCPU, 4 GiB memory, a 30 GiB persistent disk, and a reserved static IPv4; do not attach a GPU. Permit SSH only through the selected access method and TCP 80/443 for Caddy. VM, persistent disk, static IP, network egress, and backup-storage use incur recurring charges; check the selected region’s pricing before creation. Follow [Compute Engine’s instance guide](https://docs.cloud.google.com/compute/docs/instances/create-start-instance).

## 2. Release and runtime secrets

Install Docker Engine and Compose by the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). On the VM, place an accessible, exact approved release—not a moving branch—at `/opt/dhelmy-stream`:

```bash
sudo install -d -m 0755 /opt/dhelmy-stream
sudo git clone <PINNED_REPOSITORY_URL> /opt/dhelmy-stream
cd /opt/dhelmy-stream && sudo git checkout <EXACT_APPROVED_COMMIT>
sudo install -m 0600 /dev/null .env.production
sudoedit .env.production
docker compose --env-file .env.production -f compose.prod.yaml build --build-arg STUDY_RELEASE=<EXACT_APPROVED_COMMIT> web
docker compose --env-file .env.production -f compose.prod.yaml up -d --wait db
docker compose --env-file .env.production -f compose.prod.yaml run --rm web python manage.py migrate --noinput
docker compose --env-file .env.production -f compose.prod.yaml up -d --no-build web proxy
```

Use `deploy/.env.production.example` as the checklist. `DB_*` and `POSTGRES_*` values must match as marked. Runtime uses `website.production_settings`; `website.build_settings` is collection-only. `STUDY_IMAGE` selects a versioned image, `STUDY_RELEASE` labels the build, and `STUDY_RUNTIME_ENV_FILE` is only a deliberate override. A missing `RESEND_API_KEY` is rejected in production; `REVIEW_EMAIL_DELIVERY=console` is only for disposable verification/restores. Diagnose failures with `docker compose --env-file .env.production -f compose.prod.yaml ps` and service logs; never print `.env.production` or expanded Compose configuration.

## 3. DNS, OAuth, and mail

Set the apex `dhelmy.stream` A record (`@`) to `<RESERVED_IPV4>` with `<TTL_SECONDS>`. Add AAAA only after real IPv6 works. Verify DNS plus ports 80/443 before Caddy obtains HTTPS certificates; Caddy handles renewal through [automatic HTTPS](https://caddyserver.com/docs/automatic-https).

Create a public/external, in-production Google OAuth web application. Origins: `https://dhelmy.stream` and `http://127.0.0.1:8000`; callbacks: `https://dhelmy.stream/accounts/google/login/callback/` and `http://127.0.0.1:8000/accounts/google/login/callback/`. Request only profile/email, keep client credentials server-side, and bind the verified owner `dhelmy990@gmail.com`; see [allauth](https://docs.allauth.org/en/latest/socialaccount/providers/google.html) and [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

Verify the Resend sending domain through its generated DNS records, set server-only `RESEND_API_KEY` and verified `REVIEW_FROM_EMAIL`, and retain fixed digest recipient `dhelmy990@gmail.com`. See [Resend domain verification](https://resend.com/docs/dashboard/domains/introduction). Sending live mail is intentional and separate.

## 4. Owner acceptance and digest timer

After HTTPS `/healthz` passes, sign in as owner, check reader/admin toggle, publish one intentional article with Markdown, images, source labels and Galaxy links, and inspect private notes. Do not create demo data or request another person’s credentials. Fake-account browser tests cover cross-user checks.

Run `send_review_digest --dry-run` first: it may freeze a shared ReviewDay but creates no digest/attempt/provider call. Only after email configuration is ready, install the supplied units under `/etc/systemd/system`, using `/opt/dhelmy-stream`, `Requires=docker.service`, and `/usr/bin/env docker compose --env-file .env.production -f compose.prod.yaml ...`; the timer is every five minutes with `Persistent=true`. Enabling it authorizes delivery after 09:00 Singapore. Check `systemctl list-timers` and service logs. Never add `RemainAfterExit` to the oneshot service.

## 5. Backups and recovery

During maintenance, create the matched DB/media pair with `deploy/backup.sh <NEW_DESTINATION_OUTSIDE_REPO>`. Trusted overrides (`STUDY_ENV_FILE`, `STUDY_COMPOSE_FILE`, `STUDY_COMPOSE_OVERRIDE`, `STUDY_PROJECT`, `STUDY_SYSTEMCTL`) are for controlled recovery tooling, not normal deployment. The script records image IDs/release/checksums and writes `COMPLETE` last; it preserves and restores timer/digest/web state, including an activating oneshot, while leaving DB up.

For every release, save both recorded application and PostgreSQL image IDs once: `docker image save <APP_IMAGE_ID> <POSTGRES_IMAGE_ID> -o <IMAGE_BUNDLE.tar>`; encrypt and retain the bundle with its checksum alongside its data backup. On a replacement host, `docker image load -i <IMAGE_BUNDLE.tar>` before `deploy/restore-test.sh <BACKUP>`. The isolated restore uses `study-restore`, loopback 18000, console delivery, no proxy/timer, and retains its generated Compose file; use its printed cleanup command only after retaining evidence, then remove its owned temp directory. Do not downgrade migrations blindly. Roll back only to a schema-compatible image; otherwise restore the matched DB/media/image set during maintenance.

For off-VM copies, the owner selects and records `<GCS_PROJECT>`, private `<BUCKET>`, `<PREFIX>`, upload principal, public age recipient, offline private-key location, image-bundle association, and last successful restore. Generate the age private identity on a trusted machine and place only its public recipient on the VM. With `age` and `gcloud` installed, encrypt a completed backup to `.partial`, rename only after success, then upload a unique object with a no-clobber generation precondition:

```bash
tar -C "$(dirname <BACKUP>)" -czf - "$(basename <BACKUP>)" | age -R <PUBLIC_RECIPIENT_FILE> -o <ARCHIVE>.partial
mv <ARCHIVE>.partial <ARCHIVE>.age
gcloud storage cp <ARCHIVE>.age "gs://<BUCKET>/<PREFIX>/<UNIQUE_NAME>.age" --if-generation-match=0
```

Keep source, partial, and encrypted artifacts on failure. The private age key must be outside both VM and cloud backup. Bucket-scoped upload access must cover the documented `gcloud storage cp` requirements; use a dedicated bucket/principal. Restore by downloading, `age -d -i <OFFLINE_IDENTITY>`, then extracting. See [age](https://github.com/FiloSottile/age), [age format](https://age-encryption.org/v1), [GCS uploads](https://cloud.google.com/storage/docs/uploading-objects), and [GCS IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles).

## Smoke and troubleshooting

`deploy/smoke.sh https://dhelmy.stream` is read-only: it checks health, home, Galaxy, a hashed static asset, graph JSON, 404s, TLS, and optionally HTTP redirect through `SMOKE_HTTP_BASE`. For local Caddy `tls internal` testing, set `SMOKE_CURL_CA_BUNDLE=<test-ca>` and `SMOKE_HTTP_BASE=http://127.0.0.1:<port>`; never use `--insecure`.

OAuth redirect mismatch: compare exact origin/callback strings. Unverified sender: finish Resend DNS verification. DNS/certificate failure: verify A record and reachable 80/443. DB unavailable: inspect DB health and credentials. Disk full: stop writes, preserve backup artifacts, free only reviewed space. Failed note save: preserve typed text/files and retry after checking the response. Unknown digest after retry deadline: inspect provider history and persisted attempts; do not blindly resend or reset the database.
