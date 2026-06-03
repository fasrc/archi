#!/usr/bin/env bash
# Rebuild and restart the benchmark container after a code change.
#
# The benchmark image bakes src/ at build time via `COPY archi_code src` —
# but the build context is ~/.archi/archi-<name>/, NOT the project tree. So
# editing src/ in the project doesn't reach the image unless we also sync
# the changes into the deploy dir's archi_code/ subdir. (Otherwise
# `docker compose build --no-cache` just rebuilds from the stale snapshot
# taken at last `archi evaluate` time.)
#
# This script does the full edit-loop:
#   1. rsync project src/ -> ~/.archi/archi-<name>/archi_code/
#   2. docker compose build --no-cache benchmark
#   3. docker compose up -d benchmark
#   4. tail the container log
#
# Usage:
#   scripts/benchmarking/rebuild_benchmark.sh                # bench-dryrun
#   scripts/benchmarking/rebuild_benchmark.sh -n my-bench   # different deploy
#   scripts/benchmarking/rebuild_benchmark.sh -n my-bench --no-follow
#   scripts/benchmarking/rebuild_benchmark.sh --skip-sync   # build only, no src sync

set -euo pipefail

NAME="bench-dryrun"
FOLLOW="true"
SKIP_SYNC="false"

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
        --skip-sync)
            SKIP_SYNC="true"
            shift
            ;;
        -h|--help)
            sed -n '2,22p' "$0" | sed 's|^# \?||'
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

DEPLOY_DIR="${HOME}/.archi/archi-${NAME}"
COMPOSE_FILE="${DEPLOY_DIR}/compose.yaml"
CONTAINER="benchmarking-${NAME}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "ERROR: compose file not found at $COMPOSE_FILE" >&2
    echo "Did you run 'archi evaluate -n ${NAME} ...' first?" >&2
    exit 1
fi

# Find the project root (where src/ lives). Walk up from this script's dir.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ "$SKIP_SYNC" == "false" ]]; then
    SRC_DIR="${PROJECT_ROOT}/src"
    DEST_DIR="${DEPLOY_DIR}/archi_code"
    if [[ ! -d "$SRC_DIR" ]]; then
        echo "ERROR: project src/ not found at $SRC_DIR" >&2
        exit 1
    fi
    if [[ ! -d "$DEST_DIR" ]]; then
        echo "ERROR: deploy archi_code/ not found at $DEST_DIR" >&2
        echo "Run 'archi evaluate -n ${NAME} ...' once to scaffold the deploy dir." >&2
        exit 1
    fi
    echo "==> Syncing ${SRC_DIR}/  ->  ${DEST_DIR}/"
    rsync -a --delete "${SRC_DIR}/" "${DEST_DIR}/"
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
