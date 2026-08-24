set shell := ["bash", "-euo", "pipefail", "-c"]

db-up:
    docker compose up -d postgres

build:
    cmake --preset dev
    cmake --build --preset dev

test:
    cmake --preset test
    cmake --build --preset test
    ctest --preset test --output-on-failure
    npm test

migrate-personal source: build
    ./build/dev/backend/babel_backend migrate
    ./build/dev/backend/babel_backend migrate-personal --source "{{source}}"

start: build
    #!/usr/bin/env bash
    set -euo pipefail
    backend="./build/dev/backend/babel_backend"
    log_file="$(mktemp -t babel-backend.XXXXXX.log)"
    backend_pid=""
    cleanup() {
        if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null; then
            kill "$backend_pid" 2>/dev/null || true
            wait "$backend_pid" 2>/dev/null || true
        fi
        rm -f "$log_file"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    "$backend" migrate
    "$backend" serve >"$log_file" 2>&1 &
    backend_pid=$!

    ready=false
    for _ in $(seq 1 60); do
        if curl --fail --silent --show-error --max-time 1 \
            http://127.0.0.1:8787/health >/dev/null 2>&1; then
            ready=true
            break
        fi
        if ! kill -0 "$backend_pid" 2>/dev/null; then
            break
        fi
        sleep 0.25
    done
    if [[ "$ready" != true ]]; then
        echo "Babel backend did not become healthy" >&2
        tail -n 40 "$log_file" >&2 || true
        exit 1
    fi

    echo "Babel admin: http://127.0.0.1:8787/admin"
    npm start
