#!/usr/bin/env bash
# Read-only checks. Dependencies: bash, curl, python3; no session or provider calls.
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: smoke.sh https://site.example' >&2; exit 2; }
base=${1%/}
http_base=${SMOKE_HTTP_BASE:-http://${base#https://}}
http_base=${http_base%/}
python3 - "$base" "$http_base" <<'PY' || exit 2
import re, sys
from urllib.parse import urlsplit
for value, scheme in zip(sys.argv[1:], ('https', 'http')):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        labels = host.split('.')
        valid = (parsed.scheme == scheme and not parsed.path and not parsed.query
                 and not parsed.fragment and not parsed.username and not parsed.password
                 and re.fullmatch(r'https?://[^/?#]+', value)
                 and re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?', parsed.netloc)
                 and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels)
                 and (parsed.port is None or 1 <= parsed.port <= 65535)
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        sys.exit('Expected a valid ' + scheme.upper() + ' origin without credentials, path, query or fragment.')
PY
curl_args=(--silent --show-error --connect-timeout 5 --max-time 20 --proto '=http,https')
if [[ -n ${SMOKE_CURL_CA_BUNDLE:-} ]]; then
    [[ -r $SMOKE_CURL_CA_BUNDLE && -f $SMOKE_CURL_CA_BUNDLE ]] || { echo 'CA bundle must be a readable certificate file.' >&2; exit 2; }
    curl_args+=(--cacert "$SMOKE_CURL_CA_BUNDLE")
fi
fail() { echo "Smoke failed: $*" >&2; exit 1; }
# Do not follow redirects: each application endpoint must answer directly, and
# the HTTP redirect must preserve the exact expected origin and path.
redirect=$(curl "${curl_args[@]}" --output /dev/null --write-out '%{http_code} %{redirect_url}' "$http_base/healthz") || fail 'HTTP redirect request failed.'
[[ $redirect == "301 $base/healthz" || $redirect == "308 $base/healthz" ]] || fail 'HTTP must redirect /healthz to the expected HTTPS origin and path.'
get() {
    local response code
    response=$(curl "${curl_args[@]}" --write-out $'\n%{http_code}' "$base$1") || { echo "Request failed: $1" >&2; return 1; }
    code=${response##*$'\n'}
    [[ $code == "$2" ]] || { echo "Unexpected HTTP $code for $1 (expected $2)." >&2; return 1; }
    printf '%s' "${response%$'\n'*}"
}
health=$(get /healthz 200)
printf '%s' "$health" | python3 -c 'import json,sys; assert json.load(sys.stdin) == {"status":"ok"}' 2>/dev/null || fail 'healthz must be JSON with status ok.'
home=$(get / 200)
[[ $home == *'dhelmy.stream'* ]] || fail 'Home response is unexpected.'
get /galaxy 200 >/dev/null
graph=$(get /api/graph 200)
printf '%s' "$graph" | python3 -c '
import json,sys,uuid
try:
    graph=json.load(sys.stdin)
    assert isinstance(graph,dict) and isinstance(graph["nodes"],list) and isinstance(graph["edges"],list)
    ids=set()
    for node in graph["nodes"]:
        uuid.UUID(node["id"])
        assert all(isinstance(node[key],str) for key in ("id","slug","title","excerpt","color"))
        assert node["id"] not in ids
        ids.add(node["id"])
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
except (ValueError,KeyError,TypeError,AssertionError):
    sys.exit(1)
' || fail 'Graph must be valid JSON with article nodes and edges referencing those nodes.'
asset=$(printf '%s' "$home" | python3 -c 'import re,sys; matches=re.findall(r"/static/study/site\.[a-f0-9]{12}\.js",sys.stdin.read()); print(matches[0] if matches else "")')
[[ -n $asset ]] || fail 'Hashed site asset was not found in home HTML.'
[[ -n $(get "$asset" 200) ]] || fail 'Hashed site asset is empty or unavailable.'
# This multi-segment API path cannot collide with the single-segment article slug route.
get /api/__smoke_missing__ 404 >/dev/null
get /.env 404 >/dev/null
echo "Smoke checks passed for $base"
