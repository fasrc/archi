#!/usr/bin/env bash
# Start one RAGAS run of a feature-matrix arm (plan §5.1 / §5.2).
#
#   run_arm.sh <arm> <arm.yaml>                 deploy + ingest + run  (archi evaluate --hostmode)
#   run_arm.sh <arm> --rerun [--stack <name>]   re-run ONLY the benchmark container on the
#                                               existing stack (same corpus → run 2, or a
#                                               re-seeded retrieval arm on fm-00)
#
# `archi evaluate --hostmode` returns as soon as the stack is up; the run itself happens
# inside benchmarking-fm-<arm>. Follow it with `docker logs -f`, then archive_run.sh.
# Needs RAGAS_ENV_FILE (the judge key, HUIT_API_KEY) for the first form.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ARM="${1:-}"; fm_require_arm "$ARM"; shift
STACK="fm-$ARM"; RERUN=false; YAML=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rerun) RERUN=true; shift ;;
    --stack) STACK="${2:?--stack needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) fm_die "unknown option $1" ;;
    *) YAML="$1"; shift ;;
  esac
done

mkdir -p "$FM_OUT"

if [ "$RERUN" = false ]; then
  [ -n "$YAML" ] || fm_die "usage: run_arm.sh <arm> <arm.yaml>  |  run_arm.sh <arm> --rerun [--stack <name>]"
  [ -f "$YAML" ] || fm_die "arm config not found: $YAML (run from ~/Projects/archi so config/... resolves)"
  ENV_FILE="${RAGAS_ENV_FILE:-}"
  [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || fm_die "RAGAS_ENV_FILE must name the judge env file (HUIT_API_KEY); got '${ENV_FILE}'"
  [ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "benchmarking-$STACK is still running"
  fm_log "arm $ARM: deploy + ingest + run as $STACK from $YAML"
  "$FM_ARCHI" evaluate --name "$STACK" --config "$YAML" --env-file "$ENV_FILE" --hostmode
  fm_ledger_append "$(printf '{"arm":"%s","kind":"ragas-start","stack":"%s","config":"%s","started":"%s","rerun":false}' "$ARM" "$STACK" "$YAML" "$(fm_now)")"
  fm_log "stack $STACK is up; the run continues inside benchmarking-$STACK"
  fm_log "follow:  $FM_DOCKER logs -f benchmarking-$STACK"
  fm_log "then:    scripts/benchmarking/feature_matrix/archive_run.sh $ARM 1 --stack $STACK --wait"
  exit 0
fi

# --rerun: never touch Postgres or the data-manager; prove the corpus is still the pinned
# one before and after; recreate only the benchmark container (swap_arm.sh's pattern).
STACK_DIR="$(fm_stack_dir "$STACK")"
[ -f "$STACK_DIR/compose.yaml" ] || fm_die "no deployment at $STACK_DIR"
fm_require_stack_up "$STACK"
[ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "a benchmark run is still in flight on $STACK"
fm_require_pinned_corpus "$STACK"
"$FM_DOCKER" rm -f "benchmarking-$STACK" >/dev/null 2>&1 || true
"$FM_DOCKER" compose -f "$STACK_DIR/compose.yaml" up --no-deps -d benchmark
fm_require_pinned_corpus "$STACK"
fm_ledger_append "$(printf '{"arm":"%s","kind":"ragas-start","stack":"%s","started":"%s","rerun":true}' "$ARM" "$STACK" "$(fm_now)")"
fm_log "arm $ARM re-run started on $STACK (benchmark container only)"
fm_log "follow:  $FM_DOCKER logs -f benchmarking-$STACK"
