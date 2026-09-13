#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 ]] || { echo 'Usage: smoke.sh https://site.example' >&2; exit 2; }
base=${1%/}
[[ $base =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || { echo 'SITE_BASE must be one HTTPS origin without a path.' >&2; exit 2; }
curl_args=(--fail --silent --show-error --location --max-time 20)
if [[ -n ${SMOKE_CURL_CA_BUNDLE:-} ]]; then curl_args+=(--cacert "$SMOKE_CURL_CA_BUNDLE"); fi
get() { curl "${curl_args[@]}" "$1"; }
status() { curl "${curl_args[@]}" --output /dev/null --write-out '%{http_code}' "$1"; }

[[ $(get "$base/healthz") == *'"status": "ok"'* || $(get "$base/healthz") == *'"status":"ok"'* ]] || { echo 'healthz did not return ok' >&2; exit 1; }
home=$(get "$base/")
[[ $home == *'dhelmy.stream'* ]] || { echo 'home response is unexpected' >&2; exit 1; }
get "$base/galaxy" >/dev/null
[[ $(get "$base/api/graph") == *'"nodes"'* ]] || { echo 'graph response is not JSON graph data' >&2; exit 1; }
asset=$(printf '%s' "$home" | sed -nE 's/.*(\/static\/study\/site\.[a-f0-9]+\.js).*/\1/p' | head -n1)
[[ -n $asset ]] || { echo 'hashed site asset was not found in home HTML' >&2; exit 1; }
get "$base$asset" >/dev/null
[[ $(status "$base/this-must-not-exist") == 404 ]] || { echo 'missing route did not return 404' >&2; exit 1; }
[[ $(status "$base/.env") == 404 ]] || { echo 'private-file probe did not return 404' >&2; exit 1; }
if [[ -n ${SMOKE_HTTP_BASE:-} ]]; then
  redirect=$(curl --silent --show-error --output /dev/null --write-out '%{http_code} %{redirect_url}' "${SMOKE_HTTP_BASE%/}/healthz")
  [[ $redirect == 301\ https://* || $redirect == 308\ https://* ]] || { echo "HTTP did not redirect to HTTPS: $redirect" >&2; exit 1; }
fi
echo "Smoke checks passed for $base"
