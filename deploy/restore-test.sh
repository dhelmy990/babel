#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $# == 1 ]] || { echo 'Usage: restore-test.sh BACKUP_DIRECTORY' >&2; exit 2; }
backup=$(realpath -- "$1")
[[ -f $backup/COMPLETE ]] || { echo 'Incomplete backup.' >&2; exit 1; }
(cd "$backup" && sha256sum --check --status SHA256SUMS)
project=study-restore
exec 9>"${TMPDIR:-/tmp}/study-restore.lock"
flock -n 9 || { echo 'A restore is already running.' >&2; exit 1; }
[[ ${STUDY_PROJECT:-study-restore} == study-restore ]] || { echo 'Refusing a production restore target.' >&2; exit 1; }
for resource in container volume network; do
    if [[ -n $(docker "$resource" ls -q --filter "label=com.docker.compose.project=$project") ]]; then
        echo 'Restore project already has resources; refusing to reuse them.' >&2; exit 1
    fi
done
for resource_name in volume:study-restore_database volume:study-restore_media network:study-restore_default container:study-restore-db-1 container:study-restore-web-1; do
    resource=${resource_name%%:*}
    name=${resource_name#*:}
    if docker "$resource" inspect "$name" >/dev/null 2>&1; then
        echo 'A restore resource name is already in use; refusing to reuse it.' >&2; exit 1
    fi
done
image=$(cat "$backup/image.txt")
db_image=$(cat "$backup/database-image.txt")
[[ $image =~ ^sha256:[a-f0-9]{64}$ && $db_image =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Invalid recorded image identifiers.' >&2; exit 1; }
docker image inspect "$image" --format '{{.Id}}' >/dev/null
docker image inspect "$db_image" --format '{{.Id}}' >/dev/null
temporary=$(mktemp -d)
compose=(docker compose --env-file /dev/null -f "$temporary/compose.yaml" -p "$project")
created=false
finish() {
    result=$?
    trap - EXIT
    if [[ $result != 0 && $created == true ]]; then "${compose[@]}" down --volumes >/dev/null 2>&1 || true; fi
    if [[ $result != 0 ]]; then rm -rf -- "$temporary"; fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$temporary/compose.yaml" <<EOF
services:
  db:
    image: $db_image
    pull_policy: never
    environment:
      POSTGRES_DB: study_restore
      POSTGRES_USER: study_restore
      POSTGRES_PASSWORD: disposable-restore-only
    volumes: [database:/var/lib/postgresql/data]
    healthcheck:
      test: [CMD-SHELL, 'pg_isready -h 127.0.0.1 -U study_restore -d study_restore']
      interval: 1s
      timeout: 3s
      retries: 40
  web:
    image: $image
    pull_policy: never
    environment:
      DJANGO_SETTINGS_MODULE: website.production_settings
      DJANGO_DEBUG: 'false'
      DJANGO_SECRET_KEY: disposable-restore-key-not-used-in-production-0123456789
      DB_HOST: db
      DB_PORT: '5432'
      DB_NAME: study_restore
      DB_USER: study_restore
      DB_PASSWORD: disposable-restore-only
      GOOGLE_CLIENT_ID: restore-dummy
      GOOGLE_CLIENT_SECRET: restore-dummy
      REVIEW_EMAIL_DELIVERY: console
      MEDIA_ROOT: /app/media
    ports: ['127.0.0.1:18000:8000']
    volumes: [media:/app/media]
    depends_on:
      db:
        condition: service_healthy
volumes:
  database:
  media:
EOF
"${compose[@]}" config --quiet
created=true
"${compose[@]}" up -d --wait db
"${compose[@]}" exec -T db pg_restore -U study_restore -d study_restore --no-owner --no-privileges --exit-on-error < "$backup/database.dump"
# Compose creates a fresh media volume from the image's UID-10001 directory.
"${compose[@]}" create web
web=$("${compose[@]}" ps -aq web)
media=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/media"}}{{.Name}}{{end}}{{end}}' "$web")
docker run --rm -i --network none --user 0 --entrypoint tar --mount "type=volume,src=$media,dst=/media" "$image" -C /media -xzf - < "$backup/media.tar.gz"
"${compose[@]}" start web
"${compose[@]}" exec -T web python deploy/recovery.py verify < "$backup/recovery.json"
echo 'Restore verified on loopback port 18000; production Host and HTTPS proxy headers are required.'
echo "Restore configuration: $temporary/compose.yaml"
echo "Cleanup: docker compose --env-file /dev/null -p study-restore -f $temporary/compose.yaml down --volumes"
