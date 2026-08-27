#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: condition3_gate.sh FRESH_GATE_TRIAL_UUID" >&2
  exit 2
fi

GATE_TRIAL_ID=$1
CURRENT_RELEASE=/opt/babel/current
RUNTIME_ENV=/etc/babel/runtime.env
PROJECT=babel-gcp-demo

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

set -a
source "$CURRENT_RELEASE/release.env"
source "$RUNTIME_ENV"
set +a
if [[ "${BABEL_ONLINE_ALLOW_POPULATION_BUILD:-false}" != false ]]; then
  echo "BABEL_ONLINE_ALLOW_POPULATION_BUILD=false is required" >&2
  exit 1
fi
if [[ -z "${BABEL_PERFORMANCE_WORKER_TOKEN:-}" ]]; then
  echo "BABEL_PERFORMANCE_WORKER_TOKEN is required" >&2
  exit 1
fi

compose() {
  docker compose --project-name "$PROJECT" \
    --profile matrix \
    --env-file "$CURRENT_RELEASE/release.env" \
    --file "$CURRENT_RELEASE/compose.yaml" "$@"
}

restore_regular_services() {
  compose stop performance-worker >/dev/null 2>&1 || true
  compose up --detach backend serving trainer >/dev/null 2>&1 || true
}
trap restore_regular_services ERR INT TERM

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

status_phase() {
  curl --fail --silent --show-error --max-time 2 \
    --header "X-Babel-Worker-Token: $BABEL_PERFORMANCE_WORKER_TOKEN" \
    http://127.0.0.1:8792/v1/performance/status \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["phase"])'
}

curl --fail --silent --show-error --max-time 10 \
  --request POST \
  --header "X-Babel-Worker-Token: $BABEL_PERFORMANCE_WORKER_TOKEN" \
  "http://127.0.0.1:8792/v1/performance/${BABEL_GCP_TRIAL_ID}/prepare-condition-3-gate/${GATE_TRIAL_ID}" \
  >/dev/null

for _ in $(seq 1 1200); do
  phase="$(status_phase)"
  if [[ "$phase" == condition3_gate_ready ]]; then
    break
  fi
  if [[ "$phase" == failed || "$phase" == interrupted ]]; then
    echo "Condition 3 preparation failed with phase $phase" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$(status_phase)" != condition3_gate_ready ]]; then
  echo "Condition 3 preparation timed out" >&2
  exit 1
fi

curl --fail --silent --show-error --max-time 10 \
  --request POST \
  --header "X-Babel-Worker-Token: $BABEL_PERFORMANCE_WORKER_TOKEN" \
  "http://127.0.0.1:8792/v1/performance/${GATE_TRIAL_ID}/condition-3-gate" \
  >/dev/null

for _ in $(seq 1 1800); do
  phase="$(status_phase)"
  if [[ "$phase" == condition3_gate_passed ]]; then
    break
  fi
  if [[ "$phase" == failed || "$phase" == interrupted ]]; then
    echo "Condition 3 gate failed with phase $phase" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$(status_phase)" != condition3_gate_passed ]]; then
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
trap - ERR INT TERM
