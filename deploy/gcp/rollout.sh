#!/usr/bin/env bash
set -Eeuo pipefail

acquire_rollout_lock() {
  local lock_path=$1
  local timeout_seconds=$2
  exec 9>"$lock_path"
  flock --wait "$timeout_seconds" 9
}

finish_or_rollback() {
  local status=$?
  trap - EXIT
  trap '' TERM INT HUP
  if [[ "${PROMOTION_STARTED:-false}" == true ]]; then
    if ! rollback_current_release; then
      status=70
    fi
  fi
  exit "$status"
}

install_rollout_traps() {
  trap finish_or_rollback EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
}

if [[ "${BABEL_ROLLOUT_LIBRARY_ONLY:-false}" == true ]]; then
  return 0
fi

if [[ $# -ne 1 ]]; then
  echo "usage: rollout.sh RELEASE_PACKAGE_DIRECTORY" >&2
  exit 2
fi

PACKAGE_DIR="$(realpath "$1")"
RELEASE_NAME="$(basename "$PACKAGE_DIR")"
RELEASE_ROOT=/opt/babel/releases
CURRENT_LINK=/opt/babel/current
RUNTIME_ENV=/etc/babel/runtime.env
READY_PATH=/var/lib/babel-online/trainer-ready.json
PROJECT=babel-gcp-demo
PROMOTION_STARTED=false
PREVIOUS_RELEASE=""
NEW_RELEASE="${RELEASE_ROOT}/${RELEASE_NAME}"

if [[ ! "$RELEASE_NAME" =~ ^babel-release-[a-f0-9]{40}-[0-9]+-[0-9]+$ ]]; then
  echo "release package name is not canonical" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
  echo "$RUNTIME_ENV is required and must be provisioned outside GitHub" >&2
  exit 1
fi

if ! acquire_rollout_lock /var/lock/babel-gcp-demo-rollout.lock 900; then
  echo "timed out waiting for the exclusive Babel rollout lock" >&2
  exit 75
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
install -m 0700 predeploy.py "$NEW_RELEASE/predeploy.py"
install -m 0600 compose.yaml "$NEW_RELEASE/compose.yaml"
install -d -m 0750 -o 10001 -g 10001 /var/lib/babel-online

compose_for() {
  local release_dir=$1
  shift
  docker compose --project-name "$PROJECT" \
    --env-file "$release_dir/release.env" \
    --file "$release_dir/compose.yaml" "$@"
}

compose() {
  compose_for "$NEW_RELEASE" "$@"
}

release_value() {
  local release_dir=$1
  local key=$2
  sed -n "s/^${key}=//p" "$release_dir/release.env"
}

if [[ -L "$CURRENT_LINK" ]]; then
  PREVIOUS_RELEASE="$(readlink -f "$CURRENT_LINK")"
  python3 "$NEW_RELEASE/release.py" assert-newer \
    "$NEW_RELEASE/release.env" "$PREVIOUS_RELEASE/release.env"
fi

attest_containers() {
  local release_dir=$1
  local source_commit model_revision dataset_revision service key expected_ref container_id actual_ref actual_id expected_id revision compose_revision compose_model_revision compose_dataset_revision
  source_commit="$(release_value "$release_dir" BABEL_SOURCE_COMMIT)"
  model_revision="$(release_value "$release_dir" BABEL_MODEL_REVISION)"
  dataset_revision="$(release_value "$release_dir" BABEL_DATASET_REVISION)"
  for service in backend serving trainer; do
    case "$service" in
      backend) key=BABEL_BACKEND_IMAGE ;;
      serving) key=BABEL_SERVING_IMAGE ;;
      trainer) key=BABEL_TRAINER_IMAGE ;;
    esac
    expected_ref="$(release_value "$release_dir" "$key")"
    [[ "$(compose_for "$release_dir" ps --status running --services | grep -cx "$service")" == 1 ]]
    container_id="$(compose_for "$release_dir" ps --quiet "$service")"
    actual_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
    actual_id="$(docker inspect --format '{{.Image}}' "$container_id")"
    expected_id="$(docker image inspect --format '{{.Id}}' "$expected_ref")"
    revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$expected_ref")"
    compose_revision="$(docker inspect --format '{{ index .Config.Labels "dev.babel.source-commit" }}' "$container_id")"
    compose_model_revision="$(docker inspect --format '{{ index .Config.Labels "dev.babel.model-revision" }}' "$container_id")"
    compose_dataset_revision="$(docker inspect --format '{{ index .Config.Labels "dev.babel.dataset-revision" }}' "$container_id")"
    [[ "$actual_ref" == "$expected_ref" ]]
    [[ "$actual_id" == "$expected_id" ]]
    [[ "$revision" == "$source_commit" ]]
    [[ "$compose_revision" == "$source_commit" ]]
    [[ "$compose_model_revision" == "$model_revision" ]]
    [[ "$compose_dataset_revision" == "$dataset_revision" ]]
  done
}

verify_release() {
  local release_dir=$1
  local not_before_ns=$2
  local run_id health_file
  run_id="$(release_value "$release_dir" BABEL_GCP_RUN_ID)"
  health_file="$release_dir/serving-health.json"
  for _ in $(seq 1 180); do
    if curl --fail --silent --show-error --max-time 2 \
         http://127.0.0.1:8787/health | grep -q '"status":"ok"' \
       && curl --fail --silent --show-error --max-time 2 \
         http://127.0.0.1:8791/health >"$health_file" \
       && python3 "$NEW_RELEASE/release.py" validate-serving-health "$health_file" \
       && python3 "$NEW_RELEASE/release.py" validate-trainer-readiness "$READY_PATH" \
         --run-id "$run_id" --not-before-ns "$not_before_ns"; then
      attest_containers "$release_dir"
      return 0
    fi
    sleep 2
  done
  compose_for "$release_dir" ps >&2
  compose_for "$release_dir" logs --tail 100 >&2
  return 1
}

rollback_current_release() {
  local rollback_failed=false
  local rollback_started_ns
  compose stop || rollback_failed=true
  if [[ -z "$PREVIOUS_RELEASE" || ! -d "$PREVIOUS_RELEASE" ]]; then
    echo "new deployment failed and no previous release exists" >&2
    return 1
  fi
  rm -f "$READY_PATH"
  rollback_started_ns="$(date +%s%N)"
  compose_for "$PREVIOUS_RELEASE" up --detach --remove-orphans || rollback_failed=true
  if [[ "$rollback_failed" == false ]] \
     && verify_release "$PREVIOUS_RELEASE" "$rollback_started_ns"; then
    ln -sfn "$PREVIOUS_RELEASE" "${CURRENT_LINK}.rollback"
    mv -Tf "${CURRENT_LINK}.rollback" "$CURRENT_LINK"
    return 0
  fi
  echo "previous release could not be restored and attested" >&2
  return 1
}

install_rollout_traps

compose pull
source "$NEW_RELEASE/release.env"
docker run --rm --gpus all "$BABEL_SERVING_IMAGE" \
  python -c 'import torch; assert torch.cuda.is_available(), "CUDA is unavailable"'

# Demo CD permits only the unchanged 326b840 migration set. These forward-only,
# idempotent migrations run before promotion; CI rejects migration-file changes.
compose run --rm --no-deps backend migrate
docker run --rm --network host --env-file "$RUNTIME_ENV" \
  --volume "$NEW_RELEASE/predeploy.py:/opt/babel-predeploy.py:ro" \
  --entrypoint python "$BABEL_TRAINER_IMAGE" /opt/babel-predeploy.py \
  --trial-id "$BABEL_GCP_TRIAL_ID" \
  --run-id "$BABEL_GCP_RUN_ID" \
  --population-vector-sha256 "$BABEL_POPULATION_VECTOR_SHA256" \
  --population-snapshot-sha256 "$BABEL_POPULATION_SNAPSHOT_SHA256" \
  >"$NEW_RELEASE/predeploy-evidence.json"

PROMOTION_STARTED=true
if [[ -n "$PREVIOUS_RELEASE" ]]; then
  compose_for "$PREVIOUS_RELEASE" stop
fi
rm -f "$READY_PATH"
promotion_started_ns="$(date +%s%N)"
compose up --detach --remove-orphans
verify_release "$NEW_RELEASE" "$promotion_started_ns"

sample_creator_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sampleCreatorId"])' "$NEW_RELEASE/predeploy-evidence.json")"
article_number="$(date +%s%N)"
python3 - "$BABEL_GCP_RUN_ID" "$sample_creator_id" "$article_number" >"$NEW_RELEASE/smoke-request.json" <<'PY'
import json
import sys
from uuid import uuid4

run_id, creator_id, article_number = sys.argv[1:]
json.dump(
    {
        "schemaVersion": 2,
        "requestId": str(uuid4()),
        "runId": run_id,
        "creatorId": creator_id,
        "sourceBabelId": str(uuid4()),
        "sourceArticleKey": f"enwiki:{article_number}",
        "traversalSessionId": str(uuid4()),
        "parentRequestId": None,
        "traversalDepth": 0,
        "title": "GCP deployment CUDA smoke",
        "text": "A bounded recommendation request verifies the pinned Qwen CUDA serving path.",
        "historyBabelIds": [],
        "candidateCount": 10,
    },
    sys.stdout,
    sort_keys=True,
    separators=(",", ":"),
)
PY
curl --fail --silent --show-error --max-time 120 \
  --header 'Content-Type: application/json' \
  --dump-header "$NEW_RELEASE/smoke-headers.txt" \
  --data-binary "@$NEW_RELEASE/smoke-request.json" \
  http://127.0.0.1:8791/api/v2/recommendations \
  >"$NEW_RELEASE/smoke-response.json"
grep -qi $'^x-babel-encoder-device: cuda\r$' "$NEW_RELEASE/smoke-headers.txt"
python3 "$NEW_RELEASE/release.py" validate-serving-smoke \
  "$NEW_RELEASE/smoke-response.json" --run-id "$BABEL_GCP_RUN_ID"

deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 "$NEW_RELEASE/release.py" receipt \
  "$NEW_RELEASE/release.env" "$NEW_RELEASE/deployment-receipt.json" \
  --deployed-at "$deployed_at"
ln -sfn "$NEW_RELEASE" "${CURRENT_LINK}.next"
mv -Tf "${CURRENT_LINK}.next" "$CURRENT_LINK"
PROMOTION_STARTED=false
cat "$NEW_RELEASE/deployment-receipt.json"
