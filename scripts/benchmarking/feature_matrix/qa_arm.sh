#!/usr/bin/env bash
# Run the QA (gold-atoms) evaluator against a feature-matrix stack (plan §5.3).
#
#   qa_arm.sh <arm> <arm.yaml> [--stack <name>] [--run N] [--dataset <qa-v2.json>] [--profile <yaml>]
#
# --run defaults to the next unused number for this stack and arm (the closing baseline's
# QA run becomes r2 next to the opening r1). Dataset and profile overrides must hash to the
# campaign lock's values, so they exist for the smoke, not for changing inputs.
#
# 0. Proves the stack is on the requested arm: every factor key in the arm YAML must equal
#    the stack's rendered config (chunking, processing, stemming, hierarchical_rerank), or
#    it refuses — a retrieval arm left on fm-00 after a restore, or a wrong --stack, would
#    otherwise yield a plausible QA record for the wrong configuration. Also proves the
#    corpus still equals the stack's pin (the fingerprint the RAGAS runs were archived
#    with), else refuses: a drifted corpus is not comparable. The ledger entry records the
#    rendered config's sha256 and the pinned corpus fingerprint.
# 1. Writes a secret-free agent config from the stack's rendered configs/config.yaml with
#    services.chat_app.{agent_class,default_provider,default_model} overwritten from
#    services.benchmarking.{agent_class,provider,model}. An evaluate stack renders the
#    template defaults into chat_app (CMSCompOpsAgent / local / llama3.2); the QA CLI reads
#    chat_app, so without this step it scores the wrong agent against a nonexistent Ollama.
# 2. Runs `archi eval qa` against the stack's Postgres and data-manager (host network),
#    with the production spec, the campaign judge profile, one attempt per question, one
#    run worker (matches the harness's sequential calls, so duration_ms ~ time_elapsed).
# Never run concurrently with a RAGAS run on the same stack: latency would be shared.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Sweep mode: qa_arm.sh --sweep <sweep_dir> --stack <name> --arm <prompt-stem> [--run N]
# One arm of a locked prompt sweep, with that arm's own prompt, on a COPY of the prepared
# gold atoms (qa_prepare.sh --sweep): `archi eval qa run` then `score`, between two corpus
# pin checks and two category-map readings written to category_map_readings.json.
if [ "${1:-}" = --sweep ]; then
  SWEEP_DIR="${2:?--sweep needs the sweep directory}"; shift 2
  STACK=""; STEM=""; RUN=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --stack) STACK="${2:?}"; shift 2 ;;
      --arm) STEM="${2:?}"; shift 2 ;;
      --run) RUN="${2:?}"; shift 2 ;;
      *) fm_die "unknown option $1" ;;
    esac
  done
  fm_require_stack_name "$STACK"
  fm_require_sweep_lock "$STACK" --stamped
  LOCK="$(fm_sweep_lock_file "$STACK")"
  SPEC="$(fm_sweep_tools verify --lock "$LOCK" --field "arms.$STEM.prompt")"
  [ -n "$SPEC" ] || fm_die "arm '$STEM' is not in the sweep lock $LOCK"
  PROFILE="$(fm_sweep_tools verify --lock "$LOCK" --field qa.profile)"
  PREPARED="$(fm_sweep_tools verify --lock "$LOCK" --field qa.prepared)"
  STACK_DIR="$(fm_stack_dir "$STACK")"
  [ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "a RAGAS run is in flight on $STACK; QA runs are serial"
  [ -f "$STACK_DIR/secrets/pg_password.txt" ] || fm_die "no $STACK_DIR/secrets/pg_password.txt"
  if [ -z "$RUN" ]; then
    RUN=1
    while [ -e "$FM_OUT/qa/$STACK-$STEM-r$RUN" ]; do RUN=$((RUN + 1)); done
  fi
  fm_require_run_number "$RUN"
  OUT_DIR="$FM_OUT/qa/$STACK-$STEM-r$RUN"
  [ ! -e "$OUT_DIR" ] || fm_die "output dir exists: $OUT_DIR (pick --run N+1)"
  fm_require_pinned_corpus "$STACK"
  FINGERPRINT="$(tr -d '[:space:]' < "$(fm_pin_file "$STACK")")"
  AGENT_CFG="$FM_OUT/qa/$STACK-$STEM.agent-config.yaml"
  fm_write_agent_config "$STACK_DIR/configs/config.yaml" "$AGENT_CFG"
  cp -R "$PREPARED" "$OUT_DIR"
  STARTED="$(fm_now)"
  MAP_START="$(fm_category_map_digest "$STACK")"
  fm_log "QA run for sweep arm $STEM on $STACK → $OUT_DIR"
  export PG_PASSWORD_FILE="$STACK_DIR/secrets/pg_password.txt"
  export HUIT_API_KEY_FILE="${HUIT_API_KEY_FILE:-$STACK_DIR/secrets/huit_api_key.txt}"
  export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}" HOST_MODE=1
  "$FM_ARCHI" eval qa run "$OUT_DIR" --agent-config "$AGENT_CFG" --agent-spec "$SPEC" --attempts 1 --run-workers 1
  "$FM_ARCHI" eval qa score "$OUT_DIR" --evaluator-profile "$PROFILE" --score-workers "${FM_SCORE_WORKERS:-4}"
  MAP_END="$(fm_category_map_digest "$STACK")"
  fm_write_map_readings "$OUT_DIR" "$MAP_START" "$MAP_END"
  AFTER="$(fm_fingerprint "$STACK")"
  [ "$AFTER" = "$FINGERPRINT" ] || fm_die "corpus changed during the QA run (pin $FINGERPRINT, now $AFTER); output kept at $OUT_DIR but NOT recorded — the run is void"
  fm_ledger_append "$(printf '{"arm":"%s","kind":"qa","sweep":true,"stack":"%s","run":%s,"started":"%s","finished":"%s","output_dir":"%s","spec":"%s","spec_sha256":"%s","corpus_fingerprint":"%s","category_map_sha256_start":"%s","category_map_sha256_end":"%s","lock_sha256":"%s","code_sha":"%s"}' \
    "$STEM" "$STACK" "$RUN" "$STARTED" "$(fm_now)" "$OUT_DIR" "$SPEC" "$(fm_sha256 "$SPEC")" "$FINGERPRINT" "$MAP_START" "$MAP_END" "$(fm_sha256 "$LOCK")" "$(fm_code_sha)")"
  fm_log "done; join with: compare_runs.py <artifact> --qa-run $STEM=$OUT_DIR"
  exit 0
fi

ARM="${1:-}"; fm_require_arm "$ARM"; YAML="${2:-}"; fm_require_arm_yaml "$ARM" "$YAML"; shift 2
STACK="fm-$ARM"; RUN=""
DATASET="$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"
PROFILE="config/benchmarking/feature_matrix/qa/evaluator-profile.huit.yaml"
SPEC="${FM_AGENT_SPEC:-config/agents/claw/fasrc-docs.md}"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?}"; shift 2 ;;
    --run) RUN="${2:?}"; shift 2 ;;
    --dataset) DATASET="${2:?}"; shift 2 ;;
    --profile) PROFILE="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done
# Without --run, take the next unused run number for this stack+arm: the closing baseline's
# QA run lands beside the opening one as r2 instead of refusing on an existing directory.
if [ -z "$RUN" ]; then
  RUN=1
  while [ -e "$FM_OUT/qa/$STACK-arm$ARM-r$RUN" ]; do RUN=$((RUN + 1)); done
fi
fm_require_run_number "$RUN"
STACK_DIR="$(fm_stack_dir "$STACK")"
RENDERED="$STACK_DIR/configs/config.yaml"
[ -f "$RENDERED" ] || fm_die "no rendered config at $RENDERED"
[ -f "$DATASET" ] || fm_die "QA dataset not found: $DATASET — convert the bank first: scripts/benchmarking/ragas_bank_to_qa_dataset.py (fasrc/archi#418)"
[ -f "$PROFILE" ] || fm_die "evaluator profile not found: $PROFILE"
[ -f "$SPEC" ] || fm_die "agent spec not found: $SPEC"
[ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "a RAGAS run is in flight on $STACK; QA runs are serial"
[ -f "$STACK_DIR/secrets/pg_password.txt" ] || fm_die "no $STACK_DIR/secrets/pg_password.txt"
# Content, not file names, decides: the dataset, the profile and the spec must hash to the
# values the campaign lock recorded, whatever path they were given under.
fm_require_lock "$YAML"
fm_require_locked_arm "$ARM" "$YAML"
fm_require_stack_lock "$STACK"
fm_require_code_lock                      # the QA agent runs THIS checkout's code in-process
[ "$(fm_sha256 "$DATASET")" = "$(fm_lock_field qa.dataset_sha256)" ] || fm_die "QA dataset $DATASET does not match the campaign lock (locked: $(fm_lock_field qa.dataset))"
[ "$(fm_sha256 "$PROFILE")" = "$(fm_lock_field qa.profile_sha256)" ] || fm_die "evaluator profile $PROFILE does not match the campaign lock (locked: $(fm_lock_field qa.profile))"
[ "$(fm_sha256 "$SPEC")" = "$(fm_lock_field files.prompt.sha256)" ] || fm_die "agent spec $SPEC does not match the locked prompt ($(fm_lock_field files.prompt.path))"
fm_verify_stack_matches_arm "$STACK" "$YAML"
fm_require_pinned_corpus "$STACK"        # the QA run must score the SAME corpus the RAGAS runs pinned
CFG_SHA="$(fm_sha256 "$RENDERED")"
FINGERPRINT="$(tr -d '[:space:]' < "$(fm_pin_file "$STACK")")"
fm_log "stack $STACK is on arm $ARM (config sha256 ${CFG_SHA:0:12}, corpus $FINGERPRINT)"

mkdir -p "$FM_OUT/qa"
AGENT_CFG="$FM_OUT/qa/$ARM.agent-config.yaml"
fm_write_agent_config "$RENDERED" "$AGENT_CFG"

OUT_DIR="$FM_OUT/qa/$STACK-arm$ARM-r$RUN"
[ ! -e "$OUT_DIR" ] || fm_die "output dir exists: $OUT_DIR (pick --run N+1)"
STARTED="$(fm_now)"
fm_log "QA run for arm $ARM on $STACK → $OUT_DIR"
# The category map is read around the QA run too (#538 rule 1): compare_runs joins a QA
# run to a digest-bearing arm only when both readings equal the arm's end digest.
MAP_START="$(fm_category_map_digest "$STACK")"
PG_PASSWORD_FILE="$STACK_DIR/secrets/pg_password.txt" \
HUIT_API_KEY_FILE="${HUIT_API_KEY_FILE:-$STACK_DIR/secrets/huit_api_key.txt}" \
OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}" HOST_MODE=1 \
"$FM_ARCHI" eval qa \
  --dataset "$DATASET" \
  --agent-config "$AGENT_CFG" \
  --agent-spec "$SPEC" \
  --evaluator-profile "$PROFILE" \
  --output-dir "$OUT_DIR" \
  --attempts 1 --run-workers 1 --score-workers "${FM_SCORE_WORKERS:-4}"
# The QA run takes an hour or more and nothing samples the corpus for it the way the harness
# does for RAGAS arms: prove it is still the pinned corpus AFTER the run, or the answers may
# span two corpus states and the row must not be written.
MAP_END="$(fm_category_map_digest "$STACK")"
fm_write_map_readings "$OUT_DIR" "$MAP_START" "$MAP_END"
AFTER="$(fm_fingerprint "$STACK")"
[ "$AFTER" = "$FINGERPRINT" ] || fm_die "corpus changed during the QA run (pin $FINGERPRINT, now $AFTER); output kept at $OUT_DIR but NOT recorded — the run is void"
fm_ledger_append "$(printf '{"arm":"%s","kind":"qa","stack":"%s","run":%s,"started":"%s","finished":"%s","output_dir":"%s","dataset":"%s","profile":"%s","spec":"%s","arm_config":"%s","rendered_config_sha256":"%s","corpus_fingerprint":"%s","fingerprint_source":"live-stack-equals-pin","dataset_sha256":"%s","profile_sha256":"%s","spec_sha256":"%s","lock_sha256":"%s","code_sha":"%s","category_map_sha256_start":"%s","category_map_sha256_end":"%s"}' \
  "$ARM" "$STACK" "$RUN" "$STARTED" "$(fm_now)" "$OUT_DIR" "$DATASET" "$PROFILE" "$SPEC" "$YAML" "$CFG_SHA" "$FINGERPRINT" \
  "$(fm_sha256 "$DATASET")" "$(fm_sha256 "$PROFILE")" "$(fm_sha256 "$SPEC")" "$(fm_lock_sha)" "$(fm_code_sha)" "$MAP_START" "$MAP_END")"
fm_log "done; report: $OUT_DIR/report.md  summary: $OUT_DIR/summary.json"
