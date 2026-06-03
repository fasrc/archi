#!/usr/bin/env bash
# Rebuild and restart the benchmark container after a code change.
#
# The benchmark image bakes src/ at build time via `COPY archi_code src`, so
# editing service_benchmark.py (or any other archi source) requires a fresh
# build. `docker compose up -d` alone reuses the cached image.
#
# Usage:
#   scripts/benchmarking/rebuild_benchmark.sh                # bench-dryrun
#   scripts/benchmarking/rebuild_benchmark.sh -n my-bench   # different deploy
#   scripts/benchmarking/rebuild_benchmark.sh -n my-bench --no-follow
#
# By default the script tails the container log after restart; pass --no-follow
# to return immediately.

set -euo pipefail

NAME="bench-dryrun"
FOLLOW="true"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--name)
            NAME="$2"
            shift 2
            ;;
        --no-follow)
            FOLLOW="false"
            shift
            ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's|^# \?||'
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

COMPOSE_FILE="${HOME}/.archi/archi-${NAME}/compose.yaml"
CONTAINER="benchmarking-${NAME}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: compose file not found at $COMPOSE_FILE" >&2
    echo "Did you run 'archi evaluate -n ${NAME} ...' first?" >&2
    exit 1
fi

echo "==> Stopping ${CONTAINER}..."
docker stop "$CONTAINER" 2>/dev/null || echo "    (not running; continuing)"

echo "==> Rebuilding benchmark service (--no-cache)..."
docker compose -f "$COMPOSE_FILE" build --no-cache benchmark

echo "==> Starting benchmark service..."
docker compose -f "$COMPOSE_FILE" up -d benchmark

if [[ "$FOLLOW" == "true" ]]; then
    echo "==> Tailing logs (Ctrl+C to detach; container keeps running)..."
    docker logs -f "$CONTAINER" 2>&1
else
    echo "==> Done. Tail logs with: docker logs -f ${CONTAINER}"
fi
