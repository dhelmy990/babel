#!/usr/bin/env bash
# Install a reviewed updater; production stays disabled until configured locally.
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 -c 'import sys; assert sys.version_info >= (3, 13), "Python 3.13 or newer is required"'
docker compose version
for file in release_updater.py website-update.service website-update.timer review-digest.service review-digest.timer .env.production.example; do
    [[ -f $root/deploy/$file ]] || { echo "Missing installer file: $file" >&2; exit 1; }
done
[[ ! -e /opt/dhelmy-stream || -L /opt/dhelmy-stream ]] || {
    echo 'Existing unmanaged /opt/dhelmy-stream directory; inspect before installing.' >&2; exit 1;
}
install -d -m 0755 /usr/local/lib/dhelmy-stream
install -d -m 0700 /etc/dhelmy-stream /var/lib/dhelmy-stream
# A timer may already be installed. Stop only it and wait for an in-flight update
# to finish; never interrupt a migration or overwrite a running updater.
systemctl stop website-update.timer 2>/dev/null || true
while [[ $(systemctl show --property=ActiveState --value website-update.service) == activating ]]; do
    sleep 2
done
exec 8>/tmp/study-lifecycle-dhelmy-stream.lock
flock -n 8 || { echo 'A website backup or update is running; retry when it finishes.' >&2; exit 1; }
install -o root -g root -m 0644 "$root/deploy/release_updater.py" /usr/local/lib/dhelmy-stream/release_updater.py
install -o root -g root -m 0644 "$root/deploy/website-update.service" "$root/deploy/website-update.timer" /etc/systemd/system/
install -o root -g root -m 0644 "$root/deploy/review-digest.service" "$root/deploy/review-digest.timer" /etc/systemd/system/
if [[ ! -e /etc/dhelmy-stream/production.env ]]; then
    python3 - "$root/deploy/.env.production.example" <<'PY'
import os, pathlib, secrets, sys
text = pathlib.Path(sys.argv[1]).read_text()
password = secrets.token_urlsafe(36)
values = {
    'REPLACE_WITH_LONG_RANDOM_SERVER_SECRET': secrets.token_urlsafe(64),
    'REPLACE_WITH_PRODUCTION_DATABASE_NAME': 'study',
    'REPLACE_WITH_PRODUCTION_DATABASE_USER': 'study',
    'REPLACE_WITH_SAME_DB_NAME': 'study', 'REPLACE_WITH_SAME_DB_USER': 'study',
    'REPLACE_WITH_PRODUCTION_DATABASE_PASSWORD': password,
    'REPLACE_WITH_SAME_DB_PASSWORD': password,
}
for key, value in values.items():
    text = text.replace(key, value)
# The updater chooses the verified image; no tag placeholder is needed in runtime configuration.
text = '\n'.join(line for line in text.splitlines() if not line.startswith('STUDY_IMAGE=')) + '\n'
fd = os.open('/etc/dhelmy-stream/production.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(text)
PY
fi
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/website-update.service /etc/systemd/system/website-update.timer
flock -u 8
systemctl enable --now website-update.timer
echo 'Updater installed. It downloads public releases without a GitHub token.'
echo 'Production remains disabled unless /etc/dhelmy-stream/enabled already exists.'
echo 'Fill provider values with sudoedit /etc/dhelmy-stream/production.env and configure the domain before enabling.'
echo 'Inspect progress with: sudo journalctl -u website-update.service -n 30 --no-pager'
