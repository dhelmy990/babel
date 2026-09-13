#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
project=${STUDY_PROJECT:-dhelmy-stream}
[[ $project =~ ^[a-z0-9][a-z0-9_-]*$ ]] || exit 2
compose=(docker compose --env-file "${STUDY_ENV_FILE:-$root/.env.production}" -f "${STUDY_COMPOSE_FILE:-$root/compose.prod.yaml}" -p "$project")
if [[ -n ${STUDY_COMPOSE_OVERRIDE:-} ]]; then compose+=(-f "$STUDY_COMPOSE_OVERRIDE"); fi
systemctl=${STUDY_SYSTEMCTL:-systemctl}
[[ $# == 1 ]] || { echo 'Usage: backup.sh DESTINATION' >&2; exit 2; }
destination=$(realpath -m -- "$1")
[[ $destination != "$root" && $destination != "$root/"* && ! -e $destination ]] || { echo 'Use a new destination outside the repository.' >&2; exit 2; }
exec 9>"${TMPDIR:-/tmp}/study-backup-$project.lock"
flock -n 9 || { echo 'A backup is already running.' >&2; exit 1; }
web=$("${compose[@]}" ps -aq web)
db=$("${compose[@]}" ps -q db)
[[ -n $web && -n $db ]] || { echo 'Existing web and running db containers are required.' >&2; exit 1; }
image=$(docker inspect --format '{{.Image}}' "$web")
release=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
[[ $release =~ ^[a-f0-9]{7,40}$ ]] || { echo 'Image must carry its commit in org.opencontainers.image.revision.' >&2; exit 1; }
db_image=$(docker inspect --format '{{.Image}}' "$db")
media=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/media"}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}{{end}}' "$web")
[[ -n $media ]] || { echo 'Web must use a named private media volume.' >&2; exit 1; }
web_was_running=$(docker inspect --format '{{.State.Running}}' "$web")
timer_was_running=false
digest_was_running=false
"$systemctl" is-active --quiet review-digest.timer && timer_was_running=true
"$systemctl" is-active --quiet review-digest.service && digest_was_running=true
restore_states() {
    result=$?
    trap - EXIT
    set +e
    recovery_failed=0
    if [[ $web_was_running == true ]]; then "${compose[@]}" start web || recovery_failed=1; fi
    if [[ $digest_was_running == true ]]; then "$systemctl" start review-digest.service || recovery_failed=1; fi
    if [[ $timer_was_running == true ]]; then "$systemctl" start review-digest.timer || recovery_failed=1; fi
    if [[ $recovery_failed == 1 ]]; then echo 'Service state recovery failed; operator action required.' >&2; [[ $result != 0 ]] || result=1; fi
    exit "$result"
}
trap restore_states EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$systemctl" stop review-digest.timer
"$systemctl" stop review-digest.service
"${compose[@]}" stop -t 75 web
mkdir -m 700 -- "$destination"
"${compose[@]}" exec -T db sh -eu -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$destination/database.dump"
docker run --rm --network none --user 0 --entrypoint tar --mount "type=volume,src=$media,dst=/media,readonly" "$image" -C /media -czf - . > "$destination/media.tar.gz"
# The one-off command is read-only and uses the same stopped release and volume.
STUDY_IMAGE="$image" "${compose[@]}" run --rm --no-deps -T web python deploy/recovery.py snapshot > "$destination/recovery.json"
"${compose[@]}" exec -T db sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT app, name FROM django_migrations ORDER BY app, name"' > "$destination/migrations.txt"
printf '%s\n' "$image" > "$destination/image.txt"
printf '%s\n' "$db_image" > "$destination/database-image.txt"
printf '%s\n' "$release" > "$destination/release.txt"
(cd "$destination" && sha256sum database.dump media.tar.gz recovery.json migrations.txt image.txt database-image.txt release.txt > SHA256SUMS)
touch "$destination/COMPLETE"
echo 'Backup complete.'
