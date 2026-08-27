#!/usr/bin/env bash

babel_acquire_rollout_lock() {
  local lock_path=$1
  local timeout_seconds=$2
  if ! exec 9>"$lock_path"; then
    echo "failed to open the Babel rollout lock file" >&2
    return 1
  fi
  if ! flock --wait "$timeout_seconds" 9; then
    return 1
  fi
  return 0
}

babel_finish_or_rollback() {
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

babel_install_rollout_traps() {
  trap babel_finish_or_rollback EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  trap 'exit 129' HUP
}

babel_restore_previous_release() {
  local candidate_release=$1
  local previous_release=$2
  local ready_path=$3
  local current_link=$4
  local rollback_started_ns next_link

  # A failed stop is recoverable only if `up` below replaces the project and
  # the complete previous release subsequently attests. Record it explicitly;
  # do not let errexit/conditional-call semantics hide the result.
  if ! compose_for "$candidate_release" stop >/dev/null 2>&1; then
    echo "candidate release did not stop cleanly; attempting attested restore" >&2
  fi
  if [[ -z "$previous_release" || ! -d "$previous_release" ]]; then
    echo "new deployment failed and no previous release exists" >&2
    return 1
  fi
  if ! rm -f "$ready_path"; then
    echo "failed to remove stale trainer readiness before rollback" >&2
    return 1
  fi
  if ! rollback_started_ns="$(date +%s%N)"; then
    echo "failed to timestamp rollback" >&2
    return 1
  fi
  if ! compose_for "$previous_release" up --detach --remove-orphans; then
    echo "previous release failed to start" >&2
    return 1
  fi
  if ! verify_release "$previous_release" "$rollback_started_ns"; then
    echo "previous release could not be health/readiness/image attested" >&2
    return 1
  fi
  next_link="${current_link}.rollback"
  if ! ln -sfn "$previous_release" "$next_link"; then
    echo "failed to stage the restored current symlink" >&2
    return 1
  fi
  if ! mv -Tf "$next_link" "$current_link"; then
    echo "failed to promote the restored current symlink" >&2
    return 1
  fi
  return 0
}
