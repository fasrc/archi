#!/usr/bin/env bash
# Run the RAGAS benchmark against the 105-question golden-set bank.
#
# Uses the fasrc_ragas_queries.yaml config (SUT: vLLM Qwen 3.6 on :8001,
# judge: HUIT Bedrock Claude Sonnet 4.5, 300s timeout). The benchmark
# container runs on the host network (--hostmode) so it can reach the SUT.
#
# --force reuses the existing deployment name, rebuilding containers but
# preserving the data volume (the ingested KB). Without --force, a second
# run of the same name is refused.
#
# Usage:
#   scripts/benchmarking/run_ragas_eval.sh              # default: ragas-devbench, --force
#   scripts/benchmarking/run_ragas_eval.sh --fresh       # no --force: fail if exists
#   scripts/benchmarking/run_ragas_eval.sh -n my-bench   # different deployment name
#   scripts/benchmarking/run_ragas_eval.sh --no-follow   # don't tail the container log
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

NAME="ragas-devbench"
CONFIG="config/benchmarking/fasrc_ragas_queries.yaml"
ENV_FILE="${RAGAS_ENV_FILE:-$HOME/.archi/.env.benchmark}"
FORCE="true"
FOLLOW="true"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--name)    NAME="$2"; shift 2 ;;
        -c|--config)  CONFIG="$2"; shift 2 ;;
        -e|--env)     ENV_FILE="$2"; shift 2 ;;
        --fresh)      FORCE="false"; shift ;;
        --no-follow)  FOLLOW="false"; shift ;;
        -h|--help)    sed -n '2,17p' "$0" | sed 's|^# \?||'; exit 0 ;;
        *)            echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"

if [[ ! -f "$CONFIG" ]]; then
    echo "ERROR: config not found: $CONFIG" >&2
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: env file not found: $ENV_FILE" >&2
    echo "Expected HUIT_API_KEY for the RAGAS judge." >&2
    exit 1
fi

CONTAINER="benchmarking-$NAME"
FORCE_FLAG=""
[[ "$FORCE" == "true" ]] && FORCE_FLAG="--force"

echo "==> RAGAS eval: name=$NAME config=$CONFIG"
echo "    env=$ENV_FILE force=$FORCE"
echo "    container=$CONTAINER"
echo ""

archi evaluate \
    --name "$NAME" \
    --config "$CONFIG" \
    --env-file "$ENV_FILE" \
    --hostmode \
    $FORCE_FLAG

echo ""
echo "==> Benchmark deployed. Container: $CONTAINER"
echo "    Results will land in: bench_out/"

if [[ "$FOLLOW" == "true" ]]; then
    echo "==> Tailing logs (Ctrl+C to detach; container keeps running)..."
    docker logs -f "$CONTAINER" 2>&1
else
    echo "==> Tail logs with: docker logs -f $CONTAINER"
fi
