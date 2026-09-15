#!/usr/bin/env bash
# One-time Debian laptop preparation. Run with sudo; no passwords are stored.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }
operator=${1:-${SUDO_USER:-}}
[[ $operator =~ ^[a-z_][a-z0-9_-]*$ && $operator != root ]] || { echo 'Supply the normal server account name.' >&2; exit 2; }
id "$operator" >/dev/null
# This file belongs to the operating system, not the application environment.
. /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 && $(dpkg --print-architecture) == amd64 ]] || {
    echo 'This setup targets Debian 13 amd64.' >&2; exit 1;
}
exec 9>/run/lock/dhelmy-homeserver-setup.lock
flock -n 9 || { echo 'Setup is already running.' >&2; exit 1; }
backup=$(mktemp -d /var/backups/dhelmy-homeserver.XXXXXXXX)
chmod 0700 "$backup"
save_existing() {
    if [[ -e $1 || -L $1 ]]; then cp --archive --parents -- "$1" "$backup/"; fi
}
for config in /etc/systemd/logind.conf.d/server.conf /etc/systemd/sleep.conf.d/nosuspend.conf /etc/tlp.d/90-homeserver.conf; do
    [[ ! -L $config ]] || { echo "Refusing to replace a symlink: $config" >&2; exit 1; }
    save_existing "$config"
done
install -d -m 0755 /etc/systemd/logind.conf.d /etc/systemd/sleep.conf.d /etc/tlp.d
cat > /etc/systemd/logind.conf.d/server.conf <<'LOGIN'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
LOGIN
cat > /etc/systemd/sleep.conf.d/nosuspend.conf <<'SLEEP'
[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowSuspendThenHibernate=no
AllowHybridSleep=no
SLEEP
cat > /etc/tlp.d/90-homeserver.conf <<'TLP'
# Keep the server's Wi-Fi connection reliable even during a power interruption.
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=off
TLP
chmod 0644 /etc/systemd/logind.conf.d/server.conf /etc/systemd/sleep.conf.d/nosuspend.conf /etc/tlp.d/90-homeserver.conf
apt-get update
# tlp-pd is absent from trixie stable. tlp-rdw requires NetworkManager;
# leave this server's existing network management in place.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tlp docker.io docker-cli docker-compose ca-certificates curl git python3
systemctl enable --now docker.service tlp.service
usermod -aG docker "$operator"
docker run --rm hello-world
docker compose version
tlp-stat -s
tlp-stat -b
printf '\nOriginal configuration backup: %s\n' "$backup"
printf 'Setup complete. Reboot with sudo reboot, then reconnect using ssh homeserver.\n'
printf 'Keep the lid open until after that reboot. A new login also activates Docker group membership.\n'
