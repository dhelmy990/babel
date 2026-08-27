#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: condition3_gate.sh FRESH_GATE_TRIAL_UUID" >&2
  exit 2
fi

GATE_TRIAL_ID=$1
CURRENT_RELEASE=/opt/babel/current
RUNTIME_ENV=/etc/babel/runtime.env
READY_PATH=/var/lib/babel-online/trainer-ready.json
PROJECT=babel-gcp-demo
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rollout_supervisor.sh"

if [[ ! "$GATE_TRIAL_ID" =~ ^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$ ]]; then
  echo "gate trial ID must be a canonical UUID" >&2
  exit 2
fi
if [[ ! -L "$CURRENT_RELEASE" || ! -f "$CURRENT_RELEASE/release.env" ]]; then
  echo "an attested current release is required" >&2
  exit 1
fi
if [[ ! -f "$CURRENT_RELEASE/smoke-response.json" ]]; then
  echo "the regular CUDA serving smoke must pass before Condition 3" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
  echo "$RUNTIME_ENV is required" >&2
  exit 1
fi
if ! babel_acquire_rollout_lock /var/lock/babel-gcp-demo-rollout.lock 900; then
  echo "timed out waiting for the exclusive Babel rollout lock" >&2
  exit 75
fi

set -a
source "$CURRENT_RELEASE/release.env"
set +a
if [[ "${BABEL_ONLINE_ALLOW_POPULATION_BUILD:-false}" != false ]]; then
  echo "BABEL_ONLINE_ALLOW_POPULATION_BUILD=false is required" >&2
  exit 1
fi
if ! BABEL_PERFORMANCE_WORKER_TOKEN="$(
  python3 "$CURRENT_RELEASE/release.py" runtime-token "$RUNTIME_ENV"
)"; then
  echo "BABEL_PERFORMANCE_WORKER_TOKEN is invalid" >&2
  exit 1
fi

compose() {
  docker compose --project-name "$PROJECT" \
    --profile matrix \
    --env-file "$CURRENT_RELEASE/release.env" \
    --file "$CURRENT_RELEASE/compose.yaml" "$@"
}

RESTORE_REQUIRED=false

trainer_instance_evidence() {
  local container_id
  if ! container_id="$(compose ps --quiet trainer)" || [[ -z "$container_id" ]]; then
    return 1
  fi
  docker inspect --format \
    '{"containerId":"{{.Id}}","startedAt":"{{.State.StartedAt}}","pid":{{.State.Pid}},"restartCount":{{.RestartCount}}}' \
    "$container_id"
}

restore_regular_services() {
  local restored=false readiness_reset=true timestamped=true evidence_available=true
  local evidence_dir before_file after_file health_file restore_started_ns
  if ! evidence_dir="$(mktemp -d /run/babel-condition3-restore.XXXXXX)"; then
    echo "failed to create Condition 3 restoration evidence directory" >&2
    evidence_available=false
    evidence_dir=""
    before_file=""
    after_file=""
    health_file=""
  else
    before_file="$evidence_dir/trainer-before.json"
    after_file="$evidence_dir/trainer-after.json"
    health_file="$evidence_dir/serving-health.json"
  fi
  if ! compose stop performance-worker; then
    echo "Condition 3 worker did not stop cleanly; attempting restore" >&2
  fi
  if ! rm -f "$READY_PATH"; then
    echo "failed to remove stale trainer readiness before Condition 3 restore" >&2
    readiness_reset=false
  fi
  if ! restore_started_ns="$(date +%s%N)"; then
    echo "failed to timestamp Condition 3 restoration" >&2
    timestamped=false
  fi
  if ! compose up --detach backend serving trainer; then
    echo "failed to start regular services after Condition 3" >&2
  elif [[ "$readiness_reset" == true \
       && "$timestamped" == true \
       && "$evidence_available" == true ]]; then
    for _ in $(seq 1 120); do
      if [[ "$(compose ps --status running --services | grep -c '^backend$')" == 1 \
         && "$(compose ps --status running --services | grep -c '^serving$')" == 1 \
         && "$(compose ps --status running --services | grep -c '^trainer$')" == 1 \
         && "$(compose ps --status running --services | grep -c '^performance-worker$')" == 0 ]] \
         && trainer_instance_evidence >"$before_file" \
         && curl --fail --silent --show-error --max-time 2 \
           http://127.0.0.1:8787/health >/dev/null \
         && curl --fail --silent --show-error --max-time 2 \
           http://127.0.0.1:8791/health >"$health_file" \
         && python3 "$CURRENT_RELEASE/release.py" \
           validate-serving-health "$health_file" \
         && trainer_instance_evidence >"$after_file" \
         && python3 "$CURRENT_RELEASE/release.py" validate-trainer-instance \
           "$READY_PATH" "$before_file" "$after_file" \
           --run-id "$BABEL_GCP_RUN_ID" \
           --not-before-ns "$restore_started_ns"; then
        restored=true
        break
      fi
      sleep 1
    done
  fi
  if [[ "$restored" != true ]]; then
    echo "failed to restore regular services after Condition 3" >&2
    compose ps >&2 || true
    compose logs --tail 100 >&2 || true
  fi
  if [[ "$evidence_available" == true ]]; then
    rm -f "$before_file" "$after_file" "$health_file"
    if ! rmdir "$evidence_dir"; then
      echo "failed to remove Condition 3 restoration evidence directory" >&2
      restored=false
    fi
  fi
  [[ "$restored" == true ]]
}

condition3_finish() {
  local status=$?
  trap - EXIT
  trap '' TERM INT HUP
  if [[ "$RESTORE_REQUIRED" == true ]] && ! restore_regular_services; then
    status=70
  fi
  exit "$status"
}

trap condition3_finish EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

guard="$(
  compose run --rm --no-deps --entrypoint /bin/sh performance-worker \
    -c 'printf %s "$BABEL_ONLINE_ALLOW_POPULATION_BUILD"'
)"
if [[ "$guard" != false ]]; then
  echo "performance worker population-build guard differs" >&2
  exit 1
fi

# Matrix-owned serving binds 8791, so stop the standalone roles first. The
# already-smoked backend remains available and the worker stays loopback-only.
RESTORE_REQUIRED=true
compose stop serving trainer
compose up --detach backend performance-worker
for _ in $(seq 1 120); do
  if curl --fail --silent --show-error --max-time 2 \
    http://127.0.0.1:8792/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error --max-time 2 \
  http://127.0.0.1:8792/health >/dev/null

worker_curl() {
  printf 'header = "X-Babel-Worker-Token: %s"\n' \
    "$BABEL_PERFORMANCE_WORKER_TOKEN" \
    | curl --config - "$@"
}

status_phase() {
  local expected=$1
  worker_curl --fail --silent --show-error --max-time 2 \
    --url http://127.0.0.1:8792/v1/performance/status \
    | python3 -c '
import json
import sys
document = json.load(sys.stdin)
expected = sys.argv[1]
if document.get("experimentId") != expected:
    raise SystemExit("Condition 3 worker status belongs to another experiment")
print(document["phase"])
' "$expected"
}

worker_curl --fail --silent --show-error --max-time 10 \
  --request POST \
  --url "http://127.0.0.1:8792/v1/performance/${BABEL_GCP_TRIAL_ID}/prepare-condition-3-gate/${GATE_TRIAL_ID}" \
  >/dev/null

for _ in $(seq 1 1200); do
  phase="$(status_phase "$GATE_TRIAL_ID")"
  if [[ "$phase" == condition3_gate_ready ]]; then
    break
  fi
  if [[ "$phase" == failed || "$phase" == interrupted ]]; then
    echo "Condition 3 preparation failed with phase $phase" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$(status_phase "$GATE_TRIAL_ID")" != condition3_gate_ready ]]; then
  echo "Condition 3 preparation timed out" >&2
  exit 1
fi

worker_curl --fail --silent --show-error --max-time 10 \
  --request POST \
  --url "http://127.0.0.1:8792/v1/performance/${GATE_TRIAL_ID}/condition-3-gate" \
  >/dev/null

for _ in $(seq 1 1800); do
  phase="$(status_phase "$GATE_TRIAL_ID")"
  if [[ "$phase" == condition3_gate_passed ]]; then
    break
  fi
  if [[ "$phase" == failed || "$phase" == interrupted ]]; then
    echo "Condition 3 gate failed with phase $phase" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$(status_phase "$GATE_TRIAL_ID")" != condition3_gate_passed ]]; then
  echo "Condition 3 gate timed out" >&2
  exit 1
fi

receipt="/var/lib/babel-online/performance/${GATE_TRIAL_ID}/condition-3-gate.json"
python3 - "$receipt" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    document.get("status") != "passed"
    or document.get("condition")
    != "same_process.training_and_activation.pgvector"
    or document.get("cleanupVerified") is not True
    or document.get("activationVerified") is not True
    or document.get("offsetCoverageVerified") is not True
    or document.get("finalKafkaLag") != 0
    or document.get("autoContinued") is not False
):
    raise SystemExit("Condition 3 receipt failed closed validation")
print(json.dumps(document, sort_keys=True, separators=(",", ":")))
PY

# Intentionally leave only backend + performance-worker running. The caller
# must separately approve the complete formal matrix; this command never does.
RESTORE_REQUIRED=false
trap - EXIT TERM INT HUP
