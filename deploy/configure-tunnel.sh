#!/usr/bin/env bash
# One-time configuration for an updater-managed site that has not gone live.
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }
[[ ! -e /etc/dhelmy-stream/enabled ]] || {
    echo 'Production is enabled. Plan an explicit ingress migration instead.' >&2; exit 1;
}
exec 8>/tmp/study-lifecycle-dhelmy-stream.lock
flock -n 8 || { echo 'The website updater is busy; retry shortly.' >&2; exit 1; }
[[ -z $(docker ps -aq --filter label=com.docker.compose.project=dhelmy-stream) ]] || {
    echo 'Existing website containers require an explicit ingress migration.' >&2; exit 1;
}
python3 - <<'PY'
import os
from pathlib import Path
import tempfile

path = Path('/etc/dhelmy-stream/production.env')
if not path.is_file() or path.is_symlink():
    raise SystemExit('Install the website updater first.')
updates = {
    'STUDY_PROXY_BIND': '127.0.0.1',
    'STUDY_PROXY_HTTP_PORT': '8080',
    'STUDY_PROXY_HTTPS_PORT': '8443',
    'STUDY_CADDYFILE': './deploy/Caddyfile.tunnel',
    'REVIEW_EMAIL_DELIVERY': 'disabled',
    'RESEND_API_KEY': "''",
}
lines, seen = [], set()
for line in path.read_text().splitlines():
    name = line.partition('=')[0].strip()
    if name in updates:
        if name not in seen:
            lines.append(name + '=' + updates[name])
        seen.add(name)
    else:
        lines.append(line)
for name, value in updates.items():
    if name not in seen:
        lines.append(name + '=' + value)
fd, temporary = tempfile.mkstemp(prefix='.tunnel-', dir=path.parent)
try:
    with os.fdopen(fd, 'w') as output:
        output.write('\n'.join(lines) + '\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('Tunnel origin configured: http://127.0.0.1:8080')
print('Credentials preserved. Production remains disabled.')
PY
systemctl disable --now review-digest.timer
echo 'Create the Cloudflare published route for dhelmy.stream before enabling the website.'
