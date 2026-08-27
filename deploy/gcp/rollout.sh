#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: rollout.sh RELEASE_PACKAGE_DIRECTORY" >&2
  exit 2
fi

PACKAGE_DIR="$(realpath "$1")"
RELEASE_NAME="$(basename "$PACKAGE_DIR")"
RELEASE_ROOT=/opt/babel/releases
CURRENT_LINK=/opt/babel/current
RUNTIME_ENV=/etc/babel/runtime.env
PROJECT=babel-gcp-demo
PROMOTION_STARTED=false
PREVIOUS_RELEASE=""
NEW_RELEASE="${RELEASE_ROOT}/${RELEASE_NAME}"

if [[ ! "$RELEASE_NAME" =~ ^babel-release-[a-f0-9]{40}-[0-9]+$ ]]; then
  echo "release package name is not canonical" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
  echo "$RUNTIME_ENV is required and must be provisioned outside GitHub" >&2
  exit 1
fi
if [[ -e "$NEW_RELEASE" ]]; then
  echo "release attempt already exists: $NEW_RELEASE" >&2
  exit 1
fi

cd "$PACKAGE_DIR"
sha256sum --check SHA256SUMS
python3 release.py validate release.env

install -d -m 0700 "$NEW_RELEASE"
install -m 0600 release.env "$NEW_RELEASE/release.env"
install -m 0600 SHA256SUMS "$NEW_RELEASE/SHA256SUMS"
install -m 0700 rollout.sh "$NEW_RELEASE/rollout.sh"
install -m 0700 release.py "$NEW_RELEASE/release.py"
install -m 0600 compose.yaml "$NEW_RELEASE/compose.yaml"
install -d -m 0750 -o 10001 -g 10001 /var/lib/babel-online

compose() {
  docker compose --project-name "$PROJECT" \
    --env-file "$NEW_RELEASE/release.env" \
    --file "$NEW_RELEASE/compose.yaml" "$@"
}

compose pull
source "$NEW_RELEASE/release.env"
docker run --rm --gpus all "$BABEL_SERVING_IMAGE" \
  python -c 'import torch; assert torch.cuda.is_available(), "CUDA is unavailable"'
compose run --rm --no-deps backend migrate

if [[ -L "$CURRENT_LINK" ]]; then
  PREVIOUS_RELEASE="$(readlink -f "$CURRENT_LINK")"
fi

stop_current_release() {
  if [[ -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE" ]]; then
    docker compose --project-name "$PROJECT" \
      --env-file "$PREVIOUS_RELEASE/release.env" \
      --file "$PREVIOUS_RELEASE/compose.yaml" stop
  fi
}

rollback_current_release() {
  if [[ -z "$PREVIOUS_RELEASE" || ! -d "$PREVIOUS_RELEASE" ]]; then
    echo "new deployment failed and no previous release exists" >&2
    return 0
  fi
  docker compose --project-name "$PROJECT" \
    --env-file "$PREVIOUS_RELEASE/release.env" \
    --file "$PREVIOUS_RELEASE/compose.yaml" up --detach --remove-orphans
  ln -sfn "$PREVIOUS_RELEASE" "${CURRENT_LINK}.rollback"
  mv -Tf "${CURRENT_LINK}.rollback" "$CURRENT_LINK"
}

rollback_on_error() {
  status=$?
  trap - ERR
  if [[ "$PROMOTION_STARTED" == true ]]; then
    compose stop || true
    rollback_current_release || true
  fi
  exit "$status"
}
trap rollback_on_error ERR

PROMOTION_STARTED=true
stop_current_release
compose up --detach --remove-orphans

verify_health() {
  local backend_ready=false
  local serving_ready=false
  local trainer_ready=false
  for _ in $(seq 1 180); do
    backend_ready=false
    serving_ready=false
    trainer_ready=false
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:8787/health | grep -q '"status":"ok"' \
      && backend_ready=true || true
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:8791/health | grep -q '"status":"ok"' \
      && serving_ready=true || true
    [[ -s /var/lib/babel-online/trainer-ready.json ]] && trainer_ready=true
    if [[ "$backend_ready" == true && "$serving_ready" == true && "$trainer_ready" == true ]]; then
      return 0
    fi
    sleep 2
  done
  compose ps
  compose logs --tail 100
  return 1
}

verify_health
for service in backend serving trainer; do
  running="$(compose ps --status running --services | grep -cx "$service")"
  [[ "$running" == 1 ]]
  container_id="$(compose ps --quiet "$service")"
  revision="$(docker inspect --format \
    '{{ index .Config.Labels "dev.babel.source-commit" }}' "$container_id")"
  [[ "$revision" == "$BABEL_SOURCE_COMMIT" ]]
done

deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 "$NEW_RELEASE/release.py" receipt \
  "$NEW_RELEASE/release.env" "$NEW_RELEASE/deployment-receipt.json" \
  --deployed-at "$deployed_at"
ln -sfn "$NEW_RELEASE" "${CURRENT_LINK}.next"
mv -Tf "${CURRENT_LINK}.next" "$CURRENT_LINK"
PROMOTION_STARTED=false
trap - ERR
cat "$NEW_RELEASE/deployment-receipt.json"
