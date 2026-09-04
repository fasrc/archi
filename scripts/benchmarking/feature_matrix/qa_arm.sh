#!/usr/bin/env bash
# Run the QA (gold-atoms) evaluator against a feature-matrix stack (plan §5.3).
#
#   qa_arm.sh <arm> <arm.yaml> [--stack <name>] [--run N] [--dataset <qa-v2.json>] [--profile <yaml>]
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

ARM="${1:-}"; fm_require_arm "$ARM"; YAML="${2:-}"; fm_require_arm_yaml "$ARM" "$YAML"; shift 2
STACK="fm-$ARM"; RUN=1
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
FM_RENDERED="$RENDERED" FM_AGENT_CFG="$AGENT_CFG" "$FM_PYTHON" - <<'EOF'
import os, yaml
c = yaml.safe_load(open(os.environ["FM_RENDERED"]))
b = c["services"]["benchmarking"]; ca = c["services"].setdefault("chat_app", {})
ca["agent_class"] = b["agent_class"]
ca["default_provider"] = b["provider"]
ca["default_model"] = b["model"]
# the console refuses a config that carries its own evaluations block; the CLI does not
# need it either
ca.pop("evaluations", None)
with open(os.environ["FM_AGENT_CFG"], "w") as f:
    yaml.safe_dump(c, f, sort_keys=False)
print(f"agent config: {ca['agent_class']} / {ca['default_provider']} / {ca['default_model']}")
EOF

OUT_DIR="$FM_OUT/qa/$STACK-arm$ARM-r$RUN"
[ ! -e "$OUT_DIR" ] || fm_die "output dir exists: $OUT_DIR (pick --run N+1)"
STARTED="$(fm_now)"
fm_log "QA run for arm $ARM on $STACK → $OUT_DIR"
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
fm_ledger_append "$(printf '{"arm":"%s","kind":"qa","stack":"%s","run":%s,"started":"%s","finished":"%s","output_dir":"%s","dataset":"%s","profile":"%s","spec":"%s","arm_config":"%s","rendered_config_sha256":"%s","corpus_fingerprint":"%s","fingerprint_source":"live-stack-equals-pin","dataset_sha256":"%s","profile_sha256":"%s","spec_sha256":"%s","lock_sha256":"%s"}' \
  "$ARM" "$STACK" "$RUN" "$STARTED" "$(fm_now)" "$OUT_DIR" "$DATASET" "$PROFILE" "$SPEC" "$YAML" "$CFG_SHA" "$FINGERPRINT" \
  "$(fm_sha256 "$DATASET")" "$(fm_sha256 "$PROFILE")" "$(fm_sha256 "$SPEC")" "$(fm_lock_sha)")"
fm_log "done; report: $OUT_DIR/report.md  summary: $OUT_DIR/summary.json"
